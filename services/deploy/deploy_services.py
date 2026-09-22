#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署两个官网扩展服务到轻量服务器：
  1) 心灵驿站内容 API   -> /opt/aimemory-services/healing   (127.0.0.1:3010)
  2) 老照片修复 API     -> /opt/aimemory-services/restore   (127.0.0.1:3020)

用法（密码走环境变量，不落盘）：
    SSHPASS='你的密码' python deploy_services.py
依赖：pip install paramiko
"""
import os
import pathlib
import sys
import time

import paramiko

HOST = os.environ.get("SSH_HOST", "39.107.32.151")
PORT = int(os.environ.get("SSH_PORT", "22"))
USER = os.environ.get("SSH_USER", "root")
PASS = os.environ.get("SSHPASS")

BASE = "/opt/aimemory-services"
HERE = pathlib.Path(__file__).resolve().parent
SERVICES = HERE.parent            # .../services
PROJECT = SERVICES.parent         # .../AiMemoryWeb

UPLOADS = [
    # (本地, 远程, 说明)
    (SERVICES / "healing-api" / "app.py",        f"{BASE}/healing/app.py",   "心灵驿站服务"),
    (SERVICES / "healing-api" / "seed.json",     f"{BASE}/healing/seed.json", "心灵驿站初始内容"),
    (SERVICES / "photo-restore" / "app.py",      f"{BASE}/restore/app.py",   "老照片修复 HTTP 层"),
    (SERVICES / "photo-restore" / "restore.py",  f"{BASE}/restore/restore.py", "老照片修复推理管线"),
    (SERVICES / "deploy" / "healing-api.service",   "/etc/systemd/system/healing-api.service",   "systemd 单元"),
    (SERVICES / "deploy" / "photo-restore.service", "/etc/systemd/system/photo-restore.service", "systemd 单元"),
]

if not PASS:
    sys.exit("缺少环境变量 SSHPASS（服务器 root 密码）")


def log(m):
    print(f"[svc] {m}", flush=True)


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log(f"连接 {USER}@{HOST} ...")
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=30, look_for_keys=False, allow_agent=False)
    log("SSH 连接成功")
    return c


def run(ssh, cmd, timeout=300, check=False):
    stdin, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace").rstrip()
    e = err.read().decode("utf-8", "replace").rstrip()
    rc = out.channel.recv_exit_status()
    if o:
        print(o)
    if e and rc != 0:
        print("[err]", e, file=sys.stderr)
    if check and rc != 0:
        raise RuntimeError(f"命令失败({rc}): {cmd}")
    return rc, o, e


def upload(ssh):
    sftp = ssh.open_sftp()
    run(ssh, f"mkdir -p {BASE}/healing/data/images {BASE}/restore/models {BASE}/restore/data {BASE}/logs")
    for local, remote, desc in UPLOADS:
        if not local.exists():
            log(f"!! 本地文件不存在，跳过: {local}")
            continue
        sftp.put(str(local), remote)
        size = local.stat().st_size
        log(f"  -> {remote}  ({size:,} B)  {desc}")
    sftp.close()
    log("代码上传完成")


def main():
    ssh = connect()
    try:
        upload(ssh)
        log("重新加载 systemd 并启用服务 ...")
        run(ssh, "systemctl daemon-reload", check=True)
        run(ssh, "systemctl enable healing-api.service photo-restore.service 2>&1 | tail -3")
        run(ssh, "systemctl restart healing-api.service photo-restore.service", check=True)
        log("等待服务就绪 ...")
        time.sleep(6)

        print("\n===== 服务状态 =====")
        run(ssh, "systemctl --no-pager --full status healing-api.service photo-restore.service "
                 "| grep -E 'Active:|Loaded:|Main PID:|healing-api.service|photo-restore.service'")

        print("\n===== 本地探活 =====")
        run(ssh, "curl -s -m 10 http://127.0.0.1:3010/health; echo; "
                 "curl -s -m 10 http://127.0.0.1:3020/health; echo")

        print("\n===== 心灵驿站内容 API 令牌 =====")
        run(ssh, f"cat {BASE}/healing/data/api_token.txt 2>/dev/null; echo")

        print("\n===== 最近日志 =====")
        run(ssh, f"tail -8 {BASE}/logs/healing.log 2>/dev/null; echo '--- restore ---'; "
                 f"tail -15 {BASE}/logs/restore.log 2>/dev/null; "
                 f"echo '--- err ---'; tail -12 {BASE}/logs/restore.err 2>/dev/null")

        log("服务部署完成 ✅")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
