#!/usr/bin/env bash
# =============================================================================
# router-monitor セットアップスクリプト
#
# 使い方:
#   bash setup.sh               # 通常のセットアップ
#   bash setup.sh --skip-systemd  # systemd の設定をスキップ
#
# 実行内容:
#   1. Python バージョン確認
#   2. 仮想環境（.venv）の作成
#   3. 依存パッケージのインストール
#   4. 設定ファイルのコピー（config.yaml / .env）
#   5. data/ ディレクトリの作成
#   6. systemd サービスファイルのパス自動置換（スキップ可）
#   7. 起動前チェック（check_env.py）の実行
#
# このスクリプトは冪等です（何度実行しても安全）。
# すでに完了している手順はスキップされます。
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# カラー出力
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET='\033[0m'
    C_GREEN='\033[92m'
    C_YELLOW='\033[93m'
    C_RED='\033[91m'
    C_BOLD='\033[1m'
    C_GRAY='\033[90m'
else
    C_RESET='' C_GREEN='' C_YELLOW='' C_RED='' C_BOLD='' C_GRAY=''
fi

ok()   { echo -e "  ${C_GREEN}[OK]${C_RESET}   $*"; }
skip() { echo -e "  ${C_GRAY}[SKIP]${C_RESET} $*"; }
warn() { echo -e "  ${C_YELLOW}[WARN]${C_RESET} $*"; }
err()  { echo -e "  ${C_RED}[ERROR]${C_RESET} $*"; }
info() { echo -e "  ${C_GRAY}       $*${C_RESET}"; }
header() {
    echo ""
    echo -e "${C_BOLD}━━━ $* ━━━${C_RESET}"
    echo ""
}

# ---------------------------------------------------------------------------
# オプション解析
# ---------------------------------------------------------------------------
SKIP_SYSTEMD=false
for arg in "$@"; do
    case "$arg" in
        --skip-systemd) SKIP_SYSTEMD=true ;;
        -h|--help)
            echo "使い方: bash setup.sh [--skip-systemd]"
            echo "  --skip-systemd  systemd サービスファイルの設定をスキップする"
            exit 0
            ;;
        *)
            err "不明なオプション: $arg"
            echo "使い方: bash setup.sh [--skip-systemd]"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# スクリプト自身のディレクトリへ移動（どこから呼び出しても動くように）
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${C_BOLD}router-monitor セットアップを開始します${C_RESET}"
echo -e "${C_GRAY}作業ディレクトリ: $SCRIPT_DIR${C_RESET}"

# ---------------------------------------------------------------------------
# ステップ 1: Python バージョン確認
# ---------------------------------------------------------------------------
header "ステップ 1: Python バージョン確認"

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON_CMD="$cmd"
            ok "Python $("$cmd" --version 2>&1) が使用されます"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    err "Python 3.9 以上が見つかりませんでした"
    info "インストール方法:"
    info "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    info "  macOS:         brew install python3"
    info "  その他:        https://www.python.org/downloads/"
    exit 1
fi

# ---------------------------------------------------------------------------
# ステップ 2: 仮想環境の作成
# ---------------------------------------------------------------------------
header "ステップ 2: 仮想環境（.venv）の作成"

if [ -d ".venv" ]; then
    skip ".venv がすでに存在します（スキップ）"
else
    "$PYTHON_CMD" -m venv .venv
    ok ".venv を作成しました"
fi

# 仮想環境を有効化
# shellcheck disable=SC1091
source .venv/bin/activate
ok "仮想環境を有効化しました"

# ---------------------------------------------------------------------------
# ステップ 3: 依存パッケージのインストール
# ---------------------------------------------------------------------------
header "ステップ 3: 依存パッケージのインストール"

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
ok "依存パッケージをインストールしました（requirements.txt）"

# ---------------------------------------------------------------------------
# ステップ 4: 設定ファイルのコピー
# ---------------------------------------------------------------------------
header "ステップ 4: 設定ファイルの準備"

if [ -f "config.yaml" ]; then
    skip "config.yaml がすでに存在します（スキップ）"
else
    cp config.yaml.example config.yaml
    ok "config.yaml.example から config.yaml を作成しました"
    warn "config.yaml を開いて、あなたの環境に合わせて編集してください"
    info "  特に以下の項目を確認してください:"
    info "    - ping_targets.router_lan_ip  : ルーターの LAN IP（例: 192.168.0.1）"
    info "    - home_assistant.base_url     : Home Assistant の URL（例: http://192.168.0.10:8123）"
    info "    - home_assistant.entity_id    : スマートプラグのエンティティ ID"
fi

if [ -f ".env" ]; then
    skip ".env がすでに存在します（スキップ）"
else
    cp .env.example .env
    ok ".env.example から .env を作成しました"
    warn ".env を開いて HA_TOKEN に Home Assistant のトークンを設定してください"
    info "  取得方法: Home Assistant → プロフィール → 長期アクセストークン → トークンを作成"
fi

# ---------------------------------------------------------------------------
# ステップ 5: data/ ディレクトリの作成
# ---------------------------------------------------------------------------
header "ステップ 5: data/ ディレクトリの作成"

if [ -d "data" ]; then
    skip "data/ がすでに存在します（スキップ）"
else
    mkdir -p data
    ok "data/ ディレクトリを作成しました"
fi

# ---------------------------------------------------------------------------
# ステップ 6: systemd サービスファイルのパス置換（オプション）
# ---------------------------------------------------------------------------
header "ステップ 6: systemd サービスファイルの準備"

if [ "$SKIP_SYSTEMD" = true ]; then
    skip "--skip-systemd が指定されたため systemd の設定をスキップします"
else
    SERVICE_FILE="systemd/router-monitor.service"
    CURRENT_USER="$(whoami)"
    CURRENT_DIR="$SCRIPT_DIR"

    if [ ! -f "$SERVICE_FILE" ]; then
        warn "systemd/router-monitor.service が見つかりませんでした（スキップ）"
    else
        # サービスファイル内のパスを現在の環境に合わせて置換
        # 既存のパス（/path/to/... 等）を現在のパスに置換する
        ORIGINAL_WORKDIR=$(grep 'WorkingDirectory=' "$SERVICE_FILE" | sed 's/WorkingDirectory=//')
        ORIGINAL_USER=$(grep '^User=' "$SERVICE_FILE" | sed 's/User=//')

        if [ "$ORIGINAL_WORKDIR" = "$CURRENT_DIR" ] && [ "$ORIGINAL_USER" = "$CURRENT_USER" ]; then
            skip "systemd サービスファイルはすでに現在の環境に合わせて設定されています"
        else
            # バックアップを作成してから置換
            cp "$SERVICE_FILE" "${SERVICE_FILE}.bak"
            sed -i \
                -e "s|WorkingDirectory=.*|WorkingDirectory=${CURRENT_DIR}|g" \
                -e "s|ExecStart=/usr/bin/python3 .*|ExecStart=${CURRENT_DIR}/.venv/bin/python3 ${CURRENT_DIR}/main.py --config ${CURRENT_DIR}/config.yaml --env ${CURRENT_DIR}/.env|g" \
                -e "s|^User=.*|User=${CURRENT_USER}|g" \
                -e "s|^Group=.*|Group=${CURRENT_USER}|g" \
                -e "s|ReadWritePaths=.*|ReadWritePaths=${CURRENT_DIR}/data|g" \
                "$SERVICE_FILE"
            ok "systemd/router-monitor.service のパスを現在の環境に合わせて更新しました"
            info "  バックアップ: ${SERVICE_FILE}.bak"
        fi

        echo ""
        echo -e "  ${C_YELLOW}systemd サービスとして登録するには、以下のコマンドを実行してください:${C_RESET}"
        echo ""
        echo -e "    ${C_BOLD}sudo cp systemd/router-monitor.service /etc/systemd/system/${C_RESET}"
        echo -e "    ${C_BOLD}sudo systemctl daemon-reload${C_RESET}"
        echo -e "    ${C_BOLD}sudo systemctl enable router-monitor${C_RESET}"
        echo -e "    ${C_BOLD}sudo systemctl start router-monitor${C_RESET}"
        echo ""
        info "ログの確認: journalctl -u router-monitor -f"
    fi
fi

# ---------------------------------------------------------------------------
# ステップ 7: 起動前チェック
# ---------------------------------------------------------------------------
header "ステップ 7: 起動前チェック（check_env.py）"

echo -e "  ${C_GRAY}設定に問題がないか確認します...${C_RESET}"
echo ""

# check_env.py を実行（終了コードは保存するが、このスクリプト自体は継続）
python3 check_env.py || true

# ---------------------------------------------------------------------------
# 完了メッセージ
# ---------------------------------------------------------------------------
echo ""
echo -e "${C_BOLD}━━━ セットアップ完了 ━━━${C_RESET}"
echo ""
echo -e "  次のステップ:"
echo ""
echo -e "  1. ${C_YELLOW}config.yaml${C_RESET} と ${C_YELLOW}.env${C_RESET} を編集して設定を完了させてください"
echo -e "     ${C_GRAY}（まだ編集していない場合）${C_RESET}"
echo ""
echo -e "  2. 設定を確認:"
echo -e "     ${C_BOLD}python3 check_env.py${C_RESET}"
echo ""
echo -e "  3. 動作テスト:"
echo -e "     ${C_BOLD}source .venv/bin/activate${C_RESET}"
echo -e "     ${C_BOLD}python3 main.py${C_RESET}"
echo -e "     ${C_GRAY}（Ctrl+C で停止）${C_RESET}"
echo ""
