"""双击启动入口 —— 只有托盘图标，不会出现黑色控制台窗口。

Windows 把 `.pyw` 关联到 `pythonw.exe`（无控制台解释器），所以双击这个文件
比双击 `main.py` 更干净。`main.py` 里也做了自动隐藏控制台的处理，两条路都行。

想要日志：在命令行里跑 `python main.py --keep-console`。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 双击运行时 cwd 不一定是项目目录，显式加一下，避免 import src 失败
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import main  # noqa: E402  (要在 sys.path 调整之后)

if __name__ == "__main__":
    sys.exit(main())
