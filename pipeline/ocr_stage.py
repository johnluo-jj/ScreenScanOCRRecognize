import cv2
import importlib.util
import logging
import numpy as np
import os
import sys
import threading
import time
import types

from config.config import config
from utils.logger import logger

# PaddleX 在 import 阶段（official_models 模块级 _ModelManager()）就会对 4 个模型源逐个
# HTTP HEAD 探测连通性，运行机实测 ~1.7s，网络差时更久。模型已缓存在本地时纯属浪费，默认跳过；
# 外部环境变量可覆盖。必须在 import paddleocr 之前设置（paddleocr 在 _get_ocr 里懒导入）。
os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')


class _DeferredModule(types.ModuleType):
    """占位模块：首次访问属性时才真正导入并转发。
    不用 importlib.util.LazyLoader —— `import x` 语句会读 __spec__，直接触发它加载，等于没延迟。"""

    def __getattr__(self, attr):
        if sys.modules.get(self.__name__) is self:
            del sys.modules[self.__name__]
        return getattr(importlib.import_module(self.__name__), attr)


def _defer_heavy_imports():
    """paddlex official_models 顶层无条件 `import modelscope`，modelscope 又会 import torch
    （运行机实测 ~1.1s，且 torch 与 paddle 的 CUDA DLL 有加载顺序冲突）。modelscope 只在
    「从 ModelScope 下载模型」时用到，这里先放占位模块，真用到时再导入。
    必须在 import paddleocr 之前调用；已导入或未安装时什么都不做。"""
    if 'modelscope' in sys.modules or importlib.util.find_spec('modelscope') is None:
        return
    sys.modules['modelscope'] = _DeferredModule('modelscope')


_ocr_instance = None
_ocr_init_config = None  # (lang, gpu_enabled) 初始化时的配置
# 启动预加载线程和扫描线程可能同时进 _get_ocr：后到者阻塞等前者构造完，复用同一个实例，
# 而不是并发再构造一份（双倍显存 + 双倍耗时）。
_ocr_lock = threading.Lock()


def _get_ocr():
    with _ocr_lock:
        return _get_ocr_locked()


def _get_ocr_locked():
    global _ocr_instance, _ocr_init_config
    lang = config.get('ocr.language')
    use_gpu = config.get('gpu.enabled')
    current_config = (lang, use_gpu)

    if _ocr_instance is not None and _ocr_init_config == current_config:
        return _ocr_instance

    # 配置变更或首次初始化
    _defer_heavy_imports()
    from paddleocr import PaddleOCR, __version__ as paddle_version
    major = int(paddle_version.split('.')[0])
    device = 'gpu' if use_gpu else 'cpu'

    logger.info(f"初始化 PaddleOCR: lang={lang}, device={device}")

    # PaddleOCR/paddlex 构造函数会偷偷调 logging.basicConfig 或把 root level 改成 WARNING，
    # 导致后续 INFO 日志全被吞掉（症状：日志只能看到 WARNING+）。前后快照 + 恢复 root level
    # 和所有 handler 的 level。
    _root = logging.getLogger()
    _saved_root_level = _root.level
    _saved_handler_levels = [(h, h.level) for h in list(_root.handlers)]

    if major >= 3:
        # 屏幕截图始终正向，禁用文档方向/矫正模型以避免 PP-LCNet padding bug 并提速
        _ocr_instance = PaddleOCR(
            lang=lang, device=device, enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    else:
        _ocr_instance = PaddleOCR(
            lang=lang, use_gpu=use_gpu,
            use_angle_cls=True, enable_mkldnn=False
        )

    _root.setLevel(_saved_root_level)
    for h, lvl in _saved_handler_levels:
        h.setLevel(lvl)

    _ocr_init_config = current_config
    logger.info("PaddleOCR 初始化完成")
    return _ocr_instance


class OCRStage:
    def __init__(self):
        # 模型走模块级单例 _ocr_instance，不缓存实例字段，避免配置切换后引用不同步。
        pass

    def init(self):
        """预初始化 OCR 模型（让 5-15s 加载发生在 scan 循环之前）。
        不做预热推理：运行机实测预热 ~0.5s 只换来首帧快 ~0.35s，「打开就扫」时反而更慢。"""
        _get_ocr()

    def recognize(self, frame_bgr):
        """
        OCR 识别。每次取最新模型实例，配置变更自动生效。
        Args:
            frame_bgr: numpy BGR 数组
        Returns:
            list of dict: [{'text': str, 'confidence': float, 'bbox': list}, ...]
        """
        ocr = _get_ocr()

        # 可选图像反色
        if config.get('ocr.enable_image_invert'):
            frame_bgr = cv2.bitwise_not(frame_bgr)

        start = time.time()
        result = ocr.ocr(frame_bgr)
        duration = time.time() - start
        logger.debug(f"OCR 耗时: {duration:.3f}s")

        # 提取结果
        texts = []
        min_conf = config.get('ocr.min_confidence')

        if result and len(result) > 0:
            ocr_result = result[0]
            if isinstance(ocr_result, dict):
                # PaddleOCR 3.x 格式
                rec_texts = ocr_result.get('rec_texts', [])
                rec_scores = ocr_result.get('rec_scores', [])
                rec_polys = ocr_result.get('rec_polys', [])
                for i, text in enumerate(rec_texts):
                    conf = float(rec_scores[i]) if i < len(rec_scores) else 1.0
                    if conf >= min_conf:
                        texts.append({
                            'text': text,
                            'confidence': conf,
                            'bbox': (
                                rec_polys[i].tolist()
                                if i < len(rec_polys) else None
                            )
                        })
            elif isinstance(ocr_result, list):
                # PaddleOCR 2.x 格式
                for line in ocr_result:
                    if line and len(line) >= 2:
                        text = line[1][0]
                        conf = float(line[1][1])
                        if conf >= min_conf:
                            texts.append({
                                'text': text,
                                'confidence': conf,
                                'bbox': line[0]
                            })

        logger.info(f"OCR 识别 {len(texts)} 行, 耗时 {duration:.3f}s")
        return texts

    def release(self):
        global _ocr_instance, _ocr_init_config
        with _ocr_lock:
            _ocr_instance = None
            _ocr_init_config = None
        import gc
        gc.collect()
