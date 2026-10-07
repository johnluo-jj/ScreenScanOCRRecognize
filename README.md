# ScreenScanOCRRecognize

<img src="ui/icons/app.png" alt="logo" width="96" align="left" />

Windows 屏幕扫描 OCR 工具：**定时截图 → OCR 识别 → 关键词匹配 → 屏幕浮窗提示**。
PySide6 GUI + CLI 两种入口，单引擎 PaddleOCR。

<br clear="left" />

## 核心特性

- **帧差检测自动跳过**：画面无变化时不跑 OCR，省 GPU/CPU
- **ROI 区域扫描**：可框选区域 + 保存多套预设，按需切换
- **Aho-Corasick 子串匹配**：毫秒级、casefold 不区分大小写
- **全局热键**：`Ctrl+Alt+1` ROI 扫描开关 / `Ctrl+Alt+2` 全屏扫描开关（同键再按即停止；扫描中按另一个键直接切换模式）
- **系统托盘**：关闭主窗口缩进托盘后台跑
- **屏幕浮窗**：命中关键词弹半透明卡片（双列：累计命中 / 本次 OCR）+ C 大三和弦提示音
- **关键词文件热重载**：mtime 检测，编辑即生效

## 环境要求

- Windows 10 / 11
- Python 3.10+
- PaddleOCR 3.x（GPU 推荐，CPU 也能跑）

## 安装

### 1. Python 依赖

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 2. GPU 准备（强烈推荐）

开发环境用的是 **CUDA 11.8 + cuDNN 8.x + paddlepaddle-gpu 3.2.2** 验证过的组合。装 CUDA 是一次性投入，OCR 速度通常提升 5–10 倍。

**Step 1：检查显卡驱动 / CUDA 版本**

```powershell
nvidia-smi
```

输出顶部 `CUDA Version: 12.x` 是驱动支持的最高 CUDA 版本（向后兼容），不是已装的 Toolkit 版本。只要驱动 CUDA ≥ 11.8 即可用 11.8 的 paddlepaddle-gpu。

**Step 2：装 CUDA Toolkit 11.8 + cuDNN**

- CUDA Toolkit 11.8：https://developer.nvidia.com/cuda-11-8-0-download-archive
- cuDNN 8.x（匹配 CUDA 11.8）：https://developer.nvidia.com/rdp/cudnn-archive

cuDNN 解压后把 `bin/` `include/` `lib/` 三个目录里的文件复制进 CUDA 安装目录对应位置（默认 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8\`）。

**Step 3：装 paddlepaddle-gpu**

```powershell
# CUDA 11.8（推荐）
pip install paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

# CUDA 12.x 想用对应版本（实验性，不一定有匹配 wheel）：
pip install paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu120/
```

**Step 4：验证**

```powershell
python -c "import paddle; print('paddle', paddle.__version__, 'CUDA:', paddle.is_compiled_with_cuda(), 'GPU#:', paddle.device.cuda.device_count())"
```

应输出 `CUDA: True` 和至少 1 个 GPU。

### CPU 版本（无显卡 / 不想装 CUDA）

```powershell
pip install paddlepaddle==3.2.2
```

装完去 GUI 设置页关掉 GPU 加速，或改 `config/config.yaml`：`gpu.enabled: false`。

## 运行

```powershell
# GUI（推荐）
python app.py
# 或双击 gui.bat（用 .venv\Scripts\pythonw 启动，无控制台窗口）

# CLI（无浮窗，stdout 打印识别结果）
python cli.py
```

首次启动会下载 PaddleOCR 模型（几百 MB），需要网络。

## 配置

主配置 `config/config.yaml`，默认值由 `config/defaults.py` 兜底（缺失键自动补默认值）。GUI 设置页修改会自动写回 yaml。

常用配置项：

| 键 | 默认 | 说明 |
|---|---|---|
| `scan.interval_seconds` | 5.0 | 两次扫描间隔（秒）|
| `scan.enable_roi` | true | 启用 ROI 区域扫描 |
| `scan.roi_rect` | `[1170,256,1880,843]` | ROI 坐标 `[x1,y1,x2,y2]` |
| `scan.diff_threshold` | 5.0 | 帧差跳过阈值（MSE，越小越敏感）|
| `ocr.language` | `ch` | OCR 语言，PaddleOCR 一次只能装一种 |
| `ocr.min_confidence` | 0.3 | 置信度门槛 |
| `gpu.enabled` | true | GPU 加速 |
| `files.banlist_file` | `...desktop\banlist.txt` | 关键词文件路径 |
| `matching.enable_sound` | true | 命中时播 C 大三和弦 |
| `app.minimize_to_tray` | true | 关闭主窗时缩进托盘 |
| `app.startup_mode` | `paused` | `paused`=等用户点开始；`auto`=OCR 加载完自动开扫 |

完整列表见 `config/defaults.py`。

## 关键词文件格式

纯文本，一行一条 `关键词 提示词`：

```
ERROR    红色警告
admin    管理员账号
034:身份证号
```

- **空格分隔**（任意空白）或 **冒号分隔** 都支持
- casefold 比较，不区分大小写
- 子串匹配（不要求边界）—— 注意 `ID` 之类短关键词会匹配 `IDLE` 之类长串
- 文件改动后自动重新加载（mtime 检测）

## 项目结构

```
ScreenScanOCRRecognize/
├── app.py / cli.py / gui.bat / build.spec
├── config/
│   ├── config.py       # Config 单例（DEFAULT_CONFIG + yaml 深合并）
│   ├── defaults.py     # 默认值唯一来源
│   └── config.yaml     # 用户配置（GUI 写回）
├── pipeline/
│   ├── capture.py      # mss 截屏
│   ├── diff_gate.py    # 帧差跳过
│   ├── ocr_stage.py    # PaddleOCR 封装
│   ├── matcher.py      # Aho-Corasick 子串匹配
│   └── pipeline.py     # ScanPipeline 编排
├── utils/              # logger / hotkey
├── ui/                 # PySide6：main_window / scan_worker / overlay / tray / pages/ widgets/ styles/ icons/
├── tests/ docs/ logs/
```

详细架构 / 线程模型 / 扩展模式参考 [CLAUDE.md](CLAUDE.md)。

## 打包成 EXE

```powershell
pyinstaller build.spec
```

产物在 `dist/ScreenScanOCR.exe`。打包后体积较大（含 OCR 模型，几百 MB 起）。目标机器要用 GPU 仍需自行安装 CUDA / cuDNN。

## 测试

```powershell
.venv\Scripts\python tests\test_config_keys.py
.venv\Scripts\python tests\test_capture_enable_roi.py
```

## License

MIT
