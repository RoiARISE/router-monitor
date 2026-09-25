"""
Discord の Incoming Webhook を使ったテキスト通知。

ここでの失敗はログに記録されるが、上位には例外を伝播させない。
通知失敗が監視ループのクラッシュやルーター/リレー障害と
誤認されてはならないため。
"""

import requests


class DiscordNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: int = 5, logger=None):
        self.webhook_url = webhook_url
        self.timeout = timeout_seconds
        # logger が渡されない場合は何もしない関数をセット
        self.log = logger or (lambda event, **kw: None)

    def send(self, message: str) -> bool:
        """
        Discord にメッセージを送信する。

        Returns:
            True  : 送信成功
            False : HTTP エラーまたはネットワークエラー（例外は送出しない）
        """
        try:
            resp = requests.post(
                self.webhook_url,
                json={"content": message},
                timeout=self.timeout,
            )
            if not resp.ok:
                self.log("discord_notify_failed", status_code=resp.status_code)
                return False
            return True
        except requests.RequestException as exc:
            self.log("discord_notify_failed", error=str(exc))
            return False