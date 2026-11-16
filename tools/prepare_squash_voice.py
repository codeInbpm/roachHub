"""从 `assets/sounds/grab.wav` 派生「被拍死」的短促惨叫（3 个变体）。

    QT_QPA_PLATFORM=offscreen python tools/prepare_squash_voice.py

## 为什么是"同一句人声的死刑版"

抓取时播的是完整一句「你干嘛~哎呦」（3.45 秒，还能抗议）；
被拍死时只剩**半声「哎哟」**（0.25~0.45 秒，音调被顶上去了）。

好处有三条，都不是为了省事：

1. **零新素材**。同源派生，没有版权问题，也不用再找配音。
2. **叙事闭环**。抓的时候它说得完整，是因为只是被拎起来；拍死时只出得来半声，
   是因为胸腔已经被压扁了 —— 玩家会自己补上这层因果。
3. **音色一致**。听起来就是同一只虫，而不是"又换了个音效库"。

## 处理链（6 步，顺序不能换）

    取「哎」的起音 ──→ 变速升调 ──→ 高通 320Hz ──→ 软削波 ──→ 掐尾 ──→ 归一化

1. **裁切窗口 1.80~2.30/2.42/2.55s**：`grab.wav` 的包络实测（20ms 窗）：
   1.82~1.96 是「哎」的起音短音，1.98~2.02 有个 -28dB 的字间谷，2.04 起进长音。
   起音必须从 1.80 开始，晚 20ms 就会把辅音吃掉，听起来变成"啊"而不是"哎"。
2. **变速 = 升调**（重采样，不是 time-stretch）：比率 1.70/1.85/2.00。
   纯重采样会让时长与音高同时变化，这里恰好是想要的 —— **又短又尖**，
   正是"气被挤出来"的听感。用 time-stretch 保时长反而会像正常说话被加速。
3. **高通 240Hz**：去掉胸腔共鸣。**不能设到 320Hz 以上** —— 源素材基频只有
   197Hz，一刀切在 320 会把基频压掉 7dB，自相关置信度从 0.56 掉到 0.24，
   听感从"哎哟"退化成"滋"（只剩谐波，认不出字）。240Hz 在基频之上一点点，
   既削掉了闷的胸腔音，又保住了"哎"的可辨识度。
4. **软削波 tanh(drive=1.7)**：模拟挤压失真，同时把动态压平，短促音更"冲"。
   drive 给到 2.2 会把元音削成方波、听感变电子味，1.7 是个能保住词的甜点。
5. **掐尾**：淡出只给 6ms（正常素材是 120ms）。
   这是整个音效的灵魂 —— **它必须听起来是被切断的**，而不是自己说完的。
6. **归一化到 -2.8dB**：给同帧的 `splat`（拍面撞击声）留出叠加余量。

## 三个变体的分工

| 文件 | 窗口 | 比率 | 时长 | 用途 |
| --- | --- | --- | --- | --- |
| `squash_1.wav` | 1.80~2.55 | 1.70 | ≈0.44s | 单杀的主力 |
| `squash_2.wav` | 1.80~2.42 | 1.85 | ≈0.34s | 双杀的第二声 |
| `squash_3.wav` | 1.80~2.30 | 2.00 | ≈0.25s | 三杀以上的"齐叫" |
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SR = 22050
SRC = ROOT / "assets" / "sounds" / "grab.wav"
OUT_DIR = ROOT / "assets" / "sounds"

# 起音必须从 1.80 开始，见模块文档第 1 条
_TAKE_START = 1.80
_VARIANTS = (
    ("squash_1.wav", _TAKE_START, 0.75, 1.70),
    ("squash_2.wav", _TAKE_START, 0.62, 1.85),
    ("squash_3.wav", _TAKE_START, 0.50, 2.00),
)

HIGHPASS_HZ = 240.0
DRIVE = 1.7
FADE_IN = 0.003
FADE_OUT = 0.006
PEAK = 0.72          # ≈ -2.8dBFS


def read_wav(path: Path) -> list[int]:
    """读 16bit PCM。素材本来就是 22050Hz 单声道，不需要再走解码器。"""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name} 不是 16bit PCM")
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
        rate = w.getframerate()
    if rate != SR:
        raise ValueError(f"{path.name} 采样率是 {rate}，期望 {SR}")
    data = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    return list(data[::ch]) if ch > 1 else list(data)


def resample(seg: list[float], ratio: float) -> list[float]:
    """线性插值重采样：ratio > 1 同时缩短时长并升调。"""
    n = max(1, int(len(seg) / ratio))
    out = [0.0] * n
    for i in range(n):
        pos = i * ratio
        i0 = int(pos)
        frac = pos - i0
        a = seg[i0]
        b = seg[i0 + 1] if i0 + 1 < len(seg) else a
        out[i] = a + (b - a) * frac
    return out


def highpass(samples: list[float], fc: float) -> list[float]:
    """一阶 IIR 高通：y[n] = a·(y[n-1] + x[n] - x[n-1])。"""
    dt = 1.0 / SR
    rc = 1.0 / (2.0 * math.pi * fc)
    a = rc / (rc + dt)
    out = [0.0] * len(samples)
    prev_x = 0.0
    prev_y = 0.0
    for i, x in enumerate(samples):
        y = a * (prev_y + x - prev_x)
        out[i] = y
        prev_x, prev_y = x, y
    return out


def soft_clip(samples: list[float], drive: float) -> list[float]:
    """tanh 软削波；除以 tanh(drive) 保持归一化前的电平关系。"""
    norm = math.tanh(drive)
    return [math.tanh(drive * x) / norm for x in samples]


def shape(samples: list[float]) -> list[float]:
    n = len(samples)
    fi = max(1, int(FADE_IN * SR))
    fo = max(1, int(FADE_OUT * SR))
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
    if not SRC.exists():
        raise SystemExit(f"缺少素材 {SRC}；先跑 tools/prepare_voice.py")

    src = read_wav(SRC)
    print(f"源: {SRC.name}  {len(src) / SR:.2f}s  {SR}Hz")

    for name, start, length, ratio in _VARIANTS:
        a = int(start * SR)
        b = min(len(src), a + int(length * SR))
        seg = [s / 32768.0 for s in src[a:b]]

        out = resample(seg, ratio)
        out = highpass(out, HIGHPASS_HZ)
        out = soft_clip(out, DRIVE)
        out = shape(out)
        path = OUT_DIR / name
        write(path, out, PEAK)
        print(
            f"  {name}  取 {start:.2f}s 起 {length:.2f}s → 比率 {ratio:.2f} "
            f"→ 输出 {len(out) / SR:.3f}s  ({path.stat().st_size} B)"
        )

    print(f"完成，共 {len(_VARIANTS)} 个变体")


if __name__ == "__main__":
    main()
