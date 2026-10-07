"""屏幕浮窗（PySide6 版）。

paintEvent 自绘双列：左列累计匹配（kw + hint），右列本次 OCR 文本（命中红 / 未命中绿 / 摘要灰）。
半透明深色背景圆角卡片，文字带 1px 黑色阴影。
"""
import threading

from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import QGuiApplication, QPainter, QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import QWidget

# C 大三和弦 PCM 字节（命中提示音）
from .sound import CHORD_WAV as _CHORD_WAV

try:
    import winsound
    _SOUND_AVAILABLE = True
except ImportError:
    _SOUND_AVAILABLE = False


# 颜色（白色半透明卡片底；文字颜色按白底可读性挑过）
_C_MATCH        = '#E5484D'   # 命中关键词 / 命中 OCR 行（白底用稍暗的红）
_C_OCR_OK       = '#16A34A'   # 未命中的正常 OCR 行（白底用深绿，替代旧的 #00FF00 荧光绿）
_C_MUTED        = '#64748B'   # "+ N 已隐藏" 摘要
_C_PLACEHOLDER  = '#94A3B8'   # "暂无匹配 / 暂无识别结果"
_C_SHADOW       = '#000000'   # 1px 偏移阴影，在白底上仅起加粗效果
_C_BG_FILL      = QColor(255, 255, 255, 25)   # 白色卡片底，alpha=25/255 ≈ 90% 透明
_C_BG_BORDER    = QColor(0, 0, 0, 90)         # 边框拉到 alpha=90 让接近全透的卡片仍能看清轮廓


class Overlay(QWidget):
    """屏幕浮窗：frameless + always-on-top + 透明背景。
    update() 每次扫描调用；display_duration 秒无新调用后自动隐藏。"""

    PADDING = 12
    BG_RADIUS = 10
    KW_HINT_GAP = 12
    MID_GAP = 28
    HEADER_DATA_GAP = 8
    _HEADER_TEXT_LEFT = '提示'
    _HEADER_TEXT_RIGHT = 'OCR结果'

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._session_matches = {}      # {keyword: hint}
        # 渲染数据（paintEvent 用）
        self._left_rows = []   # [(kw, hint, color_kw, color_hint), ...]
        self._right_rows = []  # [(text, color), ...]
        self._line_height = 24
        self._kw_w = 0
        self._hint_w = 0
        self._ocr_w = 0
        self._shadow_offset = 1
        self._font = QFont('Microsoft YaHei', 16, QFont.Bold)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            # WindowTransparentForInput 在 Windows 下走 WS_EX_TRANSPARENT，OS 层面
            # 让点击穿透。仅 WA_TransparentForMouseEvents 在 frameless Tool 窗口上不稳。
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # 全局 QSS 给所有 QWidget 涂了 #F1F5FA 底色，会把半透明背景盖掉；这里显式
        # 把本控件的 background 设回 transparent，paintEvent 负责画圆角卡片。
        self.setStyleSheet('background: transparent;')

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self.hide()

    # ============ 公开 API ============

    def update(self, ocr_results, matches):
        """每次扫描后调用。无匹配也调用以保持显示连续。"""
        ocr_results = ocr_results or []
        matches = matches or []

        # 累计 + 新匹配音效
        new_keywords = []
        for m in matches:
            kw = m.get('keyword', '')
            if kw and kw not in self._session_matches:
                self._session_matches[kw] = m.get('hint', '')
                new_keywords.append(kw)
        if new_keywords and self._cfg('matching.enable_sound', True):
            self._play_chord()

        self._compute_rows(ocr_results, matches)
        size = self._compute_size()
        self.setFixedSize(size)
        self._reposition(size)
        self.show()
        super().update()  # 触发 paintEvent

        duration_ms = int(self._cfg('matching.display_duration', 3.0) * 1000)
        self._hide_timer.start(duration_ms)

    def clear_session(self):
        self._session_matches.clear()

    def hide(self):
        self._hide_timer.stop()
        super().hide()

    def destroy(self):  # noqa: A003
        self._hide_timer.stop()
        try:
            super().close()
            super().deleteLater()
        except Exception:
            pass

    # ============ 渲染：行数据计算 ============

    def _compute_rows(self, ocr_results, matches):
        # 字体
        font_size = int(self._cfg('matching.font_size', 18))
        effective = max(10, font_size - 2)
        self._font = QFont('Microsoft YaHei', effective, QFont.Bold)
        fm = QFontMetrics(self._font)
        self._line_height = int(fm.height() * 1.15)
        self._shadow_offset = max(1, effective // 30)

        screen = QGuiApplication.primaryScreen()
        sg = screen.geometry() if screen else None
        screen_w = sg.width() if sg else 1920
        screen_h = sg.height() if sg else 1080
        kw_cap = int(screen_w * 0.20)
        hint_cap = int(screen_w * 0.20)
        ocr_cap = int(screen_w * 0.40)
        height_cap = int(screen_h * 0.80)

        matched_keywords = {m['keyword'] for m in matches if m.get('keyword')}

        # 左列：累计匹配按 kw 字母序
        left_pairs = sorted(self._session_matches.items(), key=lambda kv: kv[0].casefold())
        if not left_pairs:
            left_rows = [('暂无匹配', '', _C_PLACEHOLDER, _C_PLACEHOLDER)]
        else:
            left_rows = [(kw, hint, _C_MATCH, _C_MATCH) for kw, hint in left_pairs]

        # 右列：命中行先（红，按命中 kw 字母序）+ 未命中（绿）+ 占位
        # 同一行文本只显示一次（OCR 偶尔会重复返回同一行 / 屏上重复出现的内容）。
        # 行数不在这里硬截断；下方 max_visible 按 height_cap 统一处理截断与"+ N 已隐藏"。
        matched_with_keys = []
        unmatched_lines = []
        seen_texts = set()
        for r in ocr_results:
            text = r.get('text', '') if isinstance(r, dict) else ''
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            text_cf = text.casefold()
            hit = [kw for kw in matched_keywords if kw.casefold() in text_cf]
            if hit:
                matched_with_keys.append((min(kw.casefold() for kw in hit), text))
            else:
                unmatched_lines.append(text)
        matched_with_keys.sort(key=lambda x: x[0])

        right_rows = [(t, _C_MATCH) for _, t in matched_with_keys]
        for text in unmatched_lines:
            right_rows.append((text, _C_OCR_OK))
        if not right_rows:
            right_rows.append(('暂无识别结果', _C_PLACEHOLDER))

        # 行数封顶 + "+ N 已隐藏"
        max_visible = max(1, (height_cap - 8) // self._line_height)
        if max(len(left_rows), len(right_rows)) > max_visible:
            visible_n = max(1, max_visible - 1)
            if len(left_rows) > visible_n:
                hidden = len(left_rows) - visible_n
                left_rows = left_rows[:visible_n] + [
                    (f'+ {hidden} 项已隐藏', '', _C_MUTED, _C_MUTED),
                ]
            if len(right_rows) > visible_n:
                hidden = len(right_rows) - visible_n
                right_rows = right_rows[:visible_n] + [
                    (f'+ {hidden} 行已隐藏', _C_MUTED),
                ]

        # 列宽（自适应，封顶）
        def w_of(text):
            return fm.horizontalAdvance(text) if text else 0

        header_kw_w = w_of(self._HEADER_TEXT_LEFT)
        header_ocr_w = w_of(self._HEADER_TEXT_RIGHT)
        self._kw_w   = min(max(header_kw_w, max((w_of(r[0]) for r in left_rows), default=0)), kw_cap)
        self._hint_w = min(max((w_of(r[1]) for r in left_rows), default=0), hint_cap)
        self._ocr_w  = min(max(header_ocr_w, max((w_of(r[0]) for r in right_rows), default=0)), ocr_cap)

        self._left_rows = left_rows
        self._right_rows = right_rows

    def _compute_size(self):
        rows = max(len(self._left_rows), len(self._right_rows))
        w = (
            self.PADDING + self._kw_w + self.KW_HINT_GAP + self._hint_w
            + self.MID_GAP + self._ocr_w + self._shadow_offset + self.PADDING
        )
        h = self._line_height + self.HEADER_DATA_GAP + rows * self._line_height + self.PADDING * 2
        screen = QGuiApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            w = min(w, int(sg.width() * 0.80))
            h = min(h, int(sg.height() * 0.80))
        return QSize(max(w, 100), max(h, 40))

    # ============ paintEvent 自绘 ============

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # 圆角半透明背景卡
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(QPen(_C_BG_BORDER, 1))
        p.setBrush(_C_BG_FILL)
        p.drawRoundedRect(rect, self.BG_RADIUS, self.BG_RADIUS)

        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        ascent = fm.ascent()

        kw_x   = self.PADDING
        hint_x = kw_x + self._kw_w + self.KW_HINT_GAP
        ocr_x  = hint_x + self._hint_w + self.MID_GAP

        header_y = self.PADDING + ascent
        self._draw_text(p, kw_x, header_y, self._HEADER_TEXT_LEFT, _C_MUTED)
        self._draw_text(p, ocr_x, header_y, self._HEADER_TEXT_RIGHT, _C_MUTED)

        y0 = header_y + self._line_height + self.HEADER_DATA_GAP

        rows = max(len(self._left_rows), len(self._right_rows))
        for i in range(rows):
            y = y0 + i * self._line_height
            if i < len(self._left_rows):
                kw, hint, c_kw, c_hint = self._left_rows[i]
                self._draw_text(p, kw_x, y, kw, c_kw)
                self._draw_text(p, hint_x, y, hint, c_hint)
            if i < len(self._right_rows):
                text, color = self._right_rows[i]
                self._draw_text(p, ocr_x, y, text, color)
        p.end()

    def _draw_text(self, p, x, y, text, color_hex):
        if not text:
            return
        # 黑色阴影 +1
        p.setPen(QColor(_C_SHADOW))
        p.drawText(x + self._shadow_offset, y + self._shadow_offset, text)
        # 主色
        p.setPen(QColor(color_hex))
        p.drawText(x, y, text)

    # ============ 工具 ============

    def _cfg(self, key, default):
        if self._config is None:
            return default
        v = self._config.get(key, default)
        return v if v is not None else default

    def _play_chord(self):
        if not _SOUND_AVAILABLE:
            return
        threading.Thread(
            target=lambda: winsound.PlaySound(_CHORD_WAV, winsound.SND_MEMORY),
            daemon=True,
        ).start()

    def _reposition(self, size):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        sg = screen.geometry()
        w, h = size.width(), size.height()
        margin = 50
        position = self._cfg('matching.position', 'center')
        if position == 'top':
            x = (sg.width() - w) // 2
            y = margin
        elif position == 'bottom':
            x = (sg.width() - w) // 2
            y = sg.height() - h - margin
        elif position == 'top-left':
            x = margin; y = margin
        elif position == 'top-right':
            x = sg.width() - w - margin; y = margin
        elif position == 'bottom-left':
            x = margin; y = sg.height() - h - margin
        elif position == 'bottom-right':
            x = sg.width() - w - margin; y = sg.height() - h - margin
        else:  # center
            x = (sg.width() - w) // 2
            y = (sg.height() - h) // 2
        self.move(sg.left() + x, sg.top() + y)
