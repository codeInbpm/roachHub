"""把任意一段外部音频（mp3/wav/m4a）切成"拍死惨叫"的三个变体。

    QT_QPA_PLATFORM=offscreen python tools/prepare_clip_voice.py \
        --src "C:/Users/wangben/Desktop/myinstants(2).mp3" \
        --set myinstants --start 0.00 --end 0.60

`prepare_yamero_voice.py` 是这句话的**特化版**（窗口与比率都写死、注释里带着
实测包络）；本工具是同一个处理链的**通用入口**：窗口和变速比都从命令行给，
用来换素材，不用改代码。

## 只做三件事

1. 解码（复用 `prepare_yamero_voice.decode_mp3`，坑都在那个模块的文档里）；
2. 按 `--start/--end` 裁切 —— 这一步必须由人决定，见下面"为什么窗口不能自动选"；
3. 按 `--ratios` 变速（纯重采样 = 同时变短变尖），加淡入淡出，归一化落盘。

## 一段不够用时：`--join`

想留下"开头那句 + 结尾那句"、掐掉中间时，用 `--join "0:0.70,1.71:2.42"`：
每段 `起:止`（秒），段间插 `--gap` 秒静音，再走同一套变速流程。
`--end 0`（或 `--join` 里某段写 `0`）表示"到文件末尾"。

段与段之间**不做交叉淡化**：这是两句话，本来就不连续；包络上的浅间隙
（-18dB 左右）才是可以下刀的地方，插 100~150ms 静音比硬接更像"人换了口气"。

## 为什么窗口不能自动选

试过"取能量最大的 N 毫秒"，结果会把一个**字**拦腰切在中间：包络上看不出
"这一刀是不是切在音节边界上"，只能看出响不响。所以窗口交给 `--start/--end`，
默认值只是"这首素材能听"的经验值。

辅助判断可以看 `--probe` 打出来的 10ms 包络：**真正的分句处会连掉 10dB 以上
并保持几帧**，那才是能下刀的地方。

## 三个变体为什么是升调

多杀时两只会同时叫。同一个音色叠两次只是更响（像音量加倍），
升调后听起来才是"两只不同的虫在叫"。1.12/1.25 倍 ≈ 升 2 / 4 个半音，
再多就开始像花栗鼠，不吓人了。
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from prepare_yamero_voice import SR, decode_mp3, resample, shape, write   # noqa: E402

OUT_DIR = ROOT / "assets" / "sounds"

#: 归一化峰值 ≈ -2.8dBFS，给同帧的 splat 留叠加余量（与 yamero 那套一致）
PEAK = 0.72

DEFAULT_RATIOS = "1.00,1.12,1.25"


def parse_join(spec: str, total: float) -> list[tuple[float, float]]:
    """``"0:0.70,1.71:2.42"`` → [(0.0, 0.70), (1.71, 2.42)]。

    `止` 写 0 表示到文件末尾；`起` 写 0 表示从头。越界直接夹到 [0, total]。
    """
    out: list[tuple[float, float]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"--join 每段要写成 起:止，收到 {part!r}")
        a_s, b_s = part.split(":", 1)
        a = max(0.0, float(a_s or 0))
        b = float(b_s or 0)
        b = total if b <= 0 else min(total, b)
        if b - a < 0.02:
            raise SystemExit(f"--join 段 {part!r} 太短或方向反了（可用总长 {total:.3f}s）")
        out.append((a, b))
    if not out:
        raise SystemExit("--join 没有解析出任何段")
    return out


def probe(samples: list[int], upto: float = 0.0) -> None:
    """打印 10ms 包络，用来找能下刀的分句点。

    看两列：
    - `dB`：峰值电平。**掉 10dB 以上并连续几帧** 才是真间隙，掉一两帧是音节内部；
    - `zc`：过零率。0.05~0.25 是有声音（浊音），接近 0 是静音或极低频。
    """
    win = max(1, int(0.01 * SR))
    end = len(samples) if upto <= 0 else min(len(samples), int(upto * SR))
    row = []
    for i in range(0, max(0, end - win), win):
        seg = samples[i:i + win]
        peak = max(abs(v) for v in seg) / 32768.0
        db = 20.0 * math.log10(max(peak, 1e-6))
        zc = sum(1 for k in range(1, len(seg)) if (seg[k - 1] < 0) != (seg[k] < 0)) / len(seg)
        row.append(f"{i / SR:5.2f}:{db:6.1f}/{zc:.2f}")
        if len(row) == 5:
            print("   " + "  ".join(row))
            row = []
    if row:
        print("   " + "  ".join(row))


def render_wave(
    samples: list[int],
    windows: list[tuple[float, float]],
    out: Path,
    title: str,
) -> None:
    """画一张"源素材包络 + 被采用的窗口"对照图，用来肉眼确认这一刀切在哪。

    只画峰值包络（每列取 min/max）：语音的包络形状本身就能认出"哪里是一句"，
    比数字表格直观 —— 用户说"不是这句"时，指着图报秒数最快。
    """
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

    if QGuiApplication.instance() is None:
        QGuiApplication(sys.argv[:1])

    W, H = 1400, 300
    left, right, top, bottom = 64, 24, 44, 44
    plot_w = W - left - right
    plot_h = H - top - bottom
    mid = top + plot_h / 2.0
    total = len(samples) / SR
    peak = max(1, max(abs(v) for v in samples))

    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(250, 247, 242))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def x_of(t: float) -> float:
        return left + plot_w * (t / total)

    # 窗口底色
    for i, (a, b) in enumerate(windows):
        p.fillRect(QRectF(x_of(a), top, x_of(b) - x_of(a), plot_h), QColor(46, 125, 90, 38))
        p.setPen(QPen(QColor(46, 125, 90, 150), 1))
        p.drawRect(QRectF(x_of(a) + 0.5, top + 0.5, x_of(b) - x_of(a) - 1, plot_h - 1))
        p.setPen(QColor(32, 96, 68))
        p.setFont(QFont("Microsoft YaHei", 8))
        p.drawText(QPointF(x_of(a) + 4, top + 14), f"段{i + 1}  {a:.2f}~{b:.2f}s")

    # 中线 + 时间刻度
    p.setPen(QPen(QColor(120, 112, 104, 70), 1, Qt.PenStyle.DashLine))
    p.drawLine(QPointF(left, mid), QPointF(left + plot_w, mid))
    p.setFont(QFont("Microsoft YaHei", 8))
    tick = 0.0
    while tick <= total + 1e-6:
        x = x_of(tick)
        p.setPen(QPen(QColor(150, 142, 132, 110), 1))
        p.drawLine(QPointF(x, top + plot_h), QPointF(x, top + plot_h + 4))
        p.setPen(QColor(110, 102, 94))
        p.drawText(QPointF(x - 12, top + plot_h + 18), f"{tick:.2f}")
        tick += 0.25

    # 峰值包络
    p.setPen(QPen(QColor(92, 84, 76), 1))
    n = len(samples)
    for col in range(int(plot_w)):
        a = int(n * col / plot_w)
        b = max(a + 1, int(n * (col + 1) / plot_w))
        seg = samples[a:b]
        hi = max(seg) / peak
        lo = min(seg) / peak
        x = left + col + 0.5
        p.drawLine(QPointF(x, mid - hi * plot_h / 2), QPointF(x, mid - lo * plot_h / 2))

    p.setPen(QColor(70, 64, 58))
    p.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.DemiBold))
    p.drawText(QPointF(left, 26), title)
    p.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG")
    print(f"  图: {out}（窗口来自 --join / --start/--end）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="源音频路径")
    ap.add_argument("--set", required=True, dest="set_name",
                    help="素材组名，输出 <set>_1.wav ...（要同时写进 SOUND_SETS）")
    ap.add_argument("--start", type=float, default=0.0, help="裁切起点（秒）")
    ap.add_argument("--end", type=float, default=0.0,
                    help="裁切终点（秒）；0 = 到文件末尾")
    ap.add_argument("--join", default="",
                    help='多段拼接，如 "0:0.70,1.71:0"（止写 0 = 到末尾）；给了它就不用 --start/--end')
    ap.add_argument("--gap", type=float, default=0.10, help="拼接时段间静音秒数")
    ap.add_argument("--ratios", default=DEFAULT_RATIOS, help="各变体的变速比，逗号分隔")
    ap.add_argument("--fade", default="0.055,0.040,0.030",
                    help="各变体的淡出秒数，逗号分隔（短素材要更短的淡出）")
    ap.add_argument("--peak", type=float, default=PEAK, help="归一化峰值 0~1")
    ap.add_argument("--wave", default="", help="另存一张「包络 + 采用窗口」对照图（PNG）")
    ap.add_argument("--probe", action="store_true", help="只打包络，不写文件")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise SystemExit(f"缺少素材 {src}")

    samples = decode_mp3(src)
    total = len(samples) / SR
    print(f"源: {src.name}  {total:.3f}s  {SR}Hz  "
          f"峰值 {max(abs(v) for v in samples) / 32768:.3f}")

    if args.probe:
        probe(samples)
        return

    if args.join:
        windows = parse_join(args.join, total)
    else:
        windows = [(args.start, args.end if args.end > 0 else total)]

    gap = max(0, int(args.gap * SR))
    seg: list[float] = []
    for i, (a_t, b_t) in enumerate(windows):
        a = max(0, int(a_t * SR))
        b = min(len(samples), int(b_t * SR))
        if b <= a:
            raise SystemExit(f"第 {i + 1} 段是空的，检查 --join / --start / --end")
        if seg and gap:
            seg.extend([0.0] * gap)      # 段间留一口换气
        seg.extend(s / 32768.0 for s in samples[a:b])
        print(f"  段 {i + 1}: {a_t:.2f}~{b_t:.2f}s（{(b - a) / SR:.3f}s）")
    print(f"裁切后 {len(seg) / SR:.3f}s"
          + (f"（{len(windows)} 段，段间 {args.gap * 1000:.0f}ms 静音）" if len(windows) > 1 else ""))

    if args.wave:
        render_wave(samples, windows, Path(args.wave),
                    f"源素材包络 {total:.3f}s · {args.set_name} 采用的窗口")

    ratios = [float(v) for v in args.ratios.split(",") if v.strip()]
    fades = [float(v) for v in args.fade.split(",") if v.strip()]
    while len(fades) < len(ratios):
        fades.append(fades[-1])

    for i, ratio in enumerate(ratios):
        out = resample(seg, ratio)
        out = shape(out, fades[i])       # shape() 自带 3ms 淡入，削掉硬起音的 click
        path = OUT_DIR / f"{args.set_name}_{i + 1}.wav"
        write(path, out, args.peak)
        print(f"  {path.name}  变速 {ratio:.2f}×  → {len(out) / SR:.3f}s  "
              f"淡出 {fades[i] * 1000:.0f}ms  ({path.stat().st_size} B)")

    print(f"完成，共 {len(ratios)} 个变体 → {OUT_DIR}")


if __name__ == "__main__":
    main()
