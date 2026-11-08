"""启动即后台预加载 OCR 模型。

app.py 在建窗口前调用 start_preload()，让 paddle import + 模型构造（运行机 GUI 内实测 ~5s）
与 UI 初始化并行，用户点「开始」/按热键时模型通常已就绪。

扫描线程照旧走 pipeline.init() → OCRStage.init() → _get_ocr()：
- 预加载已完成：直接复用单例
- 预加载进行中：阻塞在 _ocr_lock 上等它构造完，不会重复加载
- 预加载失败：_ocr_instance 仍为空，扫描线程会自己重试，错误走原有异常路径
"""
import threading
import time

from utils.logger import logger
from .ocr_stage import OCRStage

IDLE, LOADING, READY, FAILED = 'idle', 'loading', 'ready', 'failed'

_lock = threading.Lock()
_state = IDLE
_elapsed = 0.0
_callbacks = []


def start_preload():
    """启动后台预加载线程（幂等，重复调用忽略）。"""
    global _state
    with _lock:
        if _state != IDLE:
            return
        _state = LOADING
    threading.Thread(target=_run, name='ocr-preload', daemon=True).start()


def state():
    return _state


def add_done_callback(fn):
    """注册 fn(state, elapsed_seconds)，预加载结束（READY / FAILED）后调用。
    注意回调跑在预加载线程里，Qt 侧需要用 Signal 转回主线程；
    若注册时已经结束则立即在当前线程调用。"""
    with _lock:
        if _state in (IDLE, LOADING):
            _callbacks.append(fn)
            return
        done_state, elapsed = _state, _elapsed
    fn(done_state, elapsed)


def _run():
    global _state, _elapsed
    t0 = time.time()
    logger.info('后台预加载 OCR 模型…')
    try:
        OCRStage().init()
        result = READY
        logger.info(f'OCR 模型预加载完成（{time.time() - t0:.1f}s）')
    except Exception:
        result = FAILED
        logger.exception('OCR 模型预加载失败，将在开始扫描时重试')
    with _lock:
        _state = result
        _elapsed = time.time() - t0
        elapsed = _elapsed
        callbacks = list(_callbacks)
        _callbacks.clear()
    for fn in callbacks:
        try:
            fn(result, elapsed)
        except Exception:
            logger.exception('预加载回调异常')
