@echo off
REM Doble clic: prepara Python y las dependencias (solo la primera vez o tras actualizar).
REM Usa Python 3.12 (o 3.11 / 3.13). Versiones mas nuevas no tienen aun todas las
REM librerias precompiladas y pip intentaria compilarlas (error "Visual C++ 14.0").
cd /d "%~dp0"
set PY=
py -3.12 --version >nul 2>&1 && set PY=py -3.12
if not defined PY py -3.11 --version >nul 2>&1 && set PY=py -3.11
if not defined PY py -3.13 --version >nul 2>&1 && set PY=py -3.13
if not defined PY (
  echo No se encuentra Python 3.12, 3.11 ni 3.13.
  echo.
  echo Instala Python 3.12 desde:
  echo   https://www.python.org/downloads/release/python-31210/
  echo   ^(abajo: "Windows installer 64-bit"^). Marca "Add python.exe to PATH".
  echo Puedes tener varias versiones a la vez: no hace falta desinstalar la otra.
  echo Despues vuelve a abrir este fichero.
  pause
  exit /b 1
)
echo Usando %PY%
REM Si el entorno existente es de otra version de Python, se rehace.
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)"
  if errorlevel 1 (
    echo El entorno .venv es de una version de Python no compatible: se vuelve a crear.
    rmdir /s /q .venv
  )
)
if not exist .venv\Scripts\python.exe (
  echo Creando el entorno de Python...
  %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install --only-binary=:all: -r requirements.txt || goto :error
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
