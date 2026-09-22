# -*- coding: utf-8 -*-
"""
老照片修复推理管线（端侧小模型 / CPU 友好）
=================================================================
模型全部为 ONNX，总权重约 145MB，可在无 GPU 的 2 核服务器上运行。

  人脸检测   SCRFD-2.5G       3.1 MB   InsightFace 轻量检测器，输出框 + 5 关键点
  人脸修复   GPEN-BFR-256    72.3 MB   盲人脸修复（Blind Face Restoration），
                                       专治老照片里模糊 / 曝光差 / 有噪点的人脸
  超分降噪   SPAN x4          1.6 MB   Swift Parameter-free Attention Network，
                                       仅 549K 参数却接近 SOTA 的超分模型
  精修超分   Real-ESRGAN x4  66.3 MB   更激进的细节重建（"精修"档使用）

处理流程：
  1) 方向校正 & 尺寸约束
  2) 人脸检测 → 5 点相似变换对齐 → GPEN 修复 → 反变换羽化贴回
  3) 全图超分（SPAN / Real-ESRGAN，支持分块以控制内存）
  4) 传统画质增强：自动白平衡（去泛黄）、自动色阶（救褪色）、轻度锐化

用法：
  python restore.py --selftest input.jpg output.jpg --mode standard
"""

from __future__ import annotations

import argparse
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFile, ImageOps

Image.MAX_IMAGE_PIXELS = None
# 容忍轻微截断的图片（老照片扫描件、传输不全的文件很常见）
ImageFile.LOAD_TRUNCATED_IMAGES = True

MODEL_DIR = os.environ.get("RESTORE_MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))

# --------------------------------------------------------------------------
# 每档模式的参数
# --------------------------------------------------------------------------
# max_side 直接决定内存与耗时：输出边长 = max_side * 4。
# 服务器只有 2 核 / 3.5G（还跑着主站），所以这里刻意压住上限。
MODES: Dict[str, Dict[str, Any]] = {
    # 快速：只做超分 + 校色，不修脸。秒级返回。
    "fast": {"sr": "span", "face": False, "max_side": 800, "tile": 448},
    # 标准：超分 + 人脸修复 + 校色。默认档（实测约 13s @650px 输入）。
    "standard": {"sr": "span", "face": True, "max_side": 880, "tile": 448},
    # 精修：优先 Real-ESRGAN（实测 2 核跑 650px 输入要 ~4.6 分钟），
    # 因此只有小图（<=480px）才真正用 R-ESRGAN，大图自动降级 SPAN + 增强加强。
    "strong": {"sr": "real_esrgan", "sr_fallback": "span", "face": True,
               "max_side": 880, "tile": 256, "enhance": 1.35},
}

# 输出边长上限：x4 模型输出 = max_side*4，超过这个尺寸文件巨大且无意义
# （原片往往没有那么高分辨率）。实际 max_side 会被压到 OUT_CAP//4。
OUT_CAP = 2600

# 5 点对齐模板（ArcFace-128，归一化坐标；对齐后人脸居正）
FACE_TEMPLATE = np.array(
    [
        [0.34191607, 0.46157411],
        [0.65653393, 0.45983393],
        [0.50022500, 0.64050536],
        [0.37097589, 0.82469196],
        [0.63151696, 0.82325089],
    ],
    dtype=np.float32,
)

_SESSIONS: Dict[str, ort.InferenceSession] = {}


# --------------------------------------------------------------------------
# 模型加载
# --------------------------------------------------------------------------
def _session(name: str) -> ort.InferenceSession:
    if name in _SESSIONS:
        return _SESSIONS[name]
    path = os.path.join(MODEL_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"模型缺失: {path}")
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = int(os.environ.get("ORT_THREADS", "2"))
    opts.inter_op_num_threads = 1
    opts.enable_mem_pattern = True
    # 这些导出的模型把大量初始化权重放在 graph input 里，ORT 会刷一屏 WARNING，
    # 与业务无关，压到只报 ERROR
    opts.log_severity_level = 3
    sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
    _SESSIONS[name] = sess
    return sess


def model_signature() -> Dict[str, Any]:
    """打印各模型的输入输出签名，便于核对预处理是否匹配。"""
    out = {}
    for f in sorted(os.listdir(MODEL_DIR)) if os.path.isdir(MODEL_DIR) else []:
        if not f.endswith(".onnx"):
            continue
        s = _session(f)
        out[f] = {
            "inputs": [{"name": i.name, "shape": i.shape, "type": i.type} for i in s.get_inputs()],
            "outputs": [{"name": o.name, "shape": o.shape, "type": o.type} for o in s.get_outputs()],
        }
    return out


def _input_name(sess: ort.InferenceSession) -> str:
    return sess.get_inputs()[0].name


def _nchw(img: np.ndarray) -> np.ndarray:
    return np.transpose(img.astype(np.float32), (2, 0, 1))[None, ...]


# --------------------------------------------------------------------------
# 1) 人脸检测：SCRFD
# --------------------------------------------------------------------------
def _scrfd_anchors(height: int, width: int, stride: int, num_anchors: int = 2) -> np.ndarray:
    """按特征的 (h,w) 生成 anchor 中心。"""
    grid = np.stack(np.meshgrid(np.arange(width), np.arange(height)), axis=-1).astype(np.float32)
    grid = (grid * stride).reshape(-1, 2)
    return np.repeat(grid, num_anchors, axis=0)


def detect_faces(bgr: np.ndarray, det_size: int = 640, score_thr: float = 0.5,
                 nms_thr: float = 0.4) -> List[Dict[str, Any]]:
    """
    返回 [{bbox:[x1,y1,x2,y2], kps:[[x,y]*5], score}]，坐标基于输入图。

    注意：本模型的 9 个输出是「按类型分组」的：
        outputs[0:3] = 三个 stride(8/16/32) 的 score
        outputs[3:6] = 三个 stride 的 bbox
        outputs[6:9] = 三个 stride 的 kps
    为免依赖具体导出顺序，这里按「最后一维长度 + 行数」自动配对。
    """
    sess = _session("scrfd_2.5g.onnx")
    h0, w0 = bgr.shape[:2]
    scale = det_size / max(h0, w0)
    nh = max(32, int(round(h0 * scale)))
    nw = max(32, int(round(w0 * scale)))
    nh -= nh % 32
    nw -= nw % 32
    resized = cv2.resize(bgr, (nw, nh))

    blob = _nchw(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)).astype(np.float32)
    blob = (blob - 127.5) / 128.0
    outputs = [np.asarray(o) for o in sess.run(None, {_input_name(sess): blob})]

    # 按输出张量最后一维归组：score=1, bbox=4, kps=10
    groups: Dict[int, List[np.ndarray]] = {1: [], 4: [], 10: []}
    for o in outputs:
        if o.ndim >= 2 and o.shape[-1] in groups:
            groups[o.shape[-1]].append(o)

    NUM_ANCHORS = 2
    scores_all, boxes_all, kps_all = [], [], []
    for stride in (8, 16, 32):
        fh, fw = nh // stride, nw // stride
        n = fh * fw * NUM_ANCHORS
        sc = next((a.reshape(-1) for a in groups[1] if a.shape[0] == n), None)
        bb = next((a.reshape(-1, 4) for a in groups[4] if a.shape[0] == n), None)
        kp = next((a.reshape(-1, 10) for a in groups[10] if a.shape[0] == n), None)
        if sc is None or bb is None or kp is None:
            print(f"[scrfd] stride={stride} 未匹配到输出 (n={n})，跳过")
            continue
        bb = bb * stride
        kp = kp * stride
        anchors = _scrfd_anchors(fh, fw, stride)
        keep = np.where(sc >= score_thr)[0]
        if keep.size == 0:
            continue
        a = anchors[keep]
        scores_all.append(sc[keep])
        boxes_all.append(np.stack(
            [a[:, 0] - bb[keep, 0], a[:, 1] - bb[keep, 1],
             a[:, 0] + bb[keep, 2], a[:, 1] + bb[keep, 3]], axis=1))
        k = kp[keep].reshape(-1, 5, 2)
        kps_all.append(np.stack([k[:, :, 0] + a[:, 0:1], k[:, :, 1] + a[:, 1:2]],
                                axis=-1).reshape(-1, 10))

    if not boxes_all:
        return []

    scores = np.concatenate(scores_all)
    boxes = np.concatenate(boxes_all)
    kps = np.concatenate(kps_all)

    keep = _nms(boxes, scores, nms_thr)
    inv = 1.0 / scale
    faces = []
    for i in keep:
        b = boxes[i] * inv
        k = kps[i].reshape(5, 2) * inv
        faces.append({
            "bbox": [float(max(0, b[0])), float(max(0, b[1])),
                     float(min(w0, b[2])), float(min(h0, b[3]))],
            "kps": k.tolist(),
            "score": float(scores[i]),
        })
    faces.sort(key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]), reverse=True)
    return faces


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> List[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou <= thr]
    return keep


# --------------------------------------------------------------------------
# 2) 人脸修复：GPEN-BFR
# --------------------------------------------------------------------------
def _similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """求把 src 点映射到 dst 点的相似变换矩阵（2x3）。"""
    src_mean = src.mean(axis=0, keepdims=True)
    dst_mean = dst.mean(axis=0, keepdims=True)
    s0, d0 = src - src_mean, dst - dst_mean
    norm_s = math.sqrt((s0 ** 2).sum())
    norm_d = math.sqrt((d0 ** 2).sum())
    if norm_s < 1e-6 or norm_d < 1e-6:
        return np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
    a = d0.T @ s0 / norm_s
    u, _, vt = np.linalg.svd(a)
    r = u @ vt
    scale = norm_d / norm_s
    m = np.zeros((2, 3), dtype=np.float32)
    m[:2, :2] = r * scale
    m[:, 2] = (dst_mean - m[:2, :2] @ src_mean.squeeze()).flatten()
    return m


def restore_faces(bgr: np.ndarray, faces: List[Dict[str, Any]], size: int = 256,
                  blend: float = 0.85) -> Tuple[np.ndarray, int]:
    """对每张脸做对齐 → GPEN 修复 → 羽化贴回。返回 (图, 成功修复的人脸数)。"""
    if not faces:
        return bgr, 0
    sess = _session("gpen_bfr_256.onnx")
    dst_pts = FACE_TEMPLATE * size
    out = bgr.copy()
    done = 0
    for f in faces:
        src_pts = np.array(f["kps"], dtype=np.float32)
        try:
            m = _similarity_transform(src_pts, dst_pts)
            face = cv2.warpAffine(bgr, m, (size, size), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
            blob = _nchw(cv2.cvtColor(face, cv2.COLOR_BGR2RGB)).astype(np.float32)
            blob = (blob - 127.5) / 127.5  # -> [-1, 1]
            res = sess.run(None, {_input_name(sess): blob})[0]
            res = np.asarray(res).reshape(3, size, size).transpose(1, 2, 0)
            res = np.clip((res + 1.0) * 127.5, 0, 255).astype(np.uint8)
            res = cv2.cvtColor(res, cv2.COLOR_RGB2BGR)

            inv = cv2.invertAffineTransform(m)
            warped = cv2.warpAffine(res, inv, (out.shape[1], out.shape[0]), flags=cv2.INTER_LINEAR)
            mask = np.zeros((size, size), np.uint8)
            cv2.ellipse(mask, (size // 2, int(size * 0.52)),
                        (int(size * 0.40), int(size * 0.48)), 0, 0, 360, 255, -1)
            mask = cv2.GaussianBlur(mask, (0, 0), size * 0.06).astype(np.float32) / 255.0
            mask = cv2.warpAffine(mask, inv, (out.shape[1], out.shape[0]))
            mask = (mask * blend)[..., None]
            out = (out.astype(np.float32) * (1 - mask) + warped.astype(np.float32) * mask)
            out = np.clip(out, 0, 255).astype(np.uint8)
            done += 1
        except Exception as exc:  # 单张脸失败不影响整体
            print(f"[restore] 人脸修复失败: {type(exc).__name__} {exc}")
    return out, done


# --------------------------------------------------------------------------
# 3) 超分：SPAN x4 / Real-ESRGAN x4（分块，控制内存）
# --------------------------------------------------------------------------
def _run_sr_tile(bgr: np.ndarray, model: str) -> np.ndarray:
    sess = _session(model)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = _nchw(rgb)
    res = sess.run(None, {_input_name(sess): blob})[0]
    res = np.asarray(res)[0].transpose(1, 2, 0)
    res = np.clip(res * 255.0, 0, 255).astype(np.uint8)
    return cv2.cvtColor(res, cv2.COLOR_RGB2BGR)


def upscale(bgr: np.ndarray, scale: int = 4, which: str = "span",
            tile: int = 448, overlap: int = 24) -> np.ndarray:
    """
    分块超分 + 重叠羽化拼接。

    注意：羽化掩码必须建在「输出分辨率」上（边长 = tile*scale），
    否则与放大后的分块尺寸对不上（2026-09-22 踩过）。
    """
    model = "span_kendata_x4.onnx" if which == "span" else "real_esrgan_x4.onnx"
    h, w = bgr.shape[:2]
    if max(h, w) <= tile:
        return _run_sr_tile(bgr, model)

    step = max(1, tile - overlap)
    ys = list(range(0, max(1, h - overlap), step))
    xs = list(range(0, max(1, w - overlap), step))
    oh, ow = h * scale, w * scale
    canvas = np.zeros((oh, ow, 3), np.float32)
    weight = np.zeros((oh, ow, 1), np.float32)

    # 输出分辨率下的羽化斜坡
    T = tile * scale
    ramp = np.ones((T, T), np.float32)
    r = min(overlap * scale, T // 2 - 1)
    if r >= 1:
        lin = np.linspace(0.0, 1.0, r, dtype=np.float32)
        ramp[:r, :] *= lin[:, None]
        ramp[-r:, :] *= lin[::-1, None]
        ramp[:, :r] *= lin[None, :]
        ramp[:, -r:] *= lin[::-1][None, :]

    for y in ys:
        for x in xs:
            y2, x2 = min(y + tile, h), min(x + tile, w)
            patch = bgr[y:y2, x:x2]
            if patch.shape[0] < 8 or patch.shape[1] < 8:
                continue
            up = _run_sr_tile(patch, model).astype(np.float32)
            uh, uw = up.shape[:2]
            m = ramp[:uh, :uw][..., None]
            canvas[y * scale:y * scale + uh, x * scale:x * scale + uw] += up * m
            weight[y * scale:y * scale + uh, x * scale:x * scale + uw] += m

    weight[weight == 0] = 1.0
    return np.clip(canvas / weight, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# 4) 传统画质增强
# --------------------------------------------------------------------------
def auto_enhance(bgr: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """去泛黄（灰世界白平衡）+ 自动色阶（救褪色）+ CLAHE 局部对比 + 轻度锐化。"""
    img = bgr.astype(np.float32)

    # 4.1 灰世界白平衡，修正老照片偏黄/偏红
    means = img.reshape(-1, 3).mean(axis=0)
    gray = means.mean()
    gain = gray / np.maximum(means, 1e-3)
    gain = 1.0 + (gain - 1.0) * 0.85 * strength
    img = np.clip(img * gain[None, None, :], 0, 255)

    # 4.2 自动色阶：按 0.4% / 99.6% 分位拉伸，解决褪色发灰
    out = np.empty_like(img)
    for c in range(3):
        ch = img[:, :, c]
        lo, hi = np.percentile(ch, 0.4), np.percentile(ch, 99.6)
        if hi - lo < 8:
            out[:, :, c] = ch
        else:
            out[:, :, c] = np.clip((ch - lo) * 255.0 / (hi - lo), 0, 255)

    # 4.3 CLAHE 提局部层次（在 LAB 的 L 通道上做，不动颜色）
    lab = cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.6 * strength, tileGridSize=(8, 8))
    lab = cv2.merge([clahe.apply(l), a, b])
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR).astype(np.float32)

    # 4.4 轻度非锐化掩蔽
    blur = cv2.GaussianBlur(out, (0, 0), 1.1)
    out = cv2.addWeighted(out, 1.0 + 0.35 * strength, blur, -0.35 * strength, 0)
    return np.clip(out, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def load_image(path: str, max_side: int, auto_orient: bool = True) -> np.ndarray:
    pil = Image.open(path)
    if auto_orient:
        pil = ImageOps.exif_transpose(pil)
    pil = pil.convert("RGB")
    if max(pil.size) > max_side:
        ratio = max_side / max(pil.size)
        pil = pil.resize((max(8, int(pil.width * ratio)), max(8, int(pil.height * ratio))),
                         Image.LANCZOS)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def restore(path: str, mode: str = "standard", out_path: Optional[str] = None,
            progress=None) -> Dict[str, Any]:
    """完整修复流程，返回 {ok, out_path, steps, faces, elapsed}。"""
    cfg = MODES.get(mode, MODES["standard"])
    t0 = time.time()
    steps: List[Dict[str, Any]] = []

    def mark(name: str, t_start: float) -> None:
        dt = round(time.time() - t_start, 2)
        steps.append({"name": name, "seconds": dt})
        if progress:
            progress(name, dt)

    # 1) 读取与尺寸约束（输出=4x输入，压到 OUT_CAP 内，避免产出巨型文件）
    t = time.time()
    bgr = load_image(path, min(cfg["max_side"], OUT_CAP // 4))
    mark("读取与预处理", t)

    # 2) 人脸检测 + 修复
    face_count = 0
    if cfg["face"]:
        t = time.time()
        faces = detect_faces(bgr)
        mark(f"人脸检测({len(faces)}张)", t)
        if faces:
            t = time.time()
            bgr, face_count = restore_faces(bgr, faces)
            mark(f"人脸修复({face_count}张)", t)

    # 3) 超分降噪（Real-ESRGAN 只对小图启用，大图降级 SPAN，保证响应时间）
    sr_model = cfg["sr"]
    if sr_model == "real_esrgan" and max(bgr.shape[:2]) > 480:
        sr_model = cfg.get("sr_fallback", "span")
    t = time.time()
    bgr = upscale(bgr, 4, sr_model, cfg["tile"])
    mark(f"超分降噪x4({'SPAN' if sr_model == 'span' else 'Real-ESRGAN'})", t)

    # 4) 画质增强
    t = time.time()
    bgr = auto_enhance(bgr, cfg.get("enhance", 1.0))
    mark("画质增强", t)

    # 5) 输出
    t = time.time()
    out_path = out_path or os.path.splitext(path)[0] + f"_restored_{mode}.jpg"
    cv2.imwrite(out_path, bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    mark("输出保存", t)

    h, w = bgr.shape[:2]
    return {
        "ok": True, "mode": mode, "out_path": out_path,
        "width": w, "height": h, "faces": face_count,
        "steps": steps, "elapsed": round(time.time() - t0, 2),
    }


# CLI：跑自检 / 单张处理
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--mode", default="standard", choices=list(MODES))
    ap.add_argument("--signature", action="store_true", help="打印模型签名")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.signature:
        import json
        print(json.dumps(model_signature(), ensure_ascii=False, indent=2))
    elif a.input:
        r = restore(a.input, a.mode, a.output)
        print(json.dumps(r, ensure_ascii=False, indent=2))
