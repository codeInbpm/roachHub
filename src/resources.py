"""资源与贴图资产管理器。"""

from __future__ import annotations
from pathlib import Path
import logging
from typing import Optional, Dict

from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtCore import Qt, QPointF

logger = logging.getLogger("roachHub.resources")


class ResourceManager:
    """管理图像、图标与高清精灵图贴图资产。"""

    _tray_icon: Optional[QIcon] = None
    _sprites: Dict[str, Optional[QPixmap]] = {}

    @classmethod
    def get_assets_dir(cls) -> Path:
        return Path(__file__).resolve().parent.parent / "assets"

    @classmethod
    def get_sprites_dir(cls) -> Path:
        return cls.get_assets_dir() / "sprites"

    @classmethod
    def get_tray_icon(cls) -> QIcon:
        """获取系统托盘图标。"""
        if cls._tray_icon is not None:
            return cls._tray_icon

        icon_file = cls.get_assets_dir() / "icons" / "tray_icon.png"
        if icon_file.exists():
            cls._tray_icon = QIcon(str(icon_file))
            return cls._tray_icon

        # 动态绘制托盘图标
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        body_color = QColor(100, 45, 18)
        outline_color = QColor(40, 15, 8)

        pen_legs = QPen(outline_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_legs)

        painter.drawLine(24, 26, 10, 18)
        painter.drawLine(20, 32, 8, 32)
        painter.drawLine(22, 40, 10, 48)

        painter.drawLine(40, 26, 54, 18)
        painter.drawLine(44, 32, 56, 32)
        painter.drawLine(42, 40, 54, 48)

        pen_antennae = QPen(QColor(130, 60, 25), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_antennae)
        painter.drawLine(30, 20, 14, 4)
        painter.drawLine(34, 20, 50, 4)

        painter.setPen(QPen(outline_color, 2))
        painter.setBrush(QBrush(body_color))
        painter.drawEllipse(24, 20, 16, 30)

        painter.setBrush(QBrush(QColor(70, 25, 10)))
        painter.drawEllipse(27, 14, 10, 10)

        painter.end()

        cls._tray_icon = QIcon(pixmap)
        return cls._tray_icon

    @classmethod
    def get_sprite(cls, sprite_name: str) -> Optional[QPixmap]:
        """获取指定名称的高清透明精灵图贴图 (如 'cockroach_adult', 'cockroach_nymph', 'ootheca')。"""
        if sprite_name in cls._sprites:
            return cls._sprites[sprite_name]

        sprite_file = cls.get_sprites_dir() / f"{sprite_name}.png"
        if sprite_file.exists():
            pix = QPixmap(str(sprite_file))
            if not pix.isNull():
                logger.info("已成功装载写实精灵贴图: %s (尺寸: %dx%d)", sprite_name, pix.width(), pix.height())
                cls._sprites[sprite_name] = pix
                return pix

        cls._sprites[sprite_name] = None
        return None

    @classmethod
    def reload_sprites(cls) -> None:
        """清空缓存，重新扫描贴图。"""
        cls._sprites.clear()
