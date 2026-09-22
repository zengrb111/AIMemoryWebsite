# -*- coding: utf-8 -*-
"""
老照片修复管线 · 端到端自检
=================================================================
1) 打印各 ONNX 模型的输入/输出签名（核对预处理是否匹配）
2) 对给定图片逐一跑各档位，输出耗时与阶段明细
3) 生成「原图 | 修复后」左右对比图，便于人工确认效果

用法：
    python selftest.py samples/00.jpg [--modes fast,standard,strong]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restore as R  # noqa: E402


def side_by_side(src_path: str, out_path: str, dst_path: str) -> str:
    """原图（按输出高度放大，用最近邻保留原始像素感）与修复结果并排。"""
    src = cv2.imread(src_path)
    dst = cv2.imread(dst_path)
    if src is None or dst is None:
        return ""
    h = dst.shape[0]
    w = max(1, int(round(src.shape[1] * h / src.shape[0])))
    left = cv2.resize(src, (w, h), interpolation=cv2.INTER_NEAREST)
    gap = np.full((h, 14, 3), 250, np.uint8)
    combo = np.hstack([left, gap, dst])
    # 太宽则等比缩小，便于查看
    maxw = 2200
    if combo.shape[1] > maxw:
        s = maxw / combo.shape[1]
        combo = cv2.resize(combo, (maxw, int(combo.shape[0] * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(out_path, combo, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--modes", default="standard")
    ap.add_argument("--signature", action="store_true", default=True)
    a = ap.parse_args()

    if a.signature:
        print("=" * 70)
        print("模型签名")
        print("=" * 70)
        sig = R.model_signature()
        for name, s in sig.items():
            ins = ", ".join(f"{i['name']}{i['shape']}" for i in s["inputs"])
            outs = ", ".join(f"{o['name']}{o['shape']}" for o in s["outputs"])
            print(f"\n[{name}]")
            print(f"  输入: {ins}")
            print(f"  输出({len(s['outputs'])}): {outs[:400]}")
        print()

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    results = []
    for img in a.images:
        for mode in modes:
            print("=" * 70)
            print(f"处理 {img}  模式={mode}")
            print("=" * 70)
            out = os.path.splitext(img)[0] + f".{mode}.jpg"
            try:
                r = R.restore(img, mode, out)
                for st in r["steps"]:
                    print(f"   · {st['name']:<28s} {st['seconds']:>7.2f}s")
                print(f"   => {r['width']}x{r['height']}  人脸 {r['faces']}  总计 {r['elapsed']}s")
                cmp_path = os.path.splitext(img)[0] + f".{mode}.compare.jpg"
                side_by_side(img, cmp_path, out)
                print(f"   => 对比图 {cmp_path}")
                results.append(r)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                print(f"   !! 失败: {type(exc).__name__}: {exc}")
            print()

    print("=" * 70)
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
