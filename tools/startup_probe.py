"""启动耗时探针：拆解「打开程序 → 能出第一帧 OCR 结果」各段耗时，在运行机上跑。

用法（项目根目录，用 GUI 同一个解释器 .venv）：
    .venv\\Scripts\\python.exe tools\\startup_probe.py              # 默认每个变体跑 2 次
    .venv\\Scripts\\python.exe tools\\startup_probe.py --repeat 1 --skip-mobile
需要截屏，必须在已登录的桌面会话里跑（SSH 会话里截不到屏）。

每次测量都在全新子进程里做（import 必须冷启动才有意义），分段计时：
    import paddleocr → import paddle → 构造 PaddleOCR → 首帧真实截图 → 第二帧
（顺序和 GUI 一致：先 paddleocr 再 paddle。反过来先 import paddle 会让随后 modelscope 拉起的
 torch 加载 shm.dll 失败 WinError 127。）
变体：
    旧版         —— 联网源检查 + 同步 import modelscope/torch
    新版         —— 跳过源检查 + modelscope 延迟导入（与 pipeline/ocr_stage.py 一致）
    新版+mobile  —— 新版 + PP-OCRv5 mobile 模型（首次需联网下载，可 --skip-mobile）
结果打印到终端并写入 logs/startup_probe_<时间>.txt，把整段输出贴回即可。
"""
import argparse
import json
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_TAG = '__PROBE_RESULT__'

VARIANTS = {
    'old': dict(label='旧版', new=False, mobile=False),
    'new': dict(label='新版', new=True, mobile=False),
    'new_mobile': dict(label='新版+mobile', new=True, mobile=True),
}


def child(name):
    """子进程：按变体测一次，最后一行输出 JSON 结果。"""
    v = VARIANTS[name]
    t_start = time.perf_counter()
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True' if v['new'] else 'False'
    sys.path.insert(0, PROJECT_ROOT)
    r = {}

    def mark(key, t0):
        r[key] = round(time.perf_counter() - t0, 3)
        return time.perf_counter()

    t = time.perf_counter()
    from config.config import config
    config.load()
    lang = config.get('ocr.language')
    use_gpu = bool(config.get('gpu.enabled'))
    import cv2
    import numpy as np
    import mss
    with mss.mss() as sct:
        frame = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2BGR)
    t = mark('prep(config+截图)', t)

    if v['new']:
        from pipeline.ocr_stage import _defer_heavy_imports
        _defer_heavy_imports()
    from paddleocr import PaddleOCR
    t = mark('import paddleocr', t)
    import paddle  # noqa: F401
    t = mark('import paddle', t)
    r['torch已加载'] = 'torch' in sys.modules

    kwargs = dict(
        lang=lang, device='gpu' if use_gpu else 'cpu', enable_mkldnn=False,
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    if v['mobile']:
        kwargs.update(text_detection_model_name='PP-OCRv5_mobile_det',
                      text_recognition_model_name='PP-OCRv5_mobile_rec')
    ocr = PaddleOCR(**kwargs)
    t = mark('构造 PaddleOCR', t)

    res = ocr.ocr(frame)
    t = mark('首帧 OCR', t)
    ocr.ocr(frame)
    t = mark('第二帧 OCR', t)
    lines = 0
    if res and isinstance(res[0], dict):
        lines = len(res[0].get('rec_texts', []))
    r['识别行数'] = lines
    r['总计(进程内)'] = round(time.perf_counter() - t_start, 3)
    print(RESULT_TAG + json.dumps(r, ensure_ascii=False), flush=True)


def run_variant(name):
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, os.path.abspath(__file__), '--child', name],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        cwd=PROJECT_ROOT, timeout=600,
        # 子进程 stdout 是管道，Windows 默认按 GBK 编码，强制 UTF-8 才能和这里的解码对上
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )
    wall = round(time.perf_counter() - t0, 3)
    for line in reversed(p.stdout.splitlines()):
        if line.startswith(RESULT_TAG):
            r = json.loads(line[len(RESULT_TAG):])
            r['总计(含解释器启动)'] = wall
            return r, None
    tail = '\n'.join((p.stdout + '\n' + p.stderr).strip().splitlines()[-15:])
    return None, f'returncode={p.returncode}\n{tail}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--child')
    ap.add_argument('--repeat', type=int, default=2)
    ap.add_argument('--skip-mobile', action='store_true')
    args = ap.parse_args()
    if args.child:
        child(args.child)
        return

    os.makedirs(os.path.join(PROJECT_ROOT, 'logs'), exist_ok=True)
    out_path = os.path.join(PROJECT_ROOT, 'logs', time.strftime('startup_probe_%Y%m%d_%H%M%S.txt'))
    out = open(out_path, 'w', encoding='utf-8')

    def emit(s=''):
        print(s, flush=True)
        out.write(s + '\n')

    emit(f'Python {sys.version.split()[0]}  {sys.executable}')
    names = [n for n in VARIANTS if not (args.skip_mobile and VARIANTS[n]['mobile'])]
    # 轮转顺序而不是同一变体连跑，减少磁盘缓存冷/热对单个变体的偏置
    for i in range(args.repeat):
        for name in names:
            label = VARIANTS[name]['label']
            emit(f'\n[{i + 1}/{args.repeat}] {label} ...')
            r, err = run_variant(name)
            if err:
                emit(f'  失败: {err}')
                continue
            for k, val in r.items():
                emit(f'  {k:<16} {val}')
    emit(f'\n结果已写入 {out_path}')
    out.close()


if __name__ == '__main__':
    main()
