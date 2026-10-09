# Liquidation Heatmap BTC · v2

Mapa de liquidaciones **estimado** de BTCUSDT (Binance Futuros, perpetuo) con velas en tiempo real,
comprobado contra liquidaciones **reales** de 8 exchanges y contra las posiciones **reales** de Hyperliquid.
También graba el **libro de órdenes** de Binance para el mapa de liquidez. El mapa no es una señal de entrada; la única
señal es el chip de tendencia de fondo (COMPRA · ESPERA · VENTA), probado con datos desde 2018.

## Qué se ve
- **? Manual** (arriba, junto a ⚙): explica cada cosa en sencillo, por apartados plegables y con los mismos colores
  del gráfico. Para mandarlo a alguien: la dirección de la web terminada en `/#manual` lo abre directamente.
- **Heatmap** (vista Pro por defecto): solo se pinta lo que supera «Total ≥» (24 % de «maxHeat»), en 4 colores
  (turquesa, verde, amarillo, rojo). La vista «Completa» muestra todos los niveles con degradado.
- **Perfil** a la derecha, apilado por apalancamiento, con curvas acumuladas (roja: cortos arriba, verde: largos abajo).
- **Hyperliquid real** (capa «HL», encendida por defecto): Hyperliquid es on-chain y cada posición publica su precio de
  liquidación exacto. Las más grandes salen como líneas discontinuas a la derecha («HL corto 3.5M · 25x»).
  El modelo «Hyperliquid real» pinta esas posiciones en el tiempo (desde que se empezaron a grabar) con la misma vista Pro.
  Solo es Hyperliquid, no todo el mercado.
- **Mapa de liquidez** (⚙ → Mapa: «Liquidaciones», «Liquidez (libro)» o «Las dos»): el libro de órdenes de Binance Futuros
  grabado en el tiempo, como el «Liquidity Heatmap» de CoinGlass. Cuanto más claro, más dinero esperando en órdenes límite
  (blanco = los muros más grandes). Son órdenes reales, no liquidaciones. Empieza a grabar al desplegar: al principio solo
  se ve cerca del precio y se completa con las horas. El cursor dice cuánto hay en ese tramo («Libro ≈ 316K–633K»).
- **Paneles inferiores** (hasta 3, se eligen en ⚙; al añadir un cuarto sale el más antiguo; en el móvil salen 2):
  - Δ Open Interest por vela.
  - Liquidaciones reales por vela (escala logarítmica: una cascada enorme no aplasta al resto).
  - **CVD** (compras − ventas a mercado acumuladas: blanco futuros de Binance, violeta spot; cada línea con su escala).
    El de futuros se mueve en directo con la vela.
  - **Funding** de Binance por periodo (▲ pagan los largos, ▼ pagan los cortos) y el previsto ahora.
  - **Gasolina**: liquidaciones estimadas que siguen pendientes al cierre de cada vela (▲ cortos, ▼ largos).
    Con el modelo «Hyperliquid real», las posiciones reales de Hyperliquid por liquidar desde que se graban.
- **Señal de tendencia** (chip arriba a la izquierda; no se dibuja nada en el gráfico): 3 votos con velas cerradas de
  Binance Futuros: cierre diario sobre la EMA100, Elliott (EWO 5/35 diario en impulso alcista, con bandas de ruptura) y
  EMA50 sobre EMA200 en 4h. **COMPRA** = 3 de 3; **ESPERA** = 1 o 2; **VENTA** = 0 (vender o no comprar; abrir cortos con
  ella perdió dinero). Al tocarlo: los votos, desde cuándo, a qué cierre diario cambia y el estudio. BTC 2018-2026 con
  comisiones: +1.963 % frente a +496 % de aguantar y peor caída −49 % frente a −81 %; acierta 4 de cada 10 operaciones
  y en años muy alcistas gana menos que aguantar. SMC/ICT, rango asiático, ondas de Elliott, Bollinger, votos de
  indicadores de 5m y EQH/EQL no superaron la prueba. Todo en `research/` (scripts y resultados).
- **Sesgo**: reparto de la liquidez cerca del precio (±2 % en 5m … ±20 % en 1D) y el «imán»: la zona más fuerte dentro de ese margen.
- **En directo**: precio y vela de Binance por WebSocket; liquidaciones reales de los 8 exchanges cada 4 s,
  con pulso en el gráfico (≥ 50K $) y aviso ⚡ cuando suman ≥ 250K $ en 2,5 s.
- **Encuadre automático**: el eje de precios incluye las zonas fuertes cercanas aunque las velas no lleguen.
- **Barra de Pools**: valor de cada apalancamiento en el precio del cursor, «Si llega aquí» (acumulado),
  ΔOI, liquidaciones y delta de la vela del cursor, Grupo (grosor del tramo; en amarillo si es tan bajo que las líneas
  salen finísimas, lo normal es dejarlo vacío = automático) e intensidad. En el modelo real, los pools filtran sus posiciones.
- **⚙ Capas**: Perfil, Paneles, Calor/Lado, Zonas, Asia, Liq (burbujas), Libro (muros del libro), HL; qué mapa y qué paneles van abajo.
- **✓ Validez**: acierto del heatmap frente al azar, calibración, fuentes, **cuánto coincide el mapa estimado con las
  posiciones reales de Hyperliquid** (frente al azar), estado de la grabación del libro, funding, ratio largos/cortos y
  qué estrategias se probaron para la señal de tendencia.

## Cómo se estima
- OI sube en una vela: entran largos y cortos por la misma cantidad al precio típico (máx + mín + cierre) / 3,
  repartidos en 3x, 5x, 10x, 25x, 50x y 100x. Precio de liquidación = entrada × (1 ∓ 1/apalancamiento ± margen de
  mantenimiento). Cada nivel vive hasta que el precio lo toca (como Hyblock y Trading Different).
- Autocalibración cada 3 h: prueba 72 combinaciones (apalancamientos, vida media, filtro de picos, cierres y margen de
  mantenimiento del 0,5 % o del 0,4 %, el de Binance para posiciones pequeñas de BTC) contra las liquidaciones reales
  guardadas, y el modelo «Auto» usa la mejor solo si supera a la estándar (25X+50X+100X, 0,5 %) en ≥ 5 %.
  Necesita 100 liquidaciones y 24 h escuchando.

## Fuentes
- Open Interest con histórico: Binance USDT, Binance USDC, Binance COIN-M, Bybit, OKX USDT y OKX USD.
- Open Interest grabado cada minuto: Hyperliquid, Bitget, Deribit y BitMEX (con Coinalyze llegan con histórico).
- Liquidaciones reales con precio (guardadas en SQLite): Binance, Bybit, OKX, Deribit y BitMEX por WebSocket;
  Bitget, Gate y HTX por su API pública cada 10 s.
- Posiciones reales de Hyperliquid: direcciones que operan BTC (canal público de operaciones) y las cuentas más grandes
  de la clasificación pública; se consulta cada una sin pasar del 25 % del límite de su API.
- CVD: velas de Binance Futuros y Spot (compras a mercado frente al volumen total). Funding: histórico de Binance.
- Libro de órdenes de Binance Futuros: instantánea de 1000 niveles por lado + cambios cada 500 ms por WebSocket (con las
  reglas de Binance para no perder cambios; si hay un hueco, se vuelve a sincronizar). Cada 30 s se suma el dinero por
  tramos de 10 $ (±6 % del precio) y se guarda la media de cada 5 min (7 días) y de cada hora (60 días). Las temporalidades
  de 5m y 15m usan los bloques de 5 min; las de 1h, 4h y 1D, los de 1 h.
- **Coinalyze** (opcional, clave gratis): OI con histórico de los mercados de BTC con más OI que no tenemos (Gate, Kraken…)
  y liquidaciones por vela de los mercados que no escuchamos; también rellena las velas en las que el servidor no estuvo
  escuchando. Como mucho 34 consultas por minuto (su límite es 40). La web cita la fuente con enlace, como piden.

## Pruebas
`python3 tests/test_backend.py . /tmp/salida` (servidor) y `node tests/harness.js . /tmp/salida` (web), sin red. Más en `tests/README.md`.
El estudio de la señal de tendencia se repite con los scripts de `research/` (ver `research/README.md`).

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
| ALERT_TF · ALERT_DIST_PCT · ALERT_MIN_RATIO · ALERT_COOLDOWN_MIN | ajustes de las alertas | 15m · 0.4 · 0.6 · 60 |
| ASIA_START_UTC / ASIA_END_UTC | rango asiático en hora UTC | 0 y 7 |
| OI_SOURCES | fuentes de OI activas, separadas por comas | todas |
| DEFAULT_MODEL | auto, oi, vol o hl | auto |
| BIN_PCT · RANGE_PCT · ZONE_GAP_PCT | grosor de tramo (vista Completa), rango y unión de zonas, en % | 0.02 · 8 · 0.04 |
| LIQ_MIN_USD · LIQ_RETENTION_DAYS | burbuja mínima en USD y días guardados | 5000 · 30 |
| CALIB_MIN_EVENTS · CALIB_MIN_HOURS · CALIB_MARGIN | requisitos y margen de la calibración | 100 · 24 · 1.05 |
| CACHE_TTL · VAL_TTL | caché de datos y de validación, en segundos | 60 · 300 |
| BOOK · BOOK_BIN_USD · BOOK_RANGE_PCT | grabación del libro (0 = apagada), tramo en USD y rango en % | 1 · 10 · 6 |
| COLLECT | 0 desactiva la recogida (pruebas) | 1 |

## Rutas
| Ruta | Qué devuelve |
|---|---|
| `/api/data?tf=5m&model=auto&lev=25,50,100&bin=0.05&ex=binance` | velas, heatmap, zonas, ΔOI, OI total, liquidaciones por vela, CVD, funding, gasolina, Hyperliquid, contexto y `trend` (señal de tendencia: etiqueta, votos, cambio de la EMA100, desde cuándo y el estudio) |
| `/api/data?...&book=1` | lo mismo + el mapa de liquidez (`book_map`: por vela, niveles 1–7 del libro en tramos) |
| `/api/data?...&v=2&want=liq,fuel,cvd,walls` | respuesta compacta (la que usa la web): velas como listas y sin burbujas, gasolina, CVD ni muros salvo que se pidan |
| `/api/live?after=CURSOR` | liquidaciones reales nuevas (para el directo) |
| `/api/validate?tf=5m` | acierto del heatmap frente al azar y coincidencia con Hyperliquid |
| `/api/calibrate` | fuerza una calibración (una cada 10 min) |
| `/api/status` | estado de los recolectores, la base de datos, el libro de órdenes, Hyperliquid, Coinalyze y la señal de tendencia |
| `/api/test-alert` | mensaje de prueba a Telegram |
| `/health` | ok |
