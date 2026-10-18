from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame

from config.defaults import APP_VERSION

# 状态名 -> (圆点颜色, 状态文字颜色)
_STATUS_COLOR = {
    '初始化中': ('#94A3B8', '#1E293B'),
    '待机':     ('#94A3B8', '#1E293B'),
    '模型加载中': ('#94A3B8', '#1E293B'),  # 启动后台预加载中，尚未开扫
    '就绪':     ('#2BA471', '#1E293B'),    # 预加载完成，点开始即可秒开
    '模型加载失败': ('#D32F2F', '#1E293B'),
    '运行中':   ('#2BA471', '#1E293B'),
    '已停止':   ('#94A3B8', '#1E293B'),  # 用户主动停止 — 灰
    '异常停止': ('#D32F2F', '#1E293B'),  # 崩溃停止 — 强红
    '已暂停':   ('#E0A82E', '#1E293B'),
}


class _StatusDot(QWidget):
    """8px 实心圆点 + 3px 同色透明 halo（mockup .status-dot 的 box-shadow 等价物）。
    QSS 不支持 box-shadow，所以用 paintEvent 自绘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor('#94A3B8')

    def set_color(self, color_hex):
        self._color = QColor(color_hex)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # halo（外圈，0.18 alpha）
        halo = QColor(self._color)
        halo.setAlphaF(0.18)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(0, 0, 16, 16)
        # 内圈实心 8px
        p.setBrush(self._color)
        p.drawEllipse(4, 4, 8, 8)
        p.end()


def _get_memory_mb():
    """纯 Win32 ctypes 读 WorkingSetSize，不依赖 psutil。
    必须正确声明 HANDLE 的 restype，否则 64 位 Python 上伪句柄 -1 会被截断。"""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        handle = kernel32.GetCurrentProcess()

        # Win 7+ 直接走 kernel32.K32GetProcessMemoryInfo
        K32 = getattr(kernel32, 'K32GetProcessMemoryInfo', None)
        if K32 is not None:
            K32.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            K32.restype = wintypes.BOOL
            if K32(handle, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / (1024 * 1024)

        # 回退到 psapi.dll
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024 * 1024)
    except Exception:
        pass
    return None


class StatusBar(QWidget):
    """4 字段：运行状态 / 内存 / 版本 / 引擎。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 子类 QWidget 必须显式 styled-background 才会真画 QSS 给的渐变背景
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName('statusBar')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        # 圆点（自绘 8px 实心 + 3px halo）
        self.dot = _StatusDot()

        # "运行状态：" 标签 + 状态值（值用 statusValue 加粗，颜色随状态变）
        self.lbl_status_prefix = QLabel('运行状态：')
        self.lbl_status_value = QLabel('初始化中')
        self.lbl_status_value.setObjectName('statusValue')

        self.lbl_mem = QLabel('内存占用：--')
        self.lbl_mem_value = QLabel('-- MB')
        self.lbl_mem_value.setObjectName('statusValue')

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setObjectName('statusSep')
        sep.setFixedHeight(14)

        self.lbl_version = QLabel(f'版本：{APP_VERSION}')
        self.lbl_engine = QLabel('引擎：PaddleOCR')

        # 左侧组：dot + 运行状态 / 内存
        layout.addWidget(self.dot)
        layout.addSpacing(6)
        layout.addWidget(self.lbl_status_prefix)
        layout.addWidget(self.lbl_status_value)
        layout.addSpacing(18)
        layout.addWidget(self.lbl_mem)
        layout.addWidget(self.lbl_mem_value)
        layout.addStretch(1)
        # 右侧组：版本 | 引擎
        layout.addWidget(self.lbl_version)
        layout.addSpacing(12)
        layout.addWidget(sep)
        layout.addSpacing(12)
        layout.addWidget(self.lbl_engine)

        self.set_status('待机')

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_memory)
        self._timer.start(5000)
        self._refresh_memory()

    @Slot()
    def _refresh_memory(self):
        mb = _get_memory_mb()
        if mb is not None:
            self.lbl_mem.setText('内存占用：')
            self.lbl_mem_value.setText(f'{mb:.1f} MB')

    @Slot(str)
    def set_status(self, text):
        # 文字颜色由 QSS #statusValue 统一管；这里只切自绘圆点的颜色（QSS 不能进 paintEvent）
        dot_color, _ = _STATUS_COLOR.get(text, ('#94A3B8', '#1E293B'))
        self.dot.set_color(dot_color)
        self.lbl_status_value.setText(text)
