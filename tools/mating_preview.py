"""交配动作预览：把两只虫从对接打到抽离的过程画成图。

    python tools/mating_preview.py

输出：
- `tools/out/mating_frames.png`   2.2 秒抓 8 帧（逐帧截图，不是"最后状态画 8 次"）
- `tools/out/mating_closeup.png`  55% 时结合处放大 2.2 倍
- `tools/out/mating_angles.png`   0/35/90/135 四个朝向，验证心形永远朝屏幕上方飘

**不要**加 QT_QPA_PLATFORM=offscreen —— 该平台 `QFontDatabase.families()` 返回 0，
标签会画成方框。

实现要点：每一帧都必须**当场渲染成 QImage**。只存 (progress, female, male) 引用的话，
8 张图全是模拟结束后的同一状态 —— 这正是第一版的 bug：看起来像 8 张静帧，
其实是 1 张画了 8 遍。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import LifeStage   # noqa: E402
from src.entities.cockroach import Cockroach      # noqa: E402

OUT = ROOT / "tools" / "out"
DT = 1.0 / 60.0
DURATION = 2.2
FRAMES = 8
CELL = 300
COLS = 4


def make_pair_at(cx: float, cy: float, axis_deg: float = 90.0, gap: float = 70.0):
    """沿 axis_deg 方向摆一对尾对尾的虫（雌在起点侧，雄在终点侧）。"""
    a = math.radians(axis_deg)
    dx, dy = math.cos(a), math.sin(a)
    female = Cockroach(cx - dx * gap / 2, cy - dy * gap / 2,
                       stage=LifeStage.ADULT, gender="female", config={})
    male = Cockroach(cx + dx * gap / 2, cy + dy * gap / 2,
                     stage=LifeStage.ADULT, gender="male", config={})
    for r, ang in ((female, a + math.pi), (male, a)):
        r.energy = 100.0
        r.angle = ang
        r.target_angle = ang
        r.target_speed = 0.0
        r.current_speed = 0.0
    female.start_mating(male.id, duration=DURATION)
    male.start_mating(female.id, duration=DURATION)
    return female, male


def draw_pair(p: QPainter, female: Cockroach, male: Cockroach,
              cx: float, cy: float, zoom: float) -> None:
    """把"以 (cx,cy) 为中心的一对虫"画到当前画笔原点。

    顺序必须是 translate(画布位置) → scale(zoom) → translate(-cx,-cy)：
    先缩放再平移，平移量会被一起缩放，点 (cx,cy) 才落在原点。
    """
    p.save()
    p.scale(zoom, zoom)
    p.translate(-cx, -cy)
    female.render(p)
    male.render(p)
    # 心形单独一遍，与 overlay_window 的绘制顺序一致
    female.render_mating_hearts(p)
    male.render_mating_hearts(p)
    p.restore()


def capture(female: Cockroach, male: Cockroach, cx: float, cy: float,
            zoom: float, w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(w / 2.0, h / 2.0)
    draw_pair(p, female, male, cx, cy, zoom)
    p.end()
    return img


def simulate_shots(cx: float, cy: float, zoom: float, w: int, h: int):
    """跑完整一次交配，按进度均匀抓 FRAMES 帧（每帧当场渲染）。"""
    female, male = make_pair_at(cx, cy)
    marks = [i / (FRAMES - 1) * DURATION for i in range(FRAMES)]
    shots = []
    t = 0.0
    idx = 0
    while idx < FRAMES and t < DURATION + 0.25:
        for r in (female, male):
            r.update(DT)
        t += DT
        if idx < len(marks) and t >= marks[idx]:
            shots.append((
                female.mating_progress,
                capture(female, male, cx, cy, zoom, w, h),
                len(male._mating_rig.hearts),
            ))
            idx += 1
    return shots


def _frames_sheet() -> None:
    rows = (FRAMES + COLS - 1) // COLS
    W, H = CELL * COLS, CELL * rows + 46
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(246, 243, 238))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    cx, cy = CELL * 2.0, (CELL * rows) / 2.0 + 12.0
    shots = simulate_shots(cx, cy, 1.15, CELL, CELL)

    p.setPen(QColor(70, 64, 58))
    p.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.DemiBold))
    p.drawText(QPointF(18, 26), "交配动作：对接 → 律动 → 急促 → 抽离（2.2 秒，逐帧抓 8 张）")

    for i, (prog, shot, hearts) in enumerate(shots):
        col, row = i % COLS, i // COLS
        x0, y0 = col * CELL, row * CELL + 40
        p.fillRect(QRectF(x0 + 6, y0 + 6, CELL - 12, CELL - 12), QColor(252, 250, 246))
        p.setPen(QPen(QColor(214, 206, 196), 1))
        p.drawRect(QRectF(x0 + 6, y0 + 6, CELL - 12, CELL - 12))
        p.drawImage(QPointF(x0, y0), shot)

        p.setPen(QColor(120, 112, 104))
        p.setFont(QFont("Microsoft YaHei", 8))
        p.drawText(QPointF(x0 + 14, y0 + CELL - 16),
                   f"{prog:.2f}s · {prog / DURATION * 100:.0f}%  心形 {hearts}")
    p.end()
    img.save(str(OUT / "mating_frames.png"), "PNG")


def _closeup() -> None:
    """55% 那一帧，放大 2.2 倍看结合处。中心取两虫中点（结合处）。"""
    cw = ch = 560
    female, male = make_pair_at(0.0, 0.0)
    while female.mating_progress < DURATION * 0.55:
        female.update(DT)
        male.update(DT)
    jx = (female.pos.x + male.pos.x) / 2.0
    jy = (female.pos.y + male.pos.y) / 2.0

    img = QImage(cw, ch, QImage.Format.Format_ARGB32)
    img.fill(QColor(246, 243, 238))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(70, 64, 58))
    p.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.DemiBold))
    p.drawText(QPointF(18, 26), "结合处放大（55%）：光晕 / 每拍一圈波纹 / 上浮心形")
    p.translate(cw / 2.0, ch / 2.0 + 22)
    draw_pair(p, female, male, jx, jy, 2.2)
    p.end()
    img.save(str(OUT / "mating_closeup.png"), "PNG")


def _angle_sheet() -> None:
    """横着配对的虫：验证心形永远往上飘、不随身体转向。"""
    cw, ch = 1080, 400
    img = QImage(cw, ch, QImage.Format.Format_ARGB32)
    img.fill(QColor(246, 243, 238))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(70, 64, 58))
    p.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.DemiBold))
    p.drawText(QPointF(18, 26), "任意朝向：心形粒子在世界坐标里飘，不随虫体转向")

    for i, deg in enumerate((0.0, 35.0, 90.0, 135.0)):
        cx = 140 + i * 270
        cy = 210
        female, male = make_pair_at(cx, cy, deg)
        while female.mating_progress < DURATION * 0.70:
            female.update(DT)
            male.update(DT)
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, 1.0)
        p.translate(-cx, -cy)
        female.render(p)
        male.render(p)
        female.render_mating_hearts(p)
        male.render_mating_hearts(p)
        p.restore()
        p.setPen(QColor(120, 112, 104))
        p.setFont(QFont("Microsoft YaHei", 8))
        p.drawText(QPointF(cx - 30, ch - 16), f"{deg:.0f}°")
    p.end()
    img.save(str(OUT / "mating_angles.png"), "PNG")


def _bg_check() -> None:
    """浅色 / 深色桌面各来一张：暖光在浅底上容易糊成白雾，深底上才看得出"发光"。"""
    for bg, name in ((QColor(246, 243, 238), "light"), (QColor(38, 36, 42), "dark")):
        female, male = make_pair_at(0.0, 0.0)
        while female.mating_progress < DURATION * 0.55:
            female.update(DT)
            male.update(DT)
        img = QImage(520, 520, QImage.Format.Format_ARGB32)
        img.fill(bg)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(260.0, 272.0)
        jx = (female.pos.x + male.pos.x) / 2.0
        jy = (female.pos.y + male.pos.y) / 2.0
        draw_pair(p, female, male, jx, jy, 2.2)
        p.end()
        img.save(str(OUT / f"mating_bg_{name}.png"), "PNG")


def main() -> None:
    app = QGuiApplication(sys.argv[:1])
    app.setFont(QFont("Microsoft YaHei", 9))
    OUT.mkdir(parents=True, exist_ok=True)
    _frames_sheet()
    print("ok:", OUT / "mating_frames.png")
    _closeup()
    print("ok:", OUT / "mating_closeup.png")
    _angle_sheet()
    print("ok:", OUT / "mating_angles.png")
    _bg_check()
    print("ok:", OUT / "mating_bg_light.png", "/", OUT / "mating_bg_dark.png")


if __name__ == "__main__":
    main()
