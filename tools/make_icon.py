"""从 AI 底图生成 roachHub 的一整套图标（PNG 多尺寸 + 真正的多尺寸 ICO）。

底图是 1024×1024、白/浅灰棋盘格背景（生成模型把"透明"画成了像素），所以这里：
  1. 从四周 flood fill 抠掉棋盘格，边缘用"反解 alpha"去白边（不然在深色托盘上是灰毛边）；
  2. 按内容裁掉多余留白（顺带裁掉右下角的水印）；
  3. 合成两种成品：
       * 徽章（badge）：深色圆角底 + 蟑螂，用于窗口/任务栏/快捷方式；
       * 裸虫（bare）：只要虫子，用于系统托盘小尺寸（16~24px 更清楚）；
  4. 导出 16/24/32/48/64/128/256 七个尺寸的 .ico（≤64 用 BMP 条目，128/256 用 PNG）。

用法：
    python tools/make_icon.py                       # 生成到 assets/icons/
    python tools/make_icon.py --master 某张图.png     # 换底图
    python tools/make_icon.py --preview             # 只出对比图 tools/out/icon_preview.png
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import deque
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

DEFAULT_MASTER = "assets/icons/source/roachhub_raw.png"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

# 棋盘格两色都 ≥ 这个亮度，且几乎无彩度 —— 用它区分"背景"与"虫子"
BG_MIN_LUM = 210
BG_MAX_SAT = 16


# ------------------------------------------------------------------ 抠图

def _is_bg(r: int, g: int, b: int) -> bool:
    m = min(r, g, b)
    return m >= BG_MIN_LUM and (max(r, g, b) - m) <= BG_MAX_SAT


def key_background(src: QImage) -> QImage:
    """棋盘格背景 → 透明。边缘做 alpha 反解，去掉白边。"""
    img = src.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    stride = w * 4
    buf = bytearray()
    for y in range(h):
        buf += bytes(img.constScanLine(y))[:stride]

    n = w * h
    bg = bytearray(n)          # 1 = 确定是背景（从四边灌进来的）
    q: deque[int] = deque()

    def try_seed(idx: int) -> None:
        i = idx * 4
        if not bg[idx] and _is_bg(buf[i + 2], buf[i + 1], buf[i]):
            bg[idx] = 1
            q.append(idx)

    for x in range(w):
        try_seed(x)
        try_seed((h - 1) * w + x)
    for y in range(h):
        try_seed(y * w)
        try_seed(y * w + w - 1)

    while q:
        idx = q.popleft()
        x, y = idx % w, idx // w
        if x > 0:
            try_seed(idx - 1)
        if x + 1 < w:
            try_seed(idx + 1)
        if y > 0:
            try_seed(idx - w)
        if y + 1 < h:
            try_seed(idx + w)

    # 背景附近 4px 内的前景像素算"边缘"，只对它们做软 alpha
    fringe = bytearray(n)
    for y in range(h):
        base = y * w
        for x in range(w):
            idx = base + x
            if bg[idx]:
                continue
            hit = False
            for dy in (-4, -3, -2, -1, 0, 1, 2, 3, 4):
                yy = y + dy
                if yy < 0 or yy >= h:
                    continue
                row = yy * w
                for dx in (-4, -3, -2, -1, 0, 1, 2, 3, 4):
                    xx = x + dx
                    if 0 <= xx < w and bg[row + xx]:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                fringe[idx] = 1

    spill = 242.0  # 棋盘格的平均亮度，反解 alpha 用
    for y in range(h):
        base = y * w
        line = y * stride
        for x in range(w):
            idx = base + x
            i = line + x * 4
            if bg[idx]:
                buf[i + 3] = 0
                continue
            if not fringe[idx]:
                buf[i + 3] = 255
                continue
            m = min(buf[i + 2], buf[i + 1], buf[i])
            sat = max(buf[i + 2], buf[i + 1], buf[i]) - m
            if sat < 18 and m > 150:
                # 生成模型给虫子描的那圈"贴纸白边"：又白又灰，直接判背景。
                # 只在这一条边缘带上这么判，所以眼睛里的高光不会被挖掉。
                buf[i + 3] = 0
                continue
            a = (spill - m) / (spill - 40.0)
            a = 0.0 if a < 0.0 else (1.0 if a > 1.0 else a)
            if a < 0.06:
                buf[i + 3] = 0
                continue
            # C = a*F + (1-a)*B  →  F = (C - (1-a)*B) / a，去掉白色污染
            for c in range(3):
                v = (buf[i + c] - (1.0 - a) * spill) / a
                buf[i + c] = 0 if v < 0 else (255 if v > 255 else int(v))
            buf[i + 3] = int(a * 255)

    out = QImage(bytes(buf), w, h, stride, QImage.Format.Format_ARGB32)
    return out.copy()


def content_bbox(img: QImage, thr: int = 128) -> QRectF:
    """内容包围盒：只认"深色或带彩度"的像素，避免把灰色水印算进去。"""
    w, h = img.width(), img.height()
    stride = w * 4
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        line = bytes(img.constScanLine(y))[:stride]
        for x in range(w):
            i = x * 4
            a = line[i + 3]
            if a < thr:
                continue
            r, g, b = line[i + 2], line[i + 1], line[i]
            m = min(r, g, b)
            if (max(r, g, b) - m) < 15 and m > 110:
                continue  # 灰白 → 水印/背景残留
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
    if maxx < 0:
        return QRectF(0, 0, w, h)
    pad = int(max(maxx - minx, maxy - miny) * 0.02)
    return QRectF(
        max(0, minx - pad),
        max(0, miny - pad),
        min(w, maxx + pad + 1) - max(0, minx - pad),
        min(h, maxy + pad + 1) - max(0, miny - pad),
    )


# ------------------------------------------------------------------ 合成

def _rounded_path(size: int, radius_ratio: float = 0.225) -> QPainterPath:
    path = QPainterPath()
    r = size * radius_ratio
    path.addRoundedRect(QRectF(0, 0, size, size), r, r)
    return path


def _silhouette(src: QImage, warm: bool = True) -> QImage:
    """把贴图压成一块单色剪影 —— 16/24px 下纹理只会变成糊，形状才读得出来。"""
    out = QImage(src.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    if warm:
        g = QLinearGradient(0, 0, 0, src.height())
        g.setColorAt(0.0, QColor(245, 205, 160))
        g.setColorAt(0.55, QColor(206, 140, 80))
        g.setColorAt(1.0, QColor(150, 88, 42))
        p.fillRect(out.rect(), g)
    else:
        p.fillRect(out.rect(), QColor(30, 20, 14))
    p.end()
    return out


def render_badge(master: QImage, size: int, bug_scale: float = 0.84) -> QImage:
    """深色圆角徽章 + 居中蟑螂。小尺寸自动换成剪影版。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    path = _rounded_path(size)
    p.setClipPath(path)

    # 底：暖黑渐变
    g = QLinearGradient(0, 0, size * 0.25, size)
    g.setColorAt(0.0, QColor(74, 58, 47))
    g.setColorAt(0.55, QColor(40, 31, 24))
    g.setColorAt(1.0, QColor(22, 16, 13))
    p.fillPath(path, g)

    # 左上高光，别让纯色看着廉价
    rad = QRadialGradient(QPointF(size * 0.28, size * 0.18), size * 0.85)
    rad.setColorAt(0.0, QColor(255, 236, 210, 52))
    rad.setColorAt(0.55, QColor(255, 236, 210, 12))
    rad.setColorAt(1.0, QColor(255, 236, 210, 0))
    p.fillPath(path, rad)

    # 虫体后面的暖光晕（小尺寸下能把轮廓从底里"推"出来）
    glow = QRadialGradient(QPointF(size * 0.5, size * 0.56), size * 0.48)
    glow.setColorAt(0.0, QColor(224, 132, 52, 120))
    glow.setColorAt(0.55, QColor(196, 100, 34, 44))
    glow.setColorAt(1.0, QColor(196, 100, 34, 0))
    p.fillPath(path, glow)

    # 蟑螂
    if size <= 32:
        # 这个尺寸下真实贴图只会糊成一团，换成矢量剪影
        g = int(size * 0.70)
        glyph = _roach_glyph(g)
        p.drawImage(QPointF((size - g) / 2.0, (size - g) / 2.0 + size * 0.02), glyph)
    else:
        target = size * bug_scale
        scaled = master.scaled(
            int(target), int(target),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        p.drawImage(
            QPointF((size - scaled.width()) / 2.0, (size - scaled.height()) / 2.0 + size * 0.01),
            scaled,
        )

    # 细描边，让圆角在浅色任务栏上也有边界
    p.setClipping(False)
    pen = QPen(QColor(236, 156, 78, 84))
    pen.setWidthF(max(1.0, size * 0.012))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_rounded_path(size - max(1.0, size * 0.012)))
    p.end()
    return img


def _roach_glyph(size: int) -> QImage:
    """手绘的托盘小图标（≤32px 用）。

    真实贴图缩到这个尺寸，腿会碎成一片浅灰 —— 而托盘图标只有 16~24px，
    要的是"一眼认得出是个虫子"。所以这里用纯矢量：躯干 + 前胸背板 + 头 + 六足 + 两根触须，
    统一琥珀色，边缘做 AA。
    """
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    u = size / 100.0  # 用 0~100 的比例坐标，缩放时不用改公式
    body = QLinearGradient(0, 16 * u, 0, 88 * u)
    body.setColorAt(0.0, QColor(250, 214, 168))
    body.setColorAt(0.55, QColor(216, 152, 90))
    body.setColorAt(1.0, QColor(156, 94, 44))

    leg_pen = QPen(QColor(198, 134, 76))
    leg_pen.setWidthF(max(1.0, 3.4 * u))
    leg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(leg_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # 六足：左右各三条，稍微外八
    for y, ex, ey in ((42, 18, 34), (52, 14, 50), (64, 20, 76)):
        p.drawLine(QPointF(34 * u, y * u), QPointF(ex * u, ey * u))
        p.drawLine(QPointF(66 * u, y * u), QPointF((100 - ex) * u, ey * u))

    # 触须（顶端留 2% 余量，别被画布切掉）
    ant = QPen(QColor(220, 164, 110))
    ant.setWidthF(max(1.0, 2.8 * u))
    ant.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(ant)
    for sx in (-1, 1):
        path = QPainterPath(QPointF((50 + sx * 4) * u, 15 * u))
        path.quadTo(QPointF((50 + sx * 17) * u, 4 * u), QPointF((50 + sx * 27) * u, 2 * u))
        p.drawPath(path)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body)
    p.drawEllipse(QPointF(50 * u, 57 * u), 17 * u, 27 * u)    # 腹部
    p.drawEllipse(QPointF(50 * u, 31 * u), 16 * u, 10 * u)    # 前胸背板
    p.drawEllipse(QPointF(50 * u, 19 * u), 7.5 * u, 6 * u)    # 头
    p.end()
    return img


def render_bare(master: QImage, size: int) -> QImage:
    """托盘用：≤32px 用手绘剪影（清晰），再大用真实贴图（有质感）。"""
    if size <= 32:
        return _roach_glyph(size)

    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    inner = int(size * 0.98)
    scaled = master.scaled(
        inner, inner,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    p.drawImage(QPointF((size - scaled.width()) / 2.0, (size - scaled.height()) / 2.0), scaled)
    p.end()
    return img


# ------------------------------------------------------------------ ICO

def _bmp_payload(img: QImage) -> bytes:
    """ICO 里的 BMP 条目：BITMAPINFOHEADER + 自下而上的 BGRA + AND 掩码。"""
    w, h = img.width(), img.height()
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    stride = w * 4
    rows = [bytes(img.constScanLine(y))[:stride] for y in range(h)]
    xor = b"".join(reversed(rows))
    mask_row = ((w + 31) // 32) * 4
    and_mask = b"\x00" * (mask_row * h)
    header = struct.pack(
        "<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0
    )
    return header + xor + and_mask


def _png_payload(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(path: Path, images: list[QImage]) -> None:
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for img in images:
        blob = _png_payload(img) if max(img.width(), img.height()) >= 128 else _bmp_payload(img)
        w = 0 if img.width() >= 256 else img.width()
        h = 0 if img.height() >= 256 else img.height()
        entries.append(
            struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset + sum(len(b) for b in blobs))
        )
        blobs.append(blob)
    data = struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs)
    path.write_bytes(data)


# ------------------------------------------------------------------ 预览

def render_preview(master: QImage, out: Path) -> None:
    """按**真实尺寸**渲染 16/24/32/48/64 再放大，直接看小尺寸可读性。"""
    sizes = (16, 24, 32, 48, 64)
    cell = 200
    board = QImage(40 + cell * len(sizes), 740, QImage.Format.Format_ARGB32)
    board.fill(QColor(244, 244, 247))
    p = QPainter(board)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    for row, fn in enumerate((render_badge, render_bare)):
        y0 = 30 + row * 280
        for i, s in enumerate(sizes):
            x0 = 30 + i * cell
            small = fn(master, s)
            z = max(1, 160 // s)
            big = small.scaled(s * z, s * z, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.FastTransformation)
            p.drawImage(x0, y0, big)
            p.setPen(QColor(110, 110, 120))
            p.drawText(x0, y0 + s * z + 18, f"{s}px ×{z}")
    # 大图：浅底和深底各来一份，检查边缘有没有白边
    for bg, x0 in ((QColor(244, 244, 247), 20), (QColor(26, 28, 34), 250)):
        p.fillRect(x0, 590, 210, 130, bg)
        p.drawImage(x0 + 10, 600,
                    render_badge(master, 128).scaled(110, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
        p.drawImage(x0 + 130, 620,
                    render_bare(master, 64).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
    p.end()
    board.save(str(out))


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="生成 roachHub 图标")
    ap.add_argument("--master", default=DEFAULT_MASTER)
    ap.add_argument("--preview", action="store_true", help="只出对比图，不写 assets/")
    args = ap.parse_args()

    QGuiApplication(sys.argv[:1])

    master_path = Path(args.master)
    if not master_path.exists():
        print(f"找不到底图: {master_path}")
        return 1

    print(f"读入底图: {master_path}")
    src = QImage(str(master_path))
    keyed = key_background(src)
    box = content_bbox(keyed)
    master = keyed.copy(box.toRect())
    print(f"抠图完成，内容 {master.width()}x{master.height()}")

    out_dir = Path("tools/out")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.preview:
        render_preview(master, out_dir / "icon_preview.png")
        print("预览: tools/out/icon_preview.png")
        return 0

    icons_dir = Path("assets/icons")
    icons_dir.mkdir(parents=True, exist_ok=True)

    master.save(str(icons_dir / "roachhub_master.png"))
    render_badge(master, 512).save(str(icons_dir / "roachhub_512.png"))
    render_bare(master, 256).save(str(icons_dir / "tray_icon.png"))

    badge_imgs = [render_badge(master, s) for s in ICON_SIZES]
    write_ico(icons_dir / "roachhub.ico", badge_imgs)
    write_ico(icons_dir / "tray_icon.ico", [render_bare(master, s) for s in ICON_SIZES])

    render_preview(master, out_dir / "icon_preview.png")
    print("已生成:")
    for f in sorted(icons_dir.iterdir()):
        print(f"  {f}  {f.stat().st_size / 1024:.1f} KB")
    print("预览: tools/out/icon_preview.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
