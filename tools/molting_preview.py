"""把蜕皮过程渲染成一张对照图：白壳 → 渐变 → 正常色，外加蜕下的空壳。

    QT_QPA_PLATFORM=offscreen python tools/molting_preview.py
输出：tools/out/molting_preview.png
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from src.entities.base_entity import LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach  # noqa: E402
from src.entities.exuvia import Exuvia  # noqa: E402

CELL = 260
N = 5
W, H = CELL * N, CELL + 40
img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
img.fill(QColor(28, 62, 110))          # 近似用户桌面那种深蓝

p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

captions = ["蜕皮中（奶白嫩壳 2.2s）", "回落 70%", "回落 40%", "回落 15%", "正常体色 + 空壳"]

def smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def timer_for_alpha(alpha: float) -> float:
    """反解 smoothstep，让预览图的过渡色正好落在目标不透明度上。"""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if smoothstep(mid) < alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 * 1.4


for i in range(4):
    r = Cockroach(CELL * i + CELL / 2, CELL / 2 + 20, stage=LifeStage.ADULT,
                  gender="female", config={})
    r.angle = 0.0
    # 直接摆到目标白化浓度：1.0 = 蜕皮白化窗口内，其余 = 回落到该比例
    shares = [1.0, 0.7, 0.4, 0.15]
    if shares[i] >= 1.0:
        r.molt_timer = 2.2
        r.teneral_timer = 3.6
    else:
        r.molt_timer = 0.0
        r.teneral_timer = timer_for_alpha(shares[i])
    r.render(p)

# 最后一格：正常的成虫 + 旁边的空壳
r = Cockroach(CELL * 4 + CELL / 2 - 30, CELL / 2, stage=LifeStage.ADULT, gender="female", config={})
r.angle = 0.0
r.render(p)
husk = Exuvia(CELL * 4 + CELL / 2 + 62, CELL / 2 + 34, angle=0.4, scale=0.9)
husk.age = 1.0
husk.render(p)

p.end()

out = ROOT / "tools" / "out" / "molting_preview.png"
out.parent.mkdir(parents=True, exist_ok=True)
img.save(str(out))
print("saved", out)
for i, c in enumerate(captions):
    print(f"  [{i}] {c}")
