"""运行机环境采集脚本（只读，不改任何配置）。

用法（在项目根目录，用项目 .venv 的解释器跑）：
    .venv\\Scripts\\python.exe tools\\collect_env.py            # 完整采集 + 性能基准
    .venv\\Scripts\\python.exe tools\\collect_env.py --no-bench # 跳过基准（不加载 OCR 模型）

输出：logs/env_report_YYYYmmdd_HHMMSS.txt（logs/ 已在 .gitignore 中）。
隐私：不输出关键词内容、不输出 OCR 识别出的文字，只统计数量与耗时。
基准会截当前屏幕跑 OCR；建议先关掉正在运行的 GUI，避免和它抢 GPU 导致数据失真。
"""
import argparse
import datetime
import glob
import io
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)  # logging.file 等相对路径以项目根为准，与 gui.bat 一致

_out = io.StringIO()


def emit(text=''):
    _out.write(text + '\n')
    try:
        print(text)
    except Exception:
        pass


def section(title):
    emit('')
    emit('=' * 78)
    emit(f'## {title}')
    emit('=' * 78)


def run(cmd, timeout=60):
    """跑外部命令，兼容 UTF-8 / 本地代码页（中文 Windows 多为 GBK）输出。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=isinstance(cmd, str))
        raw = p.stdout + (b'\n[stderr]\n' + p.stderr if p.stderr.strip() else b'')
        for enc in ('utf-8', 'mbcs', 'gbk'):
            try:
                return raw.decode(enc).strip()
            except Exception:
                continue
        return raw.decode('utf-8', errors='replace').strip()
    except FileNotFoundError:
        return f'[未找到命令] {cmd}'
    except Exception as e:
        return f'[执行失败] {cmd}: {e!r}'


def powershell(script, timeout=60):
    return run(['powershell', '-NoProfile', '-Command',
                '[Console]::OutputEncoding=[Text.Encoding]::UTF8;' + script], timeout)


def guarded(fn):
    """每个采集段独立兜底，一段失败不影响后续。"""
    def wrapper(*a, **kw):
        try:
            fn(*a, **kw)
        except Exception:
            emit('[本段采集异常]')
            emit(traceback.format_exc())
    return wrapper


def _stats(values, unit='ms'):
    if not values:
        return '无数据'
    v = sorted(values)
    p95 = v[min(len(v) - 1, int(len(v) * 0.95))]
    return (f'n={len(v)} avg={statistics.mean(v):.0f}{unit} '
            f'p50={statistics.median(v):.0f}{unit} p95={p95:.0f}{unit} '
            f'min={v[0]:.0f}{unit} max={v[-1]:.0f}{unit}')


# ---------------------------------------------------------------- 系统

@guarded
def collect_system():
    section('系统 / 硬件')
    import ctypes
    emit(f'OS: {platform.platform()}  ({platform.version()})')
    emit(f'Machine: {platform.machine()}  CPU 逻辑核: {os.cpu_count()}')
    emit(f'CPU: {platform.processor()}')

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('sullAvailExtendedVirtual', ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    emit(f'内存: 总 {m.ullTotalPhys / 2**30:.1f} GB, 可用 {m.ullAvailPhys / 2**30:.1f} GB, 占用 {m.dwMemoryLoad}%')
    emit(f'管理员权限（keyboard 全局热键需要）: {bool(ctypes.windll.shell32.IsUserAnAdmin())}')
    try:
        emit(f'系统 DPI: {ctypes.windll.user32.GetDpiForSystem()} (96=100%, 120=125%, 144=150%)')
    except Exception:
        pass
    emit('')
    emit('CPU 详情:')
    emit(powershell('Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | Format-List | Out-String -Width 200'))
    emit('显卡:')
    emit(powershell('Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate | Format-List | Out-String -Width 200'))
    emit('电源计划:')
    emit(run(['powercfg', '/getactivescheme']))


@guarded
def collect_nvidia():
    section('NVIDIA GPU')
    emit(run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,pstate',
              '--format=csv']))
    emit('')
    emit(run(['nvidia-smi']))


# ---------------------------------------------------------------- 代码 / Python

@guarded
def collect_git():
    section('代码版本')
    emit('branch: ' + run(['git', 'rev-parse', '--abbrev-ref', 'HEAD']))
    emit(run(['git', 'log', '-3', '--oneline']))
    emit('status:')
    emit(run(['git', 'status', '--short']) or '(clean)')


@guarded
def collect_python():
    section('Python / 依赖')
    emit(f'executable: {sys.executable}')
    emit(f'version: {sys.version}')
    emit(f'venv: {sys.prefix != getattr(sys, "base_prefix", sys.prefix)}  prefix={sys.prefix}')
    from importlib import metadata
    keys = ['paddlepaddle', 'paddlepaddle-gpu', 'paddleocr', 'paddlex', 'numpy', 'opencv-python',
            'opencv-contrib-python', 'opencv-python-headless', 'mss', 'PySide6', 'pyahocorasick',
            'keyboard', 'pillow', 'pyyaml']
    emit('关键包:')
    for k in keys:
        try:
            emit(f'  {k:24s} {metadata.version(k)}')
        except metadata.PackageNotFoundError:
            emit(f'  {k:24s} (未安装)')
    emit('')
    emit('pip list:')
    emit(run([sys.executable, '-m', 'pip', 'list', '--format=freeze', '--disable-pip-version-check']))


@guarded
def collect_paddle():
    section('Paddle 运行时')
    t0 = time.time()
    import paddle
    emit(f'import paddle 耗时: {time.time() - t0:.1f}s')
    emit(f'paddle: {paddle.__version__}')
    emit(f'compiled_with_cuda: {paddle.device.is_compiled_with_cuda()}')
    try:
        emit(f'cuda: {paddle.version.cuda()}  cudnn: {paddle.version.cudnn()}')
    except Exception as e:
        emit(f'cuda/cudnn 版本获取失败: {e!r}')
    try:
        emit(f'cuda device_count: {paddle.device.cuda.device_count()}')
        emit(f'当前 device: {paddle.device.get_device()}')
    except Exception as e:
        emit(f'device 查询失败: {e!r}')
    try:
        import paddleocr
        emit(f'paddleocr: {paddleocr.__version__}')
    except Exception as e:
        emit(f'paddleocr 导入失败: {e!r}')


# ---------------------------------------------------------------- 屏幕 / 进程

@guarded
def collect_screen():
    section('屏幕（mss）')
    import mss
    with mss.mss() as sct:
        for i, mon in enumerate(sct.monitors):
            emit(f'  monitors[{i}]: {mon}' + ('  (所有屏合并)' if i == 0 else ''))


@guarded
def collect_processes():
    section('当前 python 进程（GUI 是否在跑）')
    emit(powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | "
        "Select-Object ProcessId,Name,@{n='WorkingSetMB';e={[int]($_.WorkingSetSize/1MB)}},CommandLine | "
        "Format-List | Out-String -Width 300"
    ) or '(无)')


# ---------------------------------------------------------------- 配置 / 词库

@guarded
def collect_config():
    section('配置')
    from config.config import config
    config.load()
    path = os.path.join(PROJECT_ROOT, 'config', 'config.yaml')
    emit(f'config.yaml ({path}):')
    with open(path, encoding='utf-8') as f:
        emit(f.read().rstrip())
    emit('')
    emit('生效值（yaml + defaults 合并后）:')
    for k in ('scan.interval_seconds', 'scan.enable_roi', 'scan.roi_rect', 'scan.enable_diff_skip',
              'scan.diff_threshold', 'ocr.language', 'ocr.min_confidence', 'ocr.enable_image_invert',
              'gpu.enabled', 'matching.display_duration', 'logging.level', 'app.startup_mode'):
        emit(f'  {k} = {config.get(k)!r}')

    from config.config import DEFAULT_BANLIST_FILE
    bl = config.get('files.banlist_file', DEFAULT_BANLIST_FILE)
    if not os.path.isabs(bl):
        bl = os.path.join(PROJECT_ROOT, bl)
    emit('')
    emit(f'词库文件: {bl}')
    if os.path.isfile(bl):
        from pipeline.matcher import parse_keyword_line, _normalize
        with open(bl, 'rb') as f:
            raw = f.read()
        try:
            text = raw.decode('utf-8')
            emit('  编码: UTF-8 OK' + ('（带 BOM）' if raw.startswith(b'\xef\xbb\xbf') else ''))
        except UnicodeDecodeError as e:
            text = raw.decode('utf-8', errors='replace')
            emit(f'  编码: 非 UTF-8！matcher 会加载失败 ({e})')
        lines = text.splitlines()
        # 与 SubstringMatcher.load 同口径：关键词非空且规范化后非空，按规范化去重
        norms = {}
        for l in lines:
            kw, _hint = parse_keyword_line(l)
            if kw and _normalize(kw):
                norms[_normalize(kw)] = kw
        kw_lens = [len(n) for n in norms]
        emit(f'  大小 {len(raw)} B, 总行 {len(lines)}, 有效关键词（去重后） {len(norms)}')
        if kw_lens:
            short = sum(1 for n in kw_lens if n <= 2)
            emit(f'  关键词长度: min={min(kw_lens)} max={max(kw_lens)} avg={statistics.mean(kw_lens):.1f}, <=2 字符的 {short} 个')
    else:
        emit('  [不存在]')


# ---------------------------------------------------------------- 日志分析

_RE_TS = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
_RE_SCAN = re.compile(r'OCR (\d+) 行，命中 (\d+) 条，耗时 (\d+) ms')
_RE_SKIP = re.compile(r'帧差跳过.*耗时 (\d+) ms')
_RE_OCR_CORE = re.compile(r'OCR 识别 (\d+) 行, 耗时 ([\d.]+)s')
_RE_INIT = re.compile(r'OCR 初始化完成（([\d.]+)s）')


@guarded
def collect_logs():
    section('日志分析（logs/app.log*）')
    files = sorted(glob.glob(os.path.join(PROJECT_ROOT, 'logs', 'app.log*')),
                   key=os.path.getmtime)
    if not files:
        emit('未找到日志文件')
        return
    for p in files:
        emit(f'  {os.path.basename(p)}  {os.path.getsize(p) / 1024:.0f} KB  '
             f'mtime={datetime.datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d %H:%M:%S}')

    scan_ms, skip_ms, core_ms, ocr_lines, init_s = [], [], [], [], []
    hits = 0
    levels = {}
    errors = []
    first_ts = last_ts = None
    all_lines = []
    for p in files:
        with open(p, encoding='utf-8', errors='replace') as f:
            all_lines.extend(f.read().splitlines())

    for i, line in enumerate(all_lines):
        m = _RE_TS.match(line)
        if m:
            first_ts = first_ts or m.group(1)
            last_ts = m.group(1)
        for lv in (' - ERROR - ', ' - WARNING - ', ' - MATCH - ', ' - CRITICAL - '):
            if lv in line:
                levels[lv.strip(' -')] = levels.get(lv.strip(' -'), 0) + 1
        if ' - ERROR - ' in line or ' - CRITICAL - ' in line or line.startswith('Traceback'):
            errors.append('\n'.join(all_lines[i:i + 12]))
        if (m := _RE_SCAN.search(line)):
            ocr_lines.append(int(m.group(1)))
            hits += int(m.group(2))
            scan_ms.append(int(m.group(3)))
        elif (m := _RE_SKIP.search(line)):
            skip_ms.append(int(m.group(1)))
        if (m := _RE_OCR_CORE.search(line)):
            core_ms.append(float(m.group(2)) * 1000)
        if (m := _RE_INIT.search(line)):
            init_s.append(float(m.group(1)))

    emit('')
    emit(f'时间跨度: {first_ts} → {last_ts}, 共 {len(all_lines)} 行')
    emit(f'级别计数: {levels}')
    emit(f'OCR 初始化耗时(s): {init_s[-10:]}')
    total = len(scan_ms) + len(skip_ms)
    if total:
        emit(f'扫描轮次 {total}: 执行 OCR {len(scan_ms)}, 帧差跳过 {len(skip_ms)} '
             f'(跳过率 {len(skip_ms) / total:.0%}), 累计命中 {hits}')
    emit(f'整轮耗时（执行 OCR 的轮次）: {_stats(scan_ms)}')
    emit(f'帧差跳过轮次耗时: {_stats(skip_ms)}')
    emit(f'纯 ocr.ocr() 耗时: {_stats(core_ms)}')
    emit(f'每轮识别行数: {_stats(ocr_lines, unit="")}')
    # 最近 200 轮单独看，排除早期旧版本数据干扰
    emit(f'最近 200 轮整轮耗时: {_stats(scan_ms[-200:])}')

    emit('')
    emit(f'ERROR / Traceback 片段（最近 10 条，共 {len(errors)} 条）:')
    for e in errors[-10:]:
        emit('-' * 40)
        emit(e)

    emit('')
    emit('日志尾部 80 行（已去掉 OCR 文本行）:')
    tail = [l for l in all_lines if ' | ' not in l][-80:]
    for l in tail:
        emit('  ' + l)


# ---------------------------------------------------------------- 基准

@guarded
def bench():
    section('性能基准（当前屏幕，真实 config）')
    from config.config import config
    config.load()
    from pipeline.capture import CaptureStage
    from pipeline.diff_gate import DiffGate
    from pipeline.ocr_stage import OCRStage

    cap = CaptureStage()
    roi = config.get('scan.roi_rect')
    roi = tuple(roi) if isinstance(roi, list) and len(roi) == 4 else None
    targets = [('全屏', None)]
    if roi and config.get('scan.enable_roi'):
        targets.insert(0, ('ROI', roi))
    elif roi:
        emit(f'注：enable_roi=False，capture 会强制全屏，ROI {roi} 不单独测')

    frames = {}
    for name, r in targets:
        cap.grab(roi=r)  # 预热（mss 首次创建实例）
        ts = []
        for _ in range(10):
            t0 = time.perf_counter()
            frames[name] = cap.grab(roi=r)
            ts.append((time.perf_counter() - t0) * 1000)
        emit(f'截图[{name}] shape={frames[name].shape}: {_stats(ts)}')

    gate = DiffGate()
    ts = []
    f0 = next(iter(frames.values()))
    for _ in range(20):
        t0 = time.perf_counter()
        gate.should_skip(f0)
        ts.append((time.perf_counter() - t0) * 1000)
    emit(f'DiffGate.should_skip: {_stats(ts)}')

    emit('')
    emit(f'加载 OCR 模型（lang={config.get("ocr.language")}, gpu={config.get("gpu.enabled")}）…')
    t0 = time.perf_counter()
    ocr = OCRStage()
    ocr.init()
    emit(f'OCR init: {time.perf_counter() - t0:.1f}s')
    emit('init 后显存: ' + run(['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader']))

    import logging
    logging.getLogger().setLevel(logging.WARNING)  # 屏蔽 OCR 文本日志，避免把屏幕内容写进报告
    for name, frame in frames.items():
        ocr.recognize(frame)  # 预热（首帧含 kernel 编译 / 显存分配）
        runs = 5 if name == 'ROI' else 3
        ts, n_lines = [], 0
        for _ in range(runs):
            t0 = time.perf_counter()
            res = ocr.recognize(frame)
            ts.append((time.perf_counter() - t0) * 1000)
            n_lines = len(res)
        emit(f'OCR[{name}] shape={frame.shape} 行数={n_lines}: {_stats(ts)}')
    logging.getLogger().setLevel(logging.INFO)
    emit('跑完后 GPU: ' + run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-bench', action='store_true', help='跳过 OCR 性能基准')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    emit(f'# ScreenScanOCRRecognize 运行机环境报告  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
    emit(f'# project_root = {PROJECT_ROOT}')
    collect_system()
    collect_nvidia()
    collect_git()
    collect_python()
    collect_paddle()
    collect_screen()
    collect_processes()
    collect_config()
    collect_logs()
    if not args.no_bench:
        bench()

    os.makedirs(os.path.join(PROJECT_ROOT, 'logs'), exist_ok=True)
    out = os.path.join(PROJECT_ROOT, 'logs', f'env_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(_out.getvalue())
    print(f'\n报告已写入: {out}')


if __name__ == '__main__':
    main()
