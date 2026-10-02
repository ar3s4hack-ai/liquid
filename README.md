# Liquidation Heatmap BTC

Velas japonesas + heatmap de liquidaciones con tiempo, rango asiático y alertas Telegram.

## Archivos
app.py · requirements.txt · Procfile · static/index.html

## Railway: comando de inicio
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4

## Variables (todas opcionales)
COINGLASS_API_KEY   clave de Coinglass (solo útil con plan que incluya el heatmap por par)
TELEGRAM_TOKEN      token del bot de Telegram (BotFather)
TELEGRAM_CHAT_ID    id del chat que recibe las alertas
ACCESS_KEY          si la pones, la web se abre con  https://TU-DOMINIO/?k=TU_CLAVE
ALERT_TF            temporalidad de las alertas (15m por defecto)
ALERT_DIST_PCT      distancia a la zona para avisar, en % (0.4)
ALERT_MIN_RATIO     intensidad mínima de zona, 0 a 1 (0.6)
ALERT_COOLDOWN_MIN  minutos entre avisos de la misma zona (60)
ASIA_START_UTC / ASIA_END_UTC   rango asiático en hora UTC (0 y 7)
USE_BYBIT           1 suma el Open Interest de Bybit, 0 solo Binance (1)
BIN_PCT             grosor de cada línea del heatmap en % del precio (0.02 ≈ 17 puntos; más alto = más grueso)

## Comprobaciones
/health          responde ok
/api/test-alert  envía un mensaje de prueba a Telegram
