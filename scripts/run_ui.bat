@echo off
REM Arranca la interfaz y la reinicia sola si se detiene. Escucha SOLO en 127.0.0.1.
REM Para el movil: Tailscale (ver docs\ui\DESPLIEGUE.md).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
:loop
echo [%date% %time%] Arrancando la interfaz...
python -m xrpbot_ui
echo [%date% %time%] La interfaz se ha detenido (codigo %errorlevel%). Reinicio en 10 s. Ctrl+C para salir.
timeout /t 10 /nobreak >nul
goto loop
