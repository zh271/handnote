@echo off
chcp 65001 >nul
rem handnote 本机快速启动：建环境 -> 装依赖 -> 拉默认字体 -> 起 Web UI
setlocal
cd /d "%~dp0"
set PORT=8765

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建虚拟环境...
  python -m venv .venv || goto :fail
)

rem 依赖只在 pyproject.toml 变化后重装
set STAMP=.venv\.installed
for %%F in (pyproject.toml) do set PYP=%%~tF
set /p OLD=<"%STAMP%" 2>nul
if not "%OLD%"=="%PYP%" (
  echo [2/3] 安装依赖...
  ".venv\Scripts\python.exe" -m pip install -q -e ".[test]" || goto :fail
  >"%STAMP%" echo %PYP%
)

echo [3/3] 检查默认字体...
".venv\Scripts\python.exe" -m handnote fonts fetch zhimang >nul || goto :fail

echo 启动 http://127.0.0.1:%PORT%/   （Ctrl+C 停止）
start "" "http://127.0.0.1:%PORT%/"
".venv\Scripts\python.exe" -m handnote serve --port %PORT%
goto :eof

:fail
echo 启动失败，见上方报错。
pause
exit /b 1
