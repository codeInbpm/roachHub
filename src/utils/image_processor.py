"""图像处理与透明通道精灵图生成工具。"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps, ImageFilter

logger = logging.getLogger("roachHub.image_processor")


def make_transparent_sprite(
    input_path: str | Path,
    output_path: str | Path,
    threshold: int = 240,
    feather_radius: int = 1,
    target_max_size: int = 512,
) -> bool:
    """将带有纯净/浅色背景的写实蟑螂图转为带 Alpha 透明通道的高清 PNG 精灵图。

    :param input_path: 原图路径
    :param output_path: 输出 PNG 路径
    :param threshold: 背景白色阈值
    :param feather_radius: 边缘柔化羽化半径
    :param target_max_size: 缩放最大限制
    """
    try:
        inp = Path(input_path)
        outp = Path(output_path)
        if not inp.exists():
            logger.error("原图不存在: %s", inp)
            return False

        img = Image.open(inp).convert("RGBA")

        # 尺寸限制，加速渲染与内存节省
        if max(img.size) > target_max_size:
            ratio = target_max_size / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 提取 RGB 与计算与背景色的距离 (以四角取样背景参考色)
        pixels = img.load()
        w, h = img.size

        # 采样四个角的背景色彩
        corners = [pixels[0, 0], pixels[w - 1, 0], pixels[0, h - 1], pixels[w - 1, h - 1]]
        bg_r = sum(c[0] for c in corners) / 4.0
        bg_g = sum(c[1] for c in corners) / 4.0
        bg_b = sum(c[2] for c in corners) / 4.0

        # 构建遮罩
        mask = Image.new("L", (w, h), 255)
        mask_pixels = mask.load()

        for y in range(h):
            for x in range(w):
                r, g, b, _ = pixels[x, y]
                # 计算与背景色的欧式色差
                dist = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
                # 若颜色极接近背景，或总体亮度超过阈值且色差很小
                is_bright = (r > threshold and g > threshold and b > threshold)
                if dist < 28 or (is_bright and dist < 45):
                    mask_pixels[x, y] = 0
                elif dist < 50:
                    # 渐变半透明边缘，保留纤细触须与刚毛倒刺
                    alpha = int(((dist - 28) / (50 - 28)) * 255)
                    mask_pixels[x, y] = alpha

        # 柔化边缘
        if feather_radius > 0:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_radius * 0.5))

        img.putalpha(mask)

        # 裁剪掉多余的完全透明边界
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)

        outp.parent.mkdir(parents=True, exist_ok=True)
        img.save(outp, format="PNG")
        logger.info("成功生成高画质透明精灵图: %s (尺寸 %dx%d)", outp, img.width, img.height)
        return True
    except Exception as e:
        logger.error("处理精灵图异常: %s", e)
        return False
