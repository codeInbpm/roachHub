"""步态诊断图：把六只脚在体坐标系里的运动轨迹叠画出来，检验支撑/摆动相位。

正确表现：每条腿的轨迹是一段椭圆状回环 —— 长直的一段是支撑相（钉地后移），
短弯的一段是摆动相（抬腿前摆），且相邻腿相位互补（交替三足）。
"""
from __future__ import annotations
import math, sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPolygonF

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import EntityState, LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach  # noqa: E402

COLORS = [QColor(220, 60, 50), QColor(240, 150, 30), QColor(60, 160, 70),
          QColor(30, 130, 210), QColor(150, 80, 200), QColor(200, 60, 160)]


def run(speed: float, frames: int = 180):
    r = Cockroach(0, 0, stage=LifeStage.ADULT, gender="female", config={})
    r.scale = 1.0
    r.state = EntityState.WALK
    r.current_speed = speed
    r.target_speed = speed
    r.pose.snap_all()
    for _ in range(60):
        r.update(1 / 60)
    trails = [[] for _ in r.leg_rig.legs]
    lifts = [[] for _ in r.leg_rig.legs]
    for _ in range(frames):
        r.update(1 / 60)
        for i, leg in enumerate(r.leg_rig.legs):
            trails[i].append((leg.foot[0], leg.foot[1]))
            lifts[i].append(leg.lift)
    return r, trails, lifts


def draw(r, trails, lifts, path: Path, title: str) -> None:
    size = 420
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(252, 252, 253))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(size / 2, size / 2)
    p.scale(3.6, 3.6)

    # 躯干轮廓
    p.setPen(QPen(QColor(180, 180, 186), 0.8))
    p.setBrush(QColor(236, 230, 226))
    p.drawRoundedRect(QRectF(-r.body_w / 2, -r.body_len / 2, r.body_w, r.body_len),
                      r.body_w * 0.28, r.body_w * 0.28)

    for i, (trail, lift) in enumerate(zip(trails, lifts)):
        pen = QPen(COLORS[i % len(COLORS)], 0.7)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for j in range(1, len(trail)):
            p.drawLine(QPointF(*trail[j - 1]), QPointF(*trail[j]))
        # 摆动相高亮
        p.setPen(QPen(COLORS[i % len(COLORS)].lighter(130), 1.6))
        for j in range(len(trail)):
            if lift[j] > 0.4:
                p.drawPoint(QPointF(*trail[j]))

    p.end()
    p2 = QPainter(img)
    p2.setPen(QColor(40, 40, 45))
    p2.setFont(QFont("Consolas", 9))
    p2.drawText(8, 16, title)
    p2.end()
    img.save(str(path))


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    out = ROOT / "tools" / "out"
    out.mkdir(parents=True, exist_ok=True)
    for speed, name in ((95.0, "gait_95"), (300.0, "gait_300"), (45.0, "gait_45")):
        r, trails, lifts = run(speed)
        draw(r, trails, lifts, out / f"{name}.png",
             f"foot paths @ {speed:.0f}px/s   (thick dots = swing phase)")
    print("ok")


if __name__ == "__main__":
    main()
