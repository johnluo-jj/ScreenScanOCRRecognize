"""系统托盘图标。
- 左键单击：显示并激活主窗口
- 右键菜单：显示主窗口 / 退出
- 关闭主窗口时（若 app.minimize_to_tray=True）由 MainWindow.closeEvent 拦截缩进托盘

主图标优先从 ui/icons/app.ico 加载（多分辨率 ICO，16/24/32/48/64/128/256）；缺失时回退到
SVG 代码绘制。app / 任务栏 / 托盘统一调 make_app_icon。
"""
import os

from PySide6.QtCore import Qt, QByteArray, QRectF, QPointF
from PySide6.QtGui import QPainter, QPixmap, QIcon, QAction, QColor, QLinearGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication


_ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons', 'app.ico')

# 与 docs/mockups/light_ui_prototype.html 的 .app-icon 一致：四角取景框 + 中央扫描线
_ICON_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
    stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 7V5a2 2 0 0 1 2-2h2"/>
    <path d="M17 3h2a2 2 0 0 1 2 2v2"/>
    <path d="M21 17v2a2 2 0 0 1-2 2h-2"/>
    <path d="M7 21H5a2 2 0 0 1-2-2v-2"/>
    <line x1="3" y1="12" x2="21" y2="12"/></svg>'''


def make_app_icon(size=32):
    """优先用 app.ico（Qt 自动按需选档）；不存在时按 size 代码绘制兜底。"""
    if os.path.exists(_ICON_FILE):
        icon = QIcon(_ICON_FILE)
        if not icon.isNull():
            return icon
    return _draw_fallback_icon(size)


def _draw_fallback_icon(size):
    """135deg 蓝渐变圆角 + 白色 scan frame。与 ICO 设计同源。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    grad.setColorAt(0.0, QColor('#4F8EF7'))
    grad.setColorAt(1.0, QColor('#2F6FEB'))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.25, size * 0.25)
    inner = int(size * 0.7)
    off = (size - inner) // 2
    renderer = QSvgRenderer(QByteArray(_ICON_SVG))
    renderer.render(p, QRectF(off, off, inner, inner))
    p.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    """托盘图标 + 右键菜单 + 左键激活。"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._main_window = main_window
        self.setIcon(make_app_icon(32))
        self.setToolTip('屏幕扫描 OCR 识别系统')

        menu = QMenu()
        act_show = QAction('显示主窗口', menu)
        act_show.triggered.connect(self._on_show)
        menu.addAction(act_show)
        menu.addSeparator()
        act_quit = QAction('退出', menu)
        act_quit.triggered.connect(self._on_quit)
        menu.addAction(act_quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        # 左键单击 / 双击都视为"显示主窗口"
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._on_show()

    def _on_show(self):
        self._main_window.show_and_activate()

    def _on_quit(self):
        # 通知 MainWindow 走真正退出路径，让 closeEvent 不再拦截缩托盘
        if hasattr(self._main_window, 'request_real_quit'):
            self._main_window.request_real_quit()
        QApplication.instance().quit()
