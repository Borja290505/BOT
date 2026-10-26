# Despliegue 24/7 en un VPS y acceso desde el iPhone

Guía desde cero. Al terminar tendrás:

- El **bot en modo SIM** funcionando 24/7 en un servidor Linux con Docker. Se
  reinicia solo si se cae o si el servidor se reinicia.
- La **interfaz como app en el iPhone** (PWA), con notificaciones push.
- Un **watchdog** que avisa por push y por Telegram si el bot deja de latir.
- **Copias de seguridad diarias** de las bases de datos.
- **Ningún puerto abierto a internet** salvo SSH (y SSH también se puede cerrar,
  paso 8.4). La interfaz solo es accesible por Tailscale.

```
 iPhone (Tailscale) ──── red privada cifrada ────► VPS
                                                    ├─ tailscale serve (HTTPS) → 127.0.0.1:8050
                                                    ├─ ui        interfaz/PWA, SIN claves, solo lee el estado del bot
                                                    ├─ bot       SIM, escribe state/ y su latido cada ~5 s
                                                    ├─ watchdog  lee el latido → push + Telegram si se para
                                                    └─ backup    copia de state/ y ui-state/ cada 24 h
```

> **Antes de empezar.** SIM no usa dinero real ni claves de Kraken. Esta guía
> deja el bot en SIM a propósito. El paso a real está en el README (plan de
> pruebas), y el backtest actual de la estrategia **no cumple** los criterios.
> Un resultado pasado, simulado o real, no garantiza resultados futuros.

Tiempo estimado: 60–90 minutos la primera vez.

---

## 1. Crear el servidor

1. **Proveedor:** cualquiera con VPS en Europa, por ejemplo Hetzner, OVH,
   Contabo o DigitalOcean.
2. **Tamaño:** 2 vCPU y 2–4 GB de RAM sobran. Disco de 20 GB o más.
3. **Sistema:** **Ubuntu 24.04 LTS**.
4. **Ubicación:** Europa (Alemania, Francia o Países Bajos), por latencia y por
   proximidad legal.
5. **Autenticación: clave SSH, no contraseña.** Créala en tu PC con Windows,
   en PowerShell:
   ```powershell
   ssh-keygen -t ed25519 -C "xrpbot-vps"
   # Pulsa Enter para la ruta por defecto y pon una frase de paso.
   Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
   ```
   Copia la línea que empieza por `ssh-ed25519 …` en el campo «SSH key» del
   proveedor. **El fichero sin `.pub` es tu clave privada:** no la compartas
   nunca, ni la pegues en un chat.
6. **Nombre del servidor:** usa uno neutro, como `vps1`. Ese nombre acabará en
   la dirección HTTPS de Tailscale, y los certificados HTTPS son públicos.
   Evita nombres como `bot-trading-borja`.
7. **Copias del proveedor:** si el proveedor ofrece «Backups» o «Snapshots»
   automáticos, actívalos. Es una segunda copia, fuera de la máquina.

Apunta la IP pública del servidor. En esta guía aparece como `IP_DEL_VPS`.

---

## 2. Primer acceso y usuario sin root

Desde PowerShell:

```powershell
ssh root@IP_DEL_VPS
```

Ya en el servidor, como root:

```bash
apt update && apt -y full-upgrade
adduser xrpbot                         # pide una contraseña: será la de sudo; guárdala bien
usermod -aG sudo xrpbot
rsync --archive --chown=xrpbot:xrpbot ~/.ssh /home/xrpbot   # copia tu clave SSH al nuevo usuario
id -u xrpbot                           # apunta el número (normalmente 1000)
```

**Sin cerrar esta ventana**, abre otra ventana de PowerShell y comprueba que
entras con el nuevo usuario:

```powershell
ssh xrpbot@IP_DEL_VPS
sudo whoami        # debe responder: root
```

Desde ahora trabaja siempre como `xrpbot`.

---

## 3. Asegurar SSH: solo clave y sin root

```bash
sudo tee /etc/ssh/sshd_config.d/99-xrpbot.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
X11Forwarding no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

Comprueba desde **otra** ventana que sigues pudiendo entrar con
`ssh xrpbot@IP_DEL_VPS`. Comprueba también que `ssh root@IP_DEL_VPS` ya **no**
entra. Si algo falla, todavía tienes abierta la primera sesión para
deshacerlo.

---

## 4. Cortafuegos

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status verbose
```

- Solo queda abierto el puerto 22 (SSH). La interfaz **no** necesita ningún
  puerto: escucha en `127.0.0.1` y Tailscale la publica dentro de tu red
  privada.
- **Importante con Docker:** los puertos publicados con `ports:` en
  `docker-compose.yml` se **saltan ufw**. Este proyecto no publica ninguno.
  No añadas `ports:` a ningún servicio.
- Si tu proveedor tiene un «Cloud Firewall», configúralo igual: entrada solo
  por TCP 22.

---

## 5. Actualizaciones automáticas y hora

```bash
sudo apt -y install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # responde «Sí»
sudo tee /etc/apt/apt.conf.d/52xrpbot-reboot >/dev/null <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF
timedatectl                                         # «System clock synchronized: yes»
```

- **Parches de seguridad:** se instalan solos.
- **Reinicios:** si un parche lo exige, el servidor se reinicia a las 04:30
  (hora del servidor, UTC).
- **El bot tras un reinicio:** vuelve a arrancar solo
  (`restart: unless-stopped`) y reconcilia su estado con el exchange. Mientras
  tanto, el stop y el TP siguen en Kraken.
- **Si prefieres reiniciar a mano**, pon `"false"`.

La hora sincronizada es imprescindible: la autenticación con Kraken y los
cierres de vela dependen de ella.

---

## 6. Instalar Docker

Sigue el repositorio oficial de Docker para Ubuntu:

```bash
sudo apt -y install ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker xrpbot
exit          # sal y vuelve a entrar para que el grupo «docker» tenga efecto
```

```bash
ssh xrpbot@IP_DEL_VPS
docker run --rm hello-world      # debe imprimir «Hello from Docker!»
```

Docker arranca solo con el sistema. Ten en cuenta que pertenecer al grupo
`docker` equivale a ser root en el servidor. Por eso el usuario `xrpbot` debe
estar protegido con tu clave SSH.

---

## 7. Descargar el proyecto y configurar `.env`

```bash
cd ~
git clone https://github.com/borja290505/bot.git xrpbot
cd xrpbot
mkdir -p state data logs reports ui-state backups
sudo chown -R 1000:1000 state data logs reports ui-state backups   # los contenedores corren con uid 1000
```

- **Repositorio privado:** `git clone` pedirá usuario y contraseña. Como
  contraseña usa un *fine-grained personal access token* de GitHub, de solo
  lectura y solo para ese repositorio (GitHub → Settings → Developer settings).
- **Usuario con otro uid:** si `id -u xrpbot` no dio 1000, el `chown` de arriba
  sigue siendo correcto. Es el uid de dentro del contenedor.

### 7.1 Telegram (canal de respaldo de los avisos)

1. En Telegram, habla con **@BotFather**, envía `/newbot` y sigue los pasos.
   Te dará un **token**.
2. Escribe cualquier mensaje a tu nuevo bot de Telegram.
3. En el servidor, consulta tu id de chat:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
   ```

### 7.2 Fichero `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Rellena **solo** esto (en SIM las claves de Kraken no se usan; déjalas vacías):

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
WATCHDOG_MAX_SILENCE_MIN=5      # [X] minutos sin latido antes de avisar
WATCHDOG_REMIND_MIN=30          # recordatorio mientras dure el problema
WATCHDOG_PING_URL=              # opcional, ver paso 11.3
```

Guarda con Ctrl+O y Enter, y sal con Ctrl+X.

- **Más adelante, para real:** se añaden las claves `KRAKEN_LIVE_*`. Deben
  tener solo los permisos de consulta y trading, **nunca retirada ni
  transferencia**, y estar restringidas a la IP del VPS.
- **Lo que nunca debe salir del servidor:** `.env` no se sube a git ni se pega
  en chats.

### 7.3 Interfaz

```bash
nano config/ui.yaml
```

Cambia estas tres líneas:

```yaml
secure_cookies: true                    # vas a entrar por HTTPS (Tailscale)
push_contact: "mailto:tu-correo@ejemplo.com"
read_only: true                         # déjalo así al principio
```

---

## 8. Tailscale en el VPS

Tailscale crea una red privada cifrada (tu *tailnet*) entre tus dispositivos.
La interfaz solo será visible dentro de ella.

### 8.1 Crear la cuenta

Entra en <https://login.tailscale.com>. Puedes registrarte con Google,
Microsoft, Apple o GitHub. El plan gratuito personal es suficiente.

### 8.2 Instalar en el VPS

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` muestra un enlace. Ábrelo en tu PC y aprueba el servidor.
Después:

```bash
tailscale status        # aparece el VPS (y tus otros dispositivos)
tailscale ip -4         # su IP privada 100.x.y.z
```

En la consola de administración de Tailscale:

1. En **DNS**, activa **MagicDNS** y **HTTPS Certificates**. Sin esto,
   `tailscale serve` no puede dar HTTPS, y el iPhone exige HTTPS para la PWA y
   las notificaciones.
2. En **Machines → (tu VPS) → ⋯**, elige **Disable key expiry**. Así el
   servidor no se desconecta solo a los 180 días.

### 8.3 Publicar la interfaz SOLO en tu tailnet

Se hace en el paso 9.3, cuando la interfaz ya esté en marcha.

> **Nunca uses `tailscale funnel`.** Funnel publica en internet. Usa
> `tailscale serve`, que solo es accesible desde tus dispositivos.

### 8.4 (Opcional, recomendado) SSH solo por Tailscale

Cuando hayas comprobado que entras con `ssh xrpbot@<IP 100.x de tailscale>`:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
sudo ufw delete allow OpenSSH
```

A partir de aquí el servidor no tiene **ningún** puerto abierto a internet.

> Si pierdes el acceso a Tailscale, entra por la consola web del proveedor
> (VNC/«Console») y ejecuta `sudo ufw allow OpenSSH`.

---

## 9. Arrancar en modo SIM

Primero construye la imagen y crea la contraseña de la interfaz (sin usuario,
la interfaz no arranca):

```bash
cd ~/xrpbot
docker compose build
docker compose run --rm ui set-password       # usuario «admin», mínimo 12 caracteres
docker compose up -d
docker compose ps
```

En unos 2 minutos, `bot` y `ui` deben mostrar `(healthy)`, y `watchdog` y
`backup` deben estar `running`.

### 9.1 Ver qué hace

```bash
docker compose logs -f bot          # Ctrl+C para salir (el bot sigue)
docker compose run --rm cli status --mode sim
cat state/heartbeat-sim.json        # el latido: "ts" se renueva cada ~5 s
```

### 9.2 Cambiar la contraseña de la interfaz

```bash
docker compose run --rm ui set-password       # cierra todas las sesiones abiertas
```

### 9.3 Publicarla en Tailscale

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8050
tailscale serve status
```

`tailscale serve status` muestra la dirección, del tipo
`https://vps1.tu-tailnet.ts.net`. Esta configuración sobrevive a reinicios.

---

## 10. Tailscale y la app en el iPhone

1. **Instalar Tailscale:** descárgala del App Store, ábrela e inicia sesión con
   **la misma cuenta** que en el paso 8.1. Acepta **Añadir configuración VPN**
   y deja el interruptor en **Conectado**.
2. **Abrir la interfaz en Safari:** ve a `https://vps1.tu-tailnet.ts.net` (la
   dirección del paso 9.3) y entra con `admin` y tu contraseña.
3. **Instalarla como app:**
   1. Pulsa el botón **Compartir** (el cuadrado con la flecha).
   2. Elige **Añadir a la pantalla de inicio** y pulsa **Añadir**.
   3. Aparece el icono **xrpbot**. Ábrela siempre desde ese icono: se ve a
      pantalla completa, sin barras de Safari, y con la parte inferior
      adaptada al notch y a la barra de inicio.
4. **Activar las notificaciones** (requiere **iOS 16.4 o posterior**: Ajustes →
   General → Información):
   1. Abre la app **desde el icono**.
   2. Ve a **Más → Configuración → Notificaciones en este dispositivo**.
   3. Pulsa **Activar notificaciones** y después **Permitir**.
   4. Pulsa **Enviar prueba**: debe llegarte un aviso en unos segundos.
5. **Las tres pantallas principales** están en la barra inferior: **Resumen**,
   **Posición** y **Riesgo**. El resto está en **Más**.

Si cambias de iPhone o borras la app, repite los pasos 3 y 4.

Si Tailscale está desconectado en el iPhone, la app muestra «Sin conexión con
el panel». Eso **no** significa que el bot esté parado: el bot sigue en el
servidor.

### Qué notificaciones llegan

| Origen | Cuándo | Canales |
|---|---|---|
| **Watchdog** | Sin latido más de `WATCHDOG_MAX_SILENCE_MIN` (5 min); vivo pero sin ciclos correctos 15 min; bot DETENIDO o CONGELADO; recordatorio cada 30 min; RECUPERADO | Push + Telegram |
| **Interfaz** | Eventos del bot de nivel aviso, error o crítico, y entradas y cierres de operaciones (`config/ui.yaml`: `push_min_level`, `push_trades`) | Push |
| **Bot** | Sus alertas propias (entradas, stops, límite diario…) | Telegram |

**Telegram es el respaldo y no debe desactivarse.** El push web depende de
Apple, de que el iPhone tenga red y de que la app siga instalada.

---

## 11. Comprobar que todo funciona

### 11.1 El watchdog avisa

```bash
docker compose stop bot
# espera 6 minutos: debe llegar «[SIM] Bot SIN LATIDO…» por push y Telegram
docker compose start bot
# en menos de 1 minuto: «[SIM] RECUPERADO»
```

### 11.2 Reinicio del servidor

```bash
sudo reboot
# vuelve a entrar al cabo de 1–2 minutos
docker compose ps         # todo arriba otra vez, sin hacer nada
```

### 11.3 (Opcional) Aviso si se cae el servidor entero

Si se cae el servidor, el watchdog cae con él y nadie avisa. Para cubrir ese
caso:

1. Crea una cuenta gratuita en un servicio de «dead man's switch», por ejemplo
   <https://healthchecks.io>.
2. Crea un *check* con *period* de 1 min y *grace* de 5 min. Conecta su
   integración de **Telegram**.
3. Pega su URL de ping en `.env`, en `WATCHDOG_PING_URL=https://hc-ping.com/…`.
4. Aplica el cambio: `docker compose up -d watchdog`.

El watchdog hace ping cada minuto **solo mientras el bot late bien**. Si dejan
de llegar pings (bot parado, watchdog parado o servidor caído), ese servicio
te avisa por Telegram.

---

## 12. Copias de seguridad

- **Qué se copia:** el servicio `backup` copia en caliente, sin parar el bot,
  `state/*.sqlite` (operaciones, órdenes, eventos y estado del límite diario)
  y `ui-state/*.sqlite` (usuarios, auditoría y suscripciones push).
- **Cuándo:** al arrancar y después cada 24 horas.
- **Dónde:** en `backups/AAAA-MM-DD/`, durante 14 días. Cada copia se verifica
  con `PRAGMA integrity_check`.

```bash
ls backups/                          # una carpeta por día
docker compose logs backup           # «Copia OK …»
docker compose run --rm backup backup --source state --source ui-state --dest backups   # copia manual ya
```

**Copia fuera del servidor.** Activa los snapshots del proveedor (paso 1) o
descárgalas de vez en cuando a tu PC por Tailscale:

```powershell
scp -r xrpbot@vps1:~/xrpbot/backups C:\xrpbot-backups
```

**Restaurar** (ejemplo con la base del bot):

```bash
docker compose stop bot
cp backups/2026-10-26/xrpbot-sim.sqlite state/xrpbot-sim.sqlite
rm -f state/xrpbot-sim.sqlite-wal state/xrpbot-sim.sqlite-shm
sudo chown 1000:1000 state/xrpbot-sim.sqlite
docker compose start bot             # al arrancar reconcilia con el exchange
```

---

## 13. Operación diaria

| Tarea | Comando (en `~/xrpbot`) |
|---|---|
| Estado general | `docker compose ps` |
| Log del bot | `docker compose logs -f --tail 200 bot` (los ficheros diarios están en `logs/`; se guardan 30 días) |
| **Kill switch** | `docker compose run --rm cli kill --mode sim` |
| Rearmar tras el límite diario | `docker compose run --rm cli rearm --mode sim` |
| Actualizar el código | `git pull && docker compose up -d --build` |
| Parar todo | `docker compose down` (el estado se conserva en `state/`) |
| Espacio en disco | `df -h` y `docker system df` (limpiar: `docker image prune -f`) |
| Auditoría de la interfaz | Pantalla **Registro → Auditoría** |

**Rotación de logs:**

- Docker guarda como máximo 5 × 20 MB por servicio (`x-logging` en
  `docker-compose.yml`).
- El bot rota además su propio fichero cada medianoche UTC.

---

## 14. Alternativa sin Docker: systemd

Si prefieres no usar Docker, las unidades están en `deploy/systemd/`:

- `xrpbot-bot.service`
- `xrpbot-ui.service`
- `xrpbot-watchdog.service`
- `xrpbot-backup.service` + `xrpbot-backup.timer` (copia diaria a las 03:30 UTC)

El procedimiento para crear los usuarios y la instalación está en
`docs/ui/DESPLIEGUE.md` §3. Para el watchdog, además:

```bash
sudo mkdir -p /etc/xrpbot
sudo tee /etc/xrpbot/watchdog.env >/dev/null <<'EOF'
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
WATCHDOG_MAX_SILENCE_MIN=5
EOF
sudo chmod 600 /etc/xrpbot/watchdog.env
sudo cp deploy/systemd/xrpbot-{watchdog,backup}.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xrpbot-watchdog xrpbot-backup.timer
```

Con systemd los logs van a journald, que los rota solo:
`journalctl -u xrpbot-bot -f`.

---

## 15. Problemas frecuentes

| Síntoma | Causa probable y solución |
|---|---|
| `bot` en `(unhealthy)` | No escribe latido. Revisa `docker compose logs bot`. Un permiso denegado en `state/` se arregla con el `chown 1000:1000` del paso 7 |
| La app dice «Sin conexión con el panel» | Tailscale está desconectado en el iPhone, o la interfaz está caída (`docker compose ps ui`) |
| No aparece «Añadir a la pantalla de inicio» | Ábrelo en **Safari** con la dirección `https://…ts.net` |
| «Las notificaciones exigen HTTPS» | Estás entrando por `http://` o por la IP 100.x. Usa la dirección `https://…ts.net` |
| «En iPhone: pulsa Compartir…» en Configuración | La has abierto en Safari y no desde el icono de la pantalla de inicio |
| No llega la notificación de prueba | Ajustes → Notificaciones → xrpbot → Permitir. Comprueba también el modo Concentración y que el servidor tenga salida a internet |
| Tras iniciar sesión vuelve al login | `secure_cookies: true` y entras por `http://`, o al revés |
| `tailscale serve` pide activar HTTPS | Activa **HTTPS Certificates** en la consola de Tailscale (paso 8.2) |
| No llega nada a Telegram | Revisa `TELEGRAM_*` en `.env`, aplica con `docker compose up -d`, y mira `docker compose logs watchdog` |

---

## 16. Lista de seguridad

- [ ] SSH solo con clave; root y contraseñas desactivados (paso 3).
- [ ] ufw activo; entrada solo por 22, o solo por Tailscale (pasos 4 y 8.4).
- [ ] Ningún `ports:` en `docker-compose.yml`; la interfaz en `127.0.0.1`.
- [ ] Acceso remoto solo con `tailscale serve`, nunca con `funnel`.
- [ ] `.env` con `chmod 600`. La interfaz y el watchdog no lo reciben.
- [ ] `secure_cookies: true` y una contraseña de 12 caracteres o más.
- [ ] `read_only: true` mientras no necesites controlar el bot desde el móvil.
- [ ] Actualizaciones automáticas activas.
- [ ] Copias diarias, más una copia fuera del servidor.
- [ ] (Real) Claves de Kraken sin retirada ni transferencia, restringidas a la
  IP del VPS y distintas de las de cualquier otra máquina.
