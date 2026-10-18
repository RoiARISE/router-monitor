"""
1つのメッセージを有効な全通知チャンネルにファンアウトする。

あるチャンネルで失敗しても他のチャンネルへの通知は継続する。
各通知クラス（Discord / Misskey）が自分自身のエラーを内部でログに記録するため、
ここでは例外処理は行わない。

チャンネルが enabled: true でも資格情報（URL・トークン）が未設定の場合は、
クラッシュせず警告ログを出してそのチャンネルをスキップする。
"""

import logging
from datetime import datetime, time as dtime
from typing import List, Optional

from router_monitor.notify.discord import DiscordNotifier
from router_monitor.notify.misskey import MisskeyNotifier

_log = logging.getLogger(__name__)


def is_in_quiet_hours(current_time: dtime, start_str: str, end_str: str) -> bool:
    """現在時刻が quiet_hours (例: 23:00 〜 07:00) の範囲内か判定する。"""
    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start = dtime(sh, sm)
        end = dtime(eh, em)
    except Exception:
        return False

    if start <= end:
        return start <= current_time < end
    else:
        # 日付を跨ぐ場合 (23:00 -> 07:00)
        return current_time >= start or current_time < end


class Notifier:
    def __init__(
        self,
        channels: List,
        quiet_hours_enabled: bool = False,
        quiet_hours_start: str = "23:00",
        quiet_hours_end: str = "07:00",
        now_fn=None,
        logger=None,
    ):
        self._channels = channels
        self.quiet_hours_enabled = quiet_hours_enabled
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end
        self.now_fn = now_fn or (lambda: datetime.now().astimezone().time())
        self.logger = logger or (lambda event, **kw: None)

    @classmethod
    def from_config(cls, notify_config, logger=None):
        """
        設定オブジェクトを元に、有効なチャンネルだけを組み込んだ
        Notifier インスタンスを生成するファクトリメソッド。

        enabled: true でも資格情報が未設定の場合は警告を出してスキップする。
        サービスはクラッシュしない。
        """
        channels = []

        if notify_config.discord_enabled:
            if notify_config.discord_webhook_url:
                channels.append(DiscordNotifier(notify_config.discord_webhook_url, logger=logger))
            else:
                _log.warning(
                    "discord_enabled=true ですが DISCORD_WEBHOOK_URL が未設定のため Discord 通知を無効化しました。"
                )

        if notify_config.misskey_enabled:
            if notify_config.misskey_api_token and notify_config.misskey_instance_url:
                channels.append(
                    MisskeyNotifier(
                        notify_config.misskey_instance_url,
                        notify_config.misskey_api_token,
                        logger=logger,
                    )
                )
            else:
                _log.warning(
                    "misskey_enabled=true ですが MISSKEY_API_TOKEN / MISSKEY_INSTANCE_URL が未設定のため "
                    "Misskey 通知を無効化しました。"
                )

        return cls(
            channels,
            quiet_hours_enabled=getattr(notify_config, "quiet_hours_enabled", False),
            quiet_hours_start=getattr(notify_config, "quiet_hours_start", "23:00"),
            quiet_hours_end=getattr(notify_config, "quiet_hours_end", "07:00"),
            logger=logger,
        )

    def send(self, message: str, force: bool = False) -> None:
        """全チャンネルにメッセージを送信する。quiet_hours 中は force=True でない限り抑制する。"""
        if self.quiet_hours_enabled and not force:
            now_time = self.now_fn()
            if is_in_quiet_hours(now_time, self.quiet_hours_start, self.quiet_hours_end):
                self.logger(
                    "notification_suppressed_quiet_hours",
                    time=now_time.strftime("%H:%M"),
                    quiet_range=f"{self.quiet_hours_start}-{self.quiet_hours_end}",
                    message_preview=message[:50],
                )
                _log.info("夜間通知抑制時間帯のため通知をスキップしました: %s", message[:50])
                return

        for channel in self._channels:
            try:
                channel.send(message)
            except Exception as exc:
                _log.exception("通知チャンネル %s で予期せぬエラーが発生しました: %s", channel, exc)

    def as_notifier_fn(self):
        """ステートマシンが期待する `notifier: Callable` シグネチャ
        (notifier(message: str)) に適合するアダプタを返す。"""
        return self.send
