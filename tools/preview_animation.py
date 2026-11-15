"""离屏渲染动画预览：生成步态帧序列图与可播放的 HTML 动画。

用于验证肢体动画的连贯性（不需要真的起桌面覆盖层）：
    QT_QPA_PLATFORM=offscreen python tools/preview_animation.py

输出：
    tools/out/preview_walk.png      游走步态逐帧图（接触表）
    tools/out/preview_states.png    各行为状态姿态对比
    tools/out/preview_animation.html  CSS 逐帧播放（浏览器/预览面板直接看）
    tools/out/frames/*.png          单帧素材
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QTransform,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.entities.base_entity import EntityState, LifeStage  # noqa: E402
from src.entities.cockroach import Cockroach  # noqa: E402

OUT = ROOT / "tools" / "out"
FRAMES = OUT / "frames"

FRAME = 210          # 单帧画布边长（局部像素）
ZOOM = 2             # 接触表放大倍数
DT = 1.0 / 60.0


def _new_roach(state: EntityState, speed: float, stage=LifeStage.ADULT) -> Cockroach:
    r = Cockroach(0, 0, stage=stage, gender="female", config={})
    r.scale = 1.0
    # 头部朝上：render 里会旋转 angle+90，取 -90 抵消
    r.angle = -math.pi / 2.0
    r.target_angle = r.angle
    r.state = state
    r.current_speed = speed
    r.target_speed = speed
    r.shadow_intensity = 0.30
    r.pose.snap_all()
    # 先空跑几帧让平滑器进入稳态
    for _ in range(90):
        r.update(DT)
    return r


def _render_frame(roach: Cockroach, bg: QColor | None = None) -> QImage:
    img = QImage(FRAME, FRAME, QImage.Format.Format_ARGB32)
    img.fill(bg if bg is not None else Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.translate(FRAME / 2.0, FRAME / 2.0)
    p.scale(ZOOM, ZOOM)
    roach.render(p)
    p.end()
    return img


def capture(roach: Cockroach, state: EntityState, speed: float,
            frames: int, every: int = 2, substeps: int = 2) -> list[QImage]:
    """按固定时间步推进并抓取指定数量的帧。"""
    roach.state = state
    roach.current_speed = speed
    roach.target_speed = speed
    roach.angle = -math.pi / 2.0
    roach.target_angle = roach.angle
    out: list[QImage] = []
    i = 0
    while len(out) < frames:
        roach.update(DT)
        i += 1
        if i % every == 0:
            out.append(_render_frame(roach))
    return out


def contact_sheet(images: list[QImage], cols: int, title: str, path: Path) -> None:
    if not images:
        return
    w, h = images[0].width(), images[0].height()
    rows = math.ceil(len(images) / cols)
    header = 26
    canvas = QImage(cols * w, rows * (h + header), QImage.Format.Format_ARGB32)
    canvas.fill(QColor(247, 247, 249))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for idx, img in enumerate(images):
        cx = (idx % cols) * w
        cy = (idx // cols) * (h + header)
        p.setPen(QPen(QColor(225, 225, 230)))
        p.drawRect(cx, cy, w - 1, h + header - 1)
        p.drawImage(cx, cy + header, img)
        f = QFont("Consolas", 8)
        p.setFont(f)
        p.setPen(QColor(90, 90, 100))
        p.drawText(QRectF(cx + 4, cy + 4, w, 18), Qt.AlignmentFlag.AlignLeft,
                   f"#{idx:02d}")
    p.setPen(QColor(40, 40, 45))
    p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
    p.drawText(QRectF(8, 4, cols * w - 16, 20), Qt.AlignmentFlag.AlignLeft, title)
    p.end()
    canvas.save(str(path))


def state_board(rows: list[tuple[str, list[QImage]]], path: Path) -> None:
    """每个状态一行，行内是同一姿态在不同相位下的样子。"""
    if not rows:
        return
    cols = max(len(imgs) for _, imgs in rows)
    w = rows[0][1][0].width()
    h = rows[0][1][0].height()
    label_w = 92
    canvas = QImage(label_w + cols * w, len(rows) * h, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(247, 247, 249))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for r, (name, imgs) in enumerate(rows):
        y = r * h
        p.setPen(QPen(QColor(228, 228, 232)))
        p.drawLine(0, y + h - 1, canvas.width(), y + h - 1)
        p.setPen(QColor(45, 45, 50))
        p.setFont(QFont("Microsoft YaHei", 9))
        p.drawText(QRectF(8, y, label_w - 12, h), Qt.AlignmentFlag.AlignVCenter, name)
        for c, img in enumerate(imgs):
            p.drawImage(label_w + c * w, y, img)
    p.end()
    canvas.save(str(path))


def write_html(path: Path, clips: list[tuple[str, list[QImage], str, float, str]]) -> None:
    """把帧序列写成 CSS 逐帧动画，方便直接看动起来的效果。"""
    FRAMES.mkdir(parents=True, exist_ok=True)
    blocks = []
    for name, imgs, note, dur, slug in clips:
        fname = f"{slug}.png"
        sheet = QImage(imgs[0].width() * len(imgs), imgs[0].height(), QImage.Format.Format_ARGB32)
        sheet.fill(Qt.GlobalColor.transparent)
        p = QPainter(sheet)
        for i, img in enumerate(imgs):
            p.drawImage(i * imgs[0].width(), 0, img)
        p.end()
        sheet.save(str(FRAMES / fname))
        n = len(imgs)
        blocks.append(f"""
    <section class="clip">
      <h2>{name}</h2>
      <p>{note}</p>
      <div class="stage">
        <div class="sprite" style="--n:{n}; --d:{dur}s; --w:{imgs[0].width()}px; --h:{imgs[0].height()}px;
             background-image:url('frames/{fname}')"></div>
      </div>
    </section>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>蟑螂肢体动画预览</title>
<style>
  body {{ margin:0; padding:28px 34px; background:#f5f5f7; color:#1c1c1e;
         font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  h1 {{ font-size:19px; margin:0 0 6px; }}
  .lead {{ color:#6b6b70; font-size:13px; margin:0 0 22px; }}
  .clip {{ background:#fff; border:1px solid #e6e6ea; border-radius:12px;
           padding:16px 18px 20px; margin-bottom:18px; }}
  .clip h2 {{ font-size:14px; margin:0 0 4px; }}
  .clip p {{ color:#77777c; font-size:12px; margin:0 0 12px; }}
  .stage {{ display:flex; gap:18px; align-items:flex-end; }}
  .sprite {{
    width: var(--w); height: var(--h);
    background-repeat:no-repeat;
    background-size: calc(var(--w) * var(--n)) var(--h);
    animation: play var(--d) steps(var(--n)) infinite;
    image-rendering: auto;
  }}
  @keyframes play {{ from {{ background-position: 0 0; }}
                     to {{ background-position: calc(var(--w) * var(--n) * -1) 0; }} }}
</style></head><body>
  <h1>蟑螂肢体动画 · 逐帧预览</h1>
  <p class="lead">每帧间隔 1/60 秒，按真实步态相位抓取。触须与六足由程序化骨架驱动，
     不是预烘焙循环图。</p>
  {''.join(blocks)}
</body></html>"""
    path.write_text(html, encoding="utf-8")


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)

    walk = _new_roach(EntityState.WALK, 95.0)
    walk_frames = capture(walk, EntityState.WALK, 95.0, frames=16, every=2)

    panic = _new_roach(EntityState.PANIC, 300.0)
    panic_frames = capture(panic, EntityState.PANIC, 300.0, frames=10, every=2)

    groom = _new_roach(EntityState.GROOMING, 0.0)
    groom_frames = capture(groom, EntityState.GROOMING, 0.0, frames=10, every=3)

    idle = _new_roach(EntityState.IDLE, 0.0)
    idle_frames = capture(idle, EntityState.IDLE, 0.0, frames=8, every=4)

    nymph = _new_roach(EntityState.WALK, 70.0, stage=LifeStage.NYMPH)
    nymph_frames = capture(nymph, EntityState.WALK, 70.0, frames=8, every=3)

    # 被捏在手上：脚踩不到地，但六足仍在原地乱蹬（flail 驱动相位）
    drag = _new_roach(EntityState.DRAGGED, 0.0)
    drag_frames = capture(drag, EntityState.DRAGGED, 0.0, frames=10, every=3)

    # 进食：前足抱住食物，腹板节律收缩
    eat = _new_roach(EntityState.EAT, 0.0)
    eat_frames = capture(eat, EntityState.EAT, 0.0, frames=8, every=4)

    contact_sheet(walk_frames, cols=4, title="WALK 95px/s  (16 frames, 1/30s apart)",
                  path=OUT / "preview_walk.png")
    state_board(
        [("游走 WALK", walk_frames[:6]),
         ("惊慌 PANIC", panic_frames[:6]),
         ("梳理 GROOM", groom_frames[:6]),
         ("静止 IDLE", idle_frames[:6]),
         ("若虫 NYMPH", nymph_frames[:6]),
         ("抓取 DRAGGED", drag_frames[:6]),
         ("进食 EAT", eat_frames[:6])],
        OUT / "preview_states.png",
    )
    write_html(
        OUT / "preview_animation.html",
        [("WALK · 95 px/s", walk_frames,
          "交替三足步态：左前/右中/左后为一组，与另一组反相抬落，任何时刻三条腿着地。",
          16 / 30, "walk"),
         ("PANIC · 300 px/s", panic_frames,
          "步频与步幅同时放大，触须后伏，躯干随高频步态抖动。", 10 / 30, "panic"),
         ("GROOM · 静止梳理", groom_frames,
          "前足抬至头部反复搓动，被梳理一侧的触须下弯到前足之间。", 10 / 20, "groom"),
         ("IDLE · 静止微调", idle_frames,
          "脚掌偶尔抬离调整落点（tarsal adjustment），触须持续扫探。", 8 / 15, "idle"),
         ("DRAGGED · 被捏住挣扎", drag_frames,
          "被拎在空中：六足原地乱蹬、触须各自狂扫、身体随甩动晃动；按住 6 秒后转为装死。",
          10 / 30, "drag"),
         ("EAT · 抱食啃咬", eat_frames,
          "两只前足把食物收到口器前，腹板以约 1.3Hz 节律收缩，每 0.4 秒咬一口。",
          8 / 30, "eat")],
    )
    print(f"已输出: {OUT}")


if __name__ == "__main__":
    main()
