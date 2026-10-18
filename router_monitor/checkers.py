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
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def host_link_ok(interface: Optional[str] = None) -> bool:
    """
    デフォルト経路（または指定された interface）のリンク状態が UP か確認する。
    自ホストの NIC 自体がダウンしている場合（誤検知防止）に False を返す。
    何らかの理由で取得できない場合は安全側に倒し True を返す。
    """
    try:
        target_iface = interface
        if not target_iface:
            # /proc/net/route からデフォルトゲートウェイのインターフェースを探す
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) >= 2 and fields[1] == "00000000":
                        target_iface = fields[0]
                        break

        if not target_iface:
            return True

        operstate_path = f"/sys/class/net/{target_iface}/operstate"
        with open(operstate_path, "r", encoding="utf-8") as f:
            state = f.read().strip().lower()
            return state != "down"
    except Exception:
        return True


def nm_connectivity(timeout: int = 2) -> Optional[str]:
    """
    nmcli -t -f CONNECTIVITY general を実行して NetworkManager の接続性状態を取得する。
    戻り値: 'full', 'limited', 'portal', 'none', 'unknown'、または失敗時 None。
    'limited' は WiFi 等には接続しているが外部インターネットに出られない（Ubuntuの「？」アイコン）状態。
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "CONNECTIVITY", "general"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().lower() or None
        return None
    except Exception:
        return None


def bluetooth_powered(timeout: int = 2) -> Optional[bool]:
    """
    bluetoothctl show を実行して Bluetooth アダプタが Powered (有効) か確認する。
    戻り値: True (有効), False (無効/off), または失敗時 None。
    """
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip().lower()
                if "powered:" in line:
                    return "yes" in line
        return None
    except Exception:
        return None