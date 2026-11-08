"""单实例：命名互斥体判重 + 命名管道唤醒。仅 Windows（部署平台）生效。"""

import os
import sys
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

pytestmark = pytest.mark.skipif(
    sys.platform != 'win32', reason='单实例实现依赖 Windows 命名对象'
)

import utils.single_instance as single_instance  # noqa: E402
from utils.single_instance import (  # noqa: E402
    InstanceLock,
    SingleInstanceGuard,
    notify_running_instance,
)


@pytest.fixture(scope='module')
def qt_app():
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    return app


def _test_name(kind):
    # 带上 PID，避免本机真在跑 GUI 时测试误连到它的互斥体/管道
    return f'ScreenScanOCRRecognize.test.{kind}.{os.getpid()}'


def test_instance_lock_detects_second_holder(monkeypatch):
    monkeypatch.setattr(
        single_instance, '_MUTEX_NAME', 'Local\\' + _test_name('mutex'))
    first = InstanceLock()
    assert not first.already_running
    second = InstanceLock()
    assert second.already_running


def test_notify_without_server_returns_false(qt_app, monkeypatch):
    monkeypatch.setattr(single_instance, 'SERVER_NAME', _test_name('none'))
    assert notify_running_instance(timeout_ms=200) is False


def test_guard_activates_on_notify(qt_app, monkeypatch):
    monkeypatch.setattr(single_instance, 'SERVER_NAME', _test_name('guard'))
    hits = []
    guard = SingleInstanceGuard(lambda: hits.append(1))
    assert guard.listen()
    try:
        assert notify_running_instance(timeout_ms=1000)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not hits:
            qt_app.processEvents()
            time.sleep(0.01)
    finally:
        guard.close()
    assert hits == [1]
