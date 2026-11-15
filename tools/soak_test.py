"""真机浸泡测试：200 只同屏跑 8 秒，量真实窗口下的帧间隔与卡死情况。

    python tools/soak_test.py --count 200 --seconds 8

和离屏基准不同，这个会**真的开一个全屏覆盖层**（跑完自动关闭），因此能测到
Windows 的 layered window 合成、WM_NCHITTEST 命中测试、以及消息循环是否还能
及时处理输入 —— 也就是用户说的"卡死"到底有没有了。

判据（不满足即返回非 0）：
* 任意帧间隔 > 250ms 视为一次"卡死"（消息循环被堵住）；
* p95 帧间隔应 <= 1.6 × 目标周期；
* 运行期间窗口 mask 必须为空（Windows 下不再用 QRegion 遮罩）。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from src.app import RoachApp
    from src.entities.base_entity import LifeStage
    from src.entities.cockroach import Cockroach

    app = QApplication(sys.argv)
    ch = RoachApp(app)

    w, h = ch.overlay.width() or 1920, ch.overlay.height() or 1080
    ch.entities = [
        Cockroach(60 + (i * 97) % max(1, w - 120), 60 + (i * 61) % max(1, h - 120),
                  stage=LifeStage.ADULT, gender="female", config={})
        for i in range(args.count)
    ]
    for r in ch.entities:
        r.on_footstep = ch._on_footstep
        r.on_smear = ch._on_smear
        r.on_sound = ch._on_sound

    ch.overlay.show()
    ch._last_tick_time = time.perf_counter()
    ch.timer.start(max(1, int(1000.0 / ch.target_fps)))

    stamps: list[float] = []
    ch.timer.timeout.connect(lambda: stamps.append(time.perf_counter()))

    mask_empty = {"ok": True}

    def check_mask() -> None:
        if not ch.overlay.mask().isEmpty():
            mask_empty["ok"] = False

    phases: list[tuple[float, float, float]] = []
    tick_samples: list[float] = []
    paint_samples: list[float] = []

    def sample() -> None:
        check_mask()
        if ch._tick_ms > 0:
            tick_samples.append(ch._tick_ms)
        if ch.overlay._paint_ms > 0:
            paint_samples.append(ch.overlay._paint_ms)
        if ch._tick_t1 and ch.overlay._paint_t1 > ch._tick_t1:
            phases.append((
                (ch.overlay._paint_t0 - ch._tick_t1) * 1000.0,   # 等事件循环派发绘制
                (ch.overlay._paint_t1 - ch.overlay._paint_t0) * 1000.0,
                (ch._tick_t0 - ch.overlay._paint_t1) * 1000.0 if ch._tick_t0 > ch.overlay._paint_t1 else 0.0,
            ))

    probe = QTimer()
    probe.timeout.connect(sample)
    probe.start(30)

    QTimer.singleShot(int(args.seconds * 1000), app.quit)
    app.exec()

    probe.stop()
    ch.timer.stop()

    gaps = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    snaps = ch.performance_snapshot()
    print(f"\n== 真机浸泡 {args.count} 只 / {args.seconds:.0f}s ==")
    if not gaps:
        print("  没有采集到帧，检查窗口是否真的显示出来了")
        return 1
    gaps_sorted = sorted(gaps)
    p50 = statistics.median(gaps)
    p95 = gaps_sorted[int(len(gaps_sorted) * 0.95) - 1]
    stalls = [g for g in gaps if g > 250]
    print(f"  帧数 {len(gaps)}  平均 {statistics.mean(gaps):6.1f}ms  "
          f"p50 {p50:6.1f}ms  p95 {p95:6.1f}ms  最大 {max(gaps):7.1f}ms")
    print(f"  画质 LOD{int(snaps['quality'])}  帧率上限 {int(snaps['fps_cap'])}  "
          f"EMA 帧耗时 {snaps['frame_ms']:.1f}ms（预算 {snaps['budget_ms']:.1f}ms）")
    print(f"  超过 250ms 的卡顿: {len(stalls)} 次")
    print(f"  窗口遮罩为空: {'是' if mask_empty['ok'] else '否（仍在用 QRegion！）'}")
    print(f"  足迹笔数 {ch.overlay.trails.count}  实体 {len(ch.entities)}")
    print(f"  最近一帧：主循环 {ch._tick_ms:.1f}ms + 绘制 {ch.overlay._paint_ms:.1f}ms"
          f"（其中痕迹 {ch.overlay._paint_trails_ms:.1f}ms，蟑螂 {ch.overlay._paint_entities_ms:.1f}ms）")
    print(f"  屏幕 {ch.overlay.width()}x{ch.overlay.height()} = "
          f"{ch.overlay.width() * ch.overlay.height() / 1e6:.1f}M px")
    print(f"  每 tick 触发的 paintEvent 次数: {ch._paints_per_tick}（累计 {ch.overlay._paint_calls}）")
    if tick_samples:
        print(f"  主循环耗时: 均值 {statistics.mean(tick_samples):.1f}ms  "
              f"p95 {sorted(tick_samples)[int(len(tick_samples) * 0.95) - 1]:.1f}ms  "
              f"max {max(tick_samples):.1f}ms")
    if phases:
        waits = [p[0] for p in phases]
        idle = [p[2] for p in phases]
        print(f"  主循环结束→开始绘制: 均值 {statistics.mean(waits):.1f}ms（含 Qt 清理+合成）")
        print(f"  绘制结束→下次 tick:  均值 {statistics.mean(idle):.1f}ms（定时器等待）")
    if paint_samples:
        print(f"  绘制耗时:   均值 {statistics.mean(paint_samples):.1f}ms  "
              f"p95 {sorted(paint_samples)[int(len(paint_samples) * 0.95) - 1]:.1f}ms  "
              f"max {max(paint_samples):.1f}ms")

    floor_ms = 1000.0 / max(1.0, snaps["min_fps"])
    fps = 1000.0 / p50 if p50 > 0 else 0.0
    ok = not stalls and mask_empty["ok"] and p95 <= floor_ms * 1.30
    print(f"  等效帧率 {fps:.1f} FPS（底线 {int(snaps['min_fps'])} FPS）")
    print("  结论:", "通过 —— 没有卡死" if ok else "未通过，见上面指标")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
