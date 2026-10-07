# Despliegue de la interfaz

La interfaz (`xrpbot_ui`) es un **proceso separado del bot**:

- No carga `.env`, no tiene claves de Kraken y no habla con el exchange.
- Escucha **solo en 127.0.0.1**: el código se niega a arrancar con otra IP.
- El acceso remoto, por ejemplo desde el móvil, es **solo por Tailscale**.
  Nunca se abre un puerto a internet.
- Por defecto funciona en **solo lectura** (`config/ui.yaml → read_only: true`).

Este documento cubre:
1. Primeros pasos, en cualquier sistema
2. Windows (desarrollo y SIM), con el Programador de tareas o NSSM
3. Linux en VPS (LIVE), con systemd o Docker
4. Acceso desde el móvil con Tailscale
5. Mantenimiento: actualizar, copias de seguridad y auditoría
6. Lista de comprobación de seguridad

---

## 1. Primeros pasos

```bash
pip install -r requirements.txt          # añade fastapi, uvicorn, jinja2, argon2-cffi
python -m xrpbot_ui set-password         # crea el usuario «admin» (mín. 12 caracteres)
python -m xrpbot_ui --demo               # datos simulados, sin bot ni Kraken
```

Abre **http://127.0.0.1:8050** y entra con `admin` y tu contraseña.

- **Escenarios de demostración:** en la cabecera hay un selector «DEMO ·
  escenario» para ver cada caso límite (bot caído, liquidación cercana, límite
  diario, LIVE PILOTO…). También se puede arrancar directamente en uno:
  `python -m xrpbot_ui --demo --scenario caido`.
- **Conectar con el bot real:** en `config/ui.yaml` pon `source: bot` y
  `mode: sim` o `live`. Hasta que se confirme el contrato (`docs/ui/contrato.md`),
  la interfaz solo lee el historial, las paradas y la posición activa, y el
  control está desactivado.
- **Activar los controles:** pon `read_only: false`. Aun así, cada acción exige
  desbloquear el modo control con tu contraseña (5 minutos) y su propia
  confirmación.

---

## 2. Windows (PC de desarrollo y SIM)

### Opción A: script con reinicio automático

```bat
scripts\run_ui.bat
```

Arranca la interfaz y la reinicia a los 10 s si se cae. Ciérrala con Ctrl + C.

### Opción B: Programador de tareas (arranque al iniciar sesión)

1. Abre el **Programador de tareas** y elige **Crear tarea…** (no la básica).
2. **General:** nombre `xrpbot-ui` y marca «Ejecutar tanto si el usuario inició
   sesión como si no».
3. **Desencadenadores:** «Al iniciar el sistema» o «Al iniciar sesión».
4. **Acciones:**
   - Programa: `C:\ruta\BOT\scripts\run_ui.bat`
   - Iniciar en: `C:\ruta\BOT`
5. **Configuración:** marca «Si la tarea no se está ejecutando, reiniciarla cada
   1 minuto».

Haz lo mismo con `scripts\run_live.bat` para el bot, o con un script equivalente
para SIM.

### Opción C: NSSM (servicio de Windows)

[NSSM](https://nssm.cc) convierte un programa en un servicio con reinicio automático:

```bat
nssm install xrpbot-ui "C:\ruta\BOT\.venv\Scripts\python.exe" "-m xrpbot_ui"
nssm set xrpbot-ui AppDirectory "C:\ruta\BOT"
nssm set xrpbot-ui AppStdout "C:\ruta\BOT\logs\ui.log"
nssm set xrpbot-ui AppStderr "C:\ruta\BOT\logs\ui.log"
nssm set xrpbot-ui AppRestartDelay 10000
nssm start xrpbot-ui
```

> **Recomendación:** mueve el proyecto **fuera de OneDrive**, por ejemplo a
> `C:\xrpbot`. OneDrive sincroniza a la nube `.env` (con claves) y las bases de
> datos SQLite, y puede bloquear ficheros que el bot está escribiendo.

---

## 3. Linux en VPS (LIVE)

### Opción A: systemd (sin Docker)

```bash
sudo useradd --system --home /opt/xrpbot xrpbot        # usuario del BOT (lee .env)
sudo useradd --system --home /opt/xrpbot xrpbot-ui     # usuario de la INTERFAZ (sin acceso a .env)
sudo mkdir -p /opt/xrpbot && sudo chown xrpbot:xrpbot /opt/xrpbot
# copia el proyecto a /opt/xrpbot, crea .venv e instala requirements.txt
sudo chmod 600 /opt/xrpbot/.env && sudo chown xrpbot:xrpbot /opt/xrpbot/.env
sudo mkdir -p /opt/xrpbot/ui-state && sudo chown xrpbot-ui:xrpbot-ui /opt/xrpbot/ui-state
sudo chmod 750 /opt/xrpbot/state && sudo setfacl -m u:xrpbot-ui:rx /opt/xrpbot/state    # la UI solo LEE el estado del bot
sudo cp deploy/systemd/xrpbot-*.service /etc/systemd/system/
sudo -u xrpbot-ui /opt/xrpbot/.venv/bin/python -m xrpbot_ui set-password
sudo systemctl daemon-reload
sudo systemctl enable --now xrpbot-bot xrpbot-ui
journalctl -u xrpbot-ui -f
```

Las unidades usan `ProtectSystem=strict`. Además, la de la interfaz declara
`InaccessiblePaths=/opt/xrpbot/.env`, así que aunque alguien comprometiera la
interfaz no podría leer las claves.

### Opción B: Docker Compose

```bash
docker compose build
docker compose up -d bot-live ui            # o bot-demo para SIM
docker compose run --rm --entrypoint python ui -m xrpbot_ui set-password
```

- **Red:** el servicio `ui` usa `network_mode: host` (solo Linux), de modo que
  `127.0.0.1` es el del servidor y no el del contenedor.
- **Volúmenes:**
  - Monta `state/` y `data/` en **solo lectura**.
  - Escribe únicamente en `ui-state/`.
  - No recibe `.env`.

---

## 4. Acceso desde el móvil: Tailscale

Tailscale crea una red privada cifrada entre tus dispositivos. No hay que abrir
puertos en el router ni exponer nada a internet.

1. Instala Tailscale en el **VPS o PC del bot** y en el **móvil**, con la misma
   cuenta.
2. En la máquina del bot, publica la interfaz **solo dentro de tu tailnet**:
   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8050
   tailscale serve status        # muestra la URL: https://<maquina>.<tailnet>.ts.net
   ```
   (En Windows: `tailscale serve --bg 8050` desde un terminal de administrador.)
3. En `config/ui.yaml` pon `secure_cookies: true`, porque el acceso pasa a ser
   HTTPS, y reinicia la interfaz.
4. Abre `https://<maquina>.<tailnet>.ts.net` en el móvil.

**No uses `tailscale funnel`:** funnel publica en internet. `serve` solo es
accesible desde los dispositivos de tu tailnet.

Opcional, en la consola de Tailscale: restringe con ACL qué dispositivos pueden
llegar al puerto 443 de la máquina del bot, y activa la caducidad de claves de
los dispositivos.

---

## 5. Mantenimiento

| Tarea | Cómo |
|---|---|
| Actualizar | `git pull`, `pip install -r requirements.txt` y reinicia `xrpbot-ui` (el bot puede seguir en marcha) |
| Cambiar contraseña | `python -m xrpbot_ui set-password` (cierra todas las sesiones abiertas) |
| Copia de seguridad | `state/` (bot) y `ui-state/` (usuarios y auditoría). Con el bot parado, o con `sqlite3 x.sqlite ".backup y.sqlite"` |
| Auditoría | Pantalla **Registro → Auditoría**, o `sqlite3 ui-state/ui.sqlite "select * from audit order by id desc limit 50"` |
| Tests | `python -m pytest -q` (incluye kill switch, cierre, rearme, solo lectura, CSRF, límite de intentos y aislamiento de claves) |

---

## 6. Lista de comprobación de seguridad

- [ ] `host: 127.0.0.1` en `config/ui.yaml` (la interfaz rechaza cualquier otro valor).
- [ ] Acceso remoto solo con `tailscale serve`, nunca con `funnel` ni abriendo puertos.
- [ ] `secure_cookies: true` si entras por HTTPS (Tailscale).
- [ ] Contraseña de 12 caracteres o más, distinta de cualquier otra.
- [ ] `read_only: true` salvo que necesites controlar el bot desde la interfaz.
- [ ] `.env` con `chmod 600`, propiedad del usuario del bot. La interfaz corre con otro usuario.
- [ ] Claves de Kraken sin permiso de transferencias ni retiradas y restringidas por IP.
- [ ] Proyecto fuera de carpetas sincronizadas (OneDrive, Dropbox…).
