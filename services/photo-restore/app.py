# -*- coding: utf-8 -*-
"""
贴心工具 · 老照片修复服务
=================================================================
对 aimemory.cafe 官网「贴心工具」频道提供老照片修复能力。

  公开接口
    GET  /                    服务信息
    GET  /health              健康检查（含模型清单、队列状态）
    GET  /modes               可用修复档位说明
    POST /restore             上传照片，创建修复任务 -> {task_id}
    GET  /task/{task_id}      查询进度 / 结果
    GET  /result/{task_id}    下载修复后的照片
    GET  /preview/{task_id}/{side}   查看原图/成图（side=in|out）

单机只有 2 核，推理串行执行（worker=1），并用队列长度做背压保护。

运行：uvicorn app:app --host 127.0.0.1 --port 3020
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import restore as R

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CST = timezone(timedelta(hours=8))
SITE = "https://aimemory.cafe"

MAX_UPLOAD = 15 * 1024 * 1024      # 单张上限 15MB
MAX_QUEUE = 12                      # 排队上限，超了直接拒绝，避免拖垮生产服务
TASK_TTL = 2 * 3600                 # 任务文件保留 2 小时

os.makedirs(DATA_DIR, exist_ok=True)

TASKS: Dict[str, Dict[str, Any]] = {}
LOCK = threading.Lock()
JOB_Q: "queue.Queue[str]" = queue.Queue()

MODE_DESC = {
    "fast": {"label": "快速修复", "desc": "仅超分降噪 + 自动校色，不修人脸，最省时间", "eta": "约 5-15 秒"},
    "standard": {"label": "标准修复", "desc": "超分降噪 + AI 人脸修复 + 自动校色（推荐）", "eta": "约 15-40 秒"},
    "strong": {"label": "精修模式", "desc": "Real-ESRGAN 重建细节 + AI 人脸修复 + 校色", "eta": "约 40-120 秒"},
}


def now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# 工作线程：串行处理，避免 2 核被抢爆
# --------------------------------------------------------------------------
def _worker() -> None:
    while True:
        tid = JOB_Q.get()
        task = TASKS.get(tid)
        if not task:
            JOB_Q.task_done()
            continue
        d = os.path.join(DATA_DIR, tid)
        try:
            task.update(status="running", started_at=now(), progress=5, stage="开始修复")
            r = R.restore(
                os.path.join(d, "in.jpg"),
                mode=task["mode"],
                out_path=os.path.join(d, "out.jpg"),
                progress=lambda name, dt: task.update(stage=name, last_stage_seconds=dt),
            )
            task.update(status="done", progress=100, stage="完成", finished_at=now(),
                        result=r, meta={"width": r["width"], "height": r["height"],
                                        "faces": r["faces"], "elapsed": r["elapsed"]})
        except Exception as exc:
            task.update(status="failed", stage="失败", finished_at=now(),
                        error=f"{type(exc).__name__}: {exc}")
            print(f"[restore] 任务 {tid} 失败: {type(exc).__name__}: {exc}")
        finally:
            JOB_Q.task_done()


for _ in range(1):
    threading.Thread(target=_worker, daemon=True).start()


def _gc() -> None:
    """清理过期任务，避免磁盘无限增长。"""
    cutoff = time.time() - TASK_TTL
    for name in os.listdir(DATA_DIR):
        p = os.path.join(DATA_DIR, name)
        if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            with LOCK:
                TASKS.pop(name, None)
    keep = {t for t, v in TASKS.items() if v.get("status") in ("queued", "running")}
    for t in list(TASKS):
        if t not in keep and t not in set(os.listdir(DATA_DIR)):
            TASKS.pop(t, None)


def _gc_loop() -> None:
    while True:
        time.sleep(900)
        try:
            _gc()
        except Exception:
            pass


threading.Thread(target=_gc_loop, daemon=True).start()


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
app = FastAPI(
    title="贴心工具 · 老照片修复 API",
    description="端侧小模型本地推理：SCRFD 人脸检测 + GPEN-BFR 人脸修复 + SPAN/Real-ESRGAN 超分",
    version="1.0.0",
    root_path=os.environ.get("ROOT_PATH", "/api/tools"),
    docs_url="/docs",
    redoc_url=None,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    try:
        import json
        sig = R.model_signature()
        print("[restore] 模型加载完成:")
        print(json.dumps({k: v["outputs"] for k, v in sig.items()}, ensure_ascii=False)[:1500])
    except Exception as exc:
        print(f"[restore] 模型自检失败: {type(exc).__name__}: {exc}")


@app.get("/", include_in_schema=False)
def root() -> Dict[str, Any]:
    return {"service": "AIMemory 老照片修复 API", "version": "1.0.0",
            "site": SITE, "docs": f"{SITE}/api/tools/docs",
            "modes": list(MODE_DESC), "endpoints": ["POST /restore", "GET /task/{id}", "GET /result/{id}"]}


@app.get("/health")
def health() -> Dict[str, Any]:
    models = []
    md = R.MODEL_DIR
    if os.path.isdir(md):
        models = sorted(f for f in os.listdir(md) if f.endswith(".onnx"))
    return {"status": "ok", "time": now(), "models": models,
            "queue": {"pending": JOB_Q.qsize(), "max": MAX_QUEUE},
            "active_tasks": sum(1 for t in TASKS.values() if t["status"] in ("queued", "running"))}


@app.get("/modes")
def modes() -> Dict[str, Any]:
    return {"modes": [{"key": k, **v} for k, v in MODE_DESC.items()], "default": "standard"}


@app.post("/restore")
async def create_restore(file: UploadFile = File(...), mode: str = Form("standard")) -> Dict[str, Any]:
    if mode not in R.MODES:
        raise HTTPException(400, f"不支持的档位: {mode}")
    if JOB_Q.qsize() >= MAX_QUEUE:
        raise HTTPException(503, "当前排队较多，请稍后再试")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, f"图片请小于 {MAX_UPLOAD // 1048576}MB")

    # 统一转成 JPEG，顺便校正 EXIF 方向
    try:
        import io

        from PIL import Image, ImageFile, ImageOps

        Image.MAX_IMAGE_PIXELS = None
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        pil = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        tid = "rt_" + uuid.uuid4().hex[:14]
        d = os.path.join(DATA_DIR, tid)
        os.makedirs(d, exist_ok=True)
        pil.save(os.path.join(d, "in.jpg"), "JPEG", quality=96)
        in_size = pil.size
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"图片解析失败: {exc}")

    with LOCK:
        TASKS[tid] = {"task_id": tid, "status": "queued", "progress": 0, "stage": "排队中",
                      "mode": mode, "created_at": now(), "input_size": list(in_size)}
    JOB_Q.put(tid)
    return {"ok": True, "task_id": tid, "status": "queued", "mode": mode,
            "position": JOB_Q.qsize(), "poll": f"{SITE}/api/tools/task/{tid}"}


@app.get("/task/{tid}")
def task(tid: str) -> Dict[str, Any]:
    t = TASKS.get(tid)
    if not t:
        if not os.path.isdir(os.path.join(DATA_DIR, os.path.basename(tid))):
            raise HTTPException(404, "任务不存在或已过期")
        raise HTTPException(410, "任务状态已过期，请重新提交")
    out = {k: v for k, v in t.items() if k not in ("result",)}
    if t["status"] == "done":
        out["result_url"] = f"{SITE}/api/tools/result/{tid}"
        out["preview_before"] = f"{SITE}/api/tools/preview/{tid}/in"
        out["preview_after"] = f"{SITE}/api/tools/preview/{tid}/out"
        out["meta"] = t.get("meta")
    if t["status"] == "failed":
        out["error"] = t.get("error")
    return out


@app.get("/result/{tid}")
def result(tid: str):
    p = os.path.join(DATA_DIR, os.path.basename(tid), "out.jpg")
    if not os.path.exists(p):
        raise HTTPException(404, "结果尚未生成")
    return FileResponse(p, media_type="image/jpeg",
                        filename=f"aimemory-restored-{tid[-6:]}.jpg")


@app.get("/preview/{tid}/{side}")
def preview(tid: str, side: str):
    name = "in.jpg" if side == "in" else "out.jpg"
    p = os.path.join(DATA_DIR, os.path.basename(tid), name)
    if not os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/models")
def models() -> JSONResponse:
    """模型签名（便于排查预处理不匹配问题）。"""
    try:
        return JSONResponse(R.model_signature())
    except Exception as exc:
        raise HTTPException(500, str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=3020)
