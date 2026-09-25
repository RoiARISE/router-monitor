"""
ROUTER_NORMAL -> ROUTER_DOWN -> POWER_CYCLE_REQUEST
   -> POWER_CYCLE_WAIT -> VERIFYING -> ROUTER_NORMAL

電源サイクル失敗時:
  POWER_CYCLE_REQUEST -> FAILED
  FAILED -> (failed_resume_seconds 後) -> ROUTER_SUSPECT (監視再開、retry_count は維持)

注意:
- ROUTER_SUSPECT は FAILED からの復帰時にのみ経由する。
  通常の障害検知では ROUTER_NORMAL -> ROUTER_DOWN に直接遷移する。
- DOWN 状態で自然回復（router_ok=True, external_ok=True）を検知した場合は
  電源サイクルを行わず ROUTER_NORMAL へ直接戻る。
- VERIFYING 中に fail_threshold 回連続失敗した場合は ROUTER_DOWN へ戻る。
- POWER_CYCLE_REQUEST で perform_power_cycle() が呼ばれないまま cooldown_seconds
  が経過した場合は FAILED へ遷移するタイムアウト安全弁を持つ。
- FAILED 通知は障害セッション中の最初の遷移時のみ送信し、重複を抑制する。
  _failed_notified フラグは _enter_normal() でリセットされる。

retry_count は ROUTER_NORMAL に戻ったときのみリセットされます（障害セッションの境界）。"""

import time
from enum import Enum
from typing import Callable, Optional


class RouterState(str, Enum):
    NORMAL = "ROUTER_NORMAL"
    SUSPECT = "ROUTER_SUSPECT"
    DOWN = "ROUTER_DOWN"
    POWER_CYCLE_REQUEST = "POWER_CYCLE_REQUEST"
    POWER_CYCLE_WAIT = "POWER_CYCLE_WAIT"
    VERIFYING = "VERIFYING"
    FAILED = "FAILED"


class RouterStateMachine:
    def __init__(
        self,
        fail_threshold: int = 6,
        cooldown_seconds: int = 90,
        reboot_lock_seconds: int = 300,
        max_retry: int = 3,
        verify_success_required: int = 3,
        failed_resume_seconds: int = 300,
        off_duration_seconds: int = 30,
        relay=None,
        logger: Optional[Callable] = None,
        notifier: Optional[Callable] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.fail_threshold = fail_threshold
        self.cooldown_seconds = cooldown_seconds
        self.reboot_lock_seconds = reboot_lock_seconds
        self.max_retry = max_retry
        self.verify_success_required = verify_success_required
        self.failed_resume_seconds = failed_resume_seconds
        self.off_duration_seconds = off_duration_seconds

        self.relay = relay
        self.log = logger or (lambda event, **kw: None)
        self.notify = notifier or (lambda msg: None)
        self.clock = clock

        self.state = RouterState.NORMAL
        self.fail_count = 0
        self.verify_success_count = 0
        self.verify_fail_count = 0
        self.retry_count = 0
        self.last_reboot_time: Optional[float] = None
        self._cooldown_until: Optional[float] = None
        self._failed_since: Optional[float] = None
        # #1: POWER_CYCLE_REQUEST タイムアウト検知用
        self._power_cycle_request_since: Optional[float] = None
        # #4: FAILED 通知の重複抑制フラグ（障害セッション中1回のみ通知）
        self._failed_notified: bool = False

    # -- public API -----------------------------------------------------

    def tick(self, router_ok: bool, external_ok: bool) -> None:
        """監視サイクルごとに 1回呼び出され、最新の ping 結果を受け取ります。"""
        handler = getattr(self, f"_handle_{self.state.name.lower()}", None)
        if handler is None:
            raise RuntimeError(f"no handler for state {self.state}")
        handler(router_ok, external_ok)

    # -- state handlers ---------------------------------------------------

    def _handle_normal(self, router_ok: bool, external_ok: bool) -> None:
        # NOTE: ここで fail_threshold に達した場合は DOWN の確定のみを行います。
        # 実際の電源サイクル要求は「次の tick」で _handle_down() 内で行われます。
        # これにより、POWER_CYCLE_REQUEST の設計通り「DOWN の確定」と
        # 「電源サイクル要求」を、2つの別々のイベントとしてログに記録できます。
        if router_ok and external_ok:
            self.fail_count = 0
            return
        if not router_ok and not external_ok:
            self.fail_count += 1
            if self.fail_count >= self.fail_threshold:
                self._enter_down()
        else:
            # router_ok=True かつ external_ok=False → 上流の障害であり、ここでの関心事ではありません
            # router_ok=False かつ external_ok=True → 矛盾した読み取りのため無視します
            self.fail_count = 0

    def _handle_suspect(self, router_ok: bool, external_ok: bool) -> None:
        if router_ok and external_ok:
            # FAILED から SUSPECT に復帰後、正常通信が確認できたら正式に NORMAL に戻し、
            # retry_count 等のセッション状態を完全にリセットする。
            self._enter_normal()
            return
        self._handle_normal(router_ok, external_ok)

    def _handle_down(self, router_ok: bool, external_ok: bool) -> None:
        # #5: DOWN 確定後に自然回復した場合は無駄な電源サイクルを避けて NORMAL へ戻る
        if router_ok and external_ok:
            self.log("router_recovered_from_down_without_power_cycle")
            self._enter_normal()
            return

        # ルーター自身は応答しているが外部が不通の場合（上流回線の障害）：
        # 通常状態と同様に、ルーター自体の再起動は行わず NORMAL へ戻る。
        if router_ok and not external_ok:
            self.log("router_up_but_upstream_down_cancelling_power_cycle")
            self._enter_normal()
            return

        if self.retry_limit_reached:
            self.log("retry_limit_reached_skipping_power_cycle", retry_count=self.retry_count)
            self._enter_failed()
            return
        if self._can_reboot():
            self._enter_power_cycle_request()
        else:
            remaining = self.reboot_lock_seconds - (self.clock() - self.last_reboot_time)
            self.log("reboot_skipped_due_to_lock", remaining_seconds=round(remaining, 1))
            # DOWN に留まります。次の tick でロックを再確認します。

    def _handle_power_cycle_request(self, router_ok: bool, external_ok: bool) -> None:
        # #1: 本来 tick() がこの状態で呼ばれることはなく、
        # main.py が即座に perform_power_cycle() を呼ぶはず。
        # 何らかの異常（例外握りつぶし等）で呼ばれ続ける場合の
        # タイムアウト安全弁。cooldown_seconds を上限として流用する。
        if self._power_cycle_request_since is None:
            return  # 初期化前（通常は起きない）
        elapsed = self.clock() - self._power_cycle_request_since
        if elapsed >= self.cooldown_seconds:
            self.log("power_cycle_request_timed_out", elapsed_seconds=round(elapsed, 1))
            self.retry_count += 1
            self._power_cycle_request_since = None
            self._enter_failed()

    def _handle_power_cycle_wait(self, router_ok: bool, external_ok: bool) -> None:
        if self.clock() >= self._cooldown_until:
            self.state = RouterState.VERIFYING
            self.verify_success_count = 0
            self.verify_fail_count = 0
            self.log("entered_verifying")

    def _handle_verifying(self, router_ok: bool, external_ok: bool) -> None:
        if router_ok and external_ok:
            self.verify_success_count += 1
            self.verify_fail_count = 0
            if self.verify_success_count >= self.verify_success_required:
                self._enter_normal()
        else:
            # verify_success_count をリセットすることで1回の失敗（起動時の不安定さ）は
            # 即座に DOWN へ戻すことなく許容されます。ただし、永遠に VERIFYING に
            # 留まることはできません。fail_threshold 回連続で失敗が続いた場合は
            # おそらくルーターが実際には復旧していないため、DOWN へ戻り
            # 通常の再起動ロック/リトライカウントの仕組みに次の電源サイクルを
            # 試行するかどうかを委ねます。
            self.verify_success_count = 0
            self.verify_fail_count += 1
            if self.verify_fail_count >= self.fail_threshold:
                self.log(
                    "verifying_timed_out_returning_to_down",
                    consecutive_failures=self.verify_fail_count,
                )
                self.verify_fail_count = 0
                self._enter_down()

    def _handle_failed(self, router_ok: bool, external_ok: bool) -> None:
        if router_ok and external_ok:
            # FAILED 状態の間にネットワークが自然に回復した場合
            self._enter_normal()
            return
        if self.clock() - self._failed_since >= self.failed_resume_seconds:
            self.state = RouterState.SUSPECT
            self.fail_count = 0
            self.log("resumed_monitoring_from_failed", retry_count=self.retry_count)

    # -- transitions -------------------------------------------------------

    def _enter_down(self) -> None:
        self.state = RouterState.DOWN
        self.log("router_down_confirmed", fail_count=self.fail_count)

    def _can_reboot(self) -> bool:
        if self.last_reboot_time is None:
            return True
        return (self.clock() - self.last_reboot_time) >= self.reboot_lock_seconds

    def _enter_power_cycle_request(self) -> None:
        self.state = RouterState.POWER_CYCLE_REQUEST
        self._power_cycle_request_since = self.clock()  # タイムアウト計測開始
        self.log("power_cycle_request_started", attempt=self.retry_count + 1)

    def perform_power_cycle(self) -> bool:
        """
        tick() によってステートマシンが POWER_CYCLE_REQUEST 状態に移行した直後に
        呼び出し元から呼び出される必要があります。サイクルが正常に要求されて
        POWER_CYCLE_WAIT へ移行した場合は True を、失敗して FAILED へ移行した場合は
        False を返します。
        """
        if self.state != RouterState.POWER_CYCLE_REQUEST:
            raise RuntimeError("perform_power_cycle called outside POWER_CYCLE_REQUEST")

        self._power_cycle_request_since = None  # タイムアウト計測クリア

        try:
            self.relay.power_cycle(off_duration=self.off_duration_seconds)
        except Exception as exc:
            self.log("power_cycle_request_failed", error=str(exc))
            self.retry_count += 1
            self._enter_failed()
            return False

        self.retry_count += 1
        self.last_reboot_time = self.clock()
        self._cooldown_until = self.clock() + self.cooldown_seconds
        self.state = RouterState.POWER_CYCLE_WAIT
        self.log(
            "power_cycle_request_succeeded",
            attempt=self.retry_count,
            cooldown_seconds=self.cooldown_seconds,
        )

        # retry_count >= max_retry の場合、次回復旧せずに DOWN が確定した際は
        # tick() の down 処理を経て直接 FAILED に移行します。
        return True

    def _enter_failed(self) -> None:
        self.state = RouterState.FAILED
        self._failed_since = self.clock()
        self.log("entered_failed", retry_count=self.retry_count)
        # #4: 障害セッション中の最初の FAILED 遷移時のみ通知（重複抑制）
        if not self._failed_notified:
            self.notify(f"🚨 ルーター復旧失敗(電源操作{self.retry_count}回試行済み)")
            self._failed_notified = True

    def _enter_normal(self) -> None:
        was_failing = self.retry_count > 0
        self.state = RouterState.NORMAL
        self.fail_count = 0
        self.verify_success_count = 0
        self.verify_fail_count = 0
        self._failed_notified = False  # #4: 障害セッション終了でリセット
        if was_failing:
            self.log("failure_session_closed", total_retries=self.retry_count)
            self.notify(f"✅ ルーターを再起動しました（{self.retry_count}回目でネット安定を確認）")
        self.retry_count = 0

    # -- convenience ---------------------------------------------------

    @property
    def awaiting_power_cycle(self) -> bool:
        return self.state == RouterState.POWER_CYCLE_REQUEST

    @property
    def retry_limit_reached(self) -> bool:
        return self.retry_count >= self.max_retry