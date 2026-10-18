import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.state_machines.router_sm import RouterStateMachine, RouterState


class FakeClock:
    """Manually-advanceable clock for deterministic tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRelay:
    def __init__(self, fail_n_times: int = 0):
        self.fail_n_times = fail_n_times
        self.calls = 0

    def power_cycle(self, off_duration: int) -> None:
        self.calls += 1
        if self.calls <= self.fail_n_times:
            raise RuntimeError("simulated relay failure")


def make_sm(relay=None, clock=None, **overrides):
    clock = clock or FakeClock()
    defaults = dict(
        fail_threshold=6,
        cooldown_seconds=90,
        reboot_lock_seconds=300,
        max_retry=3,
        verify_success_required=3,
        failed_resume_seconds=300,
        off_duration_seconds=30,
        relay=relay or FakeRelay(),
        clock=clock,
    )
    defaults.update(overrides)
    return RouterStateMachine(**defaults), clock


def run_failures(sm, n):
    for _ in range(n):
        sm.tick(router_ok=False, external_ok=False)


def test_stays_normal_under_threshold():
    sm, _ = make_sm()
    run_failures(sm, 5)  # threshold is 6
    assert sm.state == RouterState.NORMAL


def test_enters_down_at_threshold():
    sm, _ = make_sm()
    run_failures(sm, 6)
    assert sm.state == RouterState.DOWN


def test_single_success_resets_fail_count():
    sm, _ = make_sm()
    run_failures(sm, 5)
    sm.tick(router_ok=True, external_ok=True)
    assert sm.fail_count == 0
    run_failures(sm, 5)
    assert sm.state == RouterState.NORMAL  # still under threshold


def test_full_recovery_cycle():
    sm, clock = make_sm()
    run_failures(sm, 6)
    assert sm.state == RouterState.DOWN

    sm.tick(router_ok=False, external_ok=False)  # triggers power cycle request
    assert sm.state == RouterState.POWER_CYCLE_REQUEST

    ok = sm.perform_power_cycle()
    assert ok is True
    assert sm.state == RouterState.POWER_CYCLE_WAIT
    assert sm.retry_count == 1

    # before cooldown elapses, stay in WAIT
    clock.advance(50)
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.POWER_CYCLE_WAIT

    # after cooldown elapses, move to VERIFYING
    clock.advance(50)  # total 100s > 90s cooldown
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING

    # need 3 consecutive successes once inside VERIFYING
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL
    assert sm.retry_count == 0  # session closed, reset


def test_verifying_resets_on_failure_does_not_fall_back_to_down():
    sm, clock = make_sm()
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    sm.perform_power_cycle()
    clock.advance(100)
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING

    sm.tick(router_ok=True, external_ok=True)  # 1/3
    sm.tick(router_ok=False, external_ok=False)  # single bad tick: reset count, but stay in VERIFYING
    assert sm.state == RouterState.VERIFYING
    assert sm.verify_success_count == 0

    sm.tick(router_ok=True, external_ok=True)
    sm.tick(router_ok=True, external_ok=True)
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL


def test_verifying_returns_to_down_after_sustained_failure():
    """Regression test for the real-world bug: router cooled down,
    entered VERIFYING, but never actually came back. Without a timeout,
    the state machine would stay in VERIFYING forever and never attempt
    another power cycle."""
    sm, clock = make_sm(fail_threshold=6)
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    sm.perform_power_cycle()
    assert sm.retry_count == 1

    clock.advance(100)  # past cooldown
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.VERIFYING  # entered_verifying happens regardless of ping result

    # keep failing inside VERIFYING for fail_threshold consecutive ticks
    for _ in range(sm.fail_threshold - 1):
        sm.tick(router_ok=False, external_ok=False)
        assert sm.state == RouterState.VERIFYING  # not yet

    sm.tick(router_ok=False, external_ok=False)  # this is the 6th consecutive failure
    assert sm.state == RouterState.DOWN
    assert sm.verify_fail_count == 0  # reset on transition

    # and the normal DOWN handling machinery takes over from here:
    # reboot lock is still active (only 100s since last reboot, lock is 300s)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.DOWN  # waiting on lock, not stuck silently

    clock.advance(250)  # past the reboot lock now
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    ok = sm.perform_power_cycle()
    assert ok is True
    assert sm.retry_count == 2  # second power cycle attempt, same failure session


def test_verifying_single_failure_does_not_count_toward_timeout_after_success():
    """A single bad tick should not silently accumulate toward the
    sustained-failure timeout if successes are interleaved."""
    sm, clock = make_sm(fail_threshold=6, verify_success_required=3)
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    sm.perform_power_cycle()
    clock.advance(100)
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING

    # alternate fail/success repeatedly - should never accumulate to
    # fail_threshold consecutive failures, so VERIFYING must not time out
    for _ in range(10):
        sm.tick(router_ok=False, external_ok=False)
        assert sm.state == RouterState.VERIFYING
        sm.tick(router_ok=True, external_ok=True)
        assert sm.verify_fail_count == 0


def test_reboot_lock_prevents_immediate_second_reboot():
    sm, clock = make_sm(reboot_lock_seconds=300)
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    sm.perform_power_cycle()
    assert sm.retry_count == 1

    clock.advance(100)  # cooldown passes
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.VERIFYING

    # verification fails repeatedly -> but VERIFYING never falls back to DOWN
    # so to test the lock we simulate going back to DOWN via a fresh failure
    # session (not realistic in isolation, but exercises _can_reboot directly)
    assert sm._can_reboot() is False  # only 100s since last reboot, lock is 300s

    clock.advance(250)  # total 350s since reboot
    assert sm._can_reboot() is True


def test_max_retry_then_failed_skips_further_power_cycles():
    sm, clock = make_sm(max_retry=3, reboot_lock_seconds=10, cooldown_seconds=10)
    relay = sm.relay

    for attempt in range(3):
        run_failures(sm, 6)
        sm.tick(router_ok=False, external_ok=False)
        assert sm.state == RouterState.POWER_CYCLE_REQUEST
        sm.perform_power_cycle()
        assert sm.state == RouterState.POWER_CYCLE_WAIT
        clock.advance(20)  # past cooldown
        sm.tick(router_ok=False, external_ok=False)  # verification fails
        assert sm.state == RouterState.VERIFYING
        sm.fail_count = sm.fail_threshold  # force back into DOWN-eligible state
        sm.state = RouterState.DOWN  # simulate re-confirmed down for next loop
        clock.advance(20)  # past reboot lock

    assert sm.retry_count == 3
    assert relay.calls == 3

    # 4th time DOWN is confirmed, retry limit reached -> FAILED, no new relay call
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.FAILED
    assert relay.calls == 3  # unchanged


def test_failed_recovers_on_its_own_when_network_returns():
    sm, clock = make_sm()
    sm.state = RouterState.FAILED
    sm._failed_since = clock.now
    sm.retry_count = 3

    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL
    assert sm.retry_count == 0


def test_failed_resumes_monitoring_after_timeout_keeping_retry_count():
    sm, clock = make_sm(failed_resume_seconds=300)
    sm.state = RouterState.FAILED
    sm._failed_since = clock.now
    sm.retry_count = 3

    clock.advance(200)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.FAILED  # not yet

    clock.advance(150)  # total 350s > 300s
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.SUSPECT
    assert sm.retry_count == 3  # preserved, same failure session


def test_power_cycle_request_failure_goes_to_failed():
    relay = FakeRelay(fail_n_times=1)
    sm, clock = make_sm(relay=relay)
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST

    ok = sm.perform_power_cycle()
    assert ok is False
    assert sm.state == RouterState.FAILED
    # リレー操作自体の失敗ではルーター再起動枠(retry_count)を消費しない
    assert sm.retry_count == 0
    assert sm.relay_fail_count == 1



def test_upstream_issue_does_not_trigger_router_down():
    sm, _ = make_sm()
    for _ in range(20):
        sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL


# -- 新規テスト: バグ修正の検証 -------------------------------------------


def test_power_cycle_request_timeout_goes_to_failed():
    """#1: POWER_CYCLE_REQUEST で perform_power_cycle() が呼ばれないまま
    cooldown_seconds が経過した場合、FAILED へ遷移する。"""
    clock = FakeClock()
    sm, _ = make_sm(
        clock=clock,
        fail_threshold=2,
        cooldown_seconds=60,
        reboot_lock_seconds=300,
    )

    # DOWN → POWER_CYCLE_REQUEST へ
    run_failures(sm, 2)
    assert sm.state == RouterState.DOWN
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST

    # perform_power_cycle() を呼ばずに tick() し続ける
    clock.advance(30)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST  # 60秒未満、まだタイムアウトしていない

    clock.advance(31)  # 合計 61秒 >= cooldown_seconds(60)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.FAILED  # タイムアウトにより FAILED へ


def test_down_state_natural_recovery_skips_power_cycle():
    """#5: DOWN 状態でルーターが自然回復した場合、電源サイクルなしで NORMAL に戻る。"""
    relay = FakeRelay()
    sm, _ = make_sm(relay=relay, fail_threshold=2)

    # DOWN に入れる
    run_failures(sm, 2)
    assert sm.state == RouterState.DOWN

    # 自然回復
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL
    assert relay.calls == 0   # 電源サイクルは一切使っていない
    assert sm.retry_count == 0


def test_failed_notification_sent_only_once_per_session():
    """#4: FAILED → SUSPECT → DOWN → FAILED とループしても
    障害セッション中の通知は1回のみ送信される。"""
    notifications = []
    clock = FakeClock()
    relay = FakeRelay(fail_n_times=99)  # 常に失敗する relay
    sm, _ = make_sm(
        relay=relay,
        clock=clock,
        fail_threshold=2,
        cooldown_seconds=5,
        reboot_lock_seconds=10,
        failed_resume_seconds=20,
        max_retry=5,
        notifier=lambda msg: notifications.append(msg),
    )

    # 1回目: DOWN → PCR → FAILED
    run_failures(sm, 2)
    sm.tick(router_ok=False, external_ok=False)  # DOWN → PCR
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    sm.perform_power_cycle()  # relay 失敗 → FAILED
    assert sm.state == RouterState.FAILED
    failed_notifs_after_1st = sum(1 for n in notifications if "復旧失敗" in n)
    assert failed_notifs_after_1st == 1

    # failed_resume 後に SUSPECT → DOWN → FAILED（2回目）
    clock.advance(21)
    sm.tick(router_ok=False, external_ok=False)  # FAILED → SUSPECT
    assert sm.state == RouterState.SUSPECT
    run_failures(sm, 2)  # SUSPECT → DOWN
    assert sm.state == RouterState.DOWN
    sm.tick(router_ok=False, external_ok=False)  # DOWN → PCR
    sm.perform_power_cycle()  # relay 失敗 → FAILED（2回目）
    # 2回目の FAILED でも通知は追加されていない（同じ障害セッション）
    failed_notifs_after_2nd = sum(1 for n in notifications if "復旧失敗" in n)
    assert failed_notifs_after_2nd == 1


def test_suspect_recovers_to_normal_and_resets_retry_count():
    """FAILED から SUSPECT に復帰後、正常通信が戻ったら NORMAL に遷移し
    retry_count や障害通知フラグが完全にリセットされること。"""
    clock = FakeClock()
    relay = FakeRelay(fail_n_times=99)
    sm, _ = make_sm(
        relay=relay,
        clock=clock,
        fail_threshold=2,
        failed_resume_seconds=20,
        max_retry=3,
    )

    # DOWN → PCR → FAILED (リレー操作失敗)
    run_failures(sm, 2)
    sm.tick(router_ok=False, external_ok=False)
    sm.perform_power_cycle()
    assert sm.state == RouterState.FAILED
    assert sm.retry_count == 0
    assert sm.relay_fail_count == 1

    # failed_resume_seconds 経過で SUSPECT へ復帰
    clock.advance(21)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.SUSPECT
    assert sm.retry_count == 0
    assert sm.relay_fail_count == 1

    # 通信が正常に戻った場合、NORMAL に戻りリセットされる
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL
    assert sm.retry_count == 0
    assert sm.relay_fail_count == 0


def test_down_state_cancels_reboot_when_router_alive_but_external_fails():
    """DOWN 確定後にルーターは応答しているが外部が不通（上流障害）になった場合、
    ルーター再起動を要求せずに NORMAL へ復帰すること。"""
    relay = FakeRelay()
    sm, _ = make_sm(relay=relay, fail_threshold=2)

    run_failures(sm, 2)
    assert sm.state == RouterState.DOWN

    # ルーターは生きていて外部のみ落ちている場合
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL
    assert relay.calls == 0


def test_upstream_prolonged_outage_triggers_reboot():
    relay = FakeRelay()
    clock = FakeClock()
    sm, _ = make_sm(
        relay=relay,
        clock=clock,
        upstream_reboot_after_seconds=600,
    )

    # 外部のみ不通を開始
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL

    # 590秒経過（まだ閾値未満）
    clock.advance(590)
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL

    # 600秒経過（閾値到達）
    clock.advance(10)
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.DOWN
    assert sm.reboot_reason == "upstream_prolonged"

    # DOWN の次の tick で、ルーターが生きていても再起動要求へ移行すること
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    assert sm.awaiting_power_cycle is True

    ok = sm.perform_power_cycle()
    assert ok is True
    assert relay.calls == 1
    assert sm.state == RouterState.POWER_CYCLE_WAIT


def test_upstream_prolonged_outage_timer_resilient_to_router_flaps():
    """ルーターの ping が一時的に途切れても（自PCのWiFi切断など）、
    外部到達が回復しない限りタイマーがリセットされないこと。"""
    relay = FakeRelay()
    clock = FakeClock()
    sm, _ = make_sm(
        relay=relay,
        clock=clock,
        upstream_reboot_after_seconds=600,
        fail_threshold=6,
    )

    # 開始: 外部不通
    sm.tick(router_ok=True, external_ok=False)
    clock.advance(200)

    # 途中: ルーターへの ping も2回失敗（WiFi瞬断、fail_threshold未満）
    sm.tick(router_ok=False, external_ok=False)
    sm.tick(router_ok=False, external_ok=False)
    clock.advance(200)

    # 再びルーター応答あり、外部は依然不通
    sm.tick(router_ok=True, external_ok=False)
    clock.advance(200)  # 合計600秒経過

    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.DOWN
    assert sm.reboot_reason == "upstream_prolonged"


def test_upstream_prolonged_outage_resets_on_external_recovery():
    clock = FakeClock()
    sm, _ = make_sm(clock=clock, upstream_reboot_after_seconds=600)

    sm.tick(router_ok=True, external_ok=False)
    clock.advance(500)
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL

    # 外部が1回回復
    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == RouterState.NORMAL

    # 再び不通になってから200秒経過（合計では700秒だがリセットされたので未到達）
    sm.tick(router_ok=True, external_ok=False)
    clock.advance(200)
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL


def test_upstream_prolonged_outage_limited_connectivity_shortens_wait():
    clock = FakeClock()
    current_conn = ["limited"]
    sm, _ = make_sm(
        clock=clock,
        upstream_reboot_after_seconds=600,
        upstream_reboot_after_seconds_limited=300,
        connectivity=lambda: current_conn[0],
    )

    sm.tick(router_ok=True, external_ok=False)
    clock.advance(300)
    sm.tick(router_ok=True, external_ok=False)
    # limited のため 300秒で DOWN になる
    assert sm.state == RouterState.DOWN
    assert sm.reboot_reason == "upstream_prolonged"


def test_upstream_prolonged_outage_skipped_when_host_link_down():
    clock = FakeClock()
    host_up = [False]
    sm, _ = make_sm(
        clock=clock,
        upstream_reboot_after_seconds=600,
        host_link_ok=lambda: host_up[0],
    )

    sm.tick(router_ok=True, external_ok=False)
    clock.advance(700)
    # 自PCのNICがdownなら再起動トリガーされない
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.NORMAL

    # NICが復帰したら次のtickでトリガーされる
    host_up[0] = True
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == RouterState.DOWN


def test_session_reset_after_sustained_outage():
    """長時間の障害（session_reset_seconds 経過）で retry_count がリセットされ、
    再度電源サイクルが試行可能になること。"""
    relay = FakeRelay()
    clock = FakeClock()
    sm, _ = make_sm(
        relay=relay,
        clock=clock,
        max_retry=1,
        reboot_lock_seconds=100,
        failed_resume_seconds=200,
        session_reset_seconds=1800,
    )

    # 1回目の再起動
    run_failures(sm, 6)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    sm.perform_power_cycle()
    assert sm.retry_count == 1
    assert relay.calls == 1

    # クールダウン後、復旧せず DOWN へ戻る
    clock.advance(95)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.VERIFYING
    for _ in range(6):
        sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.DOWN

    # retry_count >= max_retry (1) なので FAILED へ遷移し、再起動は行われない
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.FAILED
    assert relay.calls == 1

    # failed_resume_seconds(200s) 経過で SUSPECT へ。まだ 1800s 未満なので retry_count=1 は維持
    clock.advance(200)
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.SUSPECT
    assert sm.retry_count == 1

    # 再び DOWN -> FAILED (再起動スキップ)
    run_failures(sm, 6)
    assert sm.state == RouterState.DOWN
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.FAILED
    assert relay.calls == 1

    # セッション開始から 1800s 経過後に FAILED から復帰
    clock.advance(1600)  # 合計 > 1800s
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.SUSPECT
    # retry_count が 0 にリセットされている！
    assert sm.retry_count == 0

    # 再度 DOWN になったとき、電源サイクルが実行可能になっている！
    run_failures(sm, 6)
    assert sm.state == RouterState.DOWN
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == RouterState.POWER_CYCLE_REQUEST
    sm.perform_power_cycle()
    assert relay.calls == 2
    assert sm.retry_count == 1