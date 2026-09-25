"""
1つのメッセージを有効な全通知チャンネルにファンアウトする。

あるチャンネルで失敗しても他のチャンネルへの通知は継続する。
各通知クラス（Discord / Misskey）が自分自身のエラーを内部でログに記録するため、
ここでは例外処理は行わない。

チャンネルが enabled: true でも資格情報（URL・トークン）が未設定の場合は、
クラッシュせず警告ログを出してそのチャンネルをスキップする。
"""

import logging
from typing import List

from router_monitor.notify.discord import DiscordNotifier
from router_monitor.notify.misskey import MisskeyNotifier

_log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, channels: List):
        self._channels = channels

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

        return cls(channels)

    def send(self, message: str) -> None:
        """全チャンネルにメッセージを送信する。あるチャンネルが失敗しても他方を継続する。"""
        for channel in self._channels:
            try:
                channel.send(message)
            except Exception as exc:
                _log.exception("通知チャンネル %s で予期せぬエラーが発生しました: %s", channel, exc)

    def as_notifier_fn(self):
        """ステートマシンが期待する `notifier: Callable` シグネチャ
        (notifier(message: str)) に適合するアダプタを返す。"""
        return self.send
