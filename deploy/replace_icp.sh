#!/usr/bin/env bash
# =========================================================
# 备案号替换脚本
# 用法：./replace_icp.sh 新的备案号
# 说明：把全站当前备案号替换为新号（例如未来变更时）
# =========================================================
set -euo pipefail

NEW_ICP="${1:?用法: ./replace_icp.sh 新的备案号}"
SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLACEHOLDER="京ICP备2026024457号-2"

cd "$SITE_DIR"
shopt -s nullglob
files=(index.html product.html apps.html agreement.html privacy.html contact.html)
for f in "${files[@]}"; do
  if grep -q "$PLACEHOLDER" "$f"; then
    # 跨平台 sed 兼容（Git Bash / GNU sed）
    sed -i "s/${PLACEHOLDER}/${NEW_ICP}/g" "$f"
    echo "已更新: $f"
  fi
done
echo "✅ 备案号已替换为: $NEW_ICP"
