# Estudio de la señal de tendencia (octubre de 2026)

Qué se probó para el indicador de compra / venta de la web, con qué datos y qué salió. Todo con comisiones.

## Datos y método

- **BTCUSDT de Binance** (archivos públicos de data.binance.vision): futuros en velas de 5m y 15m desde enero de 2020
  con el funding real cobrado cada 8 h, y spot en velas de 1h desde agosto de 2017. Hasta el 8 de octubre de 2026.
- **Costes**: futuros 0,06 % por lado (comisión taker + deslizamiento) más el funding; órdenes límite 0,02 %;
  spot 0,1 % por lado. Comprar y aguantar en futuros también paga el funding (≈ 12 % al año de media).
- **Sin mirar al futuro**: la señal se calcula al cierre de la vela y se opera en la siguiente.
- **Dentro de muestra 2020-2023, fuera de muestra 2024-2026.** Lo que solo funciona en una parte no vale.
- **Contra el azar**: posiciones al azar con las mismas duraciones (400 sorteos) y, en los setups con stop y objetivo,
  la misma operación en sentido contrario («espejo»): si la dirección de la señal no aporta nada, las dos dan lo mismo.
- Los setups intradía se simulan recorriendo velas de 5m; si stop y objetivo caen en la misma vela, cuenta el stop.

## Lo que pasó la prueba: la señal publicada

Tres votos de tendencia de fondo (`trend.py`, el mismo código que usa el servidor):

1. cierre diario por encima de la **EMA100 diaria**;
2. **Elliott**: oscilador EWO 5/35 diario en impulso alcista (supera su banda de ruptura y no vuelve a cero);
3. **EMA50 por encima de la EMA200 en 4h**.

COMPRA = 3 de 3 (comprado) · ESPERA = 1 o 2 · VENTA = 0 (fuera en los dos casos).

| | Señal | Comprar y aguantar |
|---|---|---|
| Spot 2018-2026 | +1.963 % · peor caída −49 % | +496 % · −81 % |
| Futuros 2020-2026 (con funding) | +829 % · −33 % | +259 % · −79 % |
| Fuera de muestra 2024-2026 (spot) | +122 % · −22 % | +93 % · −53 % |
| 2018 / 2022 (spot) | −37 % / −1 % | −72 % / −65 % |
| 2023 / 2024 (spot) | +61 % / +97 % | +156 % / +121 % |

- 48 operaciones en spot, 4 de cada 10 ganadoras: las malas se cortan pronto (−5 % de media, ~6 días) y las buenas
  duran (+32 % de media, ~47 días). Fuera del mercado el 63 % del tiempo.
- Probabilidad: con COMPRA, BTC estaba más alto 7 días después el 57 % de las veces (un día cualquiera: 53 %;
  con VENTA: 51 %). A 30 días no hay diferencia. Su valor es esquivar las caídas largas, no adivinar el día a día.
- Contra el azar: mejor que el 99 % de los sorteos con la misma exposición.
- Estable al mover los parámetros (EMA 80-150 diaria, bandas ×0,8-1,2, medias 4h 40/160 a 60/240): Sharpe 1,0-1,25
  frente a 0,64 de aguantar.
- **Cortos**: abrir corto con 0 de 3 perdió −37 % en futuros; 40 de 41 variantes en corto no ganan nada.
- Elegida después de ver 2020-2026: lo de 2024-2026 no es un fuera de muestra puro para la combinación.
  Lo que sí se repite en todos los periodos es que reduce mucho la caída.

## Todo lo probado

Reglas de posición (largo o fuera), futuros desde 2020 con funding. Aguantar: +271-428 % según el marco.
En negrita, las que forman la señal.

| Regla | 15m | 1h | 4h | 1D |
|---|---|---|---|---|
| Precio > EMA100 | | | | **+597 %** |
| Precio > EMA200 | −97 % | +115 % | +698 % | +322 % |
| Cruce EMA50/200 | +165 % | +132 % | **+781 %** | +445 % (1 operación desde 2024) |
| EWO 5/35 > 0 (Elliott) | −100 % | −31 % | +239 % | +493 % |
| EWO con bandas de ruptura | −99 % | −30 % | +305 % | **+555 %** |
| Bollinger 20/2, reversión | −99 % | −83 % | −71 % | +17 % |
| Bollinger 20/2, ruptura | −99 % | −8 % | +215 % | +69 % |
| Estructura SMC (BOS/CHoCH, swing 50) | +40 % | +195 % | +86 % | +200 % |

En 15m y 1h las comisiones se comen cualquier ventaja. Lo que funciona en 4h y diario es seguir la tendencia, y solo en largo.

Setups SMC / ICT y de la captura del bot (R = lo que se arriesga; con objetivo 2:1 el azar gana 1 de cada 3):

| Setup | Gana | Por operación, con comisiones | Antes de comisiones | Espejo |
|---|---|---|---|---|
| Barrido del rango asiático (00-07 UTC), 15m | 29 % | −0,57 R / −0,81 R | ≈ −0,1 R | ≈ 0 |
| Barrido del máximo/mínimo del día anterior, 15m | 36-39 % | −0,14 R / −0,36 R | +0,04 a +0,12 R | similar |
| CHoCH interno a favor del swing (5m, swing 70) | 34 % | −0,26 R / −0,32 R | ≈ 0 | ≈ 0 |
| FVG a favor del swing, 15m (orden límite) | 34-35 % | −0,14 R / −0,13 R | ≈ 0 | ≈ 0 |
| Order block tras BOS/CHoCH, 15m | 32-33 % | −0,25 R / −0,27 R | ≈ −0,05 R | peor |
| Onda 3 de Elliott por zigzag (15m-4h) | 25-55 % | algún ajuste positivo hasta 2023 (sin ser significativo); todos negativos desde 2024 | | |
| Rango de las 9 h de España (vela 9-10 h o rango 0-9 h) | 18-50 % | −0,03 a −0,43 R | ≈ 0 | ≈ 0 |

(dentro de muestra / fuera de muestra). Por horas (Londres, Nueva York), laborables o fines de semana y a favor o en
contra de la tendencia diaria: ningún grupo aguanta en los dos periodos.

- **Lectura por votos en 5m** (RSI 9, RSI 1h, medias 20/50/200, MACD, Bollinger %B, VWAP 24 h, compras a mercado):
  con 4 o más votos alcistas, BTC subió en la hora siguiente el 46-47 % de las veces (normal: 51 %); la ganancia media
  es la misma. Operarlo pierde las comisiones (−12 pb por operación).
- **EQH/EQL como imán**: el nivel más cercano se toca antes el 69 % de las veces, lo mismo que daría el azar solo por
  la distancia (69 %). Con máximos y mínimos sueltos, igual.

Las «bandas de ruptura» del EWO de TradingView (EWO[Giskard], EWO Breaking Bands) no publican su fórmula; aquí son la
media exponencial (35) de la parte positiva y de la negativa del oscilador.

## Datos extra (on-chain, sentimiento, derivados, ETF y macro)

Después se probaron 19 datos más como filtro: comprado solo con 3 de 3 **y** el dato a favor (también como cuarto voto,
3 de 4: no mejora en ningún caso). Umbrales habituales fijados antes de mirar, retraso de publicación de cada fuente
y «azar» = % de veces que el mismo dato desplazado en el tiempo al azar da igual o más Sharpe.

Sharpe de la señal actual → con el filtro (dentro de muestra: hasta 2023; fuera: 2024-2026):

| Dato (a favor = se puede estar comprado) | Desde | Spot dentro | Spot 2024-26 | Futuros dentro | Futuros 2024-26 | Azar spot / futuros |
|---|---|---|---|---|---|---|
| Fear & Greed < 80 (sin euforia) | 2018 | 1,17 → 0,77 | 1,15 → 1,12 | 1,32 → 0,93 | 1,01 → 1,02 | 100 % / 98 % |
| MVRV < 3,5 (sin euforia on-chain) | 2018 | 1,15 → 1,20 | 1,15 → 1,15 | 1,32 → 1,49 | 1,01 → 1,01 | 3 % / 0 % |
| Stablecoins crecen (30 días) | 2018 | 1,15 → 1,30 | 1,15 → 1,04 | 1,32 → 1,24 | 1,01 → 0,91 | 9 % / 38 % |
| Dólar bajo su EMA50 | 2018 | 1,15 → 0,78 | 1,15 → 0,49 | 1,32 → 1,06 | 1,01 → 0,44 | 62 % / 47 % |
| Tipos reales bajan (30 días) | 2018 | 1,15 → 0,98 | 1,15 → 0,22 | 1,32 → 1,27 | 1,01 → 0,15 | 48 % / 52 % |
| Liquidez de la Fed sube (4 semanas) | 2018 | 1,15 → 1,22 | 1,15 → 0,45 | 1,32 → 1,72 | 1,01 → 0,38 | 20 % / 8 % |
| S&P 500 sobre su media de 200 | 2018 | 1,15 → 1,03 | 1,15 → 0,93 | 1,32 → 1,17 | 1,01 → 0,80 | 44 % / 54 % |
| Prima de Coinbase > 0 (7 días) | 2018 | 1,15 → 1,05 | 1,15 → 0,87 | 1,32 → 1,22 | 1,01 → 0,75 | 28 % / 30 % |
| Atención (visitas a «Bitcoin» en Wikipedia) al alza | 2018 | 1,15 → 0,89 | 1,15 → 0,47 | 1,32 → 1,10 | 1,01 → 0,37 | 33 % / 36 % |
| Salen BTC de los exchanges (7 días) | 2018 | 1,15 → **1,52** | 1,15 → 0,94 | 1,32 → **1,58** | 1,01 → 0,87 | 0 % / 3 % |
| Saldo en exchanges baja (30 días) | 2018 | 1,15 → 0,81 | 1,15 → 1,05 | 1,32 → 0,82 | 1,01 → 0,97 | 21 % / 34 % |
| Mineros sin capitular (hash rate 30 > 60 días) | 2018 | 1,15 → 0,94 | 1,15 → 0,94 | 1,32 → 1,03 | 1,01 → 0,81 | 71 % / 81 % |
| Funding sin exceso (7 días, sin el 10 % más alto del año) | 2020 | 1,59 → **1,99** | 1,15 → 0,80 | 1,32 → **1,82** | 1,01 → 0,72 | 6 % / 1 % |
| OI sin subidón (30 días) | 2022 | 1,39 → 1,24 | 1,15 → 0,59 | 1,24 → 1,13 | 1,01 → 0,53 | 98 % / 97 % |
| Largos/cortos sin exceso | 2022 | 1,35 → 1,35 | 1,15 → 1,02 | 1,21 → 1,21 | 1,01 → 0,89 | 41 % / 51 % |
| Volatilidad implícita (DVOL) sin pico | 2021 | 0,77 → 0,82 | 1,15 → 1,02 | 0,62 → 0,68 | 1,01 → 0,93 | 64 % / 56 % |
| ETF con entradas netas (7 días) | 2024 | — | 1,15 → 0,99 | — | 1,01 → 0,87 | 34 % / 38 % |
| Volatilidad realizada sin pico (30 días) | 2018 | 1,26 → 1,36 | 1,15 → 1,22 | 1,32 → 1,31 | 1,01 → 1,11 | 3 % / 20 % |
| Compras a mercado > 50 % (7 días) | 2020 | 1,60 → 1,31 | 1,15 → 1,00 | 1,32 → 1,47 | 1,01 → 0,96 | 9 % / 3 % |

- **Ninguno mejora en los dos periodos y en los dos mercados a la vez.** Los mejores hasta 2023 (salidas de los exchanges,
  funding, liquidez de la Fed) empeoran la señal desde 2024: lo que funcionaba dejó de funcionar, justo lo que se
  quiere evitar.
- Quitar la euforia (Fear & Greed ≥ 80, OI disparado) saca de los mejores tramos alcistas: peor que el azar.
- MVRV > 3,5 solo pasó una vez (2021); en 2024-2026 no llegó y no cambia nada.
- La volatilidad realizada es lo más cercano a aprobar: mejora en spot con casi todos los ajustes, pero en futuros depende
  del umbral (con el percentil 90 de 30 o 60 días no mejora o empeora). No basta para meterla.
- Hacer que la señal cambie menos de estado tampoco ayuda: salir solo con 1 de 3 o pedir 3 de 3 durante 24 h baja el
  Sharpe de 2024-2026 (spot de 1,15 a 0,85 y 0,92); decidir solo al cierre diario da lo mismo (1,11).
- En la literatura pasa lo mismo: el impulso (momentum) es lo que más predice en BTC; el on-chain es lo más débil;
  funding, stablecoins, Fear & Greed y ETF explican poco o van detrás del precio; M2 y dólar, con retrasos inestables.

## Repetirlo

```bash
pip install numpy pandas numba
python3 research/get_data.py          # ~35 MB a research/data/ (hace falta acceso a data.binance.vision)
cd research
python3 trend_rules.py                # EMA, Elliott (EWO), Bollinger y estructura SMC en 15m, 1h, 4h y 1D
python3 ict.py && python3 ict_breakdown.py   # SMC/ICT: Asia, día anterior, CHoCH, FVG, order blocks
python3 photo.py                      # votos de indicadores, EQH/EQL y rango de las 9 h
python3 elliott.py                    # onda 3 por zigzag
python3 ensemble.py && python3 sens.py   # los 3 votos juntos, probabilidades y sensibilidad
python3 final_stats.py                # números publicados, con trend.py del servidor (y comprueba que coincide)
cd .. && python3 research/get_extra.py && cd research   # datos extra (~25 MB)
python3 extra.py && python3 volcheck.py && python3 variants.py   # 19 datos extra, volatilidad y variantes de la regla
```
