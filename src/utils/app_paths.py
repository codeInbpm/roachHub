"""应用数据目录（配置 / 日志）的唯一出口。

**为什么不能再让各模块自己算路径**

冻结（PyInstaller）之后 `Path(__file__).parent.parent` 指向 `sys._MEIPASS`：

| 形态                        | `_MEIPASS` 在哪                                  | 能写吗 |
| --------------------------- | ------------------------------------------------ | ------ |
| Windows 便携目录            | exe 旁边（spec 里 `contents_directory="."`）      | 能，**这就是我们要的**：绿色版配置就在 exe 边上 |
| Windows 单文件              | `%TEMP%\\_MEIxxxx`，退出即删                     | 写进去 = 每次启动都是默认配置 |
| macOS `.app`                | `Contents/Frameworks`（onedir）或临时解包目录     | 写进去会**破坏代码签名**，公证过的包直接拒启 |

所以 mac 上一律落到用户目录（`~/Library/...`），Windows 保持"配置跟着 exe 走"
的绿色版习惯，开发期则落回项目根目录 —— 三条路径在这里一次说清，别处不许再算。
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("roachHub.paths")


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源根：`_MEIPASS`（冻结）或项目根目录（开发）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    # 本文件在 <root>/src/utils/app_paths.py
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """可写目录：配置落在这里。

    * macOS 冻结：`~/Library/Application Support/roachHub`（沙盒/签名友好）
    * 其它（含 Windows 冻结）：`bundle_dir()` —— 便携目录就是 exe 旁边
    """
    if frozen() and sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "roachHub"
    return bundle_dir()


def log_dir() -> Path:
    """日志目录。

    冻结前一直是 `工作目录/logs`，问题是"工作目录"由启动方式决定：Finder 里
    双击 `.app` 时它是 `/`（只读），从快捷方式启动又可能是 exe 目录。所以冻结后
    不再看 CWD，改成确定的位置。
    """
    if frozen():
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Logs" / "roachHub"
        return bundle_dir() / "logs"
    return bundle_dir() / "logs"


def writable_log_dir() -> Path:
    """`log_dir()` 不可写时的兜底（Program Files、只读 U 盘等）。

    日志是排查"打不开/没反应"的唯一线索，宁可落到临时目录，也不能一声不响地没有。
    """
    candidates = [log_dir()]
    candidates.append(Path(tempfile.gettempdir()) / "roachHub" / "logs")
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".writable"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except OSError:
            continue
    return Path(tempfile.gettempdir())
