#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
申请并配置 HTTPS 证书（Let's Encrypt，webroot 模式，规避 SELinux 下 --nginx 插件的 403）。
前置条件：
  1) 阿里云 DNS 已把 aimemory.cafe / www.aimemory.cafe 的 A 记录指向本服务器 IP
  2) 服务器 80 端口可公网访问（HTTP-01 挑战用）
用法（密码走环境变量，不落盘）：
    SSHPASS='你的密码' python deploy_ssl.py
依赖：pip install paramiko
"""
import os
import sys
import time
import paramiko

HOST = os.environ.get("SSH_HOST", "39.107.32.151")
PORT = int(os.environ.get("SSH_PORT", "22"))
USER = os.environ.get("SSH_USER", "root")
PASS = os.environ.get("SSHPASS")
EMAIL = os.environ.get("CERT_EMAIL", "sale@aimemory.cafe")
WEBROOT = "/var/www/aimemory"

# 完整 nginx 配置：443 + 80→443 跳转 + 放行 ACME 挑战路径（供证书续期）
NGINX_CONF = f"""server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name aimemory.cafe www.aimemory.cafe;

    ssl_certificate /etc/letsencrypt/live/aimemory.cafe/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aimemory.cafe/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_session_cache shared:SSL:10m;

    root {WEBROOT};
    index index.html;

    location / {{
        try_files $uri $uri/ /index.html;
    }}

    location ~* \\.(css|js|svg|png|jpg|jpeg|gif|ico|woff2?|woff|ttf|eot)$ {{
        expires 7d;
        add_header Cache-Control "public, immutable";
    }}

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}}

server {{
    listen 80;
    listen [::]:80;
    server_name aimemory.cafe www.aimemory.cafe;

    # 证书续期挑战路径（certbot webroot 默认写到这里）
    location /.well-known/acme-challenge/ {{
        root {WEBROOT};
    }}

    # 其余全部跳转到 HTTPS
    location / {{
        return 301 https://$host$request_uri;
    }}
}}
"""


def log(m):
    print(f"[ssl] {m}", flush=True)


def main():
    if not PASS:
        sys.exit("缺少环境变量 SSHPASS")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log(f"连接 {USER}@{HOST}:{PORT} ...")
    c.connect(HOST, port=PORT, username=USER, password=PASS, timeout=30,
              look_for_keys=False, allow_agent=False)
    log("已连接")

    # 1) 停 nginx 释放 80 端口，改用 standalone 模式（certbot 自带临时 web server 响应挑战，
    #    彻底绕开 nginx 读取 challenge 文件权限导致的 403）
    c.exec_command("systemctl stop nginx")
    time.sleep(2)
    # --expand：已有证书只含 aimemory.cafe，需扩展到同时含 www.aimemory.cafe
    cmd = (f"certbot certonly --standalone --preferred-challenges http --expand "
           f"-d aimemory.cafe -d www.aimemory.cafe "
           f"--non-interactive --agree-tos -m {EMAIL} --no-eff-email")
    i, o, e = c.exec_command(cmd, timeout=300)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    rc = o.channel.recv_exit_status()
    if rc != 0:
        log("certbot 申请失败，恢复 nginx（保留原 80 配置）")
        c.exec_command("systemctl start nginx")
        c.close()
        sys.exit(1)
    log("证书申请成功")

    # 2) 写入完整 nginx 配置（443 + 80 跳转）
    sftp = c.open_sftp()
    with sftp.open("/etc/nginx/conf.d/aimemory.cafe.conf", "w") as f:
        f.write(NGINX_CONF)
    sftp.close()
    log("已写入 nginx 配置（443 + 80→443）")

    # 3) 校验并重载
    i, o, e =     c.exec_command("nginx -t", timeout=60)
    print(o.read().decode("utf-8", "replace").rstrip())
    print(e.read().decode("utf-8", "replace").rstrip())
    c.exec_command("systemctl start nginx")
    log("nginx 已启动（443 + 80→443 跳转）")

    # 4) 本地验证 443（绕过公网防火墙，验证 nginx SSL 本身正常）
    i, o, e = c.exec_command(
        "curl -sk https://aimemory.cafe/ -o /dev/null -w 'local-https=%{http_code}\\n' ; "
        "ls -l /etc/letsencrypt/live/aimemory.cafe/ 2>/dev/null | head")
    print(o.read().decode("utf-8", "replace").rstrip())
    log("HTTPS 本地验证完成。若公网 https 仍不通，请检查阿里云防火墙 443 是否真正放行。")
    c.close()


if __name__ == "__main__":
    main()
