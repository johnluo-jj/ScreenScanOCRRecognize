import os

from PySide6.QtCore import Qt, Signal, QByteArray
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QComboBox,
    QSlider, QLineEdit, QPushButton, QFrame, QFileDialog,
    QMessageBox, QGraphicsOpacityEffect,
)

from config.config import config, PROJECT_ROOT
from .save_preset_dialog import SavePresetDialog

_ICON_DIR = os.path.join(PROJECT_ROOT, 'ui', 'icons')

# 下拉里两个特殊 data 值（公共，main_window._resolve_roi 也会读取）：
# - RESELECT_TOKEN：第 0 项「重新框选...」，意图为"开扫时弹屏幕框选 UI"。
# - CUSTOM_TOKEN：第 1 项「自定义区域」，代表"使用上次框出来的、未存为命名预设的坐标"。
# 下拉本身不再有副作用——切换只更新 last_roi_choice。实际选 ROI 的动作发生在
# MainWindow._on_start 中，根据 last_roi_choice 分支处理（弹 picker / 用 roi_rect /
# 用预设坐标），这样：(a) 反复点「重新框选...」也能稳定触发；(b) 切错预设不会立即
# 改 roi_rect。
RESELECT_TOKEN = '__reselect__'
CUSTOM_TOKEN = '__custom__'
_RESELECT_LABEL = '重新框选...'
_CUSTOM_LABEL = '自定义区域'


def _render_svg(path, size):
    """渲染指定 SVG 文件成 QPixmap（透明底）。"""
    with open(path, 'rb') as f:
        renderer = QSvgRenderer(QByteArray(f.read()))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(p)
    p.end()
    return pix


class LargeButton(QFrame):
    """主按钮卡片：[icon + title] 横排在第一行 + hint 在第二行（mockup .btn-large 结构）。
    icon_svg 是 ui/icons/ 下的文件名（如 'play.svg' / 'stop.svg'），白色填充。
    支持 enabled/disabled 状态：disabled 时不响应点击，QSS 控制变浅。"""

    clicked = Signal()

    def __init__(self, title, hint, object_name, icon_svg, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(52)
        self._enabled = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignCenter)

        # 第一行：icon + title 横排（mockup .btn-row）
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.setAlignment(Qt.AlignCenter)
        lbl_icon = QLabel()
        lbl_icon.setObjectName('lbBtnIcon')
        lbl_icon.setPixmap(_render_svg(os.path.join(_ICON_DIR, icon_svg), 16))
        row.addWidget(lbl_icon)
        lbl_title = QLabel(title)
        lbl_title.setObjectName('lbBtnTitle')
        row.addWidget(lbl_title)
        layout.addLayout(row)

        # 第二行：hotkey hint
        lbl_hint = QLabel(hint)
        lbl_hint.setObjectName('lbBtnHint')
        lbl_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_hint)

    def setEnabled(self, enabled):
        """禁用态：用 QGraphicsOpacityEffect 整体 0.4 透明（mockup .btn-large.disabled
        = opacity: 0.4），bg/icon/text 一起淡，比硬编码 #ACC5F7 更忠实。"""
        self._enabled = enabled
        super().setEnabled(enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        if enabled:
            self.setGraphicsEffect(None)
        else:
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.4)
            self.setGraphicsEffect(eff)

    def mousePressEvent(self, event):
        if not self._enabled:
            return
        if event.button() == Qt.LeftButton:
            # 触发"按下"态（mockup .btn-large.active：更深底 + inset shadow）
            self.setProperty('pressed', True)
            self.style().unpolish(self)
            self.style().polish(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._enabled:
            # 在按下区域内释放才发 clicked（标准按钮行为）
            inside = self.rect().contains(event.position().toPoint())
            self.setProperty('pressed', False)
            self.style().unpolish(self)
            self.style().polish(self)
            if inside:
                self.clicked.emit()
        super().mouseReleaseEvent(event)


def _make_group(title):
    """返回 (容器 QFrame, 内层 QVBoxLayout)。
    标题用 [3×13 蓝色圆角小色块] + 6px 间距 + 加粗文字，复刻 mockup 的 ::before 样式。"""
    frame = QFrame()
    frame.setObjectName('configGroup')
    box = QVBoxLayout(frame)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(8)

    # 标题行：用 HBox 摆 [pill][gap][text] 比 QSS border-left 更接近 mockup
    title_row = QHBoxLayout()
    title_row.setContentsMargins(0, 0, 0, 0)
    title_row.setSpacing(6)
    pill = QFrame()
    pill.setObjectName('groupPill')
    pill.setFixedSize(3, 13)
    title_lbl = QLabel(title)
    title_lbl.setObjectName('groupTitle')
    title_row.addWidget(pill)
    title_row.addWidget(title_lbl)
    title_row.addStretch(1)
    box.addLayout(title_row)
    return frame, box


def _slider_row(min_v, max_v, value, on_change, scale=1):
    """slider + 右侧白色圆角小盒子显示当前值（mockup .slider-value 样式）。
    返回 QHBoxLayout。值显示固定 1 位小数（mockup 一致："3.0" / "0.3"）。
    注意：QSlider 默认 sizeHint 偏小，会把 16px 圆把手上下截掉；显式给 22px 高度。"""
    s = QSlider(Qt.Horizontal)
    s.setMinimum(int(min_v * scale))
    s.setMaximum(int(max_v * scale))
    s.setValue(int(value * scale))
    s.setMinimumHeight(22)
    value_box = QLabel(f'{value:.1f}')
    value_box.setObjectName('sliderValue')
    value_box.setAlignment(Qt.AlignCenter)
    value_box.setFixedSize(56, 28)
    s.valueChanged.connect(lambda v: (value_box.setText(f'{v/scale:.1f}'), on_change(v / scale)))
    wrap = QHBoxLayout()
    wrap.setSpacing(12)
    wrap.addWidget(s, 1)
    wrap.addWidget(value_box)
    return wrap


class ConfigPanel(QWidget):
    """主窗口左侧配置区：4 个 group + Action 按钮组。
    所有控件双向绑定 config 单例。"""

    start_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 让 ConfigPanel 真正画自己的 QSS 背景；否则只继承全局 QWidget {bg:#F1F5FA}
        # 看起来跟 sidebar 一个色（mockup .config-panel = white canvas）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName('configPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        layout.addWidget(self._build_roi_group())
        layout.addWidget(self._build_pace_group())
        layout.addWidget(self._build_ocr_group())
        layout.addWidget(self._build_match_group())
        # 控件和按钮之间留空，按钮贴底
        layout.addStretch(1)
        layout.addLayout(self._build_action_row())

        # 初始状态：未运行 → 开始可点，停止 disabled
        self.set_running(False)

    # ----- 扫描区域 -----
    def _build_roi_group(self):
        frame, box = _make_group('扫描区域')

        self.cb_enable_roi = QCheckBox('启用 ROI')
        self.cb_enable_roi.setCursor(Qt.PointingHandCursor)
        self.cb_enable_roi.setChecked(bool(config.get('scan.enable_roi')))
        self.cb_enable_roi.stateChanged.connect(
            lambda s: (config.set('scan.enable_roi', bool(s)), config.save())
        )
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self.cb_enable_roi)
        toggle_row.addStretch(1)
        box.addLayout(toggle_row)

        box.addWidget(QLabel('ROI 预设'))
        preset_row = QHBoxLayout()
        self.combo_preset = QComboBox()
        self._reload_presets()
        # 用 currentIndexChanged + currentData 走，避免显示文本和 data 撞 RESELECT
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        btn_save_preset = QPushButton('保存当前')
        btn_save_preset.setCursor(Qt.PointingHandCursor)
        btn_save_preset.clicked.connect(self._on_save_preset)
        # 「删除」按钮：仅当下拉是命名预设时启用；点击弹确认 → 删 config + reload
        self.btn_delete_preset = QPushButton('删除')
        self.btn_delete_preset.setCursor(Qt.PointingHandCursor)
        self.btn_delete_preset.clicked.connect(self._on_delete_preset)
        preset_row.addWidget(self.combo_preset, 1)
        preset_row.addWidget(btn_save_preset)
        preset_row.addWidget(self.btn_delete_preset)
        box.addLayout(preset_row)
        self._refresh_delete_btn()
        return frame

    def set_roi_enabled(self, enabled):
        """外部触发（如热键切模式）：只同步勾选框显示，配置由调用方负责写入。"""
        self.cb_enable_roi.blockSignals(True)
        self.cb_enable_roi.setChecked(bool(enabled))
        self.cb_enable_roi.blockSignals(False)

    def _reload_presets(self):
        """重建下拉：[重新框选...][自定义区域] + 所有命名预设；按 last_roi_choice
        定位当前项。全程 blockSignals，不触发 _on_preset_changed。"""
        self.combo_preset.blockSignals(True)
        self.combo_preset.clear()
        self.combo_preset.addItem(_RESELECT_LABEL, RESELECT_TOKEN)
        self.combo_preset.addItem(_CUSTOM_LABEL, CUSTOM_TOKEN)
        presets = config.get('scan.roi_presets') or {}
        for name in presets.keys():
            self.combo_preset.addItem(name, name)
        last = config.get('scan.last_roi_choice') or RESELECT_TOKEN
        idx = self.combo_preset.findData(last)
        self.combo_preset.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_preset.blockSignals(False)
        self._refresh_delete_btn()

    def _refresh_delete_btn(self):
        """删除按钮仅在选中命名预设时启用（特殊伪项 / 还没建按钮 都不启用）。"""
        if not hasattr(self, 'btn_delete_preset'):
            return
        data = self.combo_preset.currentData()
        self.btn_delete_preset.setEnabled(
            isinstance(data, str) and data not in (RESELECT_TOKEN, CUSTOM_TOKEN)
        )

    def refresh_roi_combo(self):
        """外部触发（如 picker 完成后）：按 last_roi_choice 重定位下拉。"""
        self._reload_presets()

    def _on_preset_changed(self, _index):
        """下拉只做意图选择 —— 单纯把当前选项写进 last_roi_choice。
        实际拿 ROI / 弹 picker 的动作发生在 MainWindow._on_start。"""
        data = self.combo_preset.currentData()
        if not data:
            return
        config.set('scan.last_roi_choice', data)
        config.save()
        self._refresh_delete_btn()

    def _on_save_preset(self):
        """弹 SavePresetDialog 取名字（新建 / 覆盖均走同一路径）。"""
        roi = config.get('scan.roi_rect')
        if not roi:
            QMessageBox.information(
                self, '保存预设',
                '当前还没有框选过 ROI，请先在下拉里选「重新框选...」拖出一个区域。',
            )
            return
        dlg = SavePresetDialog(self)
        if dlg.exec() != dlg.Accepted:
            return
        name = dlg.chosen_name()
        if not name:
            return
        if name in (RESELECT_TOKEN, CUSTOM_TOKEN):
            QMessageBox.warning(self, '保存预设', '该名称为保留字，请换一个。')
            return
        presets = config.get('scan.roi_presets') or {}
        presets[name] = list(roi)
        config.set('scan.roi_presets', presets)
        config.set('scan.last_roi_choice', name)
        config.save()
        self._reload_presets()

    def _on_delete_preset(self):
        """点击「删除」按钮：删当前选中的命名预设。弹确认对话框防误删。"""
        name = self.combo_preset.currentData()
        if not isinstance(name, str) or name in (RESELECT_TOKEN, CUSTOM_TOKEN):
            return
        presets = config.get('scan.roi_presets') or {}
        if name not in presets:
            return
        ret = QMessageBox.question(
            self, '删除预设',
            f'确定删除预设「{name}」吗？此操作不可撤销。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        del presets[name]
        config.set('scan.roi_presets', presets)
        if config.get('scan.last_roi_choice') == name:
            config.set('scan.last_roi_choice', CUSTOM_TOKEN)
        config.save()
        self._reload_presets()

    # ----- 扫描节奏 -----
    def _build_pace_group(self):
        frame, box = _make_group('扫描节奏')
        box.addWidget(QLabel('扫描间隔（秒）'))
        _v = config.get('scan.interval_seconds')
        box.addLayout(_slider_row(
            0.5, 10.0, float(_v) if _v is not None else 5.0,
            lambda v: (config.set('scan.interval_seconds', round(v, 1)), config.save_debounced()),
            scale=2,  # 0.5 秒步进
        ))
        return frame

    # ----- OCR 识别 -----
    def _build_ocr_group(self):
        frame, box = _make_group('OCR 识别')
        row = QHBoxLayout()
        row.addWidget(QLabel('语言'))
        self.combo_lang = QComboBox()
        for code, name in (('ch', 'ch (中文)'), ('en', 'en (English)')):
            self.combo_lang.addItem(name, code)
        cur = config.get('ocr.language') or 'ch'
        idx = self.combo_lang.findData(cur)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(
            lambda _: (config.set('ocr.language', self.combo_lang.currentData()), config.save())
        )
        row.addWidget(self.combo_lang)
        self.cb_gpu = QCheckBox('GPU 加速')
        self.cb_gpu.setCursor(Qt.PointingHandCursor)
        self.cb_gpu.setChecked(bool(config.get('gpu.enabled')))
        self.cb_gpu.stateChanged.connect(
            lambda s: (config.set('gpu.enabled', bool(s)), config.save())
        )
        row.addWidget(self.cb_gpu)
        row.addStretch(1)
        box.addLayout(row)

        box.addWidget(QLabel('最小置信度'))
        box.addLayout(_slider_row(
            0.0, 1.0, float(config.get('ocr.min_confidence') or 0.3),
            lambda v: (config.set('ocr.min_confidence', round(v, 2)), config.save_debounced()),
            scale=100,
        ))
        return frame

    # ----- 关键词匹配 -----
    def _build_match_group(self):
        frame, box = _make_group('关键词匹配')
        box.addWidget(QLabel('词库文件'))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.le_banlist = QLineEdit(str(config.get('files.banlist_file') or ''))
        self.le_banlist.setReadOnly(True)
        btn_browse = QPushButton('浏览…')
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._on_browse_banlist)
        btn_edit = QPushButton('编辑')
        btn_edit.setCursor(Qt.PointingHandCursor)
        btn_edit.clicked.connect(self._on_edit_banlist)
        row.addWidget(self.le_banlist, 1)
        row.addWidget(btn_browse)
        row.addWidget(btn_edit)
        box.addLayout(row)

        box.addWidget(QLabel('匹配后显示时长（秒）'))
        _v = config.get('matching.display_duration')
        box.addLayout(_slider_row(
            0.5, 10.0, float(_v) if _v is not None else 3.0,
            lambda v: (config.set('matching.display_duration', round(v, 1)), config.save_debounced()),
            scale=2,  # 0.5 秒步进
        ))
        return frame

    def _on_browse_banlist(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择关键词文件', '', 'Text (*.txt);;All (*)')
        if path:
            self.le_banlist.setText(path)
            config.set('files.banlist_file', path)
            config.save()

    def _on_edit_banlist(self):
        """用系统默认程序打开关键词文件（Windows: 记事本/用户自定义）。"""
        path = self.le_banlist.text().strip()
        if not path:
            return
        try:
            if os.path.isfile(path):
                os.startfile(path)  # noqa: SIM115 (Windows 专用)
        except Exception:
            pass

    # ----- Action 按钮（横向并排，每按钮 2 行：标题 + 热键提示） -----
    def _build_action_row(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        # 热键：Ctrl+Alt+1 = ROI 模式开关，Ctrl+Alt+2 = 全屏模式开关（同键再按即停止）
        self.btn_start = LargeButton('开始扫描', 'Ctrl+Alt+1 ROI / 2 全屏', 'btnPrimary', 'play.svg')
        self.btn_start.clicked.connect(self.start_clicked.emit)
        self.btn_stop = LargeButton('停止扫描', '再按同一热键', 'btnDanger', 'stop.svg')
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(self.btn_start, 1)
        row.addWidget(self.btn_stop, 1)
        return row

    def set_running(self, running):
        """更新启动/停止按钮的可用态。"""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
