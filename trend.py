"""Tendencia de fondo de BTC (diario + 4h): un indicador pequeño de compra / venta. No se dibuja nada en el gráfico.

Tres votos, solo con velas cerradas de Binance Futuros:
  1) el cierre diario está por encima de la EMA100 diaria;
  2) Elliott: el oscilador EWO 5/35 diario está en impulso alcista (rompió su banda superior y no ha vuelto a cero);
  3) en 4h la EMA50 está por encima de la EMA200.
COMPRA = 3 de 3 · ESPERA = 1 o 2 · VENTA = 0. En ESPERA y VENTA la regla está fuera del mercado:
VENTA significa vender / salir, no abrir cortos (en el estudio los cortos con estas reglas perdieron dinero).

Estudio (research/): BTCUSDT 2018-2026 con comisiones y funding, dentro y fuera de muestra y contra el azar.
SMC/ICT, rango asiático, Elliott por ondas, Bollinger, votos de indicadores de 5m y EQH/EQL no superaron la prueba.

Aparte, y sin cambiar la etiqueta: la «liquidez Zero Lag» de 4h del script de Pine del usuario (niveles en las mechas de
velas con mucho volumen; dos cierres al otro lado de un nivel giran la tendencia). Sola funciona, pero en 2022 perdió más.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

EMA_D = 100                       # EMA del cierre diario
EWO_FAST, EWO_SLOW = 5, 35        # Elliott Wave Oscillator: SMA5 − SMA35 del precio medio (máximo + mínimo) / 2
EWO_BAND = 35                     # bandas de ruptura: media exponencial de la parte positiva / negativa del EWO
EMA_FAST, EMA_SLOW = 50, 200      # medias de 4h
DAY, H4 = 86400, 14400
N_DAILY, N_H4 = 500, 1500         # velas que se piden (las medias y los niveles de liquidez se estabilizan de sobra)
TTL = 300                         # se recalcula cada 5 min como mucho
LABEL = {3: "compra", 2: "espera", 1: "espera", 0: "venta"}

# Resultados del estudio (research/final_stats.py, mismo código que aquí): comprado solo con 3 de 3, el resto fuera.
# Spot: comisión 0,1 % por lado. Futuros: 0,06 % por lado + funding real (aguantar en futuros también lo paga).
STUDY = {
    "updated": "2026-10-08",
    "spot": {"from": "2018-01", "ret": 19.63, "hold": 4.96, "dd": -0.49, "hold_dd": -0.81, "trades": 48, "win": 0.40,
             "avg_win": 0.32, "avg_loss": -0.05, "days_win": 47, "days_loss": 6, "expo": 0.37,
             "oos_ret": 1.22, "oos_hold": 0.93, "oos_dd": -0.22, "oos_hold_dd": -0.53},
    "fut": {"from": "2020-08", "ret": 8.29, "hold": 2.59, "dd": -0.33, "hold_dd": -0.79, "trades": 30, "win": 0.50,
            "oos_ret": 0.99, "oos_hold": 0.59, "oos_dd": -0.23, "oos_hold_dd": -0.54, "shorts": -0.37},
    "years": {"2018": [-0.37, -0.72], "2022": [-0.01, -0.65], "2023": [0.61, 1.56], "2024": [0.97, 1.21]},
    "p7": {"compra": 0.573, "espera": 0.480, "venta": 0.511, "base": 0.525},     # P(más alto 7 días después), spot 2018+
}

_state = {"t": 0.0, "v": None, "err": None}
_fetch_lock = threading.Lock()

# ───────── liquidez «Zero Lag» de 4h (script de Pine del usuario) ─────────
ZL_RSI, ZL_MULT, ZL_SMA, ZL_BINS, ZL_MAX = 60, 2.0, 21, 7, 500
_POC = {}       # apertura de la vela de 4h -> POC del volumen dentro de su mecha (velas cerradas: no cambia)
# Estudio de la liquidez sola (research/zl_stats.py, con este mismo código): comprado si alcista, si no fuera.
LIQ_STUDY = {"from": "2018-01", "ret": 13.61, "hold": 4.96, "dd": -0.50, "hold_dd": -0.81, "trades": 68, "win": 0.49,
             "y2018": 0.47, "y2022": -0.38, "or_ret": 78.02, "or_dd": -0.49}      # spot, comisión 0,1 % por lado


def ema(xs, n):
    a = 2.0 / (n + 1)
    out, e = [], None
    for x in xs:
        e = x if e is None else e + a * (x - e)
        out.append(e)
    return out


def sma(xs, n):
    out, s = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def daily_votes(daily):
    """daily: [(apertura_s, o, h, l, c)] velas diarias cerradas, en orden.
    Por día: (voto EMA100, voto EWO, EMA100, EWO, banda de arriba, banda de abajo, estado EWO 1/0/-1)."""
    c = [r[4] for r in daily]
    e = ema(c, EMA_D)
    hl2 = [(r[2] + r[3]) / 2 for r in daily]
    f, s = sma(hl2, EWO_FAST), sma(hl2, EWO_SLOW)
    ewo = [a - b if a is not None and b is not None else 0.0 for a, b in zip(f, s)]
    up = ema([max(x, 0.0) for x in ewo], EWO_BAND)
    lo = ema([min(x, 0.0) for x in ewo], EWO_BAND)
    out, p = [], 0
    for i, (x, u, d) in enumerate(zip(ewo, up, lo)):
        if p == 1 and x < 0:
            p = 0
        elif p == -1 and x > 0:
            p = 0
        if p == 0:
            if x > u:
                p = 1
            elif x < d:
                p = -1
        v1 = c[i] > e[i] if i >= EMA_D else None
        v2 = p == 1 if i >= EWO_SLOW + 5 else None
        out.append((v1, v2, e[i], x, u, d, p))
    return out


def h4_votes(h4):
    c = [r[4] for r in h4]
    f, s = ema(c, EMA_FAST), ema(c, EMA_SLOW)
    return [(f[i] > s[i] if i >= EMA_SLOW else None, f[i], s[i]) for i in range(len(h4))]


def series(daily, h4):
    """Puntuación (0-3) al cierre de cada vela de 4h, con el último día ya cerrado en ese momento.
    Devuelve [(apertura de la vela de 4h, puntuación o None)] y los votos de cada marco."""
    dv = daily_votes(daily)
    by_day = {r[0]: v for r, v in zip(daily, dv)}
    hv = h4_votes(h4)
    out = []
    for r, (v3, _, _) in zip(h4, hv):
        d = by_day.get((r[0] + H4) // DAY * DAY - DAY)     # último día cerrado al cerrar esta vela de 4h
        if d is None or d[0] is None or d[1] is None or v3 is None:
            out.append((r[0], None))
        else:
            out.append((r[0], int(d[0]) + int(d[1]) + int(v3)))
    return out, dv, hv


def compute(daily, h4):
    """Estado actual. daily y h4: velas cerradas [(apertura_s, o, h, l, c)]."""
    if len(daily) < EMA_D + 2 or len(h4) < EMA_SLOW + 2:
        raise ValueError("pocas velas para la tendencia")
    sc, dv, hv = series(daily, h4)
    t_last, score = sc[-1]
    if score is None:
        raise ValueError("faltan días para la tendencia")
    label = LABEL[score]
    k = len(sc) - 1
    while k > 0 and sc[k - 1][1] is not None and LABEL[sc[k - 1][1]] == label:
        k -= 1
    since = since_px = None
    if k > 0 and sc[k - 1][1] is not None:            # el cambio está dentro de las velas pedidas
        since, since_px = sc[k][0] + H4, h4[k][4]
    v1, v2, e100, ewo, up, lo, st = dv[-1]
    v3, f, s = hv[-1]
    return {
        "label": label, "score": score,
        "votes": [
            {"id": "ema100", "on": bool(v1), "ema": round(e100, 1), "close": daily[-1][4]},
            {"id": "ewo", "on": bool(v2), "ewo": round(ewo, 1), "up": round(up, 1), "lo": round(lo, 1), "state": st},
            {"id": "ema4h", "on": bool(v3), "fast": round(f, 1), "slow": round(s, 1)},
        ],
        "flip": round(e100, 1),          # un cierre diario por debajo quita el voto de la EMA100
        "since": since, "since_price": since_px,
        "asof": t_last + H4,             # cierre de la última vela de 4h usada
        "day": daily[-1][0],
        "study": STUDY,
    }


def rsi_wilder(xs, n):
    """RSI con medias de Wilder (como ta.rsi), sin necesidad de numpy."""
    out, up, dn, prev = [], 0.0, 0.0, None
    for x in xs:
        d = 0.0 if prev is None else x - prev
        prev = x
        up += (max(d, 0.0) - up) / n
        dn += (max(-d, 0.0) - dn) / n
        out.append(100.0 if dn == 0 else 100 - 100 / (1 + up / dn))
    return out


def zl_marked(h4):
    """Velas de 4h con mecha de liquidez, como el script: volumen alto (RSI del volumen > 60) y mecha media > 2 × su
    media de 21 velas. [(índice, tipo, arriba, abajo)]: tipo 1 = mecha de arriba dominante, -1 = la de abajo."""
    o, hh, ll, cc = [r[1] for r in h4], [r[2] for r in h4], [r[3] for r in h4], [r[4] for r in h4]
    vol = rsi_wilder([r[5] for r in h4], 14)
    aw = [((min(c_, o_) - l_) + (h_ - max(c_, o_))) / 2 for o_, h_, l_, c_ in zip(o, hh, ll, cc)]
    aW = sma(aw, ZL_SMA)
    out = []
    for i in range(len(h4)):
        lw, uw = min(cc[i], o[i]) - ll[i], hh[i] - max(cc[i], o[i])
        if aW[i] is None or not (vol[i] > ZL_RSI and (lw + uw) / 2 > aW[i] * ZL_MULT and uw != lw):
            continue
        if lw > uw:
            out.append((i, -1, min(cc[i], o[i]), ll[i]))
        else:
            out.append((i, 1, hh[i], max(cc[i], o[i])))
    return out


def poc_from(rows5, top, bot, bins=ZL_BINS):
    """POC del volumen (velas de 5m: [(cierre, volumen)]) dentro de la mecha, en 7 tramos; sin volumen dentro, el primero."""
    seg = (top - bot) / bins
    bounds = [bot + seg * k for k in range(bins + 1)]
    vols = [0.0] * bins
    for c5, v5 in rows5:
        for k in range(bins):
            if bounds[k] <= c5 <= bounds[k + 1]:
                vols[k] += v5
    k = vols.index(max(vols))
    return (bounds[k] + bounds[k + 1]) / 2


def liquidity(h4, pocs, full=False):
    """Tendencia de liquidez de 4h: 1 tras dos cierres por encima de un nivel de mecha de arriba, -1 tras dos por debajo
    de uno de abajo (como «liq_trend» del script). pocs: {apertura: POC}; sin POC, la mitad de la mecha."""
    marked = {i: (ty, top, bot) for i, ty, top, bot in zl_marked(h4)}
    cc = [r[4] for r in h4]
    levels = []                       # [precio, tipo], el más nuevo primero (como array.unshift)
    trend, since, series_ = 0, None, []
    for i in range(len(h4)):
        if i in marked:
            ty, top, bot = marked[i]
            p = pocs.get(h4[i][0])
            levels.insert(0, [(top + bot) / 2 if p is None else p, ty])
            del levels[ZL_MAX:]
        prev = trend
        k = len(levels) - 1
        while k >= 0:                 # del más viejo al más nuevo, como el bucle del script
            p, ty = levels[k]
            if i > 0 and ((cc[i] > p and cc[i - 1] > p) if ty == 1 else (cc[i] < p and cc[i - 1] < p)):
                trend = ty
                levels.pop(k)
            k -= 1
        if trend != prev:
            since = h4[i][0] + H4
        if full:
            series_.append(trend)
    if full:
        return series_
    price = cc[-1]
    if trend >= 0:                    # lo que la pondría bajista: el nivel de abajo más cercano
        cand = [p for p, ty in levels if ty == -1 and p < price]
        flip = max(cand) if cand else None
    else:                             # lo que la pondría alcista: el nivel de arriba más cercano
        cand = [p for p, ty in levels if ty == 1 and p > price]
        flip = min(cand) if cand else None
    return {"trend": trend, "since": since, "flip": round(flip, 1) if flip else None, "levels": len(levels)}


def liq_state(h4, fetch5=None):
    """Liquidez de 4h con el POC real de cada mecha (velas de 5m, se piden una vez y se guardan)."""
    if not h4 or len(h4[0]) < 6:
        return None
    marked = zl_marked(h4)
    need = [(h4[i][0], top, bot) for i, _, top, bot in marked if h4[i][0] not in _POC]

    def one(m):
        t, top, bot = m
        try:
            rows = fetch5(t)
            return t, poc_from(rows, top, bot) if rows else None
        except Exception:
            return t, None             # sin velas de 5m: la mitad de la mecha hasta el próximo intento
    if fetch5 and need:
        with ThreadPoolExecutor(8) as ex:
            for t, p in ex.map(one, need):
                if p is not None:
                    _POC[t] = p
    if len(_POC) > 4000:
        keep = {r[0] for r in h4}
        for t in [t for t in _POC if t not in keep]:
            del _POC[t]
    out = liquidity(h4, _POC)
    out["exact"] = all(h4[i][0] in _POC for i, *_ in marked)
    out["study"] = LIQ_STUDY
    return out


def get(fetch, fetch5=None):
    """fetch(intervalo, límite) -> velas cerradas; fetch5(apertura de 4h) -> [(cierre, volumen)] de sus velas de 5m.
    Se guarda 5 min; si otro hilo ya está pidiendo, se sirve lo que hay."""
    v = _state["v"]
    if v and time.time() - _state["t"] < TTL:
        return v
    if not _fetch_lock.acquire(blocking=v is None):
        return v
    try:
        if _state["v"] and time.time() - _state["t"] < TTL:
            return _state["v"]
        try:
            daily, h4 = fetch("1d", N_DAILY), fetch("4h", N_H4)
            v = compute(daily, h4)
            try:
                v["liq"] = liq_state(h4, fetch5)
            except Exception as e:     # la liquidez es informativa: si falla, la señal sigue
                v["liq"] = {"error": str(e)[:120]}
        except Exception as e:
            _state["err"] = str(e)[:120]
            if _state["v"]:
                return _state["v"]       # mejor el último cálculo que nada
            raise
        _state.update(t=time.time(), v=v, err=None)
        return v
    finally:
        _fetch_lock.release()


def status():
    v = _state["v"]
    liq = (v or {}).get("liq") or {}
    return {"label": v and v["label"], "score": v and v["score"], "asof": v and v["asof"],
            "liq": liq.get("trend"), "liq_exact": liq.get("exact"),
            "age_s": round(time.time() - _state["t"]) if v else None, "error": _state["err"]}
