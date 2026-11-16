"""食物腐败进程与贴图效果预览（离线出图，不启动桌面）。

跑法：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/food_preview.py

产出 tools/out/food_rot_curve.png（每种食物 4 个腐败阶段）与
tools/out/food_scene.png（和蟑螂混在一起的桌面实景）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import EntityState, LifeStage      # noqa: E402
from src.entities.food import Food                                # noqa: E402
from src.entities.food_kinds import FOOD_KINDS                    # noqa: E402

OUT = ROOT / "tools" / "out"

# 桌面常见的两种底色：深色壁纸 / 浅色文档
BG_DARK = QColor(46, 44, 42)
BG_LIGHT = QColor(226, 222, 214)

STAGES = ((1.0, "新鲜"), (0.62, "发霉"), (0.34, "长蛆"), (0.10, "烂透"))


def _rot_curve() -> QImage:
    cols = len(STAGES)
    cell_w, cell_h = 150, 168
    rows = len(FOOD_KINDS)
    img = QImage(cols * cell_w + 130, rows * cell_h + 40,
                 QImage.Format.Format_ARGB32)
    img.fill(BG_DARK)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setFont(QFont("Microsoft YaHei", 8))

    for c, (_, label) in enumerate(STAGES):
        p.setPen(QColor(235, 230, 220))
        p.drawText(QRectF(130 + c * cell_w, 8, cell_w, 22),
                   int(Qt.AlignmentFlag.AlignHCenter), label)

    for r, k in enumerate(FOOD_KINDS):
        top = 36 + r * cell_h
        p.setPen(QColor(210, 205, 195))
        p.drawText(QRectF(4, top + cell_h // 2 - 10, 122, 20),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   f"{k.name} {k.nutrition:.0f}")
        for c, (fresh, _) in enumerate(STAGES):
            food = Food(130 + c * cell_w + cell_w // 2, top + cell_h // 2 - 6,
                        kind=k.key)
            food.freshness = fresh
            food.age = (1.0 - fresh) * food.shelf_life
            food.nutrition = food.max_nutrition * (1.0 - 0.45 * (1.0 - fresh))
            food.render(p)
    p.end()
    return img


def _scene() -> QImage:
    """桌面实景：12 块食物（腐到什么程度都有）+ 3 只虫。

    注意这里**不走 overlay.grab()**：覆盖层窗口在离屏平台上 grab 出来是空白的
    （paintEvent 里 `QPainter(self)` 直接绑在窗口表面）。QWidget.render(painter)
    同样拿不到。所以这里直接在 QImage 上按同样的层级调用实体 render ——
    绘制代码是同一份，看到的效果和桌面上一致。
    """
    from src.entities.cockroach import Cockroach

    Cockroach.quality = 2
    picks = ("carrion", "maggots", "caterpillar", "droppings",
             "moldbread", "rottentomato", "moldcheese", "deadfly",
             "applecore", "millipede", "slug", "moldpatch")
    cols = 3
    cell_w, cell_h = 300, 200
    width = cols * cell_w
    height = cell_h * 4 + 130
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(BG_LIGHT)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    for i, key in enumerate(picks):
        col, row = i % cols, i // cols
        food = Food(cell_w * col + cell_w // 2, cell_h * row + cell_h // 2 + 30,
                    kind=key)
        # 四种新鲜度轮流铺开，一眼能看出腐烂梯度
        food.freshness = (1.0, 0.55, 0.28, 0.10)[i % 4]
        food.age = (1.0 - food.freshness) * food.shelf_life
        food.nutrition = food.max_nutrition * (1.0 - 0.45 * (1.0 - food.freshness))
        food.render(p)

    base_y = cell_h * 4 + 60
    for i, (rx, ry, ang) in enumerate(((150, base_y, 0.5),
                                       (450, base_y + 6, 2.4),
                                       (740, base_y - 4, -1.0))):
        roach = Cockroach(rx, ry, stage=LifeStage.ADULT,
                          gender="female" if i % 2 else "male", config={})
        roach.angle = roach.target_angle = ang
        roach.state = EntityState.WALK
        roach.update(1 / 60)
        roach.render(p)

    p.end()
    return img


def _feeding_scene() -> QImage:
    """进食位形实景：跑 6 秒真实行为+运动，把"虫停在哪"画出来。

    专门用来核对停靠距离：食物半径从 17（跳蚤）到 34.6（腐肉）差一倍，
    虫必须停在食物**边缘**、头压在食物表面上，而不是整只趴在食物中间。
    """
    from src.entities.cockroach import Cockroach
    from src.systems.behavior import BehaviorSystem
    from src.systems.movement import MovementSystem

    Cockroach.quality = 2
    width, height = 900, 560
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(BG_LIGHT)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    spots = (("carrion", 170.0, 180.0), ("flea", 470.0, 150.0),
             ("maggots", 730.0, 200.0), ("moldbread", 300.0, 420.0),
             ("droppings", 680.0, 440.0))
    foods = [Food(x, y, kind=k) for k, x, y in spots]

    roaches = []
    for i, (k, x, y) in enumerate(spots):
        for j in range(2 if i < 3 else 1):
            r = Cockroach(x + (j - 0.5) * 90.0 + 10.0, y + 150.0 + j * 30.0,
                          stage=LifeStage.ADULT,
                          gender="female" if (i + j) % 2 else "male", config={})
            r.energy = 40.0
            roaches.append(r)

    beh = BehaviorSystem({})
    mov = MovementSystem(width, height, thigmotaxis_weight=0.0)
    entities = roaches + foods
    for _ in range(300):          # 5 秒
        beh.update(entities, 1 / 60)
        mov.update(entities, 1 / 60)
        for e in entities:
            e.update(1 / 60)

    for f in foods:
        f.render(p)
    for r in roaches:
        r.render(p)
    p.end()
    return img


def _stop_ring() -> QImage:
    """单虫单食的放大对照：小 / 中 / 大三档食物，看虫到底停在哪。"""
    from src.entities.cockroach import Cockroach
    from src.systems.behavior import BehaviorSystem
    from src.systems.movement import MovementSystem

    Cockroach.quality = 2
    picks = ("flea", "maggots", "carrion")
    cell_w, cell_h = 330, 300
    img = QImage(cell_w * len(picks), cell_h, QImage.Format.Format_ARGB32)
    img.fill(BG_LIGHT)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    for i, key in enumerate(picks):
        cx = cell_w * i + cell_w // 2
        cy = cell_h // 2 - 10
        food = Food(cx, cy, kind=key)
        roach = Cockroach(cx, cy + 240, stage=LifeStage.ADULT,
                          gender="female", config={})
        roach.energy = 40.0
        beh = BehaviorSystem({})
        mov = MovementSystem(2000, 2000, thigmotaxis_weight=0.0)
        for _ in range(360):
            beh.update([roach, food], 1 / 60)
            mov.update([roach, food], 1 / 60)
            roach.update(1 / 60)
            food.update(1 / 60)
            if roach.state == EntityState.EAT:
                break
        food.render(p)
        roach.render(p)
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    _rot_curve().save(str(OUT / "food_rot_curve.png"), "PNG")
    _scene().save(str(OUT / "food_scene.png"), "PNG")
    _feeding_scene().save(str(OUT / "food_feeding.png"), "PNG")
    _stop_ring().save(str(OUT / "food_stop_ring.png"), "PNG")
    print("ok:", OUT / "food_rot_curve.png")
    print("ok:", OUT / "food_scene.png")
    print("ok:", OUT / "food_feeding.png")
    print("ok:", OUT / "food_stop_ring.png")


if __name__ == "__main__":
    main()
