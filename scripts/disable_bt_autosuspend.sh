#!/usr/bin/env bash
# Realtek Bluetooth USB アダプタ (0bda:b85b) の USB 自動サスペンドを無効化するスクリプト

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Error: このスクリプトは sudo で実行してください。" >&2
  echo "Usage: sudo bash $0" >&2
  exit 1
fi

echo "==> 恒久的な udev ルールを作成中 (/etc/udev/rules.d/50-bt-no-autosuspend.rules)..."
cat << 'EOF' > /etc/udev/rules.d/50-bt-no-autosuspend.rules
# Disable USB autosuspend for Realtek Bluetooth adapter to prevent dropouts
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="b85b", TEST=="power/control", ATTR{power/control}="on"
EOF

echo "==> udev ルールを再読み込み中..."
udevadm control --reload
udevadm trigger --subsystem-match=usb

echo "==> 現在接続されている対象デバイスの power/control を確認・設定中..."
FOUND=0
for d in /sys/bus/usb/devices/*; do
  if [ -f "$d/idVendor" ] && [ -f "$d/idProduct" ]; then
    v=$(cat "$d/idVendor")
    p=$(cat "$d/idProduct")
    if [ "$v" = "0bda" ] && [ "$p" = "b85b" ]; then
      FOUND=1
      echo "on" > "$d/power/control"
      echo "  - $d: power/control = $(cat "$d/power/control")"
    fi
  fi
done

if [ "$FOUND" -eq 1 ]; then
  echo "==> 完了: Bluetooth アダプタの USB サスペンドが無効化されました (再起動後も維持されます)。"
else
  echo "==> 完了: udev ルールを配置しました (対象デバイスは次回接続時/起動時に適用されます)。"
fi
