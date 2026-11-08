"""PySide6 GUI 入口。tkinter 旧实现已在 PySide6 迁移完成后删除。"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.config import config
from utils.logger import configure_from_config, logger
from utils.single_instance import (
    InstanceLock, SingleInstanceGuard, notify_running_instance,
)


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

    # 单实例：命名互斥体必须在 start_preload() 之前拿，用户快速双击两次时也只有
    # 一个进程继续初始化。后来者只负责唤出已有窗口；管道可能还没建好，notify 失败
    # 也直接退出——首个实例马上会自己显示窗口，不能在这里再启动一遍。
    instance_lock = InstanceLock()
    if instance_lock.already_running:
        if notify_running_instance():
            logger.info('检测到已有实例在运行，已请求其显示主窗口')
        else:
            logger.info('检测到已有实例正在启动，本次启动直接退出')
        return

    # 放到锁判断之后 import：第二个实例到此就退出了，没必要为它拉起 cv2/numpy。
    from pipeline.preload import start_preload

    # 模型加载（运行机 ~5s）放到后台，与下面的 UI 构建并行；点开始时通常已就绪。
    # PySide6 / ui 包故意在这之后才 import，让 paddle 尽早开始加载。
    start_preload()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui.main_window import MainWindow
    from ui.tray import make_app_icon
    app = QApplication(sys.argv)
    app.setWindowIcon(make_app_icon(256))
    qss_path = os.path.join(PROJECT_ROOT, 'ui', 'styles', 'light.qss')
    icon_dir = os.path.join(PROJECT_ROOT, 'ui', 'icons').replace('\\', '/')
    with open(qss_path, encoding='utf-8') as f:
        # QSS 中用 {ICON_DIR}/xxx.svg 引用，运行时替换成绝对路径（跨工作目录稳定）
        app.setStyleSheet(f.read().replace('{ICON_DIR}', icon_dir))

    # 窗口还没建好时收到的激活请求会排队到事件循环启动后，那时 window 已赋值；
    # 若构建期间有嵌套 processEvents 提前处理了连接，用 pending 标记补一次。
    window = None
    pending_activate = False

    def _activate_window():
        nonlocal pending_activate
        if window is not None:
            window.show_and_activate()
        else:
            pending_activate = True

    guard = SingleInstanceGuard(_activate_window)
    if not guard.listen():
        # 互斥体已经保证了本进程是唯一实例，这里失败说明系统层面出了问题；
        # 若继续运行，下次启动会静默退出且唤不出本窗口，所以直接报错退出。
        logger.error(f'单实例通信初始化失败: {guard.error_string()}')
        QMessageBox.warning(
            None, '屏幕扫描 OCR 识别系统',
            f'单实例通信初始化失败，程序将退出。\n{guard.error_string()}',
        )
        return

    window = MainWindow()
    window.show()
    if pending_activate:
        window.show_and_activate()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
