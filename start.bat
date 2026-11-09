@echo off
chcp 65001 >nul
rem handnote 本机快速启动：建环境 -> 检查更新 -> 装依赖 -> 拉默认字体 -> 起 Web UI
rem 参数 --no-update 或环境变量 HANDNOTE_NO_UPDATE=1 可跳过检查更新
setlocal
rem pushd 比 cd /d 多支持网络路径（\\server\share）
pushd "%~dp0" || goto :fail
set PORT=8765

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 创建虚拟环境...
  python -m venv .venv || goto :fail
)

rem 更新会改写本文件，而 cmd 边读边执行；所以更新与重启放在同一个括号块里，
rem 整块先读入内存再执行，重启后从新文件开头读起
if /i not "%~1"=="--no-update" if not defined HANDNOTE_NO_UPDATE (
  echo [2/4] 检查更新...
  ".venv\Scripts\python.exe" -m handnote.update
  if errorlevel 3 if not errorlevel 4 (
    echo 已更新，重新启动...
    popd
    "%~f0" --no-update
  )
)

rem 依赖只在 pyproject.toml 变化后重装
set STAMP=.venv\.installed
set "OLD="
for %%F in (pyproject.toml) do set PYP=%%~tF
if exist "%STAMP%" set /p OLD=<"%STAMP%"
if not "%OLD%"=="%PYP%" (
  echo [3/4] 安装依赖...
  ".venv\Scripts\python.exe" -m pip install -q -e ".[test]" || goto :fail
  >"%STAMP%" echo %PYP%
)

echo [4/4] 检查默认字体...
".venv\Scripts\python.exe" -m handnote fonts fetch zhimang >nul || goto :fail

rem 端口被占或被系统保留时自动换下一个；绑定成功后由 Python 打开浏览器
echo 启动 Web UI（Ctrl+C 停止）...
".venv\Scripts\python.exe" -m handnote serve --port %PORT% --open || goto :fail
popd
goto :eof

:fail
echo 启动失败，见上方报错。
pause
exit /b 1
