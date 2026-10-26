@echo off
REM Doble clic: prepara Python y las dependencias (solo la primera vez o tras actualizar).
cd /d "%~dp0"
set PY=
py -3.11 --version >nul 2>&1 && set PY=py -3.11
if not defined PY py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY (
  echo No se encuentra Python. Instalalo desde https://www.python.org/downloads/
  echo y marca "Add python.exe to PATH" en el instalador. Despues vuelve a abrir este fichero.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Creando el entorno de Python...
  %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :error
if not exist .env (
  copy .env.example .env >nul
  echo Creado .env: abrelo con el Bloc de notas para poner tus claves si vas a usar el modo real.
)
echo.
echo Instalacion completada. Ahora abre 2_MENU.bat
pause
exit /b 0
:error
echo.
echo Fallo la instalacion. Copia el mensaje de error de arriba.
pause
exit /b 1
