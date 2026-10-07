# Liquidation Heatmap BTC · v2

Mapa de liquidaciones **estimado** de BTCUSDT (Binance Futuros, perpetuo) con velas en tiempo real,
comprobado contra liquidaciones **reales** de 8 exchanges y contra las posiciones **reales** de Hyperliquid.
No es una señal de entrada.

## Qué se ve
- **Heatmap** (vista Pro por defecto): solo se pinta lo que supera «Total ≥» (24 % de «maxHeat»), en 4 colores
  (turquesa, verde, amarillo, rojo). La vista «Completa» muestra todos los niveles con degradado.
- **Perfil** a la derecha, apilado por apalancamiento, con curvas acumuladas (roja: cortos arriba, verde: largos abajo).
- **Hyperliquid real** (capa «HL», encendida por defecto): Hyperliquid es on-chain y cada posición publica su precio de
  liquidación exacto. Las más grandes salen como líneas discontinuas a la derecha («HL corto 3.5M · 25x»).
  El modelo «Hyperliquid real» pinta esas posiciones en el tiempo (desde que se empezaron a grabar) con la misma vista Pro.
  Solo es Hyperliquid, no todo el mercado.
- **Paneles inferiores** (hasta 3, se eligen en ⚙; en el móvil salen 2): Δ Open Interest por vela, liquidaciones reales
  por vela (escala logarítmica: una cascada enorme no aplasta al resto) y **CVD** (compras − ventas a mercado acumuladas:
  blanco futuros de Binance, violeta spot; cada línea con su escala). El CVD de futuros se mueve en directo con la vela.
- **Sesgo**: reparto de la liquidez cerca del precio (±2 % en 5m … ±20 % en 1D) y el «imán»: la zona más fuerte dentro de ese margen.
- **En directo**: precio y vela de Binance por WebSocket; liquidaciones reales de los 8 exchanges cada 4 s,
  con pulso en el gráfico (≥ 50K $) y aviso ⚡ cuando suman ≥ 250K $ en 2,5 s.
- **Encuadre automático**: el eje de precios incluye las zonas fuertes cercanas aunque las velas no lleguen.
- **Barra de Pools**: valor de cada apalancamiento en el precio del cursor, «Si llega aquí» (acumulado),
  ΔOI, liquidaciones y delta de la vela del cursor, Grupo (grosor del tramo; en amarillo si es tan bajo que las líneas
  salen finísimas, lo normal es dejarlo vacío = automático) e intensidad. En el modelo real, los pools filtran sus posiciones.
- **⚙ Capas**: Perfil, Paneles, Calor/Lado, Zonas, Asia, Liq (burbujas), Libro (muros del libro), HL; y qué paneles van abajo.
- **✓ Validez**: acierto del heatmap frente al azar, calibración, fuentes, **cuánto coincide el mapa estimado con las
  posiciones reales de Hyperliquid** (frente al azar), funding y ratio largos/cortos.

## Cómo se estima
- OI sube en una vela: entran largos y cortos por la misma cantidad al precio típico (máx + mín + cierre) / 3,
  repartidos en 3x, 5x, 10x, 25x, 50x y 100x. Cada nivel vive hasta que el precio lo toca
  (como Hyblock y Trading Different).
- Autocalibración cada 3 h: prueba 36 combinaciones contra las liquidaciones reales guardadas y el modelo «Auto»
  usa la mejor solo si supera a la estándar (25X+50X+100X) en ≥ 5 %. Necesita 100 liquidaciones y 24 h escuchando.

## Fuentes
- Open Interest con histórico: Binance USDT, Binance USDC, Binance COIN-M, Bybit, OKX USDT y OKX USD.
- Open Interest grabado cada minuto: Hyperliquid, Bitget, Deribit y BitMEX (con Coinalyze llegan con histórico).
- Liquidaciones reales con precio (guardadas en SQLite): Binance, Bybit, OKX, Deribit y BitMEX por WebSocket;
  Bitget, Gate y HTX por su API pública cada 10 s.
- Posiciones reales de Hyperliquid: direcciones que operan BTC (canal público de operaciones) y las cuentas más grandes
  de la clasificación pública; se consulta cada una sin pasar del 25 % del límite de su API.
- CVD: velas de Binance Futuros y Spot (compras a mercado frente al volumen total).
- **Coinalyze** (opcional, clave gratis): OI con histórico de los mercados de BTC con más OI que no tenemos (Gate, Kraken…)
  y liquidaciones por vela de los mercados que no escuchamos; también rellena las velas en las que el servidor no estuvo
  escuchando. Como mucho 34 consultas por minuto (su límite es 40). La web cita la fuente con enlace, como piden.

## Despliegue (Railway)
- Comando de inicio: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
- Región de Europa (Binance bloquea EE. UU. con error 451).
- **Volume montado en `/data` y variable `DATA_DIR=/data`**: sin ellos los datos se borran en cada despliegue.

## Variables (todas opcionales)
| Variable | Para qué | Por defecto |
|---|---|---|
| DATA_DIR | carpeta de la base de datos | ./data |
| ACCESS_KEY | si se pone, la web se abre con `/?k=CLAVE` | — |
| COINALYZE_API_KEY | clave gratis de coinalyze.net: OI con histórico de más exchanges y liquidaciones por vela | — |
| HL_REAL · HL_RATE · HL_TOP_LEADERS | posiciones reales de Hyperliquid (0 = apagado), consultas por segundo y cuentas de la clasificación | 1 · 2.5 · 1500 |
| TELEGRAM_TOKEN / TELEGRAM_CHAT_ID | alertas de zona cercana y barrido de Asia | — |
| COINGLASS_API_KEY | clave de CoinGlass: se comprueba cada 6 h qué deja usar el plan (su API es de pago; el heatmap y el mapa solo con Professional) | — |
| ALERT_TF · ALERT_DIST_PCT · ALERT_MIN_RATIO · ALERT_COOLDOWN_MIN | ajustes de las alertas | 15m · 0.4 · 0.6 · 60 |
| ASIA_START_UTC / ASIA_END_UTC | rango asiático en hora UTC | 0 y 7 |
| OI_SOURCES | fuentes de OI activas, separadas por comas | todas |
| DEFAULT_MODEL | auto, oi, vol o hl | auto |
| BIN_PCT · RANGE_PCT · ZONE_GAP_PCT | grosor de tramo (vista Completa), rango y unión de zonas, en % | 0.02 · 8 · 0.04 |
| LIQ_MIN_USD · LIQ_RETENTION_DAYS | burbuja mínima en USD y días guardados | 5000 · 30 |
| CALIB_MIN_EVENTS · CALIB_MIN_HOURS · CALIB_MARGIN | requisitos y margen de la calibración | 100 · 24 · 1.05 |
| CACHE_TTL · VAL_TTL | caché de datos y de validación, en segundos | 60 · 300 |
| COLLECT | 0 desactiva la recogida (pruebas) | 1 |

## Rutas
| Ruta | Qué devuelve |
|---|---|
| `/api/data?tf=5m&model=auto&lev=25,50,100&bin=0.05&ex=binance` | velas, heatmap, zonas, ΔOI, OI total, liquidaciones por vela, CVD, Hyperliquid, contexto |
| `/api/live?after=CURSOR` | liquidaciones reales nuevas (para el directo) |
| `/api/validate?tf=5m` | acierto del heatmap frente al azar y coincidencia con Hyperliquid |
| `/api/calibrate` | fuerza una calibración (una cada 10 min) |
| `/api/status` | estado de los recolectores, la base de datos, Hyperliquid, Coinalyze y la clave de CoinGlass |
| `/api/test-alert` | mensaje de prueba a Telegram |
| `/health` | ok |
