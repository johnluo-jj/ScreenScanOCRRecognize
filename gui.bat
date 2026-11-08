@echo off
rem 单实例由 app.py 保证：已有实例在运行时，本次启动会把它的主窗口唤到前台后退出。
cd /d %~dp0
if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 未找到 .venv\Scripts\pythonw.exe
    echo 请先创建虚拟环境: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py
