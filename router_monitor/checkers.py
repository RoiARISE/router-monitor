"""
監視ループで使用するネットワーク疎通確認関数。

ping() はシステムの `ping` コマンドを使用する（Linux では root 不要）。
arp_check() は `arping` を使用する（`iputils-arping` パッケージが必要。
root 以外で実行する場合は CAP_NET_RAW ケーパビリティが必要）。
"""

import subprocess
from typing import Optional


def ping(host: str, timeout: int = 2, count: int = 1) -> bool:
    """`host` が `timeout` 秒以内に ICMP echo に応答すれば True を返す。"""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        raise RuntimeError("'ping' command not found on this system")
    except Exception:
        return False


def arp_check(ip: str, interface: str, timeout: int = 2) -> Optional[bool]:
    """
    arping を実行して ARP 応答の有無を確認する。

    - True  : ARP 応答あり（ホスト存在確認）
    - False : ARP 応答なし
    - None  : arping バイナリが存在しない、またはケーパビリティ不足で
              実行自体に失敗した場合。呼び出し元は None を「不明 / 判断不可」
              として扱い、絶対に障害判定に使ってはならない。
    """
    try:
        result = subprocess.run(
            ["arping", "-c", "1", "-w", str(timeout), "-I", interface, ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return None
    except FileNotFoundError:
        return None