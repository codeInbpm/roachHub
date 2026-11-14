"""roachHub - 全面蟑螂模拟器 启动入口。"""

from __future__ import annotations
import sys
import signal
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.app import RoachApp


def setup_logging(debug: bool = False) -> None:
    """初始化日志格式与等级。"""
    level = logging.DEBUG if debug else logging.INFO
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    logging.basicConfig(
        level=level,
        format=format_str,
        datefmt="%H:%M:%S",
    )


def main() -> int:
    # 允许 Ctrl+C 中断程序
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # 启用高 DPI 缩放适配
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("roachHub")
    app.setApplicationDisplayName("roachHub - 桌面蟑螂模拟器")
    # 关闭最后一个窗口时不直接退出应用（因为有托盘驻留）
    app.setQuitOnLastWindowClosed(False)

    setup_logging(debug="--debug" in sys.argv)

    roach_app = RoachApp(app)
    roach_app.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
