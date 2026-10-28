@echo off
chcp 65001 >nul
title AI 商家营销运营助手 - 一键启动
cd /d "%~dp0"

rem ===== 1. 检查 Python =====
set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    echo.
    echo  [错误] 未检测到 Python。请先安装 Python 3.10 或更高版本，
    echo         安装时务必勾选 "Add python.exe to PATH"。
    echo         下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

rem ===== 2. 首次运行：创建虚拟环境 + 安装依赖 =====
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [首次运行] 正在创建虚拟环境...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo  [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
    echo  [首次运行] 正在安装依赖，请保持网络畅通（约 1-3 分钟）...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
    echo  [完成] 环境准备就绪。
)

rem ===== 3. 配置大模型（DeepSeek） =====
rem 密钥不写死在代码里。首次使用：把 .env.example 复制成 .env，填 LLM_API_KEY。
rem 若系统环境变量里已有 LLM_API_KEY，则直接使用，不覆盖。
if not defined LLM_API_KEY (
    if exist ".env" (
        for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
            if /i "%%a"=="LLM_API_KEY" set "LLM_API_KEY=%%b"
        )
    )
)
if not defined LLM_BASE_URL set "LLM_BASE_URL=https://api.deepseek.com/v1"
if not defined LLM_MODEL set "LLM_MODEL=deepseek-chat"
if not defined LLM_API_KEY (
    echo.
    echo  [提示] 未检测到 LLM_API_KEY，将以 Mock 模式启动。
    echo         全流程仍可演示：测算与追问是规则实现，不依赖大模型。
    echo         需要真实对话时，把 .env.example 复制为 .env 并填入密钥。
    echo.
)

rem ===== 4. 启动服务 =====
echo.
echo  ============================================
echo   启动成功！浏览器访问  http://127.0.0.1:8000
echo   停止服务：关闭本窗口，或按 Ctrl+C
echo  ============================================
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process 'http://127.0.0.1:8000'"

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
