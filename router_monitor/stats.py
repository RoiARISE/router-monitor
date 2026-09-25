"""
reboots.jsonl イベントログから、再起動の統計情報（本日 / 今月 / 累計）を
算出します。個別の統計ストレージはなく、数値は常にログ（単一の信頼できる情報源）から
再計算されます。
"""

import json
from datetime import datetime
from typing import Dict
from router_monitor.logging_utils import read_jsonl_reverse_iter


def get_reboot_stats(reboots_path: str, now: datetime = None) -> Dict[str, int]:
    now = now or datetime.now().astimezone()
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    today_count = 0
    month_count = 0
    total_count = 0

    # reboots.jsonl はファイルサイズが小さいため全件走査でも問題ないが、
    # 逆順で読み込むことで新しい順に処理できる（今回は単なるカウントなので結果は同じ）
    for entry in read_jsonl_reverse_iter(reboots_path):
        ts = entry.get("timestamp", "")
        total_count += 1
        if ts.startswith(today_str):
            today_count += 1
        if ts.startswith(month_str):
            month_count += 1

    return {"today": today_count, "month": month_count, "total": total_count}


def format_stats_message(stats: Dict[str, int]) -> str:
    return f"本日: {stats['today']}回 / 今月: {stats['month']}回 / 総計: {stats['total']}回"


def get_daily_ping_stats(events_path: str, date: datetime = None) -> Dict[str, int]:
    """
    指定日の ping_cycle イベントを集計して通信安定性を返す。

    Returns:
        {
          "total_cycles" : その日の総監視回数,
          "router_false" : ルーター LAN ping が失敗した回数,
          "external_false": 外部疎通が失敗した回数,
        }
    """
    date = date or datetime.now().astimezone()
    date_str = date.strftime("%Y-%m-%d")

    total = 0
    router_false = 0
    external_false = 0

    for entry in read_jsonl_reverse_iter(events_path):
        ts = entry.get("timestamp", "")
        # ts format: YYYY-MM-DDTHH:MM:SS
        ts_date = ts[:10]

        # 逆順に読み込んでいるため、指定日より前の日付のログに到達したら
        # それ以上古いログを探す必要はないのでループを打ち切る（O(1) パフォーマンス）
        if ts_date < date_str:
            break

        # 指定日より後の日付（未来）は無視して次へ
        if ts_date > date_str:
            continue

        if entry.get("event") == "ping_cycle":
            total += 1
            if not entry.get("router_ok", True):
                router_false += 1
            if not entry.get("external_ok", True):
                external_false += 1

    return {"total_cycles": total, "router_false": router_false, "external_false": external_false}


def format_daily_stability_message(
    ping_stats: Dict[str, int],
    date: datetime,
    reboot_count: int = 0,
) -> str:
    """日次安定レポートを通知向けテキストにフォーマットする。"""
    date_str = date.strftime("%Y/%m/%d")
    total = ping_stats["total_cycles"]

    if total == 0:
        return f"📊 {date_str} の通信記録がありません"

    rf = ping_stats["router_false"]
    ef = ping_stats["external_false"]
    r_pct = rf / total * 100
    e_pct = ef / total * 100

    stability = "✅ 安定" if rf == 0 and ef == 0 and reboot_count == 0 else "⚠️ 不安定あり"
    reboot_str = f"ルーター再起動: {reboot_count}回 / " if reboot_count > 0 else ""
    return (
        f"📊 {date_str} 通信安定レポート {stability}\n"
        f"監視: {total}回 / "
        f"{reboot_str}"
        f"ルーター応答失敗: {rf}回 ({r_pct:.1f}%) / "
        f"外部疎通失敗: {ef}回 ({e_pct:.1f}%)"
    )