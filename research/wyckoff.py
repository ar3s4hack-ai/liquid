"""Wyckoff y VSA con datos reales (BTCUSDT futuros 2020-2026, con comisiones; ver sim.py).

Reglas fijadas antes de mirar los resultados (velas cerradas; entrada a la apertura de la vela siguiente):

1. Spring / upthrust en un rango (15m, 1h, 4h): rango = las 40 velas anteriores, de 3 a 12 ATR de alto, sin
   tendencia dentro (el cierre se movió menos de medio rango) y con al menos dos visitas a cada extremo. Spring: la
   vela perfora el mínimo del rango (como mucho 0,75 ATR) y cierra dentro. Upthrust: lo mismo arriba.
   Stop 0,2 ATR más allá de la mecha; objetivo el otro lado del rango (si da ≥ 1R) o 2R.
   Variantes: volumen bajo (spring nº 3, entrada directa) o alto; y con «test»: en las 10 velas siguientes una vela
   vuelve cerca del soporte sin perforar el spring, con menos del 60 % de su volumen y cerrando en la mitad alta.
2. Clímax de venta / compra (1h, 4h): nuevo mínimo de 20 velas tras caer ≥ 4 ATR, volumen ≥ 2,5 veces su media de 50,
   rango ≥ 1,5 ATR y cierre en la mitad alta (al revés para el clímax de compra). Stop 0,2 ATR más allá, 2R.
3. Esfuerzo sin resultado / absorción (1h, 4h): volumen ≥ 2 veces su media de 20 con rango ≤ 0,6 ATR, en el mínimo
   de 20 velas (compra) o en el máximo (venta). Stop 0,25 ATR más allá, 2R. (En BTC no hay ninguna vela así; se usa
   la versión relativa: volumen por punto de rango ≥ 2 veces su mediana de 100 velas y volumen ≥ 1,3 veces la media.)
4. VSA sin demanda / sin oferta (1h, 4h): vela alcista estrecha (< 0,7 del rango medio) con menos volumen que las dos
   anteriores, sobre su media de 20 → venta; la contraria bajo la media → compra. Stop 0,25 ATR más allá, 2R.
5. Divergencia de esfuerzo en los giros (1h, 4h): máximo más alto con un tramo de subida que lleva menos del 80 % del
   volumen del tramo anterior → venta; mínimo más bajo con menos volumen de bajada → compra. Stop más allá del giro, 2R.

Salida por tiempo: 2 × 40 velas en el spring, 30 velas en el resto."""
import json
import sys

import numpy as np
import pandas as pd
from numba import njit

from common import atr, load, resample, secs, sma
from sim import BUF, Fine, show, trade
from smc2 import pivots

RES = {}


def ctx(df):
    t = secs(df.index)
    a = atr(df, 14)
    return t, df.o.values, df.h.values, df.l.values, df.c.values, df.v.values, a, t[1] - t[0]


# ───────── 1) Spring / upthrust ─────────
@njit(cache=True)
def ranges(h, l, c, a, N):
    """Para cada vela k: techo y suelo del rango de las N velas anteriores si cumple las condiciones; NaN si no."""
    n = len(c)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    for k in range(N + 1, n):
        hi = h[k - N]
        lo = l[k - N]
        for j in range(k - N, k):
            if h[j] > hi:
                hi = h[j]
            if l[j] < lo:
                lo = l[j]
        H = hi - lo
        ak = a[k - 1]
        if not (3 * ak <= H <= 12 * ak):
            continue
        if abs(c[k - 1] - c[k - N]) > 0.5 * H:
            continue
        cl, ch = 0, 0
        lastl, lasth = -100, -100
        for j in range(k - N, k):
            if l[j] <= lo + 0.15 * H:
                if j - lastl >= 5:
                    cl += 1
                lastl = j
            if h[j] >= hi - 0.15 * H:
                if j - lasth >= 5:
                    ch += 1
                lasth = j
        if cl >= 2 and ch >= 2:
            top[k] = hi
            bot[k] = lo
    return top, bot


def springs(df, F, N=40, vol="todos", tp_mode="rango", test=False, pen=0.75, vlow=1.0, vhigh=1.5, sides=(1, -1)):
    t, o, h, l, c, v, a, step = ctx(df)
    top, bot = ranges(h, l, c, a, N)
    vs = sma(v, 20)
    n = len(c)
    out = []
    last = -10**9
    for k in np.flatnonzero(np.isfinite(top)):
        if k - last < 5:
            continue
        for side in sides:
            if side > 0:
                ok = l[k] < bot[k] and c[k] > bot[k] and bot[k] - l[k] <= pen * a[k - 1]
            else:
                ok = h[k] > top[k] and c[k] < top[k] and h[k] - top[k] <= pen * a[k - 1]
            if not ok:
                continue
            rv = v[k] / vs[k - 1] if vs[k - 1] > 0 else np.nan
            if (vol == "bajo" and not rv < vlow) or (vol == "alto" and not rv >= vhigh):
                continue
            ext = l[k] if side > 0 else h[k]
            stop = ext - side * 0.2 * a[k - 1]
            H = top[k] - bot[k]
            k_in = k
            if test:
                k_in = -1
                for j in range(k + 1, min(n, k + 11)):
                    if (side > 0 and l[j] < l[k]) or (side < 0 and h[j] > h[k]):
                        break
                    near = l[j] <= bot[k] + 0.1 * H if side > 0 else h[j] >= top[k] - 0.1 * H
                    upper = c[j] > (h[j] + l[j]) / 2 if side > 0 else c[j] < (h[j] + l[j]) / 2
                    if near and upper and v[j] < 0.6 * v[k]:
                        k_in = j
                        break
                if k_in < 0:
                    continue
            ts = int(t[k_in] + step)
            end = int(t[k_in] + step + 2 * N * step)
            if tp_mode == "rango":
                r = trade(F, ts, side, stop, end, tp_px=top[k] if side > 0 else bot[k], min_R=1.0)
            else:
                r = trade(F, ts, side, stop, end, R=2.0)
            if r:
                out.append(r)
                last = k
            break
    return out


# ───────── 2) Clímax ─────────
def climax(df, F, R=2.0, max_bars=30):
    t, o, h, l, c, v, a, step = ctx(df)
    v50 = sma(v, 50)
    n = len(c)
    out = []
    for k in range(51, n - 1):
        ak, rng = a[k - 1], h[k] - l[k]
        if not (v50[k - 1] > 0 and v[k] >= 2.5 * v50[k - 1] and rng >= 1.5 * ak and rng > 0):
            continue
        pos = (c[k] - l[k]) / rng
        if l[k] <= l[k - 20:k].min() and c[k - 20:k].max() - l[k] >= 4 * ak and pos >= 0.5:
            side = 1
        elif h[k] >= h[k - 20:k].max() and h[k] - c[k - 20:k].min() >= 4 * ak and pos <= 0.5:
            side = -1
        else:
            continue
        stop = (l[k] - 0.2 * ak) if side > 0 else (h[k] + 0.2 * ak)
        r = trade(F, int(t[k] + step), side, stop, int(t[k] + step + max_bars * step), R=R)
        if r:
            out.append(r)
    return out


# ───────── 3) Absorción (esfuerzo sin resultado) ─────────
def absorption(df, F, R=2.0, max_bars=30):
    """Primera versión (volumen ≥ 2 veces la media con rango ≤ 0,6 ATR): en BTC no hay ni una vela así; el volumen
    alto siempre viene con rango amplio. Versión relativa: volumen por punto de rango ≥ 2 veces su mediana de 100
    velas y volumen ≥ 1,3 veces su media de 20."""
    t, o, h, l, c, v, a, step = ctx(df)
    v20 = sma(v, 20)
    er = v / np.maximum(h - l, 1e-9)
    med = pd.Series(er).rolling(100).median().values
    n = len(c)
    out = []
    for k in range(101, n - 1):
        ak = a[k - 1]
        if not (v20[k - 1] > 0 and v[k] >= 1.3 * v20[k - 1] and er[k] >= 2 * med[k - 1]):
            continue
        if l[k] <= l[k - 20:k].min() + 0.25 * ak:
            side = 1
        elif h[k] >= h[k - 20:k].max() - 0.25 * ak:
            side = -1
        else:
            continue
        stop = (l[k] - 0.25 * ak) if side > 0 else (h[k] + 0.25 * ak)
        r = trade(F, int(t[k] + step), side, stop, int(t[k] + step + max_bars * step), R=R)
        if r:
            out.append(r)
    return out


# ───────── 4) Sin demanda / sin oferta ─────────
def vsa_nd_ns(df, F, R=2.0, max_bars=30):
    t, o, h, l, c, v, a, step = ctx(df)
    sp = sma(h - l, 20)
    m20 = sma(c, 20)
    n = len(c)
    out = []
    for k in range(21, n - 1):
        narrow = (h[k] - l[k]) < 0.7 * sp[k - 1]
        lowv = v[k] < v[k - 1] and v[k] < v[k - 2]
        if not (narrow and lowv):
            continue
        ak = a[k - 1]
        if c[k] > c[k - 1] and c[k] > m20[k]:
            side = -1           # sin demanda
        elif c[k] < c[k - 1] and c[k] < m20[k]:
            side = 1            # sin oferta
        else:
            continue
        stop = (l[k] - 0.25 * ak) if side > 0 else (h[k] + 0.25 * ak)
        r = trade(F, int(t[k] + step), side, stop, int(t[k] + step + max_bars * step), R=R)
        if r:
            out.append(r)
    return out


# ───────── 5) Divergencia de esfuerzo en los giros ─────────
def effort_div(df, F, L=5, ratio=0.8, R=2.0, max_bars=30):
    t, o, h, l, c, v, a, step = ctx(df)
    ph, pl = pivots(h, l, L)
    cv = np.r_[0.0, np.cumsum(v)]
    his, los = np.flatnonzero(ph), np.flatnonzero(pl)
    out = []
    for side, piv, opp, px in ((-1, his, los, h), (1, los, his, l)):
        for a_i in range(1, len(piv)):
            i, ip = piv[a_i], piv[a_i - 1]
            if side < 0 and not h[i] > h[ip]:
                continue
            if side > 0 and not l[i] < l[ip]:
                continue
            o_new = opp[(opp > ip) & (opp < i)]
            o_old = opp[opp < ip]
            if not len(o_new) or not len(o_old):
                continue
            s_new, s_old = o_new[-1], o_old[-1]
            v_new = cv[i + 1] - cv[s_new]
            v_old = cv[ip + 1] - cv[s_old]
            if not v_new < ratio * v_old:
                continue
            k = i + L                      # el giro se conoce L velas después
            if k + 1 >= len(c):
                continue
            stop = px[i] * (1 + BUF) if side < 0 else px[i] * (1 - BUF)
            if (c[k] - stop) * side <= 0:
                continue
            r = trade(F, int(t[k] + step), side, stop, int(t[k] + step + max_bars * step), R=R)
            if r:
                out.append(r)
    return out


def main():
    f5, f15 = load("fut_5m"), load("fut_15m")
    F = Fine(f5)
    h1, h4 = resample(f15, "1h"), resample(f15, "4h")
    print("\n### 1) Spring / upthrust en rangos")
    for tfn, df in (("15m", f15), ("1h", h1), ("4h", h4)):
        for vol in ("todos", "bajo", "alto"):
            for tpm in ("rango", "2R"):
                show(f"Spring/UT {tfn} vol {vol} obj {tpm}", springs(df, F, vol=vol, tp_mode=tpm), RES)
        show(f"Spring/UT {tfn} con test, obj rango", springs(df, F, test=True), RES)
    print("\n### 2) Clímax de venta / compra")
    for tfn, df in (("1h", h1), ("4h", h4)):
        show(f"Clímax {tfn}", climax(df, F), RES)
    print("\n### 3) Absorción (esfuerzo sin resultado)")
    for tfn, df in (("1h", h1), ("4h", h4)):
        show(f"Absorción {tfn}", absorption(df, F), RES)
    print("\n### 4) VSA sin demanda / sin oferta")
    for tfn, df in (("1h", h1), ("4h", h4)):
        show(f"Sin demanda/oferta {tfn}", vsa_nd_ns(df, F), RES)
    print("\n### 5) Divergencia de esfuerzo en los giros")
    for tfn, df in (("1h", h1), ("4h", h4)):
        show(f"Divergencia de volumen {tfn}", effort_div(df, F), RES)
    with open("wyckoff.json", "w") as fh:
        json.dump(RES, fh, default=float, indent=1)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
