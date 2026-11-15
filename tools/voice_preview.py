"""生成 `tools/out/voice_preview.html`：波形 + 分段标记 + 三个可试听的裁切版本。

    QT_QPA_PLATFORM=offscreen python tools/voice_preview.py

为什么需要它：裁切点是用基频猜的，人耳一听就知道对不对，但我听不见。
把三个候选窗口都编码成内嵌 WAV，点开网页就能逐个听，不需要来回改命令行。
"""

from __future__ import annotations

import base64
import io
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.prepare_voice import SR, decode_mono, slice_and_shape  # noqa: E402

OUT = ROOT / "tools" / "out" / "voice_preview.html"
VIEW_SECONDS = 4.2

# (标签, 起, 时长, 说明)
CANDIDATES = [
    ("完整：你干嘛 + 哎呦", 0.17, 3.45, "当前默认。覆盖到 3.62s 的「呦」收尾，再用 120ms 淡出"),
    ("只有「你干嘛」", 0.17, 1.80, "第一版（错误）的切法：1.97s 之后全丢，听不到「哎呦」"),
    ("只有「哎呦」", 1.95, 1.70, "2.22~3.18s 的长音 + 3.21~3.62s 的下行尾巴"),
]

# 浊音段（来自 tools/voice_sections.py）
SECTIONS = [
    (0.18, 0.29, "你"),
    (0.32, 2.12, "你干嘛"),
    (2.22, 3.18, "哎——"),
    (3.21, 3.62, "呦"),
]


def wav_base64(samples: list[float], peak: float = 0.92) -> str:
    scale = peak / max(1e-6, max(abs(s) for s in samples))
    payload = b"".join(
        int(s * scale * 32767).to_bytes(2, "little", signed=True) for s in samples
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(payload)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def peak_envelope(samples: list[int], bucket: int) -> list[list[int]]:
    """每 bucket 个样本取 min/max —— 画波形只关心轮廓，几万个点塞不进画布。"""
    out: list[list[int]] = []
    for i in range(0, len(samples) - bucket, bucket):
        seg = samples[i : i + bucket]
        out.append([min(seg), max(seg)])
    return out


def build_html(env: list[list[int]], cuts: list[tuple[str, float, str, str]]) -> str:
    env_json = json.dumps(env)
    cards = []
    for label, secs, b64, note in cuts:
        cards.append(
            f"""
      <div class="card">
        <div class="head">
          <span class="name">{label}</span>
          <span class="meta">{secs:.2f}s</span>
        </div>
        <audio controls preload="none" src="data:audio/wav;base64,{b64}"></audio>
        <p class="note">{note}</p>
      </div>"""
        )

    bands = "".join(
        f'<div class="band" style="left:{a / VIEW_SECONDS * 100:.2f}%;'
        f'width:{(b - a) / VIEW_SECONDS * 100:.2f}%"><span>{t}</span></div>'
        for a, b, t in SECTIONS
    )
    ticks = "".join(
        f'<div class="tick" style="left:{i / VIEW_SECONDS * 100:.2f}%"><span>{i}s</span></div>'
        for i in range(int(VIEW_SECONDS) + 1)
    )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>抓取音效裁切对比</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:28px 32px 40px; background:#12131a; color:#e8e9f0;
         font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; }}
  h1 {{ font-size:18px; margin:0 0 4px; font-weight:600; letter-spacing:.3px; }}
  .sub {{ color:#8f90a0; font-size:12.5px; margin:0 0 22px; }}
  .wave-wrap {{ position:relative; height:150px; background:#191a23;
                border:1px solid #262838; border-radius:10px; overflow:hidden; }}
  #wave {{ display:block; width:100%; height:100%; }}
  .band {{ position:absolute; top:0; bottom:22px; background:#3d7bff22;
           border-left:1px solid #3d7bff66; border-right:1px solid #3d7bff66; }}
  .band span {{ position:absolute; top:4px; left:5px; font-size:11px; color:#8fb4ff;
                white-space:nowrap; }}
  .tick {{ position:absolute; bottom:0; height:22px; border-left:1px solid #2c2e40;
           font-size:10px; color:#6d6f80; }}
  .tick span {{ position:absolute; top:3px; left:3px; }}
  .legend {{ display:flex; gap:18px; flex-wrap:wrap; margin:10px 0 26px;
             font-size:12px; color:#8f90a0; }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:2px;
          background:#3d7bff66; margin-right:6px; vertical-align:middle; }}
  .grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); }}
  .card {{ background:#191a23; border:1px solid #262838; border-radius:10px; padding:14px 16px; }}
  .head {{ display:flex; justify-content:space-between; align-items:baseline; gap:10px; }}
  .name {{ font-weight:600; }}
  .meta {{ font-size:12px; color:#8f90a0; font-variant-numeric:tabular-nums; }}
  audio {{ width:100%; margin:10px 0 6px; height:34px; }}
  .note {{ margin:0; font-size:12px; color:#8f90a0; }}
  code {{ background:#22232f; padding:1px 5px; border-radius:4px; font-size:12px; }}
</style>
<h1>抓取音效：裁到哪儿才对</h1>
<p class="sub">上排是源文件 0~4.2 秒的波形与浊音段；下面三个版本直接点播放试听。当前默认是第一个。</p>

<div class="wave-wrap">
  <canvas id="wave"></canvas>
  {bands}
  {ticks}
</div>
<div class="legend">
  <span><i class="dot"></i>蓝色区间 = 基频检测出的浊音段（真实发声的字）</span>
  <span>0~0.17s 的前导噪声已裁掉，3.90s 之后全是静音</span>
</div>

<div class="grid">{"".join(cards)}</div>

<p class="sub" style="margin-top:24px">
  想改窗口不必编辑代码：<code>python tools/prepare_voice.py --start 0.17 --length 3.45</code>
</p>

<script>
const env = {env_json};
const cv = document.getElementById('wave');
function draw() {{
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const mid = h * 0.5, amp = h * 0.42;
  ctx.strokeStyle = '#5b8cff';
  ctx.lineWidth = 1;
  const step = w / env.length;
  for (let i = 0; i < env.length; i++) {{
    const [lo, hi] = env[i];
    const x = i * step + step * 0.5;
    const y1 = mid - (hi / 32768) * amp;
    const y2 = mid - (lo / 32768) * amp;
    ctx.beginPath();
    ctx.moveTo(x, Math.min(y1, y2));
    ctx.lineTo(x, Math.max(y1, y2) + 0.6);
    ctx.stroke();
  }}
  ctx.strokeStyle = '#2c2e40';
  ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
}}
draw();
window.addEventListener('resize', draw);
</script>
"""


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    samples, _ = decode_mono(ROOT / "assets" / "sounds" / "grab.mp3")
    samples = samples[: int(VIEW_SECONDS * SR)]
    env = peak_envelope(samples, int(0.004 * SR))  # 4ms 一柱

    cuts = []
    for label, start, length, note in CANDIDATES:
        clip = slice_and_shape(samples, start, length, 0.012, 0.12)
        cuts.append((label, length, wav_base64(clip), note))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(env, cuts), encoding="utf-8")
    print(f"输出 {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.0f} KB")
    for label, length, _b64, _note in cuts:
        print(f"  · {label}  {length:.2f}s")


if __name__ == "__main__":
    main()
