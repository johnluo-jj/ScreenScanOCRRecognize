# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

ScreenScanOCRRecognize 是一个 Windows 平台的屏幕扫描 OCR 应用。核心流程：定时截图 → OCR 识别 → 关键词匹配 → 屏幕弹窗提示。支持 GUI（PySide6）和 CLI 两种界面。

当前架构：pipeline（`capture → diff_gate → ocr_stage → matcher`），仅 PaddleOCR 单引擎。

> **重要：开发机 ≠ 运行机。** 本仓库所在的机器**只用于编辑代码**，应用实际运行在另一台机器上。所以**不要**用本机的 `Get-Process pythonw` / `logs/app.log` 的 mtime 来判断 GUI 是否在运行——本机看不到运行机的进程和日志。需要验证行为时，让用户在运行机上启动/查看，或请用户把运行机的日志/截图贴回来。

## 运行与构建

```bash
# GUI 模式（推荐）
python app.py
# 或 Windows 双击 gui.bat（使用项目根 .venv 启动 app.py，pythonw 隐藏控制台）

# CLI 模式
python cli.py

# 安装依赖
pip install -r requirements.txt

# GPU 加速（不在 requirements.txt 中，需单独安装）
# PaddleOCR + CUDA 11.8:
pip install paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
```

注意：`gui.bat` 需要项目根目录已存在 `.venv/` 虚拟环境。`README.md` 中提到的 `gui.py`/`main.py` 是历史名称，当前入口为 `app.py` / `cli.py`。

## 目录结构

```
ScreenScanOCRRecognize/
├── app.py                          # PySide6 GUI 入口（~30 行，QApplication 启动）
├── cli.py                          # CLI 入口（~65 行，纯交互式循环）
├── gui.bat                         # GUI 启动批处理（pythonw 隐藏控制台）
├── build.spec                      # PyInstaller 打包脚本
├── config/
│   ├── config.py                   # Config 单例（~75 行，DEFAULT_CONFIG + yaml 深合并）
│   ├── defaults.py                 # 项目级默认值（DEFAULT_CONFIG / DEFAULT_BANLIST_FILE 唯一来源）
│   └── config.yaml                 # 主配置文件（用户改动会写回此处）
├── pipeline/
│   ├── capture.py                  # CaptureStage（mss 截屏，线程归属检测自动重建）
│   ├── diff_gate.py                # DiffGate（160x120 灰度缩略图 MSE 帧差）
│   ├── ocr_stage.py                # OCRStage（PaddleOCR v2/v3 兼容，单例复用）
│   ├── matcher.py                  # SubstringMatcher（Aho-Corasick 子串匹配）
│   ├── preload.py                  # 启动即后台预加载 OCR 模型（app.py 调 start_preload）
│   └── pipeline.py                 # ScanPipeline（编排 + ScanResult）
├── utils/
│   ├── hotkey.py                   # HotkeyManager（keyboard 库全局热键）
│   └── logger.py                   # logger 单例 + configure_from_config
├── ui/                             # PySide6 全部 UI 代码
│   ├── main_window.py              # MainWindow（QMainWindow + 侧边栏 + StackedWidget）
│   ├── scan_worker.py              # ScanWorker（QThread 子类，跑 ScanPipeline）
│   ├── overlay.py                  # Overlay（无边框 QWidget，paintEvent 双列自绘）
│   ├── tray.py                     # TrayIcon（QSystemTrayIcon 包装）
│   ├── roi_border.py               # ROI 边框可视化
│   ├── log_bridge.py               # logging Handler → Qt Signal 桥接
│   ├── sound.py                    # CHORD_WAV 字节（C 大三和弦提示音）
│   ├── pages/                      # 主路由页面：scan_page / settings_page / about_page
│   ├── widgets/                    # 复用控件：sidebar / config_panel / log_panel / status_bar / settings_card
│   ├── styles/light.qss            # 样式表（{ICON_DIR} 占位符运行时替换）
│   └── icons/                      # SVG 图标
├── tests/                          # pytest 测试（自带 sys.path 注入）
├── docs/                           # 文档 + mockups（设计图/HTML 原型）
└── logs/                           # 应用日志
```

## 架构

### 数据流

```
app.py / cli.py → ScanPipeline.scan_once()
  → CaptureStage.grab(roi) → DiffGate.should_skip() → OCRStage.recognize()
  → SubstringMatcher.match() → Overlay.update()  （仅 GUI 模式）
```

### 核心模块

- **`app.py`** — PySide6 GUI 启动器（~30 行）：`config.load()` → `QApplication` + 加载 `ui/styles/light.qss`（运行时把 `{ICON_DIR}` 替换为 `ui/icons` 绝对路径）→ `MainWindow.show()`。主窗口实现在 `ui/main_window.py`。
- **`cli.py`** — CLI 入口，`config.load()` 后构建 `ScanPipeline`，循环 `scan_once()` 输出匹配。**不弹浮窗**，靠 stdout 打印结果。
- **`pipeline/matcher.py`** — `SubstringMatcher` 类，基于 `pyahocorasick` 自动机的多模式子串匹配（casefold 不区分大小写）。返回 `[{'keyword','hint','ocr_text'}, ...]`。模块级 `get_cached_matcher(path)` 提供按文件路径的单例缓存 + mtime 热重载，`parse_keyword_line(line)` 解析 `关键词 提示词`（空白分隔）或 `关键词:提示词`（冒号兼容）。
- **`pipeline/pipeline.py`** — `ScanPipeline` 类，组合四个阶段（capture/diff_gate/ocr/matcher）+ 上次结果缓存。`scan_once()` 返回 `ScanResult(ocr_results, matches, skipped, duration)`；diff_gate 命中时复用上次的 ocr_results 和 matches，仅刷新 `skipped=True` 与 `duration`。
- **`pipeline/capture.py`** — `CaptureStage`，mss 截屏。**关键：**mss 用线程本地 Windows 设备上下文，`grab()` 内做了 `_owner_thread` 检查，跨线程时自动重建 `mss.mss()`。ROI 模式按入参 rect 直接截取。
- **`pipeline/diff_gate.py`** — `DiffGate`，把帧 BGR 缩成 160x120 灰度缩略图算 MSE，低于 `scan.diff_threshold`（默认 5.0）就跳过 OCR。`reset()` 清空上一帧（设置 ROI 时调用）。
- **`pipeline/ocr_stage.py`** — `OCRStage`，封装 PaddleOCR。模块级单例 `_ocr_instance` + `_ocr_init_config = (lang, gpu)`，配置变化时重建。**PaddleOCR v3** 用 `device='gpu'/'cpu'` 并显式禁用 `use_doc_orientation_classify` / `use_doc_unwarping` / `use_textline_orientation`（屏幕截图始终正向，禁用可避免 PP-LCNet padding bug 并提速）；**v2** 用 `use_gpu=True/False` + `use_angle_cls=True`。
- **`config/config.py`** — `Config` 单例，从 `config/config.yaml` 加载并与 `defaults.DEFAULT_CONFIG` 深度合并（yaml 优先，defaults 兜底）。点号路径访问：`config.get('scan.interval_seconds')`，支持 `config.set()` + `config.save()`。**注意新版不再有 `is_dirty()` / `clear_dirty()` 脏标记机制**——pipeline 各阶段每次扫描都直接 `config.get(...)` 取最新值，无需缓存刷新。
- **`utils/hotkey.py`** — `HotkeyManager`，包装 `keyboard.add_hotkey/remove_hotkey`，`register(hotkey, callback, description)` / `unregister_all()`。
- **`utils/logger.py`** — 模块级 `logger`（StreamHandler，禁 propagate）+ `configure_from_config(cfg)` 按 `logging.level` 调整级别。
- **`pipeline/preload.py`** — `start_preload()` 起 daemon 线程跑 `OCRStage.init()`（构造 + 预热），`app.py` 在建窗口前调用，让 ~13s 模型加载与 UI 构建并行。状态 `idle/loading/ready/failed`，`add_done_callback(fn(state, elapsed))` 回调跑在预加载线程（MainWindow 用 `_preload_done` Signal 转主线程，未开扫时状态栏显示「模型加载中 / 就绪」）。扫描线程不感知预加载：照旧 `pipeline.init()`，靠 `_ocr_lock` 阻塞等待或直接复用；预加载失败时扫描线程会自行重试。
- **`ui/scan_worker.py`** — `ScanWorker(QThread)`，后台线程内 `pipeline.init()` → 循环 `scan_once()`。两个 Signal：`status_changed(str)` → StatusBar；`result_ready(ocr_results, matches)` → Overlay。`stop_scan()` 设标志位，循环以 0.3s 分段 sleep 实现 ≤300ms 退出响应。

### 线程模型（GUI 模式）

- **主线程**：Qt 事件循环。所有 UI 控件、Overlay 绘制都在这里。
- **扫描线程**：`ScanWorker(QThread)`。线程内先 `pipeline.init()`（OCR 加载 5–15 秒），再循环 `scan_once()`。通过 Signal 把状态 / 结果跨线程递回主线程：`status_changed` → StatusBar、`result_ready` → Overlay。
- **热键线程**：`keyboard` 库全局钩子（`HotkeyManager`），回调用 `QMetaObject.invokeMethod` 或闭包配合 `QTimer.singleShot(0, ...)` 切回主线程执行。
- **托盘**：`QSystemTrayIcon`（在 `ui/tray.py`），不开新线程，靠 Qt 信号直连主窗口。
- **日志桥**：`ui/log_bridge.py` 把 `logging` 的 `Handler` 包装成发 Signal 的对象，工作线程 `logging.info()` 自动转成 Qt Signal 写入 `LogPanel`。

同步原语：`ScanWorker._stop`（bool 标志，循环以分段 sleep 检查）、Qt Signal/Slot（线程安全的跨线程通信）。`mss` 截图实例在 `CaptureStage` 内做了线程归属检测，跨线程时自动重建。

### 配置体系

配置文件 `config/config.yaml`，主要分组：
- `scan.*` — 间隔秒数、ROI 开关/保存/边距、帧差检测开关/阈值
- `ocr.*` — 语言、最低置信度、可选图像反色（`enable_image_invert`，黑底白字时开）
- `gpu.*` — `enabled`（pipeline 直接读取的 bool）
- `files.*` — 关键词文件路径（`banlist_file`）
- `matching.*` — 匹配开关、显示时长/位置/字号、声音提示开关
- `logging.*` — 日志级别
- `app.*` — `minimize_to_tray` / `startup_mode`

`config/defaults.py` 是默认值唯一来源，`config.yaml` 中没有的键自动用 defaults 兜底。

## 关键设计决策

**OCR 引擎单例 + 配置感知**：`ocr_stage._get_ocr()` 缓存 `(lang, gpu_enabled)` 元组，配置不变就复用；任意一项变化触发重建。构造过程持 `_ocr_lock`，预加载线程与扫描线程并发进入时只构造一次。`OCRStage.init()` 构造后用一张带字小图预热一次推理（首帧 ~0.6s → 稳态 ~0.2s）。模块顶部 `os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')` 跳过 PaddleX 每次启动的模型源联网检查（要求模型已缓存在本地）。`OCRStage.release()` 显式置空实例 + `gc.collect()`。

**启动耗时排查**：`tools/startup_probe.py` 在运行机上逐子进程冷启动测量 import / 构造 / 预热 / 首帧各段耗时，对比源检查开关、预热、mobile 模型。

**帧差检测跳过 OCR**：`DiffGate` 把每帧缩为 160x120 灰度缩略图，与上一帧算 MSE，低于阈值就 `should_skip()=True`，pipeline 复用 `_last_result.ocr_results / matches`，大幅减少 GPU/CPU 占用。设置 ROI 时 `set_roi()` 会调用 `diff_gate.reset()` 清空上一帧。

**PaddleOCR v2/v3 兼容**：`ocr_stage._get_ocr()` 检测 `paddleocr.__version__` 主版本号选择参数集。v3 显式禁用文档方向/校正/行方向三个模型——屏幕截图都正向，禁用可避免 PP-LCNet padding bug 并提速。

**全局热键**：仅 Windows，依赖 `keyboard` 库（需管理员权限）。`HotkeyManager` 在 `register()` 内 try import，缺失时只记 warning，不影响其它功能。

**扁平包结构**：项目根直接放 `config/` `pipeline/` `utils/` `ui/` 四个顶级包；入口 `app.py` / `cli.py` 在 `import` 之前 `sys.path.insert(0, os.path.dirname(__file__))` 让根目录可被识别为 import 根；`tests/test_*.py` 同样自带 sys.path 注入。包之间用绝对 import（`from config.config import ...`），包内用相对 import（`from .matcher import ...`）。

## 扩展模式

### 添加新管线阶段
1. 在 `pipeline/` 新建模块，参考 `diff_gate.py` 的极简形式：构造 + 一个公开方法（如 `should_skip()` / `recognize()`）
2. 在 `pipeline/pipeline.py` 的 `ScanPipeline.__init__` 实例化，`scan_once()` 中按顺序串联
3. 涉及配置项时在 `config/defaults.py` 的 `DEFAULT_CONFIG` 加默认值，业务代码用 `config.get('key.path')` 读取（无需传 fallback）

### 添加 GUI 控件 / 页面
1. 通用复用控件放 `ui/widgets/`，整页放 `ui/pages/`；都继承 `QWidget`，构造里把 `config` 绑定到 Qt 信号
2. 把页面挂到 `ui/main_window.py` 的 `Sidebar` + `QStackedWidget`
3. 控件双向绑定：构造时 `config.get(...)` 初始化，控件 valueChanged 信号回写 `config.set(...)` + `config.save()`

### 修改配置
- 直接编辑 `config/config.yaml`（缺失键由 `config/defaults.py` 兜底）
- 编程：`config.set('key.path', value)` + `config.save()`

## 匹配与提示逻辑

- `SubstringMatcher` 基于 Aho-Corasick 多模式子串匹配，casefold 不区分大小写。**不做任何模糊/比例匹配**——历史上的"按字符顺序匹配比例"会让短关键词（如 `034`、`da`）误判长串中分散字符的 ID。
- `Overlay` 每次扫描都刷新（包括 diff-skip 和无匹配），左列累计匹配 + 右列本次 OCR；新匹配触发 C 大三和弦 WAV。
