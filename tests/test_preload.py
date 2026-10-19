"""验证启动预加载与扫描线程并发进 _get_ocr 时只构造一次模型、失败可重试。

用假的 paddleocr 模块替换真实依赖，只测加锁 / 状态机逻辑，不需要 paddle。
"""

import os
import sys
import threading
import time
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from pipeline import ocr_stage, preload


class _FakePaddleOCR:
    constructed = 0
    fail_next = False
    ocr_calls = 0

    def __init__(self, **kwargs):
        time.sleep(0.3)  # 模拟耗时构造，制造并发窗口
        if _FakePaddleOCR.fail_next:
            _FakePaddleOCR.fail_next = False
            raise RuntimeError('fake init failure')
        _FakePaddleOCR.constructed += 1

    def ocr(self, img):
        _FakePaddleOCR.ocr_calls += 1
        return []


@pytest.fixture(autouse=True)
def fake_paddle(monkeypatch):
    mod = types.ModuleType('paddleocr')
    mod.PaddleOCR = _FakePaddleOCR
    mod.__version__ = '3.3.2'
    monkeypatch.setitem(sys.modules, 'paddleocr', mod)
    _FakePaddleOCR.constructed = 0
    _FakePaddleOCR.fail_next = False
    _FakePaddleOCR.ocr_calls = 0
    ocr_stage.OCRStage().release()
    monkeypatch.setattr(preload, '_state', preload.IDLE)
    monkeypatch.setattr(preload, '_callbacks', [])
    yield
    ocr_stage.OCRStage().release()


def _wait_done(timeout=5):
    done = threading.Event()
    result = {}

    def cb(state, elapsed):
        result['state'] = state
        done.set()

    preload.add_done_callback(cb)
    assert done.wait(timeout), '预加载超时'
    return result['state']


def test_model_source_check_disabled_by_default():
    assert os.environ.get('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK') == 'True'


def test_concurrent_init_constructs_once():
    """预加载进行中扫描线程也来 init：应阻塞等待并复用同一个实例。"""
    preload.start_preload()
    time.sleep(0.05)  # 确保预加载线程已进入构造
    got = ocr_stage._get_ocr()  # 扫描线程侧
    assert _wait_done() == preload.READY
    assert _FakePaddleOCR.constructed == 1
    assert got is ocr_stage._get_ocr()


def test_warmup_runs_once():
    preload.start_preload()
    assert _wait_done() == preload.READY
    ocr_stage.OCRStage().init()  # 扫描线程 init 不应再预热
    assert _FakePaddleOCR.ocr_calls == 1


def test_start_preload_is_idempotent():
    preload.start_preload()
    preload.start_preload()
    assert _wait_done() == preload.READY
    assert _FakePaddleOCR.constructed == 1


def test_failed_preload_retries_on_scan_init():
    _FakePaddleOCR.fail_next = True
    preload.start_preload()
    assert _wait_done() == preload.FAILED
    assert ocr_stage._ocr_instance is None
    ocr_stage.OCRStage().init()  # 点开始时重试
    assert _FakePaddleOCR.constructed == 1


def test_callback_after_done_called_immediately():
    preload.start_preload()
    assert _wait_done() == preload.READY
    calls = []
    preload.add_done_callback(lambda s, e: calls.append(s))
    assert calls == [preload.READY]


def test_defer_heavy_imports_is_lazy(tmp_path, monkeypatch):
    """modelscope 换成 LazyLoader 后：import 不执行模块体，首次访问属性才执行。"""
    pkg = tmp_path / 'modelscope'
    pkg.mkdir()
    marker = tmp_path / 'executed'
    (pkg / '__init__.py').write_text(
        f'open(r"{marker}", "w").close()\nsnapshot_download = "real"\n', encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'modelscope', raising=False)

    ocr_stage._defer_heavy_imports()
    import modelscope
    assert not marker.exists(), 'import 阶段不应执行 modelscope 模块体'
    assert modelscope.snapshot_download == 'real'
    assert marker.exists()
    monkeypatch.delitem(sys.modules, 'modelscope', raising=False)
