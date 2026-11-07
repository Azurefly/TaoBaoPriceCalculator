@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  where python >nul 2>&1
  if errorlevel 1 (
    echo 请先安装 Python 3.11 或以上，并勾选添加至 PATH。
    pause
    exit /b 1
  )
  python scripts\bootstrap.py
) else (
  py -3 scripts\bootstrap.py
)
pause
