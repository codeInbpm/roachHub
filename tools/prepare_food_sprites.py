"""把 ImageGen 生成的 2x2 素材图切成独立食物贴图。

为什么离线做：运行时每帧给每块食物画几十条 QPainterPath 太贵，也画不出照片
质感。这里一次性把 1024x1024 的素材图按象限切成 4 张 256x256 的透明 PNG，
运行时只需要 ``drawPixmap`` 贴图。

抠图不能用"亮度阈值"一把梭（第一版就栽在这）：素材是纯白背景 + 柔和落影，
按亮度一刀切会把落影留成**实心灰块**、把白底过渡带留成**一圈白雾**，贴到
深色桌面上就是一圈"贴纸白边"。这里分三步：

1. **连通域**：从四边泛洪，只吃掉与边界连通的近白像素（物体内部的浅色——
   蛆的米白身体、奶酪的白心——跟边界不连通，所以不会被挖出洞）
2. **距离阶梯**：多源 BFS 算出每个像素离背景多远，第 1 圈直接透明、第 2/3 圈
   给半透明，软化白边
3. **落影柔化**：低饱和又比较亮的像素（灰影）压成半透明深灰，这样它在深色
   桌面上是"投影"而不是"灰块"

不用 PIL/numpy（项目里没装），全部走 QImage 的像素缓冲 + PySide6。

用法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/prepare_food_sprites.py
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "raw"
OUT = ROOT / "assets" / "sprites" / "food"

# 素材图 -> 四个象限对应的食物 key（左上、右上、左下、右下）
SHEETS: dict[str, tuple[str, str, str, str]] = {
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-30-20.png": (
        "caterpillar", "maggots", "deadfly", "woodlouse"),
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-30-56.png": (
        "roachnymph", "droppings", "moldbread", "carrion"),
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-31-08.png": (
        "deadmoth", "deadspider", "rottentomato", "rottentomato2"),
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-31-24.png": (
        "stalerice", "slime", "applecore", "moldpatch"),
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-32-09.png": (
        "moldcheese", "bananapeel", "moldpeanut", "moldbiscuit"),
    # 这一张放在最后：左上是重做过的蛆团（旧的白色版看不清），会覆盖上面的同名文件
    "A_2x2_grid_layout_on_a_pure_wh_2026-11-16T07-41-28.png": (
        "maggots", "flea", "millipede", "slug"),
}

OUT_SIZE = 256           # 输出边长：够 1x~2x 屏（实际显示 40~80 逻辑像素）
WORK_SIZE = 320          # 中间处理尺寸，兼顾速度与边缘质量
BG_WHITE = 246           # 泛洪种子：min(r,g,b) >= 此值才可能是背景
SHADOW_LUM = 190         # 落影判定：亮度高于此值
SHADOW_SAT = 18          # 且通道极差小于此值（= 灰，不是彩色物体）
SHADOW_ALPHA = 38        # 落影压到这么透明
SHADOW_DARKEN = 0.42     # 落影颜色压暗系数
# 离背景的圈数 -> alpha。前两圈直接砍掉：素材边缘有 2~3px 的"白混边"，
# 留着它不论怎么调 alpha，贴到深色桌面上都是一圈灰白光晕（贴纸感）。
RING_ALPHA = (0, 0, 0, 36)
RING_DARKEN = 0.55       # 剩下的那圈也压暗，让过渡落到"暗边"而不是"白边"
INSIDE_ALPHA = 255
# 只有 alpha 高于此值的像素参与 bbox，免得落影把取景框撑大、物体变小
BBOX_ALPHA = 96

# 腐败染色的三档：偏褐 → 偏黑绿 → 几乎焦黑。alpha 越高压得越死。
# 三档染色上各自烘多少片菌斑。
# 别贪多：贴图在桌面上只显示 40~70 像素，18 片缩下去会糊成一片白斑，
# 远看像"发霉的星形"，而不是霉点。实测 9 片是上限。
MOLD_SPOTS = (2, 5, 9)

TINT_TIERS = (
    QColor(52, 40, 22, 48),
    QColor(42, 36, 18, 88),
    QColor(32, 30, 16, 124),
)


def _largest_component_box(buf, w: int, h: int, thresh: int) -> tuple[int, int, int, int] | None:
    """在已写好的 alpha 通道上找面积最大的连通块，返回它的 bbox。

    为什么要这一步：食物的"取景框"如果按所有不透明像素算，周围几粒碎屑就能
    把框撑大一倍，主体在 256 画布里只占半屏，缩到桌面上又小又淡。
    """
    n = w * h
    seen = bytearray(n)
    best = None
    best_size = 0
    for start in range(n):
        if seen[start] or buf[start * 4 + 3] < thresh:
            continue
        stack = [start]
        seen[start] = 1
        size = 0
        minx = maxx = start % w
        miny = maxy = start // w
        while stack:
            i = stack.pop()
            size += 1
            x = i % w
            y = i // w
            if x < minx:
                minx = x
            elif x > maxx:
                maxx = x
            if y < miny:
                miny = y
            elif y > maxy:
                maxy = y
            for nb in ((i - 1) if x > 0 else -1,
                       (i + 1) if x < w - 1 else -1,
                       (i - w) if y > 0 else -1,
                       (i + w) if y < h - 1 else -1):
                if nb >= 0 and not seen[nb] and buf[nb * 4 + 3] >= thresh:
                    seen[nb] = 1
                    stack.append(nb)
        if size > best_size:
            best_size = size
            best = (minx, miny, maxx, maxy)
    return best


def _cut_quadrant(src: QImage) -> QImage:
    """单个象限 -> 去白底 + 软边 + 柔化落影 -> 裁内容 -> 居中贴进 OUT_SIZE 画布。"""
    work = src.scaled(WORK_SIZE, WORK_SIZE, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    work = work.convertToFormat(QImage.Format.Format_ARGB32)

    buf = work.bits()          # memoryview，ARGB32 在小端机器上是 BGRA 字节序
    w, h = work.width(), work.height()
    n = w * h

    lum = bytearray(n)
    sat = bytearray(n)
    for i in range(n):
        j = i * 4
        b, g, r = buf[j], buf[j + 1], buf[j + 2]
        lo = r if r < g else g
        if b < lo:
            lo = b
        hi = r if r > g else g
        if b > hi:
            hi = b
        lum[i] = lo
        sat[i] = hi - lo

    # --- 1. 连通域：只吃掉与边界连通的近白像素 --------------------------------
    is_bg = bytearray(n)
    dq: deque[int] = deque()
    for x in range(w):
        dq.append(x)
        dq.append((h - 1) * w + x)
    for y in range(h):
        dq.append(y * w)
        dq.append(y * w + w - 1)

    while dq:
        i = dq.popleft()
        if is_bg[i] or lum[i] < BG_WHITE:
            continue
        is_bg[i] = 1
        x = i % w
        y = i // w
        for nxt in ((i - 1) if x > 0 else -1,
                    (i + 1) if x < w - 1 else -1,
                    (i - w) if y > 0 else -1,
                    (i + w) if y < h - 1 else -1):
            # 入队前就筛一遍，否则纯白区域会被反复压栈，慢十倍
            if nxt >= 0 and not is_bg[nxt] and lum[nxt] >= BG_WHITE:
                dq.append(nxt)

    # --- 2. 多源 BFS：算每个像素离背景几圈（只关心前 3 圈）--------------------
    ring = bytearray(n)        # 0 = 背景，1~3 = 距离，4 = 更里面
    dq.clear()
    for i in range(n):
        if is_bg[i]:
            dq.append(i)
    while dq:
        i = dq.popleft()
        d = ring[i]
        if d >= 3:
            continue
        nd = d + 1
        x = i % w
        y = i // w
        for nxt in ((i - 1) if x > 0 else -1,
                    (i + 1) if x < w - 1 else -1,
                    (i - w) if y > 0 else -1,
                    (i + w) if y < h - 1 else -1):
            if nxt < 0 or is_bg[nxt] or ring[nxt]:
                continue
            ring[nxt] = nd
            dq.append(nxt)

    # --- 3. 写 alpha，顺手柔化落影 -------------------------------------------
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for i in range(n):
        j = i * 4
        if is_bg[i]:
            buf[j + 3] = 0
            continue

        r = ring[i]
        if r == 0:
            # 非背景、又没被 BFS 标记 = 第 4 圈以内的实体内部
            a = INSIDE_ALPHA
            if lum[i] > SHADOW_LUM and sat[i] < SHADOW_SAT:
                a = SHADOW_ALPHA
                buf[j] = int(buf[j] * SHADOW_DARKEN)
                buf[j + 1] = int(buf[j + 1] * SHADOW_DARKEN)
                buf[j + 2] = int(buf[j + 2] * SHADOW_DARKEN)
        else:
            a = RING_ALPHA[r]
            buf[j] = int(buf[j] * RING_DARKEN)
            buf[j + 1] = int(buf[j + 1] * RING_DARKEN)
            buf[j + 2] = int(buf[j + 2] * RING_DARKEN)
        if a == 0:
            buf[j + 3] = 0
            continue
        buf[j + 3] = a
        if a >= BBOX_ALPHA:
            x = i % w
            y = i // w
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y

    if max_x < min_x or max_y < min_y:
        return QImage()

    # --- 4. 取景框只认"最大连通块 ---------------------------------------------
    # 之前用"所有不透明像素的边界"当取景框，结果周围散落的小碎屑把框撑得很大，
    # 主体在 256 画布里只占一半，缩到桌面上就变得又小又淡（马陆、蛞蝓最明显）。
    # 这里改成只找面积最大的那一块，再外扩 18% 把邻近碎屑带回来。
    main_box = _largest_component_box(buf, w, h, BBOX_ALPHA)
    if main_box is not None:
        bx0, by0, bx1, by1 = main_box
        # 外扩只留 6%：留太多会把主体挤小（18% 时主体只占画布 67%，
        # 等于白放大一圈），留太少又切掉周围该有的碎屑。
        pad_x = int((bx1 - bx0 + 1) * 0.06)
        pad_y = int((by1 - by0 + 1) * 0.06)
        min_x = max(0, bx0 - pad_x)
        max_x = min(w - 1, bx1 + pad_x)
        min_y = max(0, by0 - pad_y)
        max_y = min(h - 1, by1 + pad_y)

    box_w = max_x - min_x + 1
    box_h = max_y - min_y + 1
    cut = work.copy(min_x, min_y, box_w, box_h)

    # 等比缩放并居中，四周留 4% 余量（贴图不自带硬阴影，运行时自己画投影）
    inner = int(OUT_SIZE * 0.96)
    scale = min(inner / box_w, inner / box_h)
    tw = max(1, int(box_w * scale))
    th = max(1, int(box_h * scale))

    out = QImage(OUT_SIZE, OUT_SIZE, QImage.Format.Format_ARGB32)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage((OUT_SIZE - tw) // 2, (OUT_SIZE - th) // 2,
                cut.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))
    p.end()
    return out


def _tint(src: QImage, color: QColor) -> QImage:
    """按 alpha 轮廓给贴图整体压暗上色（SourceAtop 只作用于不透明像素）。

    为什么要在离线做：运行时想"让食物变脏"最直觉的做法是在下面垫一个暗色
    椭圆，但椭圆形状和食物形状无关，在浅色桌面上立刻变成"罩着玻璃球的食物"。
    唯一干净的做法是沿着贴图轮廓染色，而那需要 QPainter 的 SourceAtop 合成
    模式——在覆盖层窗口上用它不安全（会吃到窗口已有内容），所以放在这里离线
    烘成几档，运行时 `一档一张`、按需加载。
    """
    out = src.copy()
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    p.fillRect(out.rect(), color)
    p.end()
    return out


def _bake_mold(src: QImage, spots: int, seed: int) -> QImage:
    """在贴图**不透明区域内部**烘一层菌斑。

    为什么必须离线：菌斑是静态的（位置定死、不随帧变化），运行时每帧画
    十几个三连椭圆纯属浪费 —— 实测 12 块全烂的食物要 4.0 ms/帧，占掉 60fps
    预算的四分之一。烘进贴图后运行时只剩"一次 drawPixmap"。

    用 SourceAtop 让斑点只落在不透明像素上（不会糊到透明区变成一圈绿边）。
    """
    out = src.copy()
    rnd = random.Random(seed)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    p.setPen(Qt.PenStyle.NoPen)

    w = out.width()
    for _ in range(spots):
        cx = rnd.uniform(w * 0.16, w * 0.84)
        cy = rnd.uniform(w * 0.16, w * 0.84)
        s = rnd.uniform(w * 0.028, w * 0.062)
        p.setBrush(QBrush(QColor(112, 138, 118, rnd.randint(120, 175))))
        for ox, oy, sc in ((0.0, 0.0, 1.0), (1.5, 0.7, 0.72), (-1.1, 1.2, 0.6)):
            p.drawEllipse(QRectF(cx + ox * s - s * sc, cy + oy * s - s * sc * 0.9,
                                 s * 2.0 * sc, s * 1.8 * sc))
        # 菌丛中心偏白的绒毛
        p.setBrush(QBrush(QColor(230, 238, 222, rnd.randint(46, 88))))
        p.drawEllipse(QRectF(cx - s * 0.42, cy - s * 0.38, s * 0.84, s * 0.76))
    p.end()
    return out


def main() -> None:
    QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    saved = 0
    total = 0
    for name, keys in SHEETS.items():
        path = RAW / name
        if not path.exists():
            print(f"! 缺素材：{name}")
            continue
        img = QImage(str(path))
        if img.isNull():
            print(f"! 读不出来：{name}")
            continue
        half_w = img.width() // 2
        half_h = img.height() // 2
        quads = (
            img.copy(0, 0, half_w, half_h),
            img.copy(half_w, 0, half_w, half_h),
            img.copy(0, half_h, half_w, half_h),
            img.copy(half_w, half_h, half_w, half_h),
        )
        for key, quad in zip(keys, quads):
            tile = _cut_quadrant(quad)
            if tile.isNull():
                print(f"! {key} 抠完是空的")
                continue
            dst = OUT / f"{key}.png"
            tile.save(str(dst), "PNG")
            kb = dst.stat().st_size / 1024
            total += kb
            line = f"✓ {key:14s} 原 {kb:5.1f}K"
            # 三档腐败染色：越往后越黑、越偏绿褐
            for tier, color in enumerate(TINT_TIERS, start=1):
                p = OUT / f"{key}_d{tier}.png"
                baked = _tint(tile, color)
                baked = _bake_mold(baked, MOLD_SPOTS[tier - 1], seed=hash(key) % 9973 + tier)
                baked.save(str(p), "PNG")
                kb2 = p.stat().st_size / 1024
                total += kb2
                line += f"  d{tier} {kb2:5.1f}K"
            print(line)
            saved += 1
    print(f"\n共 {saved} 组 / {total / 1024:.2f} MB，目录 {OUT}")


if __name__ == "__main__":
    main()
