"""roachHub - 全面蟑螂模拟器 启动入口。

启动方式（Windows）：
    python main.py            控制台会被自动藏起来（只留托盘图标）
    python main.py --debug    保留控制台并输出 DEBUG 日志
    python main.py --keep-console  保留控制台（日常看日志用）
    python main.py --allow-multiple 允许开多个实例（默认单实例）
"""

from __future__ import annotations
import logging
import logging.handlers
import sys
import signal
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.app import RoachApp
from src.resources import ResourceManager
from src.utils.console_window import hide_own_console
from src.utils.single_instance import SingleInstanceGuard

APP_USER_MODEL_ID = "roachHub.DesktopSimulator"


def setup_logging(debug: bool = False, log_file: bool = False) -> None:
    """初始化日志格式与等级。

    两种情况下必须落盘：控制台被藏起来了、或者用 `pythonw.exe` 跑（`sys.stderr is None`，
    往它写日志会变成一堆 "Lost sys.stderr" 噪音）—— 少了日志，出问题就无从查起。
    """
    level = logging.DEBUG if debug else logging.INFO
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    if log_file or not handlers:
        log_dir = Path("logs")
        try:
            log_dir.mkdir(exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    log_dir / "roachHub.log",
                    maxBytes=1_000_000,
                    backupCount=2,
                    encoding="utf-8",
                )
            )
        except OSError:
            pass
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S", handlers=handlers)


def _set_app_user_model_id() -> None:
    """让任务栏把我们当成独立应用（而不是 python.exe / pythonw.exe）。

    不设的话，窗口图标虽然是我们自己的，但分组、跳转列表、通知都挂在 Python 名下。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def main() -> int:
    # 允许 Ctrl+C 中断程序
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    debug = "--debug" in sys.argv
    keep_console = debug or "--keep-console" in sys.argv

    # 先藏控制台再建窗口：免得任务栏上先闪一下那个黑色的 python 图标。
    # 只有"这个控制台只挂着我们一个进程"时才会真的藏（从 cmd 启动时不能藏人家的终端）。
    console_hidden = False if keep_console else hide_own_console()

    # 启用高 DPI 缩放适配
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    _set_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName("roachHub")
    app.setApplicationDisplayName("roachHub - 桌面蟑螂模拟器")
    app.setWindowIcon(ResourceManager.get_app_icon())
    # 关闭最后一个窗口时不直接退出应用（因为有托盘驻留）
    app.setQuitOnLastWindowClosed(False)

    setup_logging(debug=debug, log_file=console_hidden or debug)

    # 单实例：第二个实例只负责把已有实例的控制台叫出来，然后自己退出。
    # 否则托盘上会出现两个一样的图标，两个全屏覆盖层还会互相抢点击。
    guard = SingleInstanceGuard()
    allow_multi = "--allow-multiple" in sys.argv
    if not allow_multi and not guard.try_acquire():
        # 这个实例的控制台已经藏了，再弹出来只会闪一下又消失；
        # 第一个实例收到唤醒后会把控制台召唤到前台，那本身就是反馈。
        print("roachHub 已在运行 —— 已把它的控制台叫到前台。")
        return 0

    roach_app = RoachApp(app)
    if not allow_multi:
        guard.activated.connect(roach_app.summon_console)
    roach_app.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
