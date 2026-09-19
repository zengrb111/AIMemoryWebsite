#!/usr/bin/env bash
# =========================================================
# 阿里云 OSS 静态托管上传脚本（方式 B）
# 前置：安装并配置 ossutil（已配置 endpoint / ak）
# 用法：BUCKET=your-bucket ./oss.sh
# =========================================================
set -euo pipefail

BUCKET="${BUCKET:?请设置 BUCKET（OSS bucket 名称）}"
SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "▶ 上传整站到 oss://${BUCKET}/"
ossutil cp -r "$SITE_DIR" "oss://${BUCKET}/" \
  --exclude ".workbuddy/*" \
  --exclude "deploy/*" \
  --update

echo "✅ 上传完成。请在 OSS 控制台绑定自定义域名 aimemory.cafe 并开启 HTTPS。"
