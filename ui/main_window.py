import logging
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QSystemTrayIcon,
)

from config.config import config
from utils.hotkey import HotkeyManager
from .log_bridge import LogBridge
from .overlay import Overlay
from .pages.about_page import AboutPage
from .pages.scan_page import ScanPage
from .pages.settings_page import SettingsPage
from .roi_border import ROIBorder
from .scan_worker import ScanWorker
from .tray import TrayIcon
from .widgets.config_panel import RESELECT_TOKEN, CUSTOM_TOKEN
from .widgets.sidebar import Sidebar

# _resolve_roi 用的哨兵：用户在屏幕 picker 上 Esc / 右键取消 → 不开扫
_PICKER_CANCELLED = object()


class MainWindow(QMainWindow):
    # 热键回调跑在 keyboard 库的线程里，靠 Signal 跨线程转主线程（queued connection）
    _hotkey_roi_toggled = Signal()
    _hotkey_fullscreen_toggled = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('屏幕扫描 OCR 识别系统')
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        outer_v = QVBoxLayout(central)
        outer_v.setContentsMargins(0, 0, 0, 0)
        outer_v.setSpacing(0)

        # 原生标题栏在浅色主题下为白色，与白色工作区融合。在标题栏正下方画一条
        # 1px 灰横线作为视觉分隔（颜色/粗细与 sidebar 右侧那条竖线统一）。
        title_sep = QFrame()
        title_sep.setObjectName('titleBarSeparator')
        title_sep.setFixedHeight(1)
        outer_v.addWidget(title_sep)

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.setFixedWidth(64)  # 与 mockup 一致
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.scan_page = ScanPage()
        self.settings_page = SettingsPage()
        self.about_page = AboutPage()
        self.stack.addWidget(self.scan_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.about_page)
        layout.addWidget(self.stack, 1)

        outer_v.addWidget(body, 1)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

        # 日志：logging.getLogger -> LogBridge -> LogPanel
        self.log_bridge = LogBridge()
        logging.getLogger().addHandler(self.log_bridge)
        logging.getLogger().setLevel(logging.INFO)
        self.log_bridge.record_emitted.connect(self.scan_page.log_panel.append)

        # 系统托盘（仅在系统支持时启用，否则关闭按钮就走原生退出）
        self._real_quit = False
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = TrayIcon(self)
            self.tray.show()

        # 屏幕浮窗（命中提示用）
        self.overlay = Overlay(config=config)
        # ROI 红框指示窗（扫描中显示在 ROI 区域上方）
        self.roi_border = ROIBorder()

        # 后台扫描线程
        self.worker = ScanWorker(self)
        self.worker.status_changed.connect(self._on_status_changed)
        self.worker.result_ready.connect(self.overlay.update)
        # 配置面板的启停按钮 → worker
        self.scan_page.config_panel.start_clicked.connect(self._on_start)
        self.scan_page.config_panel.stop_clicked.connect(self._on_stop)

        # 本轮扫描是否 ROI 模式（_on_start / 热键切换时更新），热键据此判断「同键停 / 异键切」
        self._scan_use_roi = bool(config.get('scan.enable_roi'))

        # 全局热键：Ctrl+Alt+1 ROI 模式开关 / Ctrl+Alt+2 全屏模式开关（同键再按即停止）。
        # 回调在库自己线程里跑，通过 Signal queued connection 中转到 Qt 主线程，
        # 避免直接跨线程操作 widget。
        self._hotkey_roi_toggled.connect(lambda: self._on_hotkey_toggle(use_roi=True))
        self._hotkey_fullscreen_toggled.connect(lambda: self._on_hotkey_toggle(use_roi=False))
        self.hotkey = HotkeyManager()
        self.hotkey.register('ctrl+alt+1', self._hotkey_roi_toggled.emit, 'ROI 扫描开关')
        self.hotkey.register('ctrl+alt+2', self._hotkey_fullscreen_toggled.emit, '全屏扫描开关')

        # startup_mode='auto'：UI 渲染稳定后自动开扫（延 200ms 让事件循环先转一圈）
        if (config.get('app.startup_mode') or 'paused') == 'auto':
            QTimer.singleShot(200, self._on_start)

    # ---------- 扫描启停 ----------

    def _resolve_roi(self):
        """按 last_roi_choice 决定本次启动用的 ROI。

        返回：
        - tuple (x1,y1,x2,y2) — 正常 ROI
        - None — 全屏（enable_roi=False / 预设找不到 / roi_rect 不合法）
        - _PICKER_CANCELLED — 用户在 picker 里取消，调用方应中止启动
        """
        if not bool(config.get('scan.enable_roi')):
            return None

        last = config.get('scan.last_roi_choice') or RESELECT_TOKEN

        if last == RESELECT_TOKEN:
            # 弹屏幕框选 UI；确认后写 roi_rect、切下拉到「自定义区域」
            from .roi_picker import ROIPicker
            rect = ROIPicker().pick()
            if rect is None:
                return _PICKER_CANCELLED
            config.set('scan.roi_rect', list(rect))
            config.set('scan.last_roi_choice', CUSTOM_TOKEN)
            config.save()
            self.scan_page.config_panel.refresh_roi_combo()
            return rect

        if last == CUSTOM_TOKEN:
            roi = config.get('scan.roi_rect')
            return tuple(roi) if isinstance(roi, list) and len(roi) == 4 else None

        # 命名预设：取预设坐标并同步到 roi_rect（让「保存当前」等其它逻辑也能取到）
        presets = config.get('scan.roi_presets') or {}
        if last in presets:
            rect = tuple(presets[last])
            config.set('scan.roi_rect', list(rect))
            config.save()
            return rect

        # 预设被删了或名字不对，fallback 全屏
        return None

    def _on_start(self):
        if self.worker.isRunning():
            return
        # picker 与主窗口共享主屏，会被主窗口挡住，所以先最小化让屏幕空出来再弹
        will_show_picker = (
            bool(config.get('scan.enable_roi'))
            and (config.get('scan.last_roi_choice') or RESELECT_TOKEN) == RESELECT_TOKEN
        )
        if will_show_picker:
            self._minimize_for_scan()
            QApplication.processEvents()  # 让最小化先落地再弹 picker
        roi = self._resolve_roi()
        if roi is _PICKER_CANCELLED:
            # 取消框选：之前为 picker 最小化过的话要恢复，否则窗口凭空消失
            if will_show_picker:
                self.showNormal()
                self.activateWindow()
                self.raise_()
            return
        self._scan_use_roi = bool(config.get('scan.enable_roi'))
        self.overlay.clear_session()  # 新一轮扫描，清掉上一轮累计
        self.roi_border.show_for(roi)  # ROI 红框（roi=None 则不画）
        self.worker.start_scan(roi=roi)
        # picker 路径已经最小化过，这里跳过；其它路径正常最小化
        if not will_show_picker:
            self._minimize_for_scan()

    def _minimize_for_scan(self):
        """开扫最小化：托盘可用且配置允许就缩托盘，否则退化为最小化到任务栏。"""
        if self.tray is not None and bool(config.get('app.minimize_to_tray')):
            self.hide()
        else:
            self.showMinimized()

    def _on_stop(self):
        self.worker.stop_scan()
        self.overlay.hide()
        self.roi_border.hide_border()

    # ---------- 热键：ROI / 全屏 两个模式开关 ----------

    def _on_hotkey_toggle(self, use_roi):
        """热键开关：
        - 未在扫：切到对应模式并开扫
        - 正以同一模式在扫：停止
        - 正以另一模式在扫：不停线程，直接切换扫描区域
        """
        if self.worker.is_stopping():
            logging.info('扫描正在停止中，请稍后再按热键')
            return
        if not self.worker.isRunning():
            self._set_roi_mode(use_roi)
            self._on_start()
            return
        if self._scan_use_roi == use_roi:
            self._on_stop()
            return
        self._switch_mode(use_roi)

    def _set_roi_mode(self, use_roi):
        """写 scan.enable_roi 并同步配置面板勾选框。
        capture 每帧都会按 enable_roi 二次校验，所以模式必须落到配置里。"""
        config.set('scan.enable_roi', use_roi)
        config.save()
        self.scan_page.config_panel.set_roi_enabled(use_roi)

    def _switch_mode(self, use_roi):
        """扫描中切换 ROI ↔ 全屏：复用已加载的 OCR，只换扫描区域。"""
        prev = self._scan_use_roi
        self._set_roi_mode(use_roi)
        roi = self._resolve_roi()
        if roi is _PICKER_CANCELLED:
            # 取消框选：回到原模式继续扫
            self._set_roi_mode(prev)
            return
        self._scan_use_roi = use_roi
        self.overlay.clear_session()  # 换了区域，上一区域的累计命中不再沿用
        self.roi_border.show_for(roi)  # roi=None 时自动收起红框
        self.worker.set_roi(roi)
        logging.info(f'已切换为{"ROI" if use_roi and roi is not None else "全屏"}扫描')

    def _on_status_changed(self, text):
        """worker 三态：'初始化中'/'运行中'/'已停止'。
        - 初始化中 / 运行中：start 禁、stop 启
        - 已停止：start 启、stop 禁
        - 状态栏文字也跟着切（颜色由 StatusBar 内 _STATUS_COLOR 表决定）"""
        self.scan_page.status_bar.set_status(text)
        running = text in ('初始化中', '运行中')
        self.scan_page.config_panel.set_running(running)

    def request_real_quit(self):
        """托盘 → 退出菜单调用，让随后的 closeEvent 不再拦截缩托盘。"""
        self._real_quit = True

    def closeEvent(self, event):
        """关闭按钮：若 app.minimize_to_tray=True 且托盘可用，则缩进托盘不退出。
        真正退出路径上清理 worker + 热键 + overlay。"""
        if (
            not self._real_quit
            and self.tray is not None
            and bool(config.get('app.minimize_to_tray'))
        ):
            event.ignore()
            self.hide()
            return
        # 真正退出
        try:
            self.hotkey.unregister_all()
        except Exception:
            pass
        try:
            if self.worker.isRunning():
                self.worker.stop_scan()
                self.worker.wait(2000)  # 给 worker 最多 2s 收尾
        except Exception:
            pass
        try:
            self.overlay.destroy()
        except Exception:
            pass
        try:
            self.roi_border.hide_border()
        except Exception:
            pass
        super().closeEvent(event)
