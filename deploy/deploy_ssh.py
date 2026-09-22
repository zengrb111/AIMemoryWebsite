#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 部署脚本：把 AI回忆录 静态站推到轻量服务器并配置 Nginx（HTTP 先行）。
用法（密码走环境变量，不落盘）：
    SSHPASS='你的密码' python deploy_ssh.py
依赖：pip install paramiko
"""
import os
import sys
import time
import pathlib
import paramiko

HOST = os.environ.get("SSH_HOST", "39.107.32.151")
PORT = int(os.environ.get("SSH_PORT", "22"))
USER = os.environ.get("SSH_USER", "root")
PASS = os.environ.get("SSHPASS")
REMOTE_ROOT = "/var/www/aimemory"

if not PASS:
    sys.exit("缺少环境变量 SSHPASS（服务器 root 密码）")

LOCAL_ROOT = pathlib.Path(__file__).resolve().parent.parent  # D:/OPCProjects/AiMemoryWeb
UPLOAD_ITEMS = [
    "index.html", "product.html", "apps.html",
    "healing.html", "tools.html",
    "agreement.html", "privacy.html", "contact.html", "assets",
]

# 扩展服务的反向代理片段（心灵驿站内容 API / 老照片修复 API）
# 注意：location 必须带尾斜杠 + proxy_pass 带尾斜杠 => 正好剥掉 /api/xxx/ 前缀。
#       若 location 不带尾斜杠会拼出 //health 双斜杠导致后端 404（2026-09-22 踩过）。
API_LOCATIONS = """    # ---------- 扩展服务 ----------
    # 心灵驿站内容 API（FastAPI, 127.0.0.1:3010）—— ^~ 优先于静态资源正则，
    # 否则 /api/.../xxx.png 会被 \.(png)$ 正则拦截（nginx 先查正则再查前缀）
    location ^~ /api/healing/ {
        proxy_pass http://127.0.0.1:3010/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # 贴心工具·老照片修复 API（FastAPI + ONNX, 127.0.0.1:3020）
    location ^~ /api/tools/ {
        proxy_pass http://127.0.0.1:3020/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 20m;      # 允许上传老照片
        proxy_read_timeout   300s;     # 精修档推理较慢
        proxy_send_timeout   300s;
        proxy_request_buffering off;   # 大文件直传，避免落盘两遍
    }
"""

# 证书尚不存在时的纯 HTTP 配置（首次部署用）
NGINX_CONF_HTTP = f"""server {{
    listen 80;
    listen [::]:80;
    server_name aimemory.cafe www.aimemory.cafe;

    root {REMOTE_ROOT};
    index index.html;

    client_max_body_size 20m;

{API_LOCATIONS}
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
"""

# 证书已存在时的 HTTPS 配置（80 跳转 + 443 官网）
# 注意：必须保留 443 的官网 server，否则 443 请求会落到 H5 配置的 default server，
#       导致 https://aimemory.cafe 打开的是 H5 应用而不是官网（2026-09-08 踩过）。
NGINX_CONF_HTTPS = f"""# AI回忆录官网 —— 80 跳转 + 443 HTTPS
server {{
    listen 80;
    listen [::]:80;
    server_name aimemory.cafe www.aimemory.cafe;

    location /.well-known/acme-challenge/ {{
        root {REMOTE_ROOT};
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name aimemory.cafe www.aimemory.cafe;

    ssl_certificate     /etc/letsencrypt/live/aimemory.cafe/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aimemory.cafe/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache   shared:SSL:10m;

    root {REMOTE_ROOT};
    index index.html;

    client_max_body_size 20m;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

{API_LOCATIONS}
    location / {{
        try_files $uri $uri/ =404;
    }}

    location ~* \\.(css|js|svg|png|jpg|jpeg|gif|ico|woff2?)$ {{
        expires 7d;
        add_header Cache-Control "public, immutable";
    }}
}}
"""


def log(msg):
    print(f"[deploy] {msg}", flush=True)


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log(f"连接 {USER}@{HOST}:{PORT} ...")
    cli.connect(HOST, port=PORT, username=USER, password=PASS,
                timeout=30, look_for_keys=False, allow_agent=False)
    log("SSH 连接成功")
    return cli


def run(ssh, cmd, check=True):
    log(f"$ {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=600)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        print(out.rstrip())
    if err.strip() and (rc != 0 or check is False):
        print(err.rstrip(), file=sys.stderr)
    if check and rc != 0:
        raise RuntimeError(f"命令失败 (exit={rc}): {cmd}")
    return rc, out, err


def ensure_nginx(ssh):
    rc, out, _ = run(ssh, "command -v nginx || true", check=False)
    if "nginx" in out:
        log("检测到已安装 nginx")
        return
    log("未检测到 nginx，尝试安装 ...")
    # 探测包管理器
    rc, out, _ = run(ssh, "command -v apt-get && echo APT || true; command -v yum && echo YUM || true", check=False)
    if "APT" in out:
        run(ssh, "export DEBIAN_FRONTEND=noninteractive; apt-get update -y && apt-get install -y nginx")
    elif "YUM" in out:
        run(ssh, "yum install -y nginx")
    else:
        raise RuntimeError("无法识别包管理器，请手动安装 nginx")
    log("nginx 安装完成")


def upload(ssh):
    sftp = ssh.open_sftp()
    log(f"确保远程目录 {REMOTE_ROOT} 存在")
    ssh.exec_command(f"mkdir -p {REMOTE_ROOT}")
    time.sleep(0.5)

    def put_path(rel):
        local = LOCAL_ROOT / rel
        remote = f"{REMOTE_ROOT}/{rel}"
        if local.is_dir():
            try:
                sftp.stat(remote)
            except IOError:
                sftp.mkdir(remote)
            for child in sorted(local.iterdir()):
                put_path(f"{rel}/{child.name}")
        else:
            remote_parent = str(pathlib.PurePosixPath(remote).parent)
            try:
                sftp.stat(remote_parent)
            except IOError:
                sftp.mkdir(remote_parent)
            sftp.put(str(local), remote)
            log(f"  -> {remote}")

    for item in UPLOAD_ITEMS:
        put_path(item)
    sftp.close()
    log("文件上传完成")


def write_nginx_conf(ssh):
    conf_remote = "/etc/nginx/conf.d/aimemory.cafe.conf"
    # 检测证书：已存在则写 HTTPS 配置，避免把配好的 443 覆盖成纯 80
    # （否则 443 会落到 H5 的 default server，官网变成 H5）
    _, out, _ = run(ssh, "test -f /etc/letsencrypt/live/aimemory.cafe/fullchain.pem "
                         "&& echo HAS_CERT || echo NO_CERT", check=False)
    has_cert = "HAS_CERT" in out
    conf = NGINX_CONF_HTTPS if has_cert else NGINX_CONF_HTTP
    log(f"证书检测: {'已存在 → 写入 HTTPS 配置(80跳转+443)' if has_cert else '未找到 → 写入纯 HTTP 配置'}")
    # 若 sites-enabled 体系存在则一并处理（Ubuntu 默认）
    sftp = ssh.open_sftp()
    with sftp.open(conf_remote, "w") as f:
        f.write(conf)
    sftp.close()
    log(f"已写入 nginx 配置 {conf_remote}")
    # 关闭默认站点避免冲突（可选）
    run(ssh, "rm -f /etc/nginx/sites-enabled/default", check=False)
    run(ssh, "nginx -t", check=True)
    log("nginx 配置语法校验通过")


def restart_nginx(ssh):
    for cmd in [
        "systemctl enable nginx 2>/dev/null || true",
        "systemctl restart nginx 2>/dev/null || service nginx restart 2>/dev/null || nginx -s reload",
    ]:
        run(ssh, cmd, check=False)
    time.sleep(2)
    rc, out, _ = run(ssh, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ ; echo", check=False)
    log(f"本地 HTTP 探测状态码: {out.strip()}")


def verify(ssh):
    rc, out, _ = run(ssh, "curl -s http://127.0.0.1/ | grep -o 'AI回忆录' | head -1 || true", check=False)
    if "AI回忆录" in out:
        log("首页内容校验通过：包含『AI回忆录』")
    else:
        log("警告：首页未检测到『AI回忆录』字样，请人工确认")


def main():
    ssh = connect()
    try:
        ensure_nginx(ssh)
        upload(ssh)
        write_nginx_conf(ssh)
        restart_nginx(ssh)
        verify(ssh)
        log("部署完成 ✅")
        log("说明：检测到证书时会自动写入 443 配置；首次无证书时先写 80，跑 deploy_ssl.py 后再发布一次即可启用 HTTPS。")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
