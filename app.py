"""PySide6 GUI 入口。tkinter 旧实现已在 PySide6 迁移完成后删除。"""

import os
import sys

from PySide6.QtWidgets import QApplication

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.config import config
from pipeline.preload import start_preload
from ui.main_window import MainWindow
from ui.tray import make_app_icon
from utils.logger import configure_from_config


def _set_windows_app_user_model_id():
    # Windows 按 AppUserModelID 给任务栏分组；不设的话 pythonw 启动会被归到 Python 解释器下、
    # 任务栏显示蟒蛇图标。必须在创建任何窗口前调用。
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'luoyuhan.screenscanocrrecognize.gui.1'
        )
    except Exception:
        pass


def main():
    _set_windows_app_user_model_id()
    config.load()
    configure_from_config(config)
    # 模型加载（运行机 ~13s）放到后台，与下面的 UI 构建并行；点开始时通常已就绪
    start_preload()
    app = QApplication(sys.argv)
    app.setWindowIcon(make_app_icon(256))
    qss_path = os.path.join(PROJECT_ROOT, 'ui', 'styles', 'light.qss')
    icon_dir = os.path.join(PROJECT_ROOT, 'ui', 'icons').replace('\\', '/')
    with open(qss_path, encoding='utf-8') as f:
        # QSS 中用 {ICON_DIR}/xxx.svg 引用，运行时替换成绝对路径（跨工作目录稳定）
        app.setStyleSheet(f.read().replace('{ICON_DIR}', icon_dir))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
