#!/usr/bin/env bash
# =========================================================
# AIMemory 官网扩展服务 —— 服务器环境初始化
#   1) 增加 4G swap（服务器仅 3.5G 内存，跑 ONNX 推理需防 OOM）
#   2) 建立服务目录
#   3) 创建 Python venv 并安装依赖
#   4) 下载端侧小模型（来自 hf-mirror.com，国内可达）
# 适用：Rocky Linux 9 / Python 3.9 / root
# 用法：bash setup_server.sh
# =========================================================
set -uo pipefail

BASE=/opt/aimemory-services
VENV=$BASE/venv
MODELS=$BASE/restore/models
HF=https://hf-mirror.com/facefusion/models-3.0.0/resolve/main
LOG=$BASE/logs/setup.log

mkdir -p "$BASE"/{healing/data,healing/data/images,restore/models,restore/data,logs}
exec > >(tee -a "$LOG") 2>&1
echo "===== AIMemory 服务环境初始化 $(date '+%F %T') ====="

# ---------- 1. swap ----------
if swapon --show | grep -q '/swapfile'; then
    echo "[swap] 已存在，跳过"
else
    echo "[swap] 创建 4G swapfile ..."
    fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -w vm.swappiness=10 >/dev/null
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
    echo "[swap] 完成"
fi
free -h | head -3

# ---------- 2. venv + 依赖 ----------
if [ ! -x "$VENV/bin/python" ]; then
    echo "[venv] 创建虚拟环境 ..."
    python3 -m venv "$VENV"
fi
echo "[pip] 安装依赖 ..."
"$VENV/bin/pip" install -q --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ 2>&1 | tail -2
"$VENV/bin/pip" install -q \
    fastapi uvicorn python-multipart "numpy<2" pillow onnxruntime opencv-python-headless requests \
    -i https://mirrors.aliyun.com/pypi/simple/ 2>&1 | tail -5
echo "[pip] 版本确认:"
"$VENV/bin/python" -c "
import onnxruntime, numpy, cv2, PIL, fastapi, uvicorn
print('  onnxruntime', onnxruntime.__version__)
print('  numpy', numpy.__version__, '| opencv', cv2.__version__)
print('  fastapi', fastapi.__version__, '| uvicorn', uvicorn.__version__)
print('  providers', onnxruntime.get_available_providers())
"

# ---------- 3. 模型下载 ----------
download() {
    local name="$1" url="$2" dst="$MODELS/$1"
    if [ -s "$dst" ]; then echo "[model] $name 已存在 ($(du -h "$dst" | cut -f1))，跳过"; return 0; fi
    echo "[model] 下载 $name ..."
    for i in 1 2 3; do
        if curl -sL --noproxy '*' -m 600 -o "$dst.part" "$url" && [ -s "$dst.part" ]; then
            mv "$dst.part" "$dst"; echo "[model] $name 完成 ($(du -h "$dst" | cut -f1))"; return 0
        fi
        echo "[model] $name 第 $i 次失败，重试 ..."; sleep 3
    done
    echo "[model] !! $name 下载失败"; return 1
}

download scrfd_2.5g.onnx        "$HF/scrfd_2.5g.onnx"
download fan_68_5.onnx          "$HF/fan_68_5.onnx"
download gpen_bfr_256.onnx      "$HF/gpen_bfr_256.onnx"
download span_kendata_x4.onnx   "$HF/span_kendata_x4.onnx"
download real_esrgan_x4.onnx    "$HF/real_esrgan_x4.onnx"

echo ""
echo "===== 模型清单 ====="
ls -lh "$MODELS" | awk '{printf "  %-28s %s\n", $9, $5}'
echo ""
echo "===== 初始化结束 $(date '+%F %T') ====="
