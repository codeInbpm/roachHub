"""悬停卡片的离屏预览：四种能量状态各渲染一格，拼成 2x2。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import QRect

app = QApplication(sys.argv[:1])

from src.overlay_window import OverlayWindow
from src.entities.cockroach import Cockroach
from src.entities.base_entity import LifeStage

Cockroach.quality = 2
ov = OverlayWindow()
ov.resize(460, 300)

cases = [
    (82.0, 100.0, 2, "正常"),
    (34.0, 100.0, 2, "偏饿"),
    (6.0, 74.0, 0, "饥饿 / 待受精"),
    (0.0, 62.0, 0, "能量耗尽"),
]

tiles = []
for energy, health, broods, _label in cases:
    r = Cockroach(150, 150, stage=LifeStage.ADULT, gender="female", config={})
    r.energy = energy
    r.health = health
    r.broods_left = broods
    ov.set_entities([r])
    ov._hover_entity = r
    ov._hover_until = time.monotonic() + 9999
    tiles.append(ov.grab(QRect(0, 0, 460, 300)))

out = QImage(920, 600, QImage.Format.Format_ARGB32)
out.fill(QColor(46, 48, 54))
p = QPainter(out)
for i, t in enumerate(tiles):
    p.drawPixmap((i % 2) * 460, (i // 2) * 300, t)
p.end()
out.save("tools/out/hover_card.png")
print("saved tools/out/hover_card.png")
