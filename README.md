# router-monitor

ルーターの死活監視と自動再起動を行うシステムです。
ルーターのLAN IPおよび外部（インターネット）のIPアドレスへ定期的にPingを送信し、通信断を検知した場合に、Home Assistant の API 経由でスマートプラグを制御してルーターの電源を自動で物理的に再起動（パワーサイクル）します。
設定を行えば、復旧状況や日次の安定性レポートなどをDiscord および Misskey へ自動で通知できます。

> [!NOTE]
> このREADMEは私にとっての備忘録としての役割もあります。そのため書いている内容が細かいですが、自分以外の人が使うということは大きくは考慮しておりません。
> このプログラムはGoogle Antigravity上にあるGeminiおよびClaudeが大部分を作成しています。いわゆるバイブコーディングによるものです。ご了承ください。

## 主な機能

- **死活監視 (Ping/ARP)**
  - ルーター自身（LAN）と外部のパブリックDNS（1.1.1.1や8.8.8.8など）へPingを打ち、インターネットへの疎通を確認します。
- **自動復旧・電源制御 (Home Assistant連携)**
  - ネットワークの切断が設定されたしきい値（例：6回連続失敗）に達すると、Home Assistantを介してスマートプラグ（例: SwitchBot プラグミニ）をオフ・オンし、ルーターを強制再起動させます。
- **高度な状態管理 (ステートマシン)**
  - 再起動ループを防ぐための「再起動ロック（`reboot_lock_seconds`）」や、起動完了を待つ「クールダウン（`cooldown_seconds`）」、復旧の確実性を確認する「復旧検証（`verify_success_required`）」など、厳密なステートマシンで状態を管理します。最大リトライ回数を超えた場合は、再起動を一時停止して通知のみを行います。
- **通知・レポート (Discord / Misskey)**
  - 障害検知、再起動の実行、復旧の成功/失敗時にリアルタイムで通知を送信します。
  - 毎日、前日のPing成功率や再起動回数をまとめた「日次安定性レポート」を送信します。
- **ログと統計**
  - すべてのイベントや再起動の記録を JSON Lines 形式（`events.jsonl`, `reboots.jsonl`）で保存し、障害の分析や日次レポートの生成に使用します。

---

## このシステムを動かすために必要なもの（前提条件）

セットアップを始める前に、以下がすべて揃っているか確認してください。

### ソフトウェア

| 必要なもの | 確認方法 | 備考 |
|---|---|---|
| **Python 3.9 以上** | `python3 --version` | 3.9 未満の場合は [python.org](https://www.python.org/downloads/) からインストール |
| **Git**（任意） | `git --version` | リポジトリをクローンする場合のみ必要 |

### ハードウェア・サービス

| 必要なもの | 備考 |
|---|---|
| **Home Assistant** が同一 LAN で稼働していること | Raspberry Pi などで動かすのが一般的 |
| **スマートプラグ**（例: SwitchBot プラグミニ推奨、Bluetoothで操作できるものを推奨） | Home Assistant から制御できる状態にしておくこと |

> [!NOTE]
> Home Assistant のセットアップ方法は本ドキュメントの範囲外です。
> [Home Assistant 公式ドキュメント](https://www.home-assistant.io/getting-started/) を参照してください。

---

### Home Assistant 長期アクセストークンの取得方法

`.env` に設定する `HA_TOKEN` の取得手順です。

1. ブラウザで Home Assistant を開く（例: `http://localhost:8123`）
2. 左下のユーザーアイコン → **「プロフィール」** をクリック
3. ページ下部の **「長期アクセストークン」** セクションへスクロール
4. **「トークンを作成」** ボタンをクリックし、名前（例: `router-monitor`）を入力
5. 表示されたトークン文字列をコピーして `.env` の `HA_TOKEN=` に貼り付ける

> [!IMPORTANT]
> トークンはダイアログを閉じると二度と表示されません。必ずコピーしてから閉じてください。

---

### スマートプラグの `entity_id` の調べ方

`config.yaml` に設定する `entity_id`（例: `switch.switchbot_plug_router`）の調べ方です。

1. Home Assistant のサイドバーから **「開発者ツール」**（`</>` アイコン）を開く
2. **「状態」** タブをクリック
3. 検索欄にスマートプラグの名前（例: `switchbot` や `plug`）を入力して絞り込む
4. 一覧に表示される `switch.xxxxx` の形式の文字列が `entity_id` です

---

## ステートマシン（状態遷移）の概要

本システムは、無駄な再起動や誤検知を防ぐため、以下のような状態遷移を持ちます。

1. **NORMAL (正常)**: Pingによる監視を継続中。連続失敗が `fail_threshold` に達すると DOWN へ遷移。
2. **DOWN (障害確定)**: 障害が確定した状態。再起動ロック期間中でなければ `POWER_CYCLE_REQUEST` へ遷移。最大リトライ回数を超えている場合は `FAILED` へ。
3. **POWER_CYCLE_REQUEST (再起動要求)**: Home Assistantへ電源OFF/ONをリクエスト。成功すれば `POWER_CYCLE_WAIT` へ。
4. **POWER_CYCLE_WAIT (再起動待機)**: ルーターが起動するまでのクールダウン期間。待機後 `VERIFYING` へ。
5. **VERIFYING (復旧検証)**: 復旧したかPingで確認。`verify_success_required` 回連続で成功すれば `NORMAL` へ復帰。失敗が続けば再度 `DOWN` へ。
6. **FAILED (復旧失敗)**: リトライ上限に達した、あるいは致命的なエラーが起きた状態。通知を送り、一定時間後に監視（`SUSPECT`）を再開します。

---

## セットアップ手順

本システムは Python 3.9+ で動作します。

### 方法 1: セットアップスクリプトを使う

以下の1コマンドで、仮想環境の作成・依存パッケージのインストール・設定ファイルのコピー・ディレクトリ作成をまとめて行えます。

```bash
bash setup.sh
```

スクリプト実行後、`config.yaml` と `.env` を編集して自分の環境に合わせてください。
編集が終わったら [起動前チェック](#起動前チェックcheck_envpy) を実行することをお勧めします。

---

### 方法 2: 手動でセットアップする

#### 1. リポジトリのクローンと依存パッケージのインストール

```bash
git clone <repository_url>
cd router-monitor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. 設定ファイルの準備

テンプレートから設定ファイルをコピーして、ご自身の環境に合わせて編集します。

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

**`.env` の設定項目**
機密情報を設定します。
| 変数名 | 説明 |
|--------|------|
| `HA_TOKEN` | Home Assistant の長期アクセストークン（**必須**） |
| `DISCORD_WEBHOOK_URL` | Discord の Webhook URL |
| `MISSKEY_API_TOKEN` | Misskey の API トークン |
| `MISSKEY_INSTANCE_URL` | Misskey の インスタンス URL (例: `https://misskey.io`) |

**`.env` の記述例：**
```
HA_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ4eHh4...（長い文字列）
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1234567890/xxxxxxxx
MISSKEY_API_TOKEN=
MISSKEY_INSTANCE_URL=
```

**`config.yaml` の設定項目**
監視のしきい値や対象のIPなどを設定します。
| キー | 説明 |
|------|------|
| `ping_targets.router_lan_ip` | ルーターのローカルIP（例: `192.168.0.1`） |
| `ping_targets.external` | 外部疎通確認用のIPリスト |
| `thresholds.fail_threshold` | 連続失敗でDOWNと判定する回数 |
| `thresholds.cooldown_seconds` | 再起動後にルーターの起動を待つ秒数 |
| `thresholds.reboot_lock_seconds` | 再起動後に次の再起動を禁止するロック秒数 |
| `thresholds.max_retry` | 障害発生時の最大再起動試行回数 |
| `home_assistant.base_url` | Home Assistant のURL（例: `http://localhost:8123`） |
| `home_assistant.entity_id` | 操作するスマートプラグのエンティティID |
| `notify.discord_enabled` | Discord通知を有効にするか（true/false） |

#### 3. データディレクトリの作成

ログファイルを出力するディレクトリを作成します。

```bash
mkdir -p data
```

#### 4. 起動前チェック（推奨）

`python3 main.py` を実行する前に、設定に問題がないか確認できます。

```bash
python3 check_env.py
```

#### 5. 手動での起動テスト

```bash
python3 main.py
```
正常に起動し、設定に問題がなければ監視がスタートします。`Ctrl+C`で終了できます。

---

## 起動前チェック（`check_env.py`）

`python3 main.py` を実行する前に、設定ファイルの問題を事前に検出できます。

```bash
# 基本チェック（設定ファイルの内容を確認）
python3 check_env.py

# Home Assistant への実際の HTTP 疎通も含めてチェック
python3 check_env.py --check-ha

# エラーのみ表示（OK 項目を省略）
python3 check_env.py --quiet
```

出力例：
```
✅ config.yaml が見つかりました
✅ .env が見つかりました
✅ HA_TOKEN が設定されています
✅ router_lan_ip が設定されています (192.168.0.1)
✅ home_assistant.base_url が設定されています (http://localhost:8123)
✅ data/ ディレクトリが存在します
```

終了コード: `0` = 全 OK、`1` = 警告あり（起動は可能）、`2` = エラーあり（起動不可）

---

## systemd による自動起動（推奨）

サーバーなどで常時稼働させる場合は、systemd でのサービス化を推奨します。

### サービスファイルの編集

まず `systemd/router-monitor.service` を自分の環境に合わせて編集します。

```bash
# 自分のユーザー名を確認
whoami

# このプログラムの絶対パスを確認
pwd
```

`systemd/router-monitor.service` をテキストエディタで開き、以下の箇所を書き換えてください：

```ini
[Service]
# ↓ このディレクトリのパスを「pwd の出力結果」に書き換える
WorkingDirectory=/home/username/router_reboot_system/router-monitor

# ↓ 2箇所ある /home/username/... を自分の環境のパスに書き換える
ExecStart=/usr/bin/python3 /home/username/router_reboot_system/router-monitor/main.py \
    --config /home/username/router_reboot_system/router-monitor/config.yaml \
    --env /home/username/router_reboot_system/router-monitor/.env

# ↓ User と Group を「whoami の出力結果」に書き換える
User=username
Group=username

# ↓ data/ ディレクトリのパスも書き換える
ReadWritePaths=/home/username/router_reboot_system/router-monitor/data
```

> [!TIP]
> `setup.sh` を使った場合、このパス置換は自動で行われます（sudo が必要な `cp` と `systemctl` の部分のみ手動）。

### サービスの有効化と起動

```bash
sudo cp systemd/router-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable router-monitor
sudo systemctl start router-monitor
```

ログの確認方法:
```bash
journalctl -u router-monitor -f
```

---

## ログの照会・統計確認

稼働状況は `data/` 内に JSON Lines 形式（`events.jsonl`, `reboots.jsonl`）で永続的に保存されます（サービス再起動後も消えません）。  
確認方法は **CLI** と **Web ダッシュボード** の2通りです。どちらも追加パッケージは不要です。

---

### CLI ツール（`query.py`）

ターミナルからカラー付きで確認できます。どのディレクトリからでも呼び出せるよう、エイリアスの設定を推奨します。

```bash
# ~/.bashrc に追記（一度だけ実行）
echo "alias router='python3 ~/router_reboot_system/router-monitor/query.py'" >> ~/.bashrc
source ~/.bashrc
```

設定後はどこからでも `router` コマンドで操作できます。

```bash
# 全体サマリー（再起動回数・今日の通信安定性）
router
# または: python3 query.py

# 今日の安定レポート
router --today

# 指定日のレポート
router --date 2026-07-01

# 直近30件のイベントログ（ping_cycle 正常分を除く）
router --events

# 直近50件 ─ ping_cycle も含む全件
router --events 50 --all

# 再起動の全履歴
router --reboots
```

---

### Web ダッシュボード（`dashboard.py`）

ブラウザで視覚的に確認できます。ターミナルで起動してブラウザを開くだけです。

```bash
cd ~/router_reboot_system/router-monitor
python3 dashboard.py                 # http://localhost:8088 で起動 (ローカル限定)
python3 dashboard.py --host 0.0.0.0  # 同一LAN内の別端末から開く場合
python3 dashboard.py --port 9000     # ポートを変更する場合
```

ブラウザで **http://localhost:8088** を開いてください。  
`Ctrl+C` で停止します。

> [!WARNING]
> **セキュリティに関する注意**: デフォルトでは `127.0.0.1`（ローカル環境限定）にバインドされます。`--host 0.0.0.0` を指定して起動すると同一LAN内の全ての端末からアクセス可能になります。この機能は認証機能を持たないため、インターネットに直接公開する（ポートフォワーディング等）ことは絶対に行わないでください。

**表示内容：**
- サマリーカード：累計・今日の再起動回数 / 今日の安定性 / 監視回数
- イベントログ：色分けされた直近のイベント（`ping_cycle` は切替ボタンで表示）
- 再起動履歴：日時・試行回数・OFF 時間の一覧
- 自動更新：30 秒ごとに自動リフレッシュ

---

## よくあるエラーと対処法（トラブルシューティング）

### `python3: command not found`

Python がインストールされていません。

- **Ubuntu/Debian**: `sudo apt install python3 python3-pip python3-venv`
- **macOS**: `brew install python3` または [python.org](https://www.python.org/downloads/) からインストール
- **Windows**: [python.org](https://www.python.org/downloads/) からインストーラーをダウンロード

---

### `config error: HA_TOKEN が .env に設定されていません`

`.env` ファイルに `HA_TOKEN` が設定されていません。

```bash
# .env ファイルを開いて確認・編集
nano .env
```

以下のように `=` の右側にトークンを貼り付けてください（スペースなし）：
```
HA_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...（長い文字列）
```

取得方法は [Home Assistant 長期アクセストークンの取得方法](#home-assistant-長期アクセストークンの取得方法) を参照してください。

---

### `config error: 必須の設定キーが不足しています`

`config.yaml` に必要なキーが存在しないか、書式が間違っています。

```bash
# テンプレートと比較する
diff config.yaml config.yaml.example
```

`config.yaml.example` を参考に、不足している項目を追記してください。

---

### `Connection refused` / `Failed to connect` (Home Assistant に接続できない)

`config.yaml` の `home_assistant.base_url` が正しいか確認してください。

```bash
# ブラウザまたは curl で HA に疎通できるか確認
curl http://localhost:8123/api/
```

- URL が `http://` または `https://` で始まっているか
- ポート番号（通常 `8123`）が正しいか
- Home Assistant が起動中か（HA のログを確認）

---

### `entity ... not found (HTTP 404)`

`config.yaml` の `entity_id` が間違っています。

Home Assistant の **開発者ツール → 状態** で正確な `entity_id` を確認してください（[調べ方はこちら](#スマートプラグの-entity_id-の調べ方)）。

---

### `No module named 'yaml'` / `No module named 'dotenv'` 等

依存パッケージがインストールされていません。仮想環境を有効化してからインストールしてください。

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

---

### `Permission denied: 'data/events.jsonl'`

`data/` ディレクトリが存在しないか、書き込み権限がありません。

```bash
mkdir -p data
```

---

## ディレクトリ構成

- `main.py`: プログラムのエントリーポイント。
- `setup.sh`: セットアップを半自動化するスクリプト（初心者向け）。
- `check_env.py`: 起動前に設定の問題を検出するチェックツール。
- `query.py`: CLIログ照会ツール（カラー対応）。
- `dashboard.py`: ブラウザで確認できる Web ダッシュボード。
- `config.yaml` / `.env`: 設定ファイル。
- `router_monitor/`: アプリケーションのコアロジック。
  - `config.py`: 設定ファイルの読み込みとバリデーション。
  - `checkers.py`: PingおよびARPによる疎通確認処理。
  - `state_machines/`: 障害検知や再起動のステートマシン（状態遷移）ロジック。
  - `relay/`: Home Assistant API経由でのスマートプラグ制御処理。
  - `notify/`: Discord/Misskeyへの通知送信処理。
  - `stats.py`: JSON Linesログからの日次レポートや再起動統計の生成処理。
- `data/`: `events.jsonl` や `reboots.jsonl` などのログデータが保存されます。
- `tests/`: ユニットテスト群。`python3 -m pytest tests/` で実行できます。
