"""将生成的写实图片转为透明背景 PNG 精灵图。"""

from pathlib import Path
import numpy as np
from PIL import Image

def remove_background_floodfill(
    input_path: Path,
    output_path: Path,
    target_max_size: int = 512,
    tolerance: int = 24,
) -> None:
    img = Image.open(input_path).convert("RGB")
    
    # 适当缩放保证性能
    if max(img.size) > target_max_size:
        ratio = target_max_size / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)

    arr = np.array(img, dtype=np.float32)
    h, w, _ = arr.shape

    # 采样四个边缘区域的平均背景色
    edge_pixels = np.concatenate([
        arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]
    ], axis=0)
    bg_color = np.median(edge_pixels, axis=0)

    # 计算每个像素与背景的色差距离
    diff = np.linalg.norm(arr - bg_color, axis=2)

    # 初始背景掩码: 距离小于容差
    is_bg = diff < tolerance

    # 利用双端队列或种子填充，只把与图像四边连通的背景像素标为透明，保护内部区域
    from collections import deque
    mask_transparent = np.zeros((h, w), dtype=bool)
    queue = deque()

    # 将四周边缘且是背景的像素加入种子队列
    for x in range(w):
        if is_bg[0, x]:
            mask_transparent[0, x] = True
            queue.append((0, x))
        if is_bg[h - 1, x]:
            mask_transparent[h - 1, x] = True
            queue.append((h - 1, x))
    for y in range(h):
        if is_bg[y, 0]:
            mask_transparent[y, 0] = True
            queue.append((y, 0))
        if is_bg[y, w - 1]:
            mask_transparent[y, w - 1] = True
            queue.append((y, w - 1))

    # BFS 广度优先漫水填充
    neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    while queue:
        cy, cx = queue.popleft()
        for dy, dx in neighbors:
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w:
                if not mask_transparent[ny, nx] and is_bg[ny, nx]:
                    mask_transparent[ny, nx] = True
                    queue.append((ny, nx))

    # 生成 Alpha 通道
    alpha = np.ones((h, w), dtype=np.uint8) * 255
    # 完全透明部分
    alpha[mask_transparent] = 0

    # 边缘微过渡 (羽化 1 像素，消除硬锯齿)
    rgba = np.dstack((arr.astype(np.uint8), alpha))
    out_img = Image.fromarray(rgba, mode="RGBA")

    # 裁剪透明空白边缘
    bbox = out_img.getbbox()
    if bbox:
        out_img = out_img.crop(bbox)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(output_path, format="PNG")
    print(f"Sprite 转换完成: {output_path.name} ({out_img.width}x{out_img.height})")


def main():
    brain_dir = Path(r"C:\Users\wangben\.gemini\antigravity\brain\6ef6643c-0896-4c25-8db4-2acc12089f78")
    dest_dir = Path(__file__).resolve().parent / "assets" / "sprites"

    mappings = [
        ("cockroach_adult_*.jpg", "cockroach_adult.png", 26),
        ("cockroach_nymph_*.jpg", "cockroach_nymph.png", 26),
        ("cockroach_ootheca_*.jpg", "ootheca.png", 24),
        ("cockroach_molted_white_*.jpg", "cockroach_white.png", 20),
    ]

    for pattern, out_name, tol in mappings:
        matches = list(brain_dir.glob(pattern))
        if matches:
            src = sorted(matches)[-1]
            dst = dest_dir / out_name
            remove_background_floodfill(src, dst, target_max_size=380, tolerance=tol)
        else:
            print(f"未找到匹配图像: {pattern}")


if __name__ == "__main__":
    main()
