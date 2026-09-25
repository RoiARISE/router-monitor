import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.state_machines.upstream_sm import UpstreamStateMachine, UpstreamState


def test_stays_normal_when_everything_ok():
    sm = UpstreamStateMachine(fail_threshold=6)
    for _ in range(10):
        sm.tick(router_ok=True, external_ok=True)
    assert sm.state == UpstreamState.NORMAL


def test_stays_normal_under_threshold():
    sm = UpstreamStateMachine(fail_threshold=6)
    for _ in range(5):
        sm.tick(router_ok=True, external_ok=False)
    assert sm.state == UpstreamState.NORMAL


def test_enters_down_at_threshold():
    sm = UpstreamStateMachine(fail_threshold=6)
    for _ in range(6):
        sm.tick(router_ok=True, external_ok=False)
    assert sm.state == UpstreamState.DOWN


def test_recovers_to_normal():
    sm = UpstreamStateMachine(fail_threshold=6)
    for _ in range(6):
        sm.tick(router_ok=True, external_ok=False)
    assert sm.state == UpstreamState.DOWN

    sm.tick(router_ok=True, external_ok=True)
    assert sm.state == UpstreamState.NORMAL
    assert sm.fail_count == 0


def test_notification_fires_only_once_while_down():
    notified = []
    sm = UpstreamStateMachine(fail_threshold=3, notifier=lambda msg: notified.append(msg))
    for _ in range(3):
        sm.tick(router_ok=True, external_ok=False)
    assert len(notified) == 1

    for _ in range(5):
        sm.tick(router_ok=True, external_ok=False)
    assert len(notified) == 1  # still just one, no spam


def test_router_down_case_is_ignored_by_upstream_sm():
    # not router_ok, regardless of external -> upstream machine treats as
    # "not our problem", since this is purely router-side fail count,
    # and resets its own counter (it's not tracking router failures)
    sm = UpstreamStateMachine(fail_threshold=3)
    for _ in range(10):
        sm.tick(router_ok=False, external_ok=False)
    assert sm.state == UpstreamState.NORMAL


def test_total_blackout_does_not_send_recovery_notification():
    """上流障害（UPSTREAM_DOWN）発生中にルーターも落ちて全断になった場合、
    『復旧しました』と誤通知されないこと。"""
    notified = []
    sm = UpstreamStateMachine(fail_threshold=2, notifier=lambda msg: notified.append(msg))
    sm.tick(router_ok=True, external_ok=False)
    sm.tick(router_ok=True, external_ok=False)
    assert sm.state == UpstreamState.DOWN
    assert len(notified) == 1
    assert "不通" in notified[0]

    # 全断（ルーターも落ちた）
    sm.tick(router_ok=False, external_ok=False)
    assert sm.state == UpstreamState.NORMAL
    # 「復旧」通知は送られていないこと
    assert not any("復旧" in msg for msg in notified)

