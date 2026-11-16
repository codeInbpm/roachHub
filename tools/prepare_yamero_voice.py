"""把外部素材 `yameroooo.mp3` 派生为拍死蟑螂时的叫声（`assets/sounds/yamero_*.wav`）。

    QT_QPA_PLATFORM=offscreen python tools/prepare_yamero_voice.py
    QT_QPA_PLATFORM=offscreen python tools/prepare_yamero_voice.py --src 别的声音.mp3

## 为什么不用原始 MP3

`QSoundEffect` 只吃未压缩 WAV。MP3 走 `QMediaPlayer` 的启动延迟是 100~300ms，
而"拍中"这种事件等不起 —— 手落下去的声音晚 200ms 就完全不搭了。

## 解码这个 MP3 踩到的坑（很反直觉，记下来）

给 `QAudioDecoder` 设 **22050Hz 单声道** 输出格式时，`bufferReady` 永远不触发、
`finished` 也永远不来，脚本就挂在那里等（本工具第一版就是这么卡死的）。
同样的文件改成请求 **44100Hz 立体声**，0.1 秒就跑完。

**所以流程是：解码拿 44100 立体声 → 自己下混成单声道 → 隔点抽成 22050。**
不要图省事把采样率交给 Qt 转换，那条路上什么都不发生，而且不报错。

另外这个 MP3 带一张 300×300 封面（ID3 APIC），日志里会刷一堆
`av1_mf / h264_mf / could not find any MFT` —— 那是 ffmpeg 在枚举视频
解码器，不是错误，也不影响音频。**剥掉 ID3 并不会让它变快**（试过），
所以文件保持原样读。

## 三个变体怎么来的

源素材 1.43 秒，包络实测（20ms 窗）：

- 0.00~0.11  -28~-19dB  起音，很轻但不是静音，**必须保留**（切掉就成了"从半句开始"）
- 0.12~1.21  -10dB 左右  主体，能量最扎实的一段
- 1.22~1.26  快速衰减到 -32dB
- 1.30~1.37  又抬到 -20dB  尾音/混响，比主体低 10dB
- 1.38 之后   -33~-39dB  只剩房间尾音

切点取 **0.00 ~ 1.38**：把尾音那段留住（它让"ろ"听起来是说完的），
再往后就只剩底噪。淡出 100ms 而不是淡出到 1.43，是为了不把尾巴上的
数字静音也读进来。

多杀时用升调变体（1.12× / 1.25× 变速＝升 2 / 4 个半音）：
音高不同才不会叠成一个更响的同一声，听感上是"几只一起喊"。
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SR = 22050
#: 源素材优先用项目里归档的那一份（27KB，clone 下来就能重跑）；
#: 没有才回落到最初那个桌面文件
SRC_CANDIDATES = (
    ROOT / "assets" / "sounds" / "yameroooo.mp3",
    Path("C:/Users/wangben/Desktop/yameroooo.mp3"),
)
OUT_DIR = ROOT / "assets" / "sounds"

# 裁切窗口（秒）。依据见模块文档的包络实测。
TAKE_START = 0.0
TAKE_END = 1.38

# (文件名, 变速比率, 淡出秒数)
VARIANTS = (
    ("yamero_1.wav", 1.00, 0.100),
    ("yamero_2.wav", 1.12, 0.060),
    ("yamero_3.wav", 1.25, 0.045),
)

FADE_IN = 0.003
PEAK = 0.72          # ≈ -2.8dBFS，给同帧的 splat 留叠加余量


def decode_mp3(path: Path) -> list[int]:
    """解码成 22050Hz 单声道 16bit 样本。

    必须请求 44100 立体声 —— 详见模块文档"解码踩到的坑"。
    """
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    app = None
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    except Exception:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841

    fmt = QAudioFormat()
    fmt.setSampleRate(44100)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

    dec = QAudioDecoder()
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(path)))

    chunks: list[bytes] = []

    def on_ready() -> None:
        while dec.bufferAvailable():
            buf = dec.read()
            if buf.isValid():
                chunks.append(bytes(buf.constData()[: buf.byteCount()]))

    dec.bufferReady.connect(on_ready)
    loop = QEventLoop()
    dec.finished.connect(loop.quit)
    error: list[str] = []
    dec.error.connect(lambda e: (error.append(dec.errorString()), loop.quit()))
    QTimer.singleShot(30_000, loop.quit)
    dec.start()
    loop.exec()

    if error or not chunks:
        raise RuntimeError(f"解码失败: {error or '没有拿到任何数据'}")

    raw = b"".join(chunks)
    stereo = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
    mono = [(stereo[i] + stereo[i + 1]) // 2 for i in range(0, len(stereo) - 1, 2)]
    return mono[::2]      # 44100 → 22050


def resample(seg: list[float], ratio: float) -> list[float]:
    """线性插值重采样：ratio > 1 同时缩短时长并升调。"""
    n = max(1, int(len(seg) / ratio))
    out = [0.0] * n
    last = len(seg) - 1
    for i in range(n):
        pos = i * ratio
        i0 = int(pos)
        frac = pos - i0
        a = seg[min(i0, last)]
        b = seg[min(i0 + 1, last)]
        out[i] = a + (b - a) * frac
    return out


def shape(samples: list[float], fade_out: float) -> list[float]:
    n = len(samples)
    fi = max(1, int(FADE_IN * SR))
    fo = max(1, int(fade_out * SR))
    out = list(samples)
    for i in range(min(fi, n)):
        out[i] *= i / fi
    for i in range(min(fo, n)):
        out[n - 1 - i] *= i / fo
    return out


def write(path: Path, samples: list[float], peak: float) -> None:
    scale = peak / max(1e-6, max(abs(s) for s in samples)) if samples else 0.0
    payload = b"".join(
        struct.pack("<h", max(-32767, min(32767, int(s * scale * 32767)))) for s in samples
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(payload)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="", help="源音频；留空则按 SRC_CANDIDATES 顺序找")
    args = ap.parse_args()

    if args.src:
        src_path = Path(args.src)
    else:
        src_path = next((p for p in SRC_CANDIDATES if p.exists()), SRC_CANDIDATES[0])
    if not src_path.exists():
        raise SystemExit(f"缺少素材 {src_path}")

    samples = decode_mp3(src_path)
    total = len(samples) / SR
    print(f"源: {src_path.name}  {total:.3f}s  {SR}Hz 峰值 {max(abs(v) for v in samples) / 32768:.3f}")

    a = int(TAKE_START * SR)
    b = min(len(samples), int(TAKE_END * SR))
    seg = [s / 32768.0 for s in samples[a:b]]
    print(f"裁切 {TAKE_START:.2f}~{TAKE_END:.2f}s → {len(seg) / SR:.3f}s")

    for name, ratio, fade_out in VARIANTS:
        out = resample(seg, ratio)
        out = shape(out, fade_out)
        path = OUT_DIR / name
        write(path, out, PEAK)
        print(
            f"  {name}  变速 {ratio:.2f}×  → {len(out) / SR:.3f}s  "
            f"({path.stat().st_size} B)"
        )

    print(f"完成，共 {len(VARIANTS)} 个变体")


if __name__ == "__main__":
    main()
