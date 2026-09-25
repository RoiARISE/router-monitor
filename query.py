"""
router-monitor CLI ログ照会ツール（カラー対応）

使い方:
  python3 query.py                    # 全体サマリー
  python3 query.py --today            # 今日の安定レポート
  python3 query.py --date 2026-07-01  # 指定日のレポート
  python3 query.py --events [N]       # 直近 N 件のイベント（デフォルト30）
  python3 query.py --events N --all   # ping_cycle も含む全イベント
  python3 query.py --reboots          # 全再起動履歴
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_monitor.config import load_config
from router_monitor.logging_utils import load_jsonl_reverse
from router_monitor.stats import (
    get_reboot_stats,
    get_daily_ping_stats,
    format_daily_stability_message,
)

# ---------------------------------------------------------------------------
# ANSI カラーコード
# ---------------------------------------------------------------------------
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    # 文字色
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"

def _supports_color() -> bool:
    """stdout が TTY でなければ色なし。"""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

USE_COLOR = _supports_color()

def c(code: str, text: str) -> str:
    if not USE_COLOR:
        return text
    return f"{code}{text}{C.RESET}"

def bold(text):    return c(C.BOLD,    text)
def dim(text):     return c(C.DIM,     text)
def red(text):     return c(C.RED,     text)
def green(text):   return c(C.GREEN,   text)
def yellow(text):  return c(C.YELLOW,  text)
def blue(text):    return c(C.BLUE,    text)
def magenta(text): return c(C.MAGENTA, text)
def cyan(text):    return c(C.CYAN,    text)
def gray(text):    return c(C.GRAY,    text)

# ---------------------------------------------------------------------------
# イベント → 色付きラベル
# ---------------------------------------------------------------------------
EVENT_COLORS = {
    "monitor_started":                             lambda t: gray(t),
    "ping_cycle":                                  lambda t: dim(t),
    "router_down_confirmed":                       lambda t: bold(red(t)),
    "power_cycle_request_started":                 lambda t: yellow(t),
    "power_cycle_request_succeeded":               lambda t: blue(t),
    "power_cycle_request_failed":                  lambda t: bold(red(t)),
    "power_cycle_request_timed_out":               lambda t: bold(red(t)),
    "failure_session_closed":                      lambda t: bold(green(t)),
    "entered_failed":                              lambda t: bold(red(t)),
    "entered_verifying":                           lambda t: blue(t),
    "resumed_monitoring_from_failed":              lambda t: yellow(t),
    "upstream_down":                               lambda t: yellow(t),
    "upstream_recovered":                          lambda t: green(t),
    "router_recovered_from_down_without_power_cycle": lambda t: green(t),
    "daily_stability_report":                      lambda t: magenta(t),
    "reboot_skipped_due_to_lock":                  lambda t: gray(t),
    "retry_limit_reached_skipping_power_cycle":    lambda t: red(t),
    "cycle_error":                                 lambda t: red(t),
}

def colorize_event(name: str) -> str:
    fn = EVENT_COLORS.get(name, lambda t: t)
    return fn(name)

# ---------------------------------------------------------------------------
# ボックス描画ヘルパー
# ---------------------------------------------------------------------------
WIDTH = 62

def box_top(title: str = "") -> str:
    if title:
        inner = f" {title} "
        dashes = "─" * ((WIDTH - len(title) - 2))
        return f"┌─{bold(inner)}{'─' * (WIDTH - len(inner) - 1)}┐"
    return "┌" + "─" * WIDTH + "┐"

def box_row(text: str, fill: bool = False) -> str:
    # ANSI コードを除いた表示幅を計算
    import re
    visible = re.sub(r"\033\[[0-9;]*m", "", text)
    pad = WIDTH - len(visible)
    return f"│ {text}{' ' * max(0, pad - 1)}│"

def box_sep() -> str:
    return "├" + "─" * WIDTH + "┤"

def box_bottom() -> str:
    return "└" + "─" * WIDTH + "┘"

def divider(title: str = "") -> None:
    if title:
        side = (WIDTH - len(title) - 2) // 2
        print(f"\n{'─' * side} {bold(title)} {'─' * side}\n")
    else:
        print(gray("─" * (WIDTH + 2)))

def header(title: str) -> None:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    print()
    print("┌" + "─" * WIDTH + "┐")
    print(box_row(bold(f"🔁  {title}")))
    print(box_row(gray(now)))
    print("└" + "─" * WIDTH + "┘")

# ---------------------------------------------------------------------------
# JSONL 読み込み (logging_utils に統合されました)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# サブコマンド実装
# ---------------------------------------------------------------------------
def cmd_summary(config) -> None:
    now = datetime.now().astimezone()
    reboot = get_reboot_stats(config.logging.reboots_path, now)
    ping   = get_daily_ping_stats(config.logging.events_path, now)

    header("Router Monitor  ─  統計サマリー")
    print()

    # 再起動
    rt_color = green if reboot["total"] == 0 else yellow
    print("┌─" + bold(" 再起動回数 ") + "─" * 47 + "┐")
    print(box_row(
        f"今日   {rt_color(str(reboot['today']) + '回'):12s}  "
        f"今月   {rt_color(str(reboot['month']) + '回'):12s}  "
        f"累計   {rt_color(str(reboot['total']) + '回')}"
    ))
    print("└" + "─" * WIDTH + "┘")
    print()

    # 通信安定性
    total = ping["total_cycles"]
    rf, ef = ping["router_false"], ping["external_false"]
    stable = rf == 0 and ef == 0 and reboot["today"] == 0

    print("┌─" + bold(" 今日の通信安定性 ") + "─" * 43 + "┐")
    if total == 0:
        print(box_row(gray("監視記録なし")))
    else:
        status = green("✅ 安定") if stable else yellow("⚠️  不安定あり")
        print(box_row(f"監視: {cyan(str(total) + '回')}  "
                      f"ルーター失敗: {(green if rf==0 else red)(str(rf)+'回 ('+f'{rf/total*100:.1f}'+')'):14s}  "
                      f"外部失敗: {(green if ef==0 else red)(str(ef)+'回 ('+f'{ef/total*100:.1f}'+')'):10s}"))
        print(box_row(status))
    print("└" + "─" * WIDTH + "┘")
    print()


def cmd_date_report(config, date_str: str) -> None:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").astimezone()
    except ValueError:
        print(red("エラー: 日付は YYYY-MM-DD 形式で指定してください（例: 2026-07-01）"))
        return

    ping   = get_daily_ping_stats(config.logging.events_path, dt)
    reboot = get_reboot_stats(config.logging.reboots_path, dt)
    msg    = format_daily_stability_message(ping, dt, reboot_count=reboot["today"])

    header(f"安定レポート  ─  {dt.strftime('%Y/%m/%d')}")
    print()
    for line in msg.splitlines():
        print(f"  {line}")
    print()


def cmd_events(config, limit: int, show_all: bool) -> None:
    events = load_jsonl_reverse(config.logging.events_path, limit * 5 if not show_all else limit)

    if not show_all:
        # 失敗 ping も含めて表示、正常 ping は除く
        events = [
            e for e in events
            if e.get("event") != "ping_cycle"
            or e.get("router_ok") is False
            or e.get("external_ok") is False
        ][:limit]

    header(f"直近のイベント  ─  {len(events)} 件")
    print()

    SKIP = {"timestamp", "event"}
    COL_TS    = 19
    COL_EVENT = 44

    print(f"  {bold('日時'): <{COL_TS}}  {bold('イベント'): <{COL_EVENT}}  {bold('詳細')}")
    print(f"  {gray('─' * COL_TS)}  {gray('─' * COL_EVENT)}  {gray('─' * 20)}")

    for e in events:
        ts    = (e.get("timestamp") or "")[:19].replace("T", " ")
        name  = e.get("event", "")
        kv    = "  ".join(
            f"{gray(k)}={v}" for k, v in e.items() if k not in SKIP
        )
        print(f"  {gray(ts)}  {colorize_event(name): <{COL_EVENT + 10}}  {kv}")

    print()


def cmd_reboots(config) -> None:
    reboots = load_jsonl_reverse(config.logging.reboots_path)

    header(f"再起動履歴  ─  累計 {len(reboots)} 件")
    print()

    if not reboots:
        print(f"  {gray('再起動記録がありません。')}\n")
        return

    print(f"  {bold('日時'): <21}  {bold('試行'):^6}  {bold('OFF時間')}")
    print(f"  {gray('─' * 21)}  {gray('─' * 6)}  {gray('─' * 8)}")
    for r in reboots:
        ts  = (r.get("timestamp") or "")[:19].replace("T", " ")
        att = str(r.get("attempt", "-"))
        off = str(r.get("off_duration_seconds", "-")) + " 秒"
        print(f"  {gray(ts)}  {yellow(att):^16}  {off}")
    print()


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="router-monitor ログ照会ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "使用例:\n"
            "  python3 query.py                    # サマリー\n"
            "  python3 query.py --today            # 今日のレポート\n"
            "  python3 query.py --date 2026-07-01  # 指定日レポート\n"
            "  python3 query.py --events 20        # 直近20件\n"
            "  python3 query.py --events 50 --all  # ping_cycle 含む全件\n"
            "  python3 query.py --reboots          # 再起動履歴\n"
        ),
    )
    parser.add_argument("--config",  default="config.yaml")
    parser.add_argument("--env",     default=".env")
    parser.add_argument("--today",   action="store_true",      help="今日の安定レポートを表示")
    parser.add_argument("--date",    metavar="YYYY-MM-DD",     help="指定日のレポートを表示")
    parser.add_argument("--events",  metavar="N", nargs="?", const=30, type=int,
                                                               help="直近 N 件のイベント（デフォルト30）")
    parser.add_argument("--all",     action="store_true",      help="--events に ping_cycle も含める")
    parser.add_argument("--reboots", action="store_true",      help="全再起動履歴を表示")
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.env)
    except ValueError as e:
        print(red(f"設定読み込みエラー: {e}"))
        sys.exit(1)

    if args.today:
        cmd_date_report(config, datetime.now().strftime("%Y-%m-%d"))
    elif args.date:
        cmd_date_report(config, args.date)
    elif args.events is not None:
        cmd_events(config, args.events, args.all)
    elif args.reboots:
        cmd_reboots(config)
    else:
        cmd_summary(config)


if __name__ == "__main__":
    main()
