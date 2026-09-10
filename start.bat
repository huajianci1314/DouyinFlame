@echo off
chcp 65001 >nul
title 抖音续火控制台
cd /d "%~dp0"
echo ================================
echo   抖音续火控制台启动中...
echo   启动后浏览器访问 http://127.0.0.1:8765
echo   关闭本窗口即停止服务
echo ================================
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
pause
