"""设置页：5 张卡 — 常规 / 扫描配置 / 浮窗提示 / 热键 / 配置管理。
布局对照 docs/mockups/settings_window.png。所有控件双向绑定 config 单例；
每张卡用 SettingsCard 容器，行用 SettingsRow 拼装。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider,
    QScrollArea, QFrame, QMessageBox, QSizePolicy,
)

from config.config import config
from ..widgets.settings_card import (
    SwitchToggle, SettingsCard, SettingsRow, HintRow, HotkeyDisplay, make_reset_button,
)

# 启动后默认状态选项
_STARTUP_MODES = [
    ('paused', '暂停扫描'),
    ('auto',   '自动开扫'),
]
# 浮窗位置选项
_OVERLAY_POSITIONS = [
    ('center',       '居中'),
    ('top',          '顶部'),
    ('bottom',       '底部'),
    ('top-left',     '左上'),
    ('top-right',    '右上'),
    ('bottom-left',  '左下'),
    ('bottom-right', '右下'),
]


def _slider(min_v, max_v, value, on_change, scale=1, fmt='{:.1f}'):
    """slider + 右侧值盒子。返回 QFrame 容器（用 QFrame 而非 QWidget，
    后者不在全局透明列表里会画 #F1F5FA 灰底）。"""
    s = QSlider(Qt.Horizontal)
    s.setMinimum(int(min_v * scale))
    s.setMaximum(int(max_v * scale))
    s.setValue(int(value * scale))
    s.setMinimumHeight(22)
    box = QLabel(fmt.format(value))
    box.setObjectName('sliderValue')
    box.setAlignment(Qt.AlignCenter)
    box.setFixedSize(56, 28)
    s.valueChanged.connect(lambda v: (box.setText(fmt.format(v / scale)), on_change(v / scale)))
    container = QFrame()
    h = QHBoxLayout(container)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    h.addWidget(s, 1)
    h.addWidget(box)
    return container


def _combo(items, current_value, on_change):
    """items: [(value, display), ...]；current_value 决定初始选中。"""
    cb = QComboBox()
    for v, d in items:
        cb.addItem(d, v)
    idx = cb.findData(current_value)
    if idx >= 0:
        cb.setCurrentIndex(idx)
    cb.currentIndexChanged.connect(lambda _: on_change(cb.currentData()))
    return cb


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName('settingsPage')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 整页可滚（卡多了高度可能超过窗口）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName('settingsContent')
        scroll.setWidget(content)

        # 让卡片列动态占内容区一半宽度（随窗口缩放）：
        # - inner 设 QSizePolicy.Ignored，强制忽略自身 sizeHint
        # - inner + 右侧 stretch 各取 stretch=1，于是按 1:1 严格 50/50 切分可用宽度
        # 页面背景仍是 #settingsPage 满宽；只是把卡片们集中在左半边、右半边留白。
        outer_h = QHBoxLayout(content)
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_h.setSpacing(0)

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(18)

        v.addWidget(self._build_general_card())
        v.addWidget(self._build_scan_card())
        v.addWidget(self._build_overlay_card())
        v.addWidget(self._build_hotkey_card())
        v.addWidget(self._build_reset_card())
        v.addStretch(1)

        outer_h.addWidget(inner, 1)
        outer_h.addStretch(1)

    # -------- 常规设置 --------
    def _build_general_card(self):
        card = SettingsCard('常规设置')

        sw_tray = SwitchToggle(checked=bool(config.get('app.minimize_to_tray')))
        sw_tray.toggled.connect(
            lambda v: (config.set('app.minimize_to_tray', v), config.save())
        )
        card.add_row(SettingsRow('最小化到托盘', sw_tray, with_separator=False))

        cb_mode = _combo(
            _STARTUP_MODES,
            config.get('app.startup_mode') or 'paused',
            lambda v: (config.set('app.startup_mode', v), config.save()),
        )
        cb_mode.setMinimumWidth(140)
        card.add_row(SettingsRow('启动后默认状态', cb_mode))
        return card

    # -------- 扫描配置 --------
    def _build_scan_card(self):
        card = SettingsCard('扫描配置')
        _v = config.get('scan.diff_threshold')
        slider_box = _slider(
            0.0, 20.0,
            float(_v) if _v is not None else 5.0,
            lambda v: (config.set('scan.diff_threshold', round(v, 1)), config.save_debounced()),
            scale=10,
        )
        card.add_row(SettingsRow('帧差阈值', slider_box,
                                 with_separator=False, expand_control=True))
        card.add_row(HintRow('0 = 每次都 OCR；越大跳过越多帧'))
        return card

    # -------- 浮窗提示 --------
    def _build_overlay_card(self):
        card = SettingsCard('浮窗提示')

        font_box = _slider(
            10, 36,
            int(config.get('matching.font_size') or 18),
            lambda v: (config.set('matching.font_size', int(v)), config.save_debounced()),
            scale=1, fmt='{:.0f}',
        )
        card.add_row(SettingsRow('字号', font_box,
                                 with_separator=False, expand_control=True))

        cb_pos = _combo(
            _OVERLAY_POSITIONS,
            config.get('matching.position') or 'center',
            lambda v: (config.set('matching.position', v), config.save()),
        )
        cb_pos.setMinimumWidth(110)
        card.add_row(SettingsRow('位置', cb_pos))

        sw_sound = SwitchToggle(checked=bool(config.get('matching.enable_sound')))
        sw_sound.toggled.connect(
            lambda v: (config.set('matching.enable_sound', v), config.save())
        )
        card.add_row(SettingsRow('音效提醒', sw_sound))
        return card

    # -------- 热键设置 --------
    def _build_hotkey_card(self):
        card = SettingsCard('热键设置')
        # 当前只读显示，编辑功能未实现
        # 每个热键都是开关：未扫描时按下以对应模式开扫，同键再按停止，
        # 扫描中按另一个键则直接切换模式
        card.add_row(SettingsRow('ROI 扫描 开/关',
                                 HotkeyDisplay('Ctrl + Alt + 1'),
                                 with_separator=False))
        card.add_row(SettingsRow('全屏扫描 开/关', HotkeyDisplay('Ctrl + Alt + 2')))
        return card

    # -------- 配置管理 --------
    def _build_reset_card(self):
        card = SettingsCard('配置管理')
        # 这一行内容是「左侧 标题+说明 双行 / 右侧 重置按钮」，标准 SettingsRow 不够用
        row = QFrame()
        row.setObjectName('settingsRow')
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 8, 0, 8)
        h.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title = QLabel('重置全部配置')
        title.setObjectName('settingsRowLabel')
        hint = QLabel('恢复到出厂默认值，不可撤销')
        hint.setObjectName('settingsHint')
        text_col.addWidget(title)
        text_col.addWidget(hint)
        h.addLayout(text_col)
        h.addStretch(1)
        btn = make_reset_button('重置配置')
        btn.clicked.connect(self._on_reset)
        h.addWidget(btn)
        card.add_row(row)
        return card

    def _on_reset(self):
        """重置全部配置到 DEFAULT_CONFIG。控件值在 build 时已绑死 init 值，
        这里弹"重启生效"提示而非现场刷 widget——后者要遍历重写所有 setter，
        引入歧义风险且没有功能收益。"""
        ret = QMessageBox.question(
            self, '确认重置',
            '确定要恢复所有配置到出厂默认值吗？\n\n此操作不可撤销。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        config.reset_to_defaults()
        config.save()
        QMessageBox.information(
            self, '已重置', '配置已恢复默认值。\n请重启应用让所有界面生效。',
        )
