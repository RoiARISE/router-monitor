"""
RelayController の実機テストスクリプト。

.env と config.yaml を読み込み、実際の Home Assistant / SwitchBot に対して
電源サイクルを実行する。ステートマシンは一切介在しない。

使い方:
  python3 manual_relay_test.py [--off-duration 10]
"""

import argparse
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from router_monitor.config import load_config
from router_monitor.relay.home_assistant import (
    RelayController,
    HomeAssistantUnreachable,
    HomeAssistantAuthError,
    RelayOperationFailed,
)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    default_config = str(project_root / "config.yaml")
    default_env = str(project_root / ".env")

    parser = argparse.ArgumentParser(description="RelayController 実機テスト")
    parser.add_argument("--config", default=default_config)
    parser.add_argument("--env", default=default_env)
    parser.add_argument(
        "--off-duration",
        type=int,
        default=10,
        metavar="秒",
        help="電源を OFF にしておく秒数（デフォルト: 10秒）",
    )
    args = parser.parse_args()

    cfg = load_config(args.config, args.env)

    relay = RelayController(
        base_url=cfg.home_assistant.base_url,
        token=cfg.home_assistant.token,
        entity_id=cfg.home_assistant.entity_id,
        timeout_seconds=cfg.home_assistant.request_timeout_seconds,
        retries=cfg.home_assistant.retries,
        retry_interval_seconds=cfg.home_assistant.retry_interval_seconds,
    )

    print(f"対象エンティティ: {cfg.home_assistant.entity_id}")
    print()

    # --- 疎通・認証チェック ---
    print("[1/4] 疎通・認証確認...")
    try:
        relay.check_connectivity()
        print("      OK")
    except HomeAssistantUnreachable as e:
        print(f"      NG: Home Assistant に到達できません: {e}")
        return 1
    except HomeAssistantAuthError as e:
        print(f"      NG: 認証エラー: {e}")
        return 1

    # --- 現在の状態確認 ---
    print("[2/4] 現在の状態取得...")
    state_before = relay.get_state()
    print(f"      現在: {state_before}")

    # --- 電源サイクル実行 ---
    print(f"[3/4] 電源サイクル開始 (OFF → {args.off_duration}秒待機 → ON)...")
    print("      ルーターの電源が一瞬切れます。目視で確認してください。")
    try:
        relay.power_cycle(off_duration=args.off_duration)
        print("      電源サイクル完了")
    except HomeAssistantUnreachable as e:
        print(f"      NG: HA 到達不可: {e}")
        return 1
    except HomeAssistantAuthError as e:
        print(f"      NG: 認証エラー: {e}")
        return 1
    except RelayOperationFailed as e:
        print(f"      NG: スイッチ操作失敗: {e}")
        return 1

    # --- 最終状態確認 ---
    print("[4/4] 最終状態確認...")
    state_after = relay.get_state()
    print(f"      最終: {state_after}")

    if state_after == "on":
        print()
        print("✅ テスト成功: 電源サイクルが正常に完了しました。")
        return 0
    else:
        print()
        print(f"⚠️  最終状態が 'on' ではなく '{state_after}' です。手動で確認してください。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
