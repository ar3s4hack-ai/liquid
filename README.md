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
DEFAULT_MODEL       modelo inicial: oi (Open Interest), vol (volumen) o cg (Coinglass, necesita clave)
ZONE_GAP_PCT        distancia máxima (en %) para unir niveles vecinos en una zona (0.04)
RANGE_PCT           rango de precio dibujado alrededor del precio actual, en % (8)
BIN_PCT             grosor de cada línea del heatmap en % del precio (0.02 ≈ 17 puntos; más alto = más grueso)

## Comprobaciones
/health          responde ok
/api/price       precio actual de Binance (respaldo del precio en directo)
/api/test-alert  envía un mensaje de prueba a Telegram

## Liquidaciones reales y validación (v4)
La web escucha 24 h las liquidaciones reales de BTCUSDT en Binance (solo una muestra: 1 por segundo), Bybit y OKX,
las guarda en SQLite y las dibuja como burbujas (naranja = largos liquidados, cian = cortos).
El botón «✓ Validez» compara las zonas del heatmap (las que había ANTES de cada liquidación) con lo que pasó de verdad
y con el azar. Hacen falta 100 liquidaciones y 24 h escuchando para que el resultado cuente.

IMPORTANTE: en Railway crea un Volume montado en /data y pon la variable DATA_DIR=/data.
Sin él, los datos guardados se borran en cada despliegue.

DATA_DIR            carpeta de la base de datos (./data por defecto; en Railway: /data con un Volume)
LIQ_MIN_USD         tamaño mínimo de burbuja en USD (5000)
LIQ_RETENTION_DAYS  días que se guardan las liquidaciones (30)
COLLECT             1 recoge liquidaciones, 0 no (1)
VAL_TTL             segundos de caché de la validación (300)

/api/validate    resultado de la validación (tf, lev)
/api/status      estado de los recolectores y de la base de datos

## Gráfico (v5)
- Mercado: Binance Futuros, BTCUSDT perpetuo (el mismo que en la app de Binance → Futuros USDⓈ-M).
- Hora: la del dispositivo de cada persona (como la app de Binance). Los datos internos siguen en UTC.
- Tiempo real: velas oficiales de Binance (kline), precio tick a tick (aggTrade) y liquidaciones de Binance al instante.

## v6: modelo mejorado, autocalibración y vista limpia
Modelo (basado en cómo lo hacen Hyblock, Coinglass y otros):
- OI sube: entran largos y cortos por la misma cantidad (cada contrato tiene las dos partes).
- OI baja: se cierran posiciones (vela alcista: cortos; vela bajista: largos).
- Entrada al precio típico de la vela (máx + mín + cierre) / 3.
- Envejecimiento (vida media en horas) y filtro de picos de OI opcionales.
- Apalancamientos 5x, 10x, 25x, 50x y 100x.
Autocalibración: cada 3 h prueba 18 combinaciones contra las liquidaciones reales guardadas y el modelo
«Auto» usa la mejor. Puntúa acierto y confirmación frente al azar. Necesita 100 liquidaciones y 24 h.
Vista limpia: bandas agrupadas y rótulos de la zona fuerte más cercana por encima, por debajo y la mayor.
Libro: muros reales del libro de órdenes de Binance Futuros (capa aparte, no son liquidaciones).
Precio: cuenta atrás de cierre de vela, precio de marca y variación 24 h. Respuestas comprimidas (gzip).

CALIB_MIN_EVENTS    liquidaciones mínimas para calibrar (100)
CALIB_MIN_HOURS     horas mínimas escuchando para calibrar (24)
/api/calibrate      fuerza una calibración (máximo una cada 10 minutos)
