@echo off
chcp 65001 >nul
title LAN Transfer
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python，请先安装 Python 3 并勾选 "Add to PATH"。
  pause
  exit /b 1
)

python lan_transfer.py --open

echo.
echo 服务已退出，按任意键关闭窗口。
pause >nul
