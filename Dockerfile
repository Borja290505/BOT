FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# usuario sin privilegios
RUN useradd --create-home --uid 1000 bot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY xrpbot ./xrpbot
COPY xrpbot_ui ./xrpbot_ui
COPY config ./config
RUN mkdir -p /app/state /app/data /app/logs /app/reports /app/ui-state && chown -R bot:bot /app
USER bot

# estado, datos y logs fuera del contenedor (volúmenes)
VOLUME ["/app/state", "/app/data", "/app/logs", "/app/reports"]
ENTRYPOINT ["python", "-m", "xrpbot.cli"]
CMD ["run"]
