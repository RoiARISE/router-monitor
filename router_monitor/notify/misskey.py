"""
Misskey の notes/create API を使ったノート投稿通知。

Discord 通知と同じ失敗処理の方針：
例外を送出せず、常にログを記録して bool を返す。
これにより、呼び出し元が複数チャンネルにファンアウトする際に
1つの失敗が他をブロックしないようにしている。
"""

import requests


class MisskeyNotifier:
    def __init__(self, instance_url: str, api_token: str, timeout_seconds: int = 5, logger=None):
        self.instance_url = instance_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout_seconds
        # logger が渡されない場合は何もしない関数をセット
        self.log = logger or (lambda event, **kw: None)

    def send(self, message: str, visibility: str = "specified") -> bool:
        """
        Misskey にノートを投稿する。

        Args:
            message    : 投稿テキスト。
            visibility : 公開範囲（"public" / "home" / "followers" / "specified"）。

        Returns:
            True  : 投稿成功
            False : HTTP エラーまたはネットワークエラー（例外は送出しない）
        """
        url = f"{self.instance_url}/api/notes/create"
        try:
            resp = requests.post(
                url,
                json={
                    "i": self.api_token,
                    "text": message,
                    "visibility": visibility,
                },
                timeout=self.timeout,
            )
            if not resp.ok:
                self.log("misskey_notify_failed", status_code=resp.status_code)
                return False
            return True
        except requests.RequestException as exc:
            self.log("misskey_notify_failed", error=str(exc))
            return False