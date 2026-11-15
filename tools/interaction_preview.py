"""互动与氛围的离屏验证：痕迹层、抓取挣扎、进食、触须嗅探。

    QT_QPA_PLATFORM=offscreen python tools/interaction_preview.py

输出：
    tools/out/interaction_sheet.png   四行验证带（痕迹 / 抓取 / 进食 / 嗅探）
    tools/out/trail_fade.png          痕迹淡出过程（四格）
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import EntityState, LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach  # noqa: E402
from src.entities.food import Food  # noqa: E402
from src.systems.trails import TrailCanvas  # noqa: E402
from src.utils.math_utils import Vector2D  # noqa: E402

OUT = ROOT / "tools" / "out"
DT = 1.0 / 60.0
WORLD_W, WORLD_H = 1200, 720
CELL_W, CELL_H = 560, 336

DESKTOP: QImage | None = None


def desktop_background() -> QImage:
    """像桌面的底图：渐变 + 细网格，便于观察痕迹深浅。"""
    global DESKTOP
    if DESKTOP is not None:
        return DESKTOP
    img = QImage(WORLD_W, WORLD_H, QImage.Format.Format_ARGB32)
    img.fill(QColor(238, 240, 244))
    p = QPainter(img)
    p.fillRect(0, 0, WORLD_W, WORLD_H, QColor(243, 245, 249))
    p.setPen(QColor(226, 230, 238))
    for x in range(0, WORLD_W, 40):
        p.drawLine(x, 0, x, WORLD_H)
    for y in range(0, WORLD_H, 40):
        p.drawLine(0, y, WORLD_W, y)
    p.end()
    DESKTOP = img
    return img


def new_roach(x: float, y: float, angle: float = -math.pi / 2) -> Cockroach:
    r = Cockroach(x, y, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.angle = angle
    r.target_angle = angle
    r.pose.snap_all()
    r.shadow_intensity = 0.30
    return r


def walk_with_trails(seconds: float) -> tuple[Cockroach, TrailCanvas]:
    canvas = TrailCanvas(WORLD_W, WORLD_H, 1.0, 6.0)
    r = new_roach(WORLD_W - 90.0, WORLD_H * 0.55, angle=math.pi)
    r.state = EntityState.WALK
    r.current_speed = 115.0
    r.target_speed = 115.0
    r.on_footstep = lambda wx, wy, side, strength, tone: canvas.add_footprint(
        wx, wy, strength, tone
    )
    r.on_smear = lambda wx, wy, ang, ln: canvas.add_smear(wx, wy, ang, ln)
    for _ in range(int(seconds * 60)):
        r.update(DT)
        r.pos.x += math.cos(r.angle) * r.current_speed * DT
        r.pos.y += math.sin(r.angle) * r.current_speed * DT
        canvas.tick(DT)
    return r, canvas


def cell(
    img: QImage,
    rect: QRectF,
    roaches,
    foods=(),
    trails: TrailCanvas | None = None,
    zoom: float = 0.45,
    focus: QPointF | None = None,
    cursor: QPointF | None = None,
    title: str = "",
) -> None:
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.save()
    p.setClipRect(rect)

    fx = focus.x() if focus else WORLD_W / 2
    fy = focus.y() if focus else WORLD_H / 2
    p.translate(rect.center())
    p.scale(zoom, zoom)
    p.translate(-fx, -fy)

    p.drawImage(0, 0, desktop_background())
    if trails is not None:
        trails.render(p)
    for f in foods:
        f.render(p)
    for r in roaches:
        r.render(p)
    if cursor is not None:
        p.setPen(QPen(QColor(40, 120, 240, 210), 1.6))
        p.drawLine(QPointF(cursor.x() - 9, cursor.y()), QPointF(cursor.x() + 9, cursor.y()))
        p.drawLine(QPointF(cursor.x(), cursor.y() - 9), QPointF(cursor.x(), cursor.y() + 9))
        p.drawEllipse(cursor, 5.0, 5.0)
    p.restore()

    # 注意：offscreen 平台没有字体库，任何 drawText 都会渲染成方框，
    # 因此验证图只用版面顺序表达含义，图注写在 docs 与命令行输出里。
    p.setPen(QColor(196, 200, 208))
    p.drawRect(rect)
    p.end()


def build_sheet() -> QImage:
    rows: list[tuple[str, list]] = []

    # ---- 行 1：足迹与油渍的衰减 ----
    r_walk, canvas = walk_with_trails(9.0)
    cells = []
    for i in range(3):
        if i:
            for _ in range(int(3.0 * 60)):
                canvas.tick(DT)
        cells.append(dict(trails=canvas.image.copy(), roaches=[r_walk],
                          zoom=1.15, focus=QPointF(r_walk.pos.x + 150, r_walk.pos.y),
                          title=("t=0s  fresh trail" if i == 0 else f"t={i * 3}s  fading")))
    rows.append(("FOOTPRINTS", cells))

    # ---- 行 2：抓取挣扎 -> 装死 -> 松手 ----
    struggle = new_roach(300, 340)
    struggle.begin_drag(Vector2D(300, 340))
    for i in range(100):
        struggle.update(DT)
        struggle.drag_to(Vector2D(300 + math.sin(i * 0.14) * 22, 340 + i * 0.4))

    limp = new_roach(600, 340)
    limp.begin_drag(Vector2D(600, 340))
    for _ in range(int(6.8 * 60)):
        limp.update(DT)
        limp.drag_to(Vector2D(600, 340))

    dropped = new_roach(900, 340)
    dropped.begin_drag(Vector2D(900, 340))
    for _ in range(60):
        dropped.update(DT)
    dropped.end_drag()
    for _ in range(8):
        dropped.update(DT)

    rows.append(("GRAB", [
        dict(roaches=[struggle], zoom=2.6, focus=QPointF(struggle.pos.x, struggle.pos.y),
             title="grabbed 1.6s: all six legs flailing"),
        dict(roaches=[limp], zoom=2.6, focus=QPointF(limp.pos.x, limp.pos.y),
             title="held 6.8s: playing dead"),
        dict(roaches=[dropped], zoom=2.6, focus=QPointF(dropped.pos.x, dropped.pos.y),
             title="released: splayed legs, panic"),
    ]))

    # ---- 行 3：进食，食物随营养量收缩 ----
    eat_cells = []
    for k, nut in enumerate((40.0, 24.0, 8.0)):
        f = Food(300 + k * 300, 360, nutrition=40.0)
        f.nutrition = nut
        f.claim("x")
        rr = new_roach(f.x, f.y + 42.0)
        rr.state = EntityState.EAT
        rr.eating_food = f
        for _ in range(36):
            rr.update(DT)
        eat_cells.append(dict(roaches=[rr], foods=[f], zoom=2.6,
                              focus=QPointF(f.x, f.y + 10),
                              title=f"food {nut / 40 * 100:.0f}% nutrition"))
    rows.append(("EATING", eat_cells))

    # ---- 行 4：触须跟随光标 ----
    sniff_cells = []
    for k, (dx, dy) in enumerate(((0, -95), (95, 40), (-85, 55))):
        rr = new_roach(300 + k * 300, 380)
        rr.state = EntityState.IDLE
        rr.target_speed = 0.0
        target = Vector2D(rr.pos.x + dx, rr.pos.y + dy)
        for _ in range(50):
            rr.threat_vector = target - rr.pos
            rr.threat_proximity = 0.8
            rr.alertness = 0.5
            rr.update(DT)
        sniff_cells.append(dict(roaches=[rr], zoom=1.45,
                                focus=QPointF(rr.pos.x, rr.pos.y),
                                cursor=QPointF(target.x, target.y),
                                title=f"cursor at {dx:+d},{dy:+d}"))
    rows.append(("ANTENNAE TRACKING", sniff_cells))

    sheet = QImage(CELL_W * 3 + 16, CELL_H * 4 + 34, QImage.Format.Format_ARGB32)
    sheet.fill(QColor(250, 250, 252))
    for row, (row_name, cells) in enumerate(rows):
        for col, spec in enumerate(cells):
            rect = QRectF(4 + col * (CELL_W + 4), 26 + row * (CELL_H + 2), CELL_W, CELL_H)
            trails = spec.pop("trails", None)
            traj = spec.pop("trails_img", None)
            if trails is not None and isinstance(trails, QImage):
                # 只保留痕迹图（可复现的衰减快照）
                cv = TrailCanvas(WORLD_W, WORLD_H, 1.0, 6.0)
                cv.image = trails
                cv.count = 1
                trails = cv
            cell(sheet, rect, spec.pop("roaches", []), spec.pop("foods", ()),
                 trails=trails, **spec)
        p = QPainter(sheet)
        p.setPen(QColor(214, 118, 44, 150))
        p.setBrush(QColor(214, 118, 44, 150))
        p.drawRect(QRectF(4, 26 + row * (CELL_H + 2) - 4, 26, 3))
        p.end()

    return sheet


def build_fade_series() -> QImage:
    _, canvas = walk_with_trails(4.0)
    snaps = []
    for i in range(4):
        if i:
            for _ in range(int(2.0 * 60)):
                canvas.tick(DT)
        snaps.append(canvas.image.copy())

    img = QImage(CELL_W * 2 + 12, CELL_H * 2 + 12, QImage.Format.Format_ARGB32)
    img.fill(QColor(252, 252, 253))
    focus = QPointF(WORLD_W - 520.0, WORLD_H * 0.55)
    for i, layer in enumerate(snaps):
        cv = TrailCanvas(WORLD_W, WORLD_H, 1.0, 6.0)
        cv.image = layer
        cv.count = 1
        rect = QRectF((i % 2) * (CELL_W + 8), (i // 2) * (CELL_H + 8), CELL_W, CELL_H)
        # zoom 必须为 1.0：足印是 1~2px、alpha 20~80 的极淡像素，
        # 任何 <1 的缩放都会做双线性平均，把 alpha 再压低一次，图上就"看不见"了。
        cell(img, rect, [], trails=cv, zoom=1.0, focus=focus, title=f"after {i * 2.0:.0f}s")
    return img


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)

    build_sheet().save(str(OUT / "interaction_sheet.png"))
    build_fade_series().save(str(OUT / "trail_fade.png"))

    # 性能：1920x1080 上 2400 笔痕迹的单帧成本（0.5 半分辨率 vs 1.0 原分辨率）
    probe = QImage(1920, 1080, QImage.Format.Format_ARGB32)
    probe.fill(Qt.GlobalColor.transparent)
    for scale in (0.5, 1.0):
        cv = TrailCanvas(1920, 1080, scale, 6.0)
        for i in range(2400):
            cv.add_footprint(100 + (i * 7) % 1800, 100 + (i * 13) % 1000, 0.8, 1.0)
        t0 = time.perf_counter()
        for _ in range(120):
            cv.tick(DT)
        tick_ms = (time.perf_counter() - t0) / 120 * 1000

        t0 = time.perf_counter()
        for _ in range(40):
            pp = QPainter(probe)
            pp.drawImage(QRectF(0, 0, 1920, 1080), cv.image)
            pp.end()
        draw_ms = (time.perf_counter() - t0) / 40 * 1000
        print(f"trail layer scale={scale}  {cv.width}x{cv.height}  {cv.count} prints")
        print(f"  fade {tick_ms:.2f} ms/frame | composite {draw_ms:.2f} ms/frame"
              f" | total {tick_ms + draw_ms:.2f} ms")


if __name__ == "__main__":
    main()
