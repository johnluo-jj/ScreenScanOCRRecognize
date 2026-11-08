"""单实例控制：GUI 已在运行时，第二次启动只把已有主窗口唤到前台，然后自身退出。

启动顺序（见 app.py）：

1. ``InstanceLock`` —— Windows 命名互斥体。进程存活期间持有，OS 在进程退出/崩溃时
   自动释放，没有锁文件残留问题。它必须在 ``start_preload()`` 之前构造，保证用户
   快速双击两次时只有一个进程继续初始化。
2. 后来者 ``notify_running_instance()`` —— 通过 QLocalSocket 给已有实例发激活消息，
   带超时；发完（或超时）就退出。
3. 首个实例在显示窗口前创建 ``SingleInstanceGuard``，监听同名 QLocalServer；任何
   连接都视为“显示主窗口”请求。

注意：notify 只在“确实已有实例”的路径上调用，那时 import QtNetwork 的 ~0.2s 只
影响第二次启动的退出延迟，不会拖慢首个实例（首个实例仍是先 start_preload 再
import PySide6/ui）。
"""
import sys

SERVER_NAME = 'ScreenScanOCRRecognize.gui.v1'
_ACTIVATE = b'activate'
_MUTEX_NAME = 'Local\\ScreenScanOCRRecognize.gui.v1'
_ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    """跨进程单实例锁（Windows 命名互斥体）。

    ``already_running`` 为 True 表示已有实例持有同名互斥体；此时本进程应退出。
    非 Windows 平台不做单实例限制（本项目部署平台为 Windows）。
    """

    def __init__(self):
        self.already_running = False
        self._handle = None
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            self._handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            self.already_running = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
        except Exception:
            # 拿不到锁时宁可多开，也不要阻止正常启动
            self._handle = None


def notify_running_instance(timeout_ms=1000):
    """通知已在运行的实例显示主窗口。连不上（还在启动 / 已异常）返回 False。

    使用 QLocalSocket 而不是直接 open 命名管道：已连接但服务端未处理的管道上
    写入可能阻塞，这里用 waitFor* 的显式超时保证第二次启动不会卡死。
    """
    if sys.platform != 'win32':
        return False
    _allow_set_foreground_for_peer()
    try:
        from PySide6.QtNetwork import QLocalSocket
    except Exception:
        return False
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(_ACTIVATE)
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def _allow_set_foreground_for_peer():
    """把本进程的前台权限让渡给已在运行的实例。

    否则运行中的进程调用 SetForegroundWindow 会被 Windows 前台锁拒绝，窗口只能
    在任务栏闪烁。本进程由用户双击 gui.bat 触发，通常持有前台权限。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint32]
        user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
    except Exception:
        pass


class SingleInstanceGuard:
    """首个实例持有它：监听命名管道，收到连接就调用 on_activate。"""

    def __init__(self, on_activate):
        from PySide6.QtNetwork import QLocalServer
        self._on_activate = on_activate
        self._server = QLocalServer()
        # 只允许同一用户连接，避免其它账户/进程误触发
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)

    def listen(self):
        return self._server.listen(SERVER_NAME)

    def error_string(self):
        return self._server.errorString()

    def close(self):
        self._server.close()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            # 管道名不公开，能连上就说明是 notify_running_instance()，无需校验内容
            self._on_activate()
