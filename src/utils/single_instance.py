"""单实例守卫：第二次启动不再叠一个新托盘图标，而是把已有实例的控制台叫出来。

**为什么需要它**

这个程序的形态是"托盘常驻 + 全屏置顶覆盖层"。一旦同时跑两个实例：

1. 托盘区会出现**两个一模一样的蟑螂图标**，用户点到的可能是旧实例的；
2. 两个覆盖层都压在桌面上，**点击被谁吃掉取决于谁在上层** —— 表现就是
   "点了没反应 / 右下角卡住"；
3. 两个实例各自维护一套生态，托盘上的存活数还对不上。

微信也是这个做法：重复启动只会把已在运行的那个窗口叫到面前。

**为什么用 QLockFile + 命名管道两件套**

- 占有标志用 `QLockFile`：它把 PID 写进锁文件，进程被杀掉后下一次启动能识别出
  "持有者已死"并接管，不会留下一个永远打不开的死锁。
- **不能**用 `QLocalServer.listen()` 的成败当占有标志：Windows 上同名管道允许
  同时监听多个实例（实测第二个进程 `listen()` 依然返回成功），那样守卫等于没做。
- 唤醒通道用命名管道：第二个实例连上去发一句 `show`，第一个实例收到就召唤控制台。
"""

from __future__ import annotations

import logging
import os
import tempfile
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("roachHub.single_instance")

DEFAULT_KEY = "roachHub-single-instance"
WAKE_MESSAGE = b"show\n"


class SingleInstanceGuard(QObject):
    """单实例守卫。

    `try_acquire()` 返回 True 表示"我是唯一实例"；返回 False 表示已有实例在跑，
    并且已经尽力通知对方"把控制台叫出来"。
    """

    activated = Signal()

    def __init__(self, key: str = DEFAULT_KEY, lock_path: str | None = None) -> None:
        super().__init__()
        self._key = key
        self._lock_path = lock_path or os.path.join(tempfile.gettempdir(), f"{key}.lock")
        self._lock = None
        self._server: QLocalServer | None = None

    # ------------------------------------------------------------ 占有

    def try_acquire(self) -> bool:
        from PySide6.QtCore import QLockFile

        lock = QLockFile(self._lock_path)
        # 只按 PID 判活，不按时间判陈旧：否则跑了一小时的实例会被当成"残留"抢走
        lock.setStaleLockTime(0)

        if lock.tryLock(0):
            self._lock = lock
            self._start_server()
            return True

        # 锁在别人手里：可能真的在跑，也可能它刚启动还没建好唤醒通道
        for _ in range(4):
            if self._wake_existing():
                return False
            time.sleep(0.15)
        logger.info("检测到已有实例（唤醒通道未响应），按已在运行处理")
        return False

    # ------------------------------------------------------------ 唤醒

    def _start_server(self) -> None:
        server = QLocalServer(self)
        if not server.listen(self._key):
            logger.warning("唤醒通道创建失败（不影响运行）: %s", server.errorString())
            return
        self._server = server
        server.newConnection.connect(self._on_new_connection)

    def _wake_existing(self) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self._key)
        if not sock.waitForConnected(400):
            return False
        sock.write(WAKE_MESSAGE)
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        logger.info("已有实例在运行，已请求它弹出控制台")
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                continue
            conn.waitForReadyRead(200)
            data = bytes(conn.readAll())
            conn.disconnectFromServer()
            if WAKE_MESSAGE.strip() in data or not data:
                logger.info("收到二次启动请求，召唤控制台")
                self.activated.emit()

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
