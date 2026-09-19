#!/usr/bin/env bash
# =========================================================
# AI回忆录（AIMemory）官网 · 一键部署脚本（ECS / 轻量服务器）
# 前置：本地安装 rsync + openssh；服务器已装 Nginx。
# 用法：
#   REMOTE_HOST=1.2.3.4 REMOTE_USER=root REMOTE_DIR=/var/www/aimemory ./deploy.sh
# 或导出环境变量后直接 ./deploy.sh
# =========================================================
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:?请设置 REMOTE_HOST（服务器公网 IP）}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/var/www/aimemory}"
SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-}"   # 可选：私钥路径，如 ~/.ssh/id_rsa

SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "▶ 部署目录: $SITE_DIR"
echo "▶ 目标: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR} (port ${SSH_PORT})"

SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new)
if [ -n "$SSH_KEY" ]; then SSH_OPTS+=(-i "$SSH_KEY"); fi

# 远程建目录
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}"

# 同步（排除本地工程目录）
rsync -avz --delete \
  -e "ssh ${SSH_OPTS[*]}" \
  --exclude '.workbuddy' \
  --exclude 'deploy' \
  --exclude '.git' \
  "$SITE_DIR/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

# Nginx 配置推送（可选）
if [ "${PUSH_NGINX:-1}" = "1" ]; then
  scp -P "$SSH_PORT" "${SSH_OPTS[@]}" \
    "$SITE_DIR/deploy/nginx-aimemory.cafe.conf" \
    "${REMOTE_USER}@${REMOTE_HOST}:/etc/nginx/conf.d/aimemory.cafe.conf" || \
    echo "（跳过 Nginx 配置推送，请手动放置 conf）"
  ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "nginx -t && systemctl reload nginx" || \
    echo "（请手动执行 nginx -t && systemctl reload nginx）"
fi

echo "✅ 部署完成。访问 https://aimemory.cafe"
