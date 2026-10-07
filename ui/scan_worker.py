"""扫描后台线程：QThread 子类，跑 ScanPipeline。

线程内：先 pipeline.init()（OCR 加载几秒~十几秒）→ 进入循环
    while not stop:
        result = pipeline.scan_once()
        emit result_ready(ocr_results, matches)  # → Overlay
        write logging.info / warning              # → LogBridge → LogPanel
        wait interval

UI 接：
    worker.status_changed → StatusBar.set_status
    worker.result_ready   → Overlay.update
    worker.start_scan(roi=...)
    worker.set_roi(roi)   # 运行中切换 ROI / 全屏
    worker.stop_scan()
"""
import logging
import time
from PySide6.QtCore import QThread, Signal

from config.config import config
from pipeline.pipeline import ScanPipeline


class ScanWorker(QThread):
    """后台扫描线程。Signal：status_changed(text) / result_ready(ocr_results, matches)"""

    status_changed = Signal(str)
    # ocr_results / matches 都是 list[dict]，用 object 类型让 QObject 跨线程传递更宽松
    result_ready = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pipeline = ScanPipeline()
        self._stop = False
        self._roi = None
        self._roi_dirty = False
        self._initialized = False

    # ---------- 公开 API ----------

    def start_scan(self, roi=None):
        """启动扫描线程。若已在跑则忽略（防重复 start 抛 RuntimeError）。
        roi=None 表示全屏；每次启动都覆盖，避免上一轮的 ROI 残留到全屏模式。"""
        if self.isRunning():
            return
        self._roi = roi
        self._roi_dirty = False
        self._stop = False
        self.start()  # → run()

    def set_roi(self, roi):
        """运行中切换扫描区域（ROI ↔ 全屏）。不重启线程、不重载 OCR，
        在下一次循环边界生效。roi=None 表示全屏。"""
        self._roi = roi
        self._roi_dirty = True

    def stop_scan(self):
        """请求停止。线程在下一次循环边界退出，不打断当前 OCR。"""
        self._stop = True

    def is_stopping(self):
        """已请求停止但线程还没退出（可能正卡在一次 OCR 里）。"""
        return self.isRunning() and self._stop

    # ---------- QThread.run ----------

    def run(self):
        try:
            self._do_init()
            self._do_loop()
        except Exception:
            logging.exception('扫描线程异常')
            self.status_changed.emit('已停止')
        # 注意：worker 退出不释放 pipeline。OCRStage 走模块级 (lang, gpu) 单例，
        # 释放后下次启动会重新加载模型（5–15s + 打印 "初始化 PaddleOCR"），且
        # release 调用本身有 gc 耗时。让模型驻留到进程退出，OS 收回内存。
        # 配置变更（语言 / GPU 开关）由 _get_ocr 内部的元组比对触发重建，安全。

    def _do_init(self):
        self.status_changed.emit('初始化中')
        logging.info('正在初始化 OCR 模型与关键词…')
        t0 = time.time()
        self.pipeline.init()
        self._initialized = True
        self.pipeline.set_roi(self._roi)
        logging.info(f'OCR 初始化完成（{time.time()-t0:.1f}s）')

    def _do_loop(self):
        self.status_changed.emit('运行中')
        consecutive_failures = 0
        FAILURE_THRESHOLD = 5

        while not self._stop:
            if self._roi_dirty:
                # 先清标志再读 _roi：主线程若在中间又改了一次，下一轮还会再应用
                self._roi_dirty = False
                self.pipeline.set_roi(self._roi)
            iv = config.get('scan.interval_seconds')
            interval = float(iv) if iv is not None else 5.0
            t0 = time.time()
            try:
                result = self.pipeline.scan_once()
                consecutive_failures = 0
            except Exception:
                logging.exception('scan_once 失败')
                consecutive_failures += 1
                if consecutive_failures >= FAILURE_THRESHOLD:
                    logging.error(
                        f'scan_once 连续失败 {FAILURE_THRESHOLD} 次，自动停止扫描'
                    )
                    self.status_changed.emit('异常停止')
                    return
                self._sleep_with_check(interval)
                continue

            # 日志：跳过 / 识别行数 / 匹配数 / 耗时
            if result.skipped:
                logging.info(f'帧差跳过（OCR 复用上次结果），耗时 {result.duration*1000:.0f} ms')
            else:
                logging.info(
                    f'OCR {len(result.ocr_results)} 行，'
                    f'命中 {len(result.matches)} 条，'
                    f'耗时 {result.duration*1000:.0f} ms'
                )
            for m in result.matches:
                logging.warning(f'>>> {m.get("keyword","")} → {m.get("hint","")}')

            # Overlay 数据：跳过帧也复用 last result（pipeline 内部已处理）
            self.result_ready.emit(result.ocr_results, result.matches)

            elapsed = time.time() - t0
            self._sleep_with_check(max(0.0, interval - elapsed))

        self.status_changed.emit('已停止')
        logging.info('扫描已停止')

    def _sleep_with_check(self, seconds):
        """分段 sleep，让 stop / 切换区域请求能在 ≤300ms 内响应。"""
        slept = 0.0
        step = 0.3
        while slept < seconds and not self._stop and not self._roi_dirty:
            time.sleep(min(step, seconds - slept))
            slept += step
