"""生成 `tools/out/squash_voice_preview.html`：三个变体的试听页 + 真实时序演示。

    QT_QPA_PLATFORM=offscreen python tools/squash_preview.py

为什么需要它：音效这种东西，波形和参数都对不代表好听 —— 只有耳朵能判断。
本页内嵌 WAV / MP3（base64），双击就能在浏览器里逐个听，还能听"啪叽 + yamero"
按真实时序（25ms 延迟、双杀错峰 469ms）叠起来的效果，不用先启动整个程序再拍一只。
"""

from __future__ import annotations

import base64
import json
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "out" / "squash_voice_preview.html"

# (文件, 标题, 参数说明, 用途)
VARIANTS = [
    ("yamero_1.wav", "变体 1 · 原调（1.38s）", "变速 1.00×", "单杀主力"),
    ("yamero_2.wav", "变体 2 · +2 半音（1.23s）", "变速 1.12×", "双杀的第二声"),
    ("yamero_3.wav", "变体 3 · +4 半音（1.10s）", "变速 1.25×", "三杀以上的第二声"),
]
#: 原始素材（对照用，直接把 mp3 内嵌进去 —— 28KB，比一张图还小）
SOURCE_CANDIDATES = (
    ROOT / "assets" / "sounds" / "yameroooo.mp3",
    Path("C:/Users/wangben/Desktop/yameroooo.mp3"),
)
SOURCE_LABEL = "原始素材 · yameroooo.mp3（完整 1.43s）"


def read_wav(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    data = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    return (data[::ch] if ch > 1 else data), sr


def peaks(samples: list[int], buckets: int = 220) -> list[float]:
    """归一化成 -1~1 的峰值包络，给 canvas 画波形用。"""
    if not samples:
        return []
    step = max(1, len(samples) // buckets)
    out = []
    for i in range(0, len(samples) - step + 1, step):
        seg = samples[i : i + step]
        out.append(max(abs(v) for v in seg) / 32768.0)
    top = max(out) if out else 1.0
    return [round(v / (top or 1.0), 3) for v in out]


def wav_data_uri(path: Path) -> str:
    return "data:audio/wav;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def mp3_data_uri(path: Path) -> str:
    return "data:audio/mpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def build() -> str:
    cards = []
    meta = {}
    for name, title, params, usage in VARIANTS:
        path = ROOT / "assets" / "sounds" / name
        samples, sr = read_wav(path)
        dur = len(samples) / sr
        uri = wav_data_uri(path)
        meta[name] = peaks(samples)
        cards.append(
            f"""<div class="card">
  <div class="head"><span class="name">{title}</span><span class="meta">{dur:.2f}s</span></div>
  <canvas class="wave" data-key="{name}" width="520" height="54"></canvas>
  <audio controls preload="auto" src="{uri}"></audio>
  <p class="note">{params} · <b>{usage}</b></p>
</div>"""
        )

    # 原始素材：有 mp3 就内嵌，没有就只留一句说明（别人 clone 下来不会有这个文件）
    source_mp3 = next((p for p in SOURCE_CANDIDATES if p.exists()), None)
    if source_mp3 is not None:
        src_block = f"""<div class="src">
  <div class="head"><span class="name">{SOURCE_LABEL}</span><span class="meta">对照用</span></div>
  <canvas class="wave" data-key="source" width="520" height="54"></canvas>
  <audio controls preload="auto" src="{mp3_data_uri(source_mp3)}"></audio>
  <p class="note">听 1.22s 之后那 150ms 的尾音 —— 它在主体后面低了 10dB，
  但正是它让「ろ」听起来是说完了的。切到 1.22s 收尾会像被掐断。</p>
</div>"""
        try:
            sys_s, sys_sr = read_wav(ROOT / "assets" / "sounds" / "yamero_1.wav")
            meta["source"] = peaks(sys_s)  # 近似波形（同源、只是没变速）
        except Exception:
            meta["source"] = meta.get("yamero_1.wav", [])
    else:
        src_block = ""

    return f"""<!doctype html>
<meta charset="utf-8">
<title>拍死音效：yamero</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; padding:26px 30px 40px; background:#f7f7f5; color:#24252b;
         font:14px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; }}
  h1 {{ font-size:19px; margin:0 0 4px; font-weight:600; }}
  .sub {{ color:#6f727d; font-size:12.5px; margin:0 0 20px; }}
  .lead {{ background:#fff; border:1px solid #e2e1dc; border-radius:10px;
           padding:14px 16px; margin:0 0 22px; font-size:13.5px; }}
  .lead b {{ color:#8a4a12; }}
  .grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
  .card {{ background:#fff; border:1px solid #e2e1dc; border-radius:10px; padding:13px 15px; }}
  .head {{ display:flex; justify-content:space-between; align-items:baseline; gap:10px; }}
  .name {{ font-weight:600; }}
  .meta {{ font-size:12px; color:#6f727d; font-variant-numeric:tabular-nums; }}
  canvas.wave {{ display:block; width:100%; height:54px; margin:8px 0 2px; }}
  audio {{ width:100%; height:32px; margin:4px 0 6px; }}
  .note {{ margin:0; font-size:12px; color:#6f727d; }}
  .src {{ background:#fff; border:1px solid #e2e1dc; border-radius:10px;
          padding:13px 15px; margin:0 0 20px; }}
  .demo {{ background:#1c2430; color:#e8ecf4; border-radius:10px; padding:16px 18px;
           margin:22px 0 0; }}
  .demo h2 {{ font-size:14px; margin:0 0 6px; font-weight:600; }}
  .demo p {{ margin:0 0 12px; font-size:12.5px; color:#9aa6b8; }}
  button {{ font:inherit; padding:8px 16px; border-radius:8px; border:1px solid #d6d4cd;
            background:#fff; color:#24252b; cursor:pointer; }}
  button:hover {{ background:#f0efe9; }}
  .demo button {{ border:0; background:#e8a33d; color:#241a08; font-weight:600; }}
  .demo button:hover {{ background:#f0b356; }}
  code {{ background:#efeee9; padding:1px 5px; border-radius:4px; font-size:12px; }}
  .demo code {{ background:#2b3646; color:#cfd8e6; }}
  .warn {{ background:#fff6e8; border:1px solid #f0d9b5; border-radius:10px;
           padding:13px 16px; margin:22px 0 0; font-size:13px; }}
</style>
<h1>拍死音效：yamero「やめろ / 住手」</h1>
<p class="sub">由外部素材 <code>yameroooo.mp3</code> 派生 —— 拍下去的那一下，它喊你住手。</p>

<div class="lead">
  <b>处理链</b>：解码 44100 立体声 → 下混单声道 → 抽成 22050 → 取 0.00~1.38s
  → 变速（1.00 / 1.12 / 1.25×，＝原调 / +2 / +4 半音）→ 3ms 淡入 + 45~100ms 淡出
  → 归一化 -2.8dB。<br>
  <b>没有</b>高通、<b>没有</b>软削波 —— 这两步是给"被压扁的哎哟"用的。
  完整的人声本来就该是干净的，加了失真反而像玩具。
</div>

{src_block}

<div class="grid">{"".join(cards)}</div>

<div class="demo">
  <h2>真实时序演示：啪叽 + yamero</h2>
  <p><code>splat</code> 在 0ms，人声在 <b>+25ms</b> —— 先接触、后出声。
     双杀时第二声错开 <b>469ms</b>（＝句长的 1/3），不是原来给 0.3 秒素材写的 80ms。</p>
  <button id="play-demo">▶ 拍死一只</button>
  <button id="play-double" style="margin-left:8px">▶ 一次拍死两只</button>
</div>

<div class="warn">
  <b>为什么一声要 1.38 秒还没被砍短</b>：砍到 0.9 秒就得从中段切，那句话会变成
  「めろ」或者半句「やめ」。完整一句才是这个梗的价值 —— 所以改的是<b>调度</b>而不是素材：
  1 秒内最多 2 声、双杀错峰 469ms、同变体连发换空闲实例（不把上一声从中间掐断）。
</div>

<script>
const WAVES = {json.dumps(meta, ensure_ascii=False)};
const BUF = {{}};
// 变体卡片的顺序固定是 1/2/3，直接按下标取（源素材块在最前面，不影响这里）
const cards = [...document.querySelectorAll('.card audio')];
const URIS = {{ v1: cards[0].src, v2: cards[1].src, v3: cards[2].src }};

for (const c of document.querySelectorAll('canvas.wave')) {{
  const key = c.dataset.key, data = WAVES[key] || [];
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  c.width = c.clientWidth * dpr; c.height = 54 * dpr;
  ctx.scale(dpr, dpr);
  const w = c.clientWidth, h = 54, mid = h / 2;
  ctx.strokeStyle = '#e6e4dd'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
  ctx.strokeStyle = '#c2712a'; ctx.lineWidth = 1.2;
  ctx.beginPath();
  data.forEach((v, i) => {{
    const x = (i / (data.length - 1 || 1)) * w;
    const a = Math.max(0.6, v * (h / 2 - 2));
    if (i === 0) ctx.moveTo(x, mid - a); else ctx.lineTo(x, mid - a);
  }});
  ctx.stroke();
  ctx.beginPath();
  data.forEach((v, i) => {{
    const x = (i / (data.length - 1 || 1)) * w;
    const a = Math.max(0.6, v * (h / 2 - 2));
    if (i === 0) ctx.moveTo(x, mid + a); else ctx.lineTo(x, mid + a);
  }});
  ctx.stroke();
}}

let AC = null;
async function ctx() {{
  AC = AC || new (window.AudioContext || window.webkitAudioContext)();
  if (AC.state === 'suspended') await AC.resume();
  return AC;
}}
async function load(uri) {{
  if (BUF[uri]) return BUF[uri];
  const ab = await (await fetch(uri)).arrayBuffer();
  BUF[uri] = await (await ctx()).decodeAudioData(ab);
  return BUF[uri];
}}
function splash(ac, at) {{
  // 近似的「啪叽」：噪声爆发 + 95Hz 低频撞击
  const n = Math.floor(ac.sampleRate * 0.18);
  const buf = ac.createBuffer(1, n, ac.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < n; i++) {{
    const p = i / n;
    d[i] = (Math.random() * 2 - 1) * Math.exp(-p * 14) * 0.55
         + Math.sin(2 * Math.PI * 95 * i / ac.sampleRate) * Math.exp(-p * 16) * 0.45;
  }}
  const src = ac.createBufferSource(); src.buffer = buf;
  const g = ac.createGain(); g.gain.value = 0.85;
  src.connect(g).connect(ac.destination); src.start(at);
}}
async function voice(ac, uri, at, gain) {{
  const buf = await load(uri);
  const src = ac.createBufferSource(); src.buffer = buf;
  const g = ac.createGain(); g.gain.value = gain;
  src.connect(g).connect(ac.destination); src.start(at);
}}
document.getElementById('play-demo').onclick = async () => {{
  const ac = await ctx(); const t = ac.currentTime + 0.05;
  splash(ac, t);
  voice(ac, URIS.v1, t + 0.025, 0.75);
}};
document.getElementById('play-double').onclick = async () => {{
  const ac = await ctx(); const t = ac.currentTime + 0.05;
  splash(ac, t);
  voice(ac, URIS.v1, t + 0.025, 0.79);
  voice(ac, URIS.v2, t + 0.025 + 0.469, 0.79);
}};
</script>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"写出 {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
