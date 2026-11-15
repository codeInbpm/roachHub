"""把写实精灵图拆分成「纯躯干」贴图，供程序化肢体动画使用。

原始精灵图把六条腿与两根触须烘焙进了一张静态照片，因此整只虫只能整体旋转，
肢体完全静止。本工具按「通用蟑螂躯干轮廓」裁出躯干贴图：

  1. 若贴图不是标准俯视朝向，先旋转到「头朝上、腹朝下」；
  2. 用参数化躯干轮廓（头 / 前胸背板 / 翅 / 腹部 的宽度分布）生成蒙版，
     轮廓边缘正好落在六足基节附近，附肢被切掉；
  3. 蒙版再与「暖色色度」蒙版相交，去掉原始抠图残留的白色/灰色背景补丁；
  4. 裁边输出 *_body.png，并写出尺寸信息 *_body.json。

纯 Qt 实现，不引入 numpy / PIL。运行：
    QT_QPA_PLATFORM=offscreen python tools/build_body_sprites.py
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPolygonF,
    QTransform,
)

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "assets" / "sprites"

ALPHA_FLOOR = 96
SAT_THRESHOLD = 0.25  # 默认 HSV 饱和度阈值：虫体高饱和，背景与灰影接近中性

# 躯干纵向宽度分布：(沿体长的位置 0=头 1=尾, 半宽占「最宽处半宽」的比例)
BODY_PROFILE = [
    (0.00, 0.50),   # 头顶
    (0.04, 0.78),
    (0.09, 0.90),
    (0.13, 0.97),   # 前胸背板最宽处
    (0.20, 0.93),
    (0.26, 0.84),   # 前足基节带（收窄以切掉足基残留）
    (0.34, 0.82),
    (0.44, 0.83),   # 中足基节带
    (0.56, 0.82),
    (0.66, 0.81),   # 后足基节带
    (0.78, 0.76),
    (0.88, 0.62),
    (1.00, 0.16),   # 腹末
]

# rotate: 逆时针角度，把贴图转成头朝上的俯视姿态
# box:    (cx, cy, half_w, half_len) —— 躯干在（旋转后）图中的归一化位置
SPECS = {
    "cockroach_adult": {"rotate": 0.0, "box": (0.500, 0.535, 0.192, 0.335), "sat": 0.25},
    "cockroach_nymph": {"rotate": 103.0, "box": (0.484, 0.585, 0.168, 0.265), "sat": 0.24},   # None = 由不透明包围盒自动推断
    "cockroach_white": {"rotate": 117.0, "box": (0.561, 0.594, 0.108, 0.256), "sat": 0.09},
}


def rotate_image(img: QImage, deg: float) -> QImage:
    if abs(deg) < 1e-3:
        return img
    t = QTransform().rotate(deg)
    bounds = t.mapRect(QRectF(img.rect()))
    out = QImage(int(bounds.width() + 0.5), int(bounds.height() + 0.5), QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.translate(-bounds.left(), -bounds.top())
    p.setTransform(t, True)
    p.drawImage(0, 0, img)
    p.end()
    return out


def opaque_bbox(img: QImage) -> QRectF:
    x0, y0, x1, y1 = img.width(), img.height(), -1, -1
    for y in range(img.height()):
        line = img.constScanLine(y)
        for x in range(img.width()):
            if line[x * 4 + 3] >= ALPHA_FLOOR:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < 0:
        return QRectF(0, 0, img.width(), img.height())
    return QRectF(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def _chroma_bits(img: QImage, sat_th: float = SAT_THRESHOLD) -> list[int]:
    """虫体（有彩度/很暗）像素掩码。"""
    w, h = img.width(), img.height()
    bits = [0] * (w * h)
    for y in range(h):
        line = img.constScanLine(y)
        base = y * w
        for x in range(w):
            if line[x * 4 + 3] < ALPHA_FLOOR:
                continue
            b, g, r = line[x * 4], line[x * 4 + 1], line[x * 4 + 2]
            mx = max(r, g, b)
            sat = (mx - min(r, g, b)) / mx if mx else 0.0
            if sat >= sat_th or mx < 90:
                bits[base + x] = 1
    return bits


def _erode(bits: list[int], w: int, h: int, k: int) -> list[int]:
    """方形核腐蚀（积分图加速）：去掉细长附肢，只留躯干核心。"""
    r = k // 2
    n = w + 1
    sat = [0] * ((w + 1) * (h + 1))
    for y in range(h):
        run = 0
        for x in range(w):
            run += bits[y * w + x]
            sat[(y + 1) * n + (x + 1)] = sat[y * n + (x + 1)] + run
    out = [0] * (w * h)
    for y in range(max(0, r), h - r):
        for x in range(max(0, r), w - r):
            x0, y0, x1, y1 = x - r, y - r, x + r + 1, y + r + 1
            total = sat[y1 * n + x1] - sat[y0 * n + x1] - sat[y1 * n + x0] + sat[y0 * n + x0]
            if total == k * k:
                out[y * w + x] = 1
    return out


def core_bbox(img: QImage, kernel: int = 15, sat_th: float = SAT_THRESHOLD) -> QRectF:
    """用腐蚀后的躯干核心估计躯干包围盒（不受长触须/腿影响）。"""
    w, h = img.width(), img.height()
    core = _erode(_chroma_bits(img, sat_th), w, h, kernel)
    xs = [i % w for i, v in enumerate(core) if v]
    ys = [i // w for i, v in enumerate(core) if v]
    if not xs:
        return opaque_bbox(img)
    return QRectF(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def trim_thin_spurs(out: QImage, min_run: int = 19) -> int:
    """削掉躯干侧缘上孤立的足基残茬。

    残留的足基在轮廓上表现为「横向戳出、纵向很薄」的短须：逐列看像素的纵向
    连续长度，凡是明显短于躯干厚度的碎段一律清掉（躯干本身每列都有几十到上百
    像素的连续长度，不会被误伤）。
    """
    w, h = out.width(), out.height()
    removed = 0
    for x in range(w):
        y = 0
        while y < h:
            # 找一段连续的实体像素
            if out.pixelColor(x, y).alpha() <= 12:
                y += 1
                continue
            y0 = y
            while y < h and out.pixelColor(x, y).alpha() > 12:
                y += 1
            if (y - y0) < min_run:
                for yy in range(y0, y):
                    out.setPixelColor(x, yy, QColor(0, 0, 0, 0))
                removed += y - y0
    return removed

def body_polygon(w: int, h: int, box) -> QPolygonF:
    cx, cy, hw, hl = box
    pts: list[QPointF] = []
    for t, frac in BODY_PROFILE:
        pts.append(QPointF((cx - frac * hw) * w, (cy + (t - 0.5) * 2.0 * hl) * h))
    for t, frac in reversed(BODY_PROFILE):
        pts.append(QPointF((cx + frac * hw) * w, (cy + (t - 0.5) * 2.0 * hl) * h))
    return QPolygonF(pts)


def build(name: str, spec: dict, preview: bool = True) -> dict:
    src_path = SPRITES / f"{name}.png"
    img = QImage(str(src_path)).convertToFormat(QImage.Format.Format_ARGB32)
    if img.isNull():
        raise RuntimeError(f"无法读取贴图: {src_path}")

    rotate = float(spec.get("rotate", 0.0))
    sat_th = float(spec.get("sat", SAT_THRESHOLD))
    img = rotate_image(img, rotate)
    w, h = img.width(), img.height()

    box = spec.get("box")
    if box is None:
        bb = core_bbox(img, sat_th=sat_th)
        box = (
            (bb.x() + bb.width() / 2.0) / w,
            (bb.y() + bb.height() / 2.0) / h,
            (bb.width() * 0.56) / w,
            (bb.height() * 0.52) / h,
        )

    poly = body_polygon(w, h, box)
    path = QPainterPath()
    path.addPolygon(poly)
    path.closeSubpath()

    mask = QImage(w, h, QImage.Format.Format_ARGB32)
    mask.fill(Qt.GlobalColor.transparent)
    mp = QPainter(mask)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    mp.setPen(Qt.PenStyle.NoPen)
    mp.setBrush(QColor(255, 255, 255, 255))
    mp.drawPath(path)
    mp.end()

    # 背景洪泛：从画布四边出发，穿过「轮廓之外」或「近白高光背景」的区域。
    # 躯干内部的高光（前胸背板/翅面上的强反光）被四周的虫体像素围住，洪泛到不了，
    # 因此会被保留 —— 这样既清掉了腿间的白色补丁，又不会在背板上挖出空洞。
    inside = bytearray(w * h)
    for y in range(h):
        mask_line = mask.constScanLine(y)
        for x in range(w):
            if mask_line[x * 4 + 3] >= 40:
                inside[y * w + x] = 1

    def whitish(px: int, py: int) -> bool:
        line = img.constScanLine(py)
        if line[px * 4 + 3] <= 12:
            return True
        b, g, r = line[px * 4], line[px * 4 + 1], line[px * 4 + 2]
        mx = max(r, g, b)
        sat = (mx - min(r, g, b)) / mx if mx else 0.0
        return sat < 0.16 and mx > 150

    reached = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            q.append((x, y))
    while q:
        x, y = q.popleft()
        if not (0 <= x < w and 0 <= y < h):
            continue
        idx = y * w + x
        if reached[idx]:
            continue
        if inside[idx] and not whitish(x, y):
            continue
        reached[idx] = 1
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    kept = 0
    for y in range(h):
        src_line = img.constScanLine(y)
        mask_line = mask.constScanLine(y)
        dst_line = out.scanLine(y)
        for x in range(w):
            idx = y * w + x
            if reached[idx] or not inside[idx]:
                continue
            a = src_line[x * 4 + 3]
            if not a:
                continue
            dst_line[x * 4] = src_line[x * 4]
            dst_line[x * 4 + 1] = src_line[x * 4 + 1]
            dst_line[x * 4 + 2] = src_line[x * 4 + 2]
            dst_line[x * 4 + 3] = a
            kept += 1

    bb = opaque_bbox(out)
    out = out.copy(int(bb.x()), int(bb.y()), int(bb.width()), int(bb.height()))

    dst = SPRITES / f"{name}_body.png"
    out.save(str(dst), "PNG")

    meta = {
        "source": f"{name}.png",
        "rotate_deg": rotate,
        "box": [round(v, 4) for v in box],
        "width": out.width(),
        "height": out.height(),
        "aspect": round(out.width() / max(1, out.height()), 4),
        "kept_pixels": kept,
    }
    (SPRITES / f"{name}_body.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{name}: 躯干贴图 {out.width()}x{out.height()} aspect={meta['aspect']} 保留像素={kept}")

    if preview:
        _preview(img, path, out, name)
    return meta


def _preview(full: QImage, path: QPainterPath, body: QImage, name: str) -> None:
    w, h = full.width(), full.height()
    pad = 14
    canvas = QImage(w + pad * 3 + body.width(), max(h, body.height()) + pad * 2, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(236, 236, 240))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.drawImage(pad, pad, full)

    pen = p.pen()
    pen.setColor(QColor(0, 170, 90, 220))
    pen.setWidthF(1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path.translated(pad, pad))

    p.drawImage(w + pad * 2, pad, body)
    p.end()
    canvas.save(str(ROOT / "tools" / f"_preview_{name}.png"))
    print(f"  预览: tools/_preview_{name}.png")


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    names = sys.argv[1:] or list(SPECS.keys())
    for name in names:
        spec = SPECS.get(name)
        if spec is None:
            print(f"跳过未知贴图: {name}")
            continue
        build(name, spec)


if __name__ == "__main__":
    main()
