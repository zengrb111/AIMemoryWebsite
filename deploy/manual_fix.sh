#!/bin/bash
# =========================================================
# AI回忆录官网 —— 服务器端一键修复脚本（备案号 + HTTPS）
# 用途：当 SSH 密码认证不可用时的兜底方案。
# 用法：阿里云控制台 → 轻量应用服务器 → 实例 →「远程连接」
#       → 登录后把本文件全部内容复制粘贴到终端回车即可。
# 说明：服务器已有站点文件，本脚本只改备案号文案 + 上 HTTPS，
#       不需要从本地上传任何文件。
# =========================================================
set -e

WEBROOT=/var/www/aimemory
CONF=/etc/nginx/conf.d/aimemory.cafe.conf
DOMAIN=aimemory.cafe

echo "========== 1) 更新备案号为 -2 =========="
cd "$WEBROOT"
sed -i 's|京ICP备2026024457号|京ICP备2026024457号-2|g; s|https://beian\.miit\.gov\.cn/|https://beian.miit.gov.cn|g' \
  index.html product.html apps.html agreement.html privacy.html contact.html
echo -n "index.html 中新备案号出现次数: "
grep -c '京ICP备2026024457号-2' index.html

echo "========== 2) 确保 certbot 已安装 =========="
command -v certbot >/dev/null 2>&1 || dnf install -y epel-release certbot

echo "========== 3) 申请 Let's Encrypt 证书（webroot）=========="
certbot certonly --webroot -w "$WEBROOT" \
  -d "$DOMAIN" -d "www.$DOMAIN" \
  --non-interactive --agree-tos -m sale@aimemory.cafe --no-eff-email

echo "========== 4) 写入 nginx 443 配置（含 80 跳转）=========="
cp "$CONF" "${CONF}.bak.$(date +%s)"
cat > "$CONF" <<'EOF'
server {
    listen 80;
    server_name aimemory.cafe www.aimemory.cafe;

    # 证书续期校验路径保持走 80
    location /.well-known/acme-challenge/ {
        root /var/www/aimemory;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name aimemory.cafe www.aimemory.cafe;

    root /var/www/aimemory;
    index index.html;

    ssl_certificate     /etc/letsencrypt/live/aimemory.cafe/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aimemory.cafe/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        try_files $uri $uri/ =404;
    }
}
EOF

echo "========== 5) 校验并重载 nginx =========="
nginx -t && systemctl reload nginx

echo "========== 6) 验证结果 =========="
curl -s -o /dev/null -w "HTTPS 状态码: %{http_code}\n" "https://$DOMAIN/"
curl -s "https://$DOMAIN/" | grep -o '京ICP备2026024457号-2' | head -1
echo "完成 ✅"
