@echo off
setlocal EnableExtensions
rem 在 Windows 10/11 上构建 onedir 发布包。
rem 需要：Python 3.12 x64（含 Tkinter）、pip；首次运行会创建 venv 并安装依赖。
rem 输出：%LOCALAPPDATA%\gpu-fan-controller-build\dist\

cd /d "%~dp0.."
set "PROJECT=%CD%"
set "STAGE=%LOCALAPPDATA%\gpu-fan-controller-build"

python -c "import sys; sys.exit(0 if all(ord(c) < 128 for c in r'%STAGE%') else 1)" 2>nul
if errorlevel 1 (
  echo [ERROR] 构建目录包含非 ASCII 字符：%STAGE%
  echo 请把项目复制到 ASCII 路径，或设置一个 ASCII 的 LOCALAPPDATA 后重试。
  exit /b 1
)

echo == 创建构建 venv ==
if not exist "%STAGE%\.venv\Scripts\python.exe" (
  py -3.12 -m venv "%STAGE%\.venv"
  if errorlevel 1 (
    echo [ERROR] 无法创建 venv，请安装 Python 3.12 x64 并勾选 Add to PATH
    exit /b 1
  )
)
call "%STAGE%\.venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r "%PROJECT%\requirements-build.txt"
if errorlevel 1 exit /b 1

echo == 准备 ASCII 构建目录 ==
if exist "%STAGE%\src" rmdir /s /q "%STAGE%\src"
if exist "%STAGE%\dist" rmdir /s /q "%STAGE%\dist"
if exist "%STAGE%\work" rmdir /s /q "%STAGE%\work"
mkdir "%STAGE%\src"
robocopy "%PROJECT%\gpu_fan_controller" "%STAGE%\src\gpu_fan_controller" /E /NFL /NDL /NJH /NJS >nul
robocopy "%PROJECT%\packaging" "%STAGE%\src\packaging" /E /NFL /NDL /NJH /NJS >nul
robocopy "%PROJECT%\tests" "%STAGE%\src\tests" /E /NFL /NDL /NJH /NJS >nul
copy /y "%PROJECT%\config.example.json" "%STAGE%\src\config.example.json" >nul
copy /y "%PROJECT%\pyproject.toml" "%STAGE%\src\pyproject.toml" >nul
copy /y "%PROJECT%\README.md" "%STAGE%\src\README.md" >nul
for /d /r "%STAGE%\src" %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"

cd /d "%STAGE%\src"
python packaging\check_env.py
if errorlevel 1 exit /b 1

echo == PyInstaller: CLI ==
python -m PyInstaller --noconfirm --clean --distpath "%STAGE%\dist" --workpath "%STAGE%\work\cli" packaging\gpu-fan-controller-cli.spec
if errorlevel 1 exit /b 1

echo == PyInstaller: GUI ==
python -m PyInstaller --noconfirm --clean --distpath "%STAGE%\dist" --workpath "%STAGE%\work\gui" packaging\gpu-fan-controller-gui.spec
if errorlevel 1 exit /b 1

echo == 冒烟测试 ==
"%STAGE%\dist\gpu-fan-controller\gpu-fan-controller.exe" --version
if errorlevel 1 exit /b 1
copy /y "%STAGE%\src\config.example.json" "%STAGE%\smoke-config.json" >nul
start /wait "" "%STAGE%\dist\gpu-fan-controller-gui\gpu-fan-controller-gui.exe" --config "%STAGE%\smoke-config.json" --dry-run --smoke-test --allow-concurrent
if errorlevel 1 (
  echo [ERROR] GUI 冒烟测试失败
  exit /b 1
)

(
  echo Python:
  python -VV
  echo PyInstaller:
  python -c "import PyInstaller; print(PyInstaller.__version__)"
  echo Tk:
  python -c "import tkinter; print(tkinter.TkVersion)"
  echo OS:
  ver
  echo Built: %DATE% %TIME%
) > "%STAGE%\dist\BUILD_INFO.txt"

echo == 完成 ==
echo 输出：%STAGE%\dist\
echo 注意：Windows 版本必须在真实 Windows 10/11 上运行验证；本脚本未在 Linux 上构建过。
exit /b 0
