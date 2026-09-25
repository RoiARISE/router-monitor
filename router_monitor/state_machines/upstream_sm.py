"""
UPSTREAM_NORMAL -> UPSTREAM_SUSPECT -> UPSTREAM_DOWN -> UPSTREAM_NORMAL

router_ok が True であるものの、external_ok が False の状態が
`fail_threshold` 回連続した場合にトリガーされます。リレーの操作は行わず、
ログ出力と通知のみを行います。
"""

from enum import Enum
from typing import Callable, Optional


class UpstreamState(str, Enum):
    NORMAL = "UPSTREAM_NORMAL"
    SUSPECT = "UPSTREAM_SUSPECT"
    DOWN = "UPSTREAM_DOWN"


class UpstreamStateMachine:
    def __init__(
        self,
        fail_threshold: int = 6,
        logger: Optional[Callable] = None,
        notifier: Optional[Callable] = None,
    ):
        self.fail_threshold = fail_threshold
        self.log = logger or (lambda event, **kw: None)
        self.notify = notifier or (lambda msg: None)

        self.state = UpstreamState.NORMAL
        self.fail_count = 0
        self._notified = False

    def tick(self, router_ok: bool, external_ok: bool) -> None:
        if router_ok and not external_ok:
            self.fail_count += 1
            if self.fail_count >= self.fail_threshold and self.state != UpstreamState.DOWN:
                self.state = UpstreamState.DOWN
                self.log("upstream_down", router_ok=True, external_ok=False)
                if not self._notified:
                    self.notify(
                        "\u26A0\uFE0F \u30eb\u30fc\u30bf\u30fc\u306f\u751f\u5b58\u3057\u3066\u3044\u307e\u3059\u304c\u3001"
                        "\u4e0a\u6d41\u56de\u7dda\u304c\u4e0d\u901a\u3067\u3059\u3002\u518d\u8d77\u52d5\u306f\u884c\u3044\u307e\u305b\u3093\u3002"
                    )
                    self._notified = True
        elif external_ok:
            if self.state == UpstreamState.DOWN:
                self.log("upstream_recovered")
                self.notify("\u2705 \u4e0a\u6d41\u56de\u7dda\u304c\u5fa9\u65e7\u3057\u307e\u3057\u305f")
            self.state = UpstreamState.NORMAL
            self.fail_count = 0
            self._notified = False
        else:
            # router_ok=False, external_ok=False（ルーターも落ちて全断となった場合）
            # 上流回線が復旧したわけではなく、ルーター障害に悪化した（判定不能）状態。
            # 「上流が復旧した」という誤通知は送らず、ルーター側のステートマシンに処理を委ねる。
            if self.state == UpstreamState.DOWN:
                self.log("upstream_state_indeterminate_router_down")
            self.state = UpstreamState.NORMAL
            self.fail_count = 0
            self._notified = False