"""食物贴图缓存：照片质感 + 极低内存/CPU 的关键一层。

设计目标（三件事同时满足）：

1. **高保真**：素材是 256x256 的真实照片感 PNG，缩到 20~40 像素显示，
   细节远比程序化 QPainterPath 丰富。
2. **不吃内存**：原图只在首次用到时解码一次，立刻缩到"该种类最大显示尺寸 ×
   devicePixelRatio"，然后**丢弃原图**。缓存键是 ``(sprite, 像素尺寸)``，
   一个种类通常只有一条，20 种全加载约 150~250 KB。
3. **不吃 CPU**：绘制退化成一次 ``drawPixmap(target_rect, pixmap)``。
   原来是每块食物每帧几十次 ``drawPath`` + 逐点三角函数，现在是一次位图
   缩放 blit，而且缩放由 Qt 的 SIMD 路径做。

惰性加载：没投放到桌面上的种类一次都不会读盘。读失败的种类记进
``_MISSING``，不会每帧重试。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

_SPRITE_DIR = Path(__file__).resolve().parents[2] / "assets" / "sprites" / "food"

# (sprite, 像素边长) -> QPixmap
_CACHE: dict[tuple[str, int], QPixmap] = {}
_MISSING: set[str] = set()

# 缓存条目上限。正常情况下 20 种 × 1 条，留足余量防 DPR 切换时爆掉。
_CACHE_LIMIT = 96


def available(sprite: str) -> bool:
    """贴图是否存在（不加载）。给测试和 fallback 判断用。"""
    return sprite not in _MISSING and (_SPRITE_DIR / f"{sprite}.png").exists()


def pixmap_for(sprite: str, target_px: int, dpr: float = 1.0) -> QPixmap | None:
    """取一张缩放到目标像素尺寸的贴图；没有就返回 None（调用方降级绘制）。

    :param sprite: 贴图文件名（不含扩展名）
    :param target_px: 目标**物理**像素边长
    :param dpr: devicePixelRatio，写回 pixmap 让 Qt 按逻辑尺寸摆放
    """
    if not sprite or sprite in _MISSING:
        return None

    px = max(8, min(512, int(target_px)))
    key = (sprite, px)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    src = QPixmap(str(_SPRITE_DIR / f"{sprite}.png"))
    if src.isNull():
        _MISSING.add(sprite)
        return None

    if src.width() != px or src.height() != px:
        src = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    src.setDevicePixelRatio(dpr)

    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = src
    return src


def cache_stats() -> dict[str, int]:
    """给验收脚本和性能探针看：条目数 / 总像素 / 估算内存（KB）。"""
    entries = len(_CACHE)
    pixels = sum(p.width() * p.height() for p in _CACHE.values())
    return {
        "entries": entries,
        "pixels": pixels,
        "kb": pixels * 4 // 1024,
        "missing": len(_MISSING),
    }


def clear_cache() -> None:
    _CACHE.clear()
    _MISSING.clear()
