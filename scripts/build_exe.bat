@echo off
REM Compila xrpbot.exe en la carpeta dist\ (tarda unos minutos).
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe py -3.11 -m venv .venv || python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller pillow || goto :error
python scripts\build_exe.py || goto :error
echo.
echo Listo: dist\xrpbot.exe   (copialo a la carpeta que quieras y haz doble clic)
pause
exit /b 0
:error
echo Fallo la compilacion.
pause
exit /b 1
