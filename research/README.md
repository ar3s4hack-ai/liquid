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
```
