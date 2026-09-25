"""
router-monitor 起動前チェックツール

python3 main.py を実行する前に設定ファイルの問題を事前に検出して、
わかりやすいメッセージで案内します。

使い方:
  python3 check_env.py              # 基本チェック
  python3 check_env.py --check-ha   # HA への HTTP 疎通も確認
  python3 check_env.py --quiet      # エラー・警告のみ表示（OK を省略）

終了コード:
  0 = 全 OK
  1 = 警告あり（起動は可能だが推奨しない設定がある）
  2 = エラーあり（このままでは起動に失敗する）
"""

import argparse
import ipaddress
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# ANSI カラー（TTY でなければ無効化）
# ---------------------------------------------------------------------------
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"{code}{text}\033[0m" if _USE_COLOR else text


def _green(t: str) -> str:   return _c("\033[92m", t)
def _yellow(t: str) -> str:  return _c("\033[93m", t)
def _red(t: str) -> str:     return _c("\033[91m", t)
def _bold(t: str) -> str:    return _c("\033[1m",  t)
def _gray(t: str) -> str:    return _c("\033[90m", t)


# ---------------------------------------------------------------------------
# 結果追跡
# ---------------------------------------------------------------------------
_results: List[Dict] = []   # {"level": "ok"|"warn"|"error", "msg": str, "hint": str}


def _ok(msg: str) -> None:
    _results.append({"level": "ok", "msg": msg, "hint": ""})


def _warn(msg: str, hint: str = "") -> None:
    _results.append({"level": "warn", "msg": msg, "hint": hint})


def _error(msg: str, hint: str = "") -> None:
    _results.append({"level": "error", "msg": msg, "hint": hint})


# ---------------------------------------------------------------------------
# チェック関数群
# ---------------------------------------------------------------------------

def check_python_version() -> None:
    """Python バージョンが 3.9 以上か確認する。"""
    v = sys.version_info
    if v >= (3, 9):
        _ok(f"Python {v.major}.{v.minor}.{v.micro} が使用されています")
    else:
        _error(
            f"Python {v.major}.{v.minor}.{v.micro} は非対応です（3.9 以上が必要）",
            hint="https://www.python.org/downloads/ から最新版をインストールしてください",
        )


def check_file_exists(path: str, label: str, copy_from: str = "") -> bool:
    """指定ファイルが存在するか確認する。存在すれば True を返す。"""
    if Path(path).exists():
        _ok(f"{label} が見つかりました")
        return True
    else:
        hint = f"`cp {copy_from} {path}` を実行してテンプレートからコピーしてください" if copy_from else ""
        _error(f"{label} が見つかりません ({path})", hint=hint)
        return False


def check_data_dir() -> None:
    """data/ ディレクトリの存在を確認する。"""
    if Path("data").is_dir():
        _ok("data/ ディレクトリが存在します")
    else:
        _warn(
            "data/ ディレクトリが存在しません",
            hint="`mkdir -p data` を実行してください（起動時に自動作成される場合もあります）",
        )


def check_ha_token(env_path: str) -> bool:
    """HA_TOKEN が .env に設定されているか確認する。"""
    token = ""
    if Path(env_path).exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("HA_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break

    # 環境変数からも取得を試みる
    token = token or os.environ.get("HA_TOKEN", "")

    if token and token != "your_home_assistant_long_lived_token_here":
        _ok("HA_TOKEN が設定されています")
        return True
    elif token == "your_home_assistant_long_lived_token_here":
        _error(
            "HA_TOKEN がテンプレートのままです",
            hint=(
                ".env を開き、`HA_TOKEN=` の右側に実際のトークンを貼り付けてください\n"
                "  取得方法: Home Assistant → プロフィール → 長期アクセストークン → トークンを作成"
            ),
        )
        return False
    else:
        _error(
            "HA_TOKEN が .env に設定されていません",
            hint=(
                ".env を開き、以下のように設定してください:\n"
                "  HA_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...（長い文字列）\n"
                "  取得方法: Home Assistant → プロフィール → 長期アクセストークン → トークンを作成"
            ),
        )
        return False


def check_config_yaml(config_path: str) -> Optional[Dict]:
    """config.yaml を読み込み、内容を検証する。問題がなければ dict を返す。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        _warn(
            "PyYAML がインストールされていません（config.yaml の内容チェックをスキップ）",
            hint="`pip install -r requirements.txt` を実行してください",
        )
        return None

    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        _error(f"config.yaml の書式が正しくありません: {exc}", hint="config.yaml.example と見比べて修正してください")
        return None

    if raw is None:
        _error("config.yaml が空です", hint="`cp config.yaml.example config.yaml` でテンプレートからコピーしてください")
        return None

    _ok("config.yaml の YAML 構文が正しいです")
    return raw


def check_router_lan_ip(raw: dict) -> None:
    """router_lan_ip が有効な IP アドレス形式か確認する。"""
    try:
        ip = raw["ping_targets"]["router_lan_ip"]
        ipaddress.ip_address(ip)
        _ok(f"router_lan_ip が設定されています ({ip})")
    except KeyError:
        _error(
            "ping_targets.router_lan_ip が設定されていません",
            hint="config.yaml の `router_lan_ip:` にルーターの LAN IP（例: 192.168.0.1）を設定してください",
        )
    except ValueError:
        ip = raw.get("ping_targets", {}).get("router_lan_ip", "")
        _error(
            f"router_lan_ip の値 '{ip}' が有効な IP アドレスではありません",
            hint="例: `router_lan_ip: \"192.168.0.1\"`",
        )


def check_ha_base_url(raw: dict) -> str:
    """home_assistant.base_url の形式を確認する。URL を返す（確認できない場合は空文字）。"""
    try:
        url = raw["home_assistant"]["base_url"]
    except KeyError:
        _error(
            "home_assistant.base_url が設定されていません",
            hint="config.yaml の `base_url:` に Home Assistant の URL（例: http://192.168.0.10:8123）を設定してください",
        )
        return ""

    if url.startswith("http://") or url.startswith("https://"):
        _ok(f"home_assistant.base_url が設定されています ({url})")
        return url
    else:
        _error(
            f"home_assistant.base_url の値 '{url}' が正しくありません",
            hint="http:// または https:// で始まる URL を指定してください（例: http://192.168.0.10:8123）",
        )
        return ""


def check_ha_entity_id(raw: dict) -> None:
    """home_assistant.entity_id が設定されているか確認する。"""
    try:
        entity_id = raw["home_assistant"]["entity_id"]
        if entity_id and entity_id != "switch.switchbot_plug_router":
            _ok(f"home_assistant.entity_id が設定されています ({entity_id})")
        elif entity_id == "switch.switchbot_plug_router":
            _warn(
                "home_assistant.entity_id がサンプル値のままです",
                hint=(
                    "自分のスマートプラグの entity_id に書き換えてください\n"
                    "  調べ方: Home Assistant → 開発者ツール → 状態 → スマートプラグで検索"
                ),
            )
        else:
            _error(
                "home_assistant.entity_id が空です",
                hint=(
                    "Home Assistant → 開発者ツール → 状態 でスマートプラグの entity_id を調べて設定してください"
                ),
            )
    except KeyError:
        _error(
            "home_assistant.entity_id が設定されていません",
            hint="config.yaml の `entity_id:` にスマートプラグの ID を設定してください",
        )


def check_notify_config(raw: dict, env_path: str) -> None:
    """通知設定の整合性を確認する（enabled=true なのに URL/トークン未設定など）。"""
    notify = raw.get("notify", {})

    if notify.get("discord_enabled", False):
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not webhook_url and Path(env_path).exists():
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("DISCORD_WEBHOOK_URL="):
                        webhook_url = line.split("=", 1)[1].strip()
                        break
        if not webhook_url or webhook_url == "https://discord.com/api/webhooks/xxxx":
            _warn(
                "discord_enabled=true ですが DISCORD_WEBHOOK_URL が未設定です",
                hint=".env の `DISCORD_WEBHOOK_URL=` に Discord の Webhook URL を設定してください",
            )
        else:
            _ok("Discord 通知の設定が確認できました")


def check_ha_connectivity(base_url: str, env_path: str) -> None:
    """Home Assistant への実際の HTTP 疎通を確認する（--check-ha 指定時のみ）。"""
    try:
        import requests  # type: ignore
    except ImportError:
        _warn("requests がインストールされていないため HA 疎通チェックをスキップします")
        return

    # トークンを取得
    token = os.environ.get("HA_TOKEN", "")
    if not token and Path(env_path).exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("HA_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break

    url = f"{base_url.rstrip('/')}/api/"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            _ok(f"Home Assistant ({base_url}) に正常に接続できました")
        elif resp.status_code in (401, 403):
            _error(
                f"Home Assistant への認証に失敗しました (HTTP {resp.status_code})",
                hint="HA_TOKEN が正しいか確認してください。トークンの有効期限が切れている可能性もあります。",
            )
        elif resp.status_code == 404:
            _warn(
                f"Home Assistant から予期しない応答がありました (HTTP {resp.status_code})",
                hint="base_url のパス部分を確認してください（ポート番号の後にパスは不要です）",
            )
        else:
            _warn(f"Home Assistant から予期しない応答がありました (HTTP {resp.status_code})")
    except Exception as exc:
        kind = type(exc).__name__
        _error(
            f"Home Assistant ({base_url}) に接続できませんでした",
            hint=(
                f"エラー詳細: {kind}: {exc}\n"
                "  確認事項:\n"
                "  - Home Assistant が起動しているか\n"
                "  - config.yaml の base_url が正しいか（IP アドレス・ポート番号）\n"
                "  - このマシンから HA へネットワーク的に到達できるか"
            ),
        )


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def _print_results(quiet: bool) -> int:
    """結果を出力し、終了コードを返す。"""
    has_warn = any(r["level"] == "warn" for r in _results)
    has_error = any(r["level"] == "error" for r in _results)

    print()
    print(_bold("━━━ router-monitor 設定チェック ━━━"))
    print()

    for r in _results:
        level = r["level"]
        msg = r["msg"]
        hint = r["hint"]

        if level == "ok":
            if not quiet:
                print(f"  {_green('✅')} {msg}")
        elif level == "warn":
            print(f"  {_yellow('⚠️ ')} {_yellow(msg)}")
            if hint:
                for line in hint.splitlines():
                    print(f"       {_gray(line)}")
        elif level == "error":
            print(f"  {_red('❌')} {_red(msg)}")
            if hint:
                for line in hint.splitlines():
                    print(f"       {_gray(line)}")

    print()

    if has_error:
        print(_red(_bold("  ✗ エラーがあります。上記の内容を修正してから python3 main.py を実行してください。")))
        print()
        return 2
    elif has_warn:
        print(_yellow(_bold("  ⚠ 警告があります。確認してから python3 main.py を実行することをお勧めします。")))
        print()
        return 1
    else:
        print(_green(_bold("  ✓ すべてのチェックが通りました。python3 main.py を実行できます！")))
        print()
        return 0


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="router-monitor 起動前チェックツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "使用例:\n"
            "  python3 check_env.py              # 基本チェック\n"
            "  python3 check_env.py --check-ha   # HA への HTTP 疎通も確認\n"
            "  python3 check_env.py --quiet      # エラー・警告のみ表示\n"
        ),
    )
    parser.add_argument("--config",   default="config.yaml", help="config.yaml のパス（デフォルト: config.yaml）")
    parser.add_argument("--env",      default=".env",        help=".env のパス（デフォルト: .env）")
    parser.add_argument("--check-ha", action="store_true",   help="Home Assistant への実際の HTTP 疎通も確認する")
    parser.add_argument("--quiet",    action="store_true",   help="OK 項目を非表示にしてエラー・警告のみ表示する")
    args = parser.parse_args()

    # --- Python バージョン ---
    check_python_version()

    # --- ファイル存在確認 ---
    config_exists = check_file_exists(args.config, "config.yaml", copy_from="config.yaml.example")
    check_file_exists(args.env, ".env", copy_from=".env.example")

    # --- data/ ディレクトリ ---
    check_data_dir()

    # --- HA_TOKEN ---
    check_ha_token(args.env)

    # --- config.yaml の内容 ---
    raw = None
    if config_exists:
        raw = check_config_yaml(args.config)

    if raw:
        check_router_lan_ip(raw)
        base_url = check_ha_base_url(raw)
        check_ha_entity_id(raw)
        check_notify_config(raw, args.env)

        # --- HA 疎通（オプション）---
        if args.check_ha and base_url:
            check_ha_connectivity(base_url, args.env)

    return _print_results(args.quiet)


if __name__ == "__main__":
    sys.exit(main())
