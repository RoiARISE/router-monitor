"""
Home Assistant の REST API を経由したリレー制御。

実際のハードウェア（BLE 接続の SwitchBot プラグミニ）は
Home Assistant が完全に管理している。このモジュールは HA に HTTP で
話しかけるだけで Bluetooth を直接操作しない。
これにより、この監視プログラムが BLE スタックと疎結合に保たれる。

失敗モードは意図的に 3種類に区別している：
  - HomeAssistantUnreachable : HA 自体が応答しなかった（ネットワーク断 / HA 停止）。
  - HomeAssistantAuthError   : HA は応答したがトークンを拒否した（401 / 403）。
  - RelayOperationFailed     : HA への到達・認証は成功したが、リトライ後も
                               スイッチ操作が期待する状態にならなかった。

呼び出し元（main.py のステートマシンドライバ）は運用上この 3つを同一に扱う
（電源サイクル失敗 → FAILED 状態）が、ログ上では区別することで
原因調査を容易にしている。
"""

import time
from typing import Optional

import requests


class HomeAssistantUnreachable(Exception):
    """HA 自体に到達できなかった場合の例外。"""
    pass


class HomeAssistantAuthError(Exception):
    """HA に到達できたが認証に失敗した場合の例外。"""
    pass


class RelayOperationFailed(Exception):
    """スイッチ操作が期待する状態にならなかった場合の例外。"""
    pass


class RelayController:
    def __init__(
        self,
        base_url: str,
        token: str,
        entity_id: str,
        timeout_seconds: int = 10,
        retries: int = 3,
        retry_interval_seconds: int = 5,
        logger=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.entity_id = entity_id
        self.timeout = timeout_seconds
        self.retries = retries
        self.retry_interval = retry_interval_seconds
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # logger が渡されない場合は何もしない関数をセット
        self.log = logger or (lambda event, **kw: None)

    # -- 疎通・認証チェック -----------------------------------------------

    def check_connectivity(self) -> None:
        """
        認証済みの API パスが使用可能かどうかを確認する。
        問題がなければ正常終了し、失敗時は以下の例外を送出する：
          - HomeAssistantUnreachable
          - HomeAssistantAuthError

        /api/ ではなく実際の操作で使う states/{entity_id} エンドポイントを
        叩くことで、トークン期限切れやエンティティ名変更を
        実際の電源サイクル試行前に検知できる。
        """
        url = f"{self.base_url}/api/states/{self.entity_id}"
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise HomeAssistantUnreachable(str(exc)) from exc

        if resp.status_code in (401, 403):
            raise HomeAssistantAuthError(f"HA rejected token: HTTP {resp.status_code}")
        if resp.status_code == 404:
            raise HomeAssistantUnreachable(
                f"entity {self.entity_id} not found (HTTP 404) - check entity_id in config"
            )
        if not resp.ok:
            raise HomeAssistantUnreachable(f"unexpected HTTP {resp.status_code} from HA")

    # -- 低レベルの状態取得・サービス呼び出し --------------------------------

    def get_state(self) -> str:
        """エンティティの現在状態（"on" または "off"）を返す。"""
        url = f"{self.base_url}/api/states/{self.entity_id}"
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["state"]

    def _call_service(self, service: str) -> None:
        """HA の switch サービス（turn_on / turn_off）を呼び出す。"""
        url = f"{self.base_url}/api/services/switch/{service}"
        resp = requests.post(
            url,
            headers=self.headers,
            json={"entity_id": self.entity_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    # -- リトライ付き高レベル操作 -------------------------------------------

    def _set_state(self, target: str, sleep_fn=time.sleep) -> None:
        """
        target（"on" または "off"）になるまでリトライする。
        リトライ上限に達しても状態が一致しない場合は RelayOperationFailed を送出する。

        sleep_fn を差し替えることでテスト時に実際の待機をスキップできる。
        """
        service = "turn_on" if target == "on" else "turn_off"

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                self._call_service(service)
                sleep_fn(2)  # BLE の反映を待つ
                state = self.get_state()
                if state == target:
                    self.log(
                        "relay_set_state_succeeded",
                        target=target,
                        attempt=attempt,
                    )
                    return
                last_error = RuntimeError(f"state is '{state}', expected '{target}'")
            except requests.RequestException as exc:
                last_error = exc

            self.log(
                "relay_set_state_attempt_failed",
                target=target,
                attempt=attempt,
                error=str(last_error),
            )
            if attempt < self.retries:
                sleep_fn(self.retry_interval)

        raise RelayOperationFailed(
            f"failed to set {self.entity_id} to '{target}' after {self.retries} attempts: {last_error}"
        )

    def ensure_on(self, sleep_fn=time.sleep) -> None:
        """プラグが OFF の場合に ON に復元する（安全弁）。"""
        try:
            state = self.get_state()
            if state == "off":
                self.log("relay_unexpected_off_detected_recovering")
                self._set_state("on", sleep_fn=sleep_fn)
        except Exception as exc:
            self.log("relay_ensure_on_failed", error=str(exc))
            raise

    def power_cycle(self, off_duration: int, sleep_fn=time.sleep) -> None:
        """
        プラグを OFF → 待機 → ON する電源サイクルを実行する。

        OFF を試みた後は、いかなる例外が発生した場合でもルーターが通電断のまま
        放置されるのを防ぐため、finally 節で必ず ON への復旧を試行する。
        """
        self.check_connectivity()
        off_succeeded = False
        try:
            self._set_state("off", sleep_fn=sleep_fn)
            off_succeeded = True
            sleep_fn(off_duration)
        finally:
            if off_succeeded:
                try:
                    self._set_state("on", sleep_fn=sleep_fn)
                except Exception as on_exc:
                    self.log("relay_power_cycle_on_recovery_failed", error=str(on_exc))
                    raise

