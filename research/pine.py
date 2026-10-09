"""El script de Pine del usuario («AA FVG ZL + EMA»), módulo a módulo, en Python y con backtest.

  1) FVG Profiles: FVG grande (hueco > 1,5 × media de los 50 últimos huecos) y su mitigación.
  2) Zero Lag Liquidity: niveles en las mechas de velas con volumen alto (RSI del volumen > 60) y mecha > 2 × su media;
     «tendencia de liquidez» = lado del último nivel roto con dos cierres; rechazos.
  3) Rango 09:00-10:00 con hora fija UTC+2 (07:00-08:00 UTC todo el año).
  4) TrendCraft ICT SwiftEdge: estructura BOS/MSS con pivotes (2 velas en 15m/30m, 3 en 1h, 4 en 4h, 5 en 1D) y
     canal SMA20 de máximos y mínimos; Buy/Sell y su estado (1 / -1 / 0 dentro del canal).
  5) Cruce EMA 10/50.
Mismo método que el resto del estudio: comisiones (y funding en futuros), dentro de muestra hasta 2023, fuera desde 2024,
comparación con aguantar y con el azar."""
import sys

import numpy as np
import pandas as pd
from numba import njit

from common import COST, SPLIT, ema_fast, fmt_pct, hold_metrics, load, metrics, random_benchmark, resample, run, secs

RL = {"15m": 2, "30m": 2, "1h": 3, "4h": 4, "1d": 5}


# ───────── 5) EMA 10/50 ─────────
def ema_cross(df):
    c = df.c.values
    return np.where(ema_fast(c, 10) > ema_fast(c, 50), 1.0, -1.0)


# ───────── 4) TrendCraft ICT SwiftEdge ─────────
def pivots(h, l, n):
    """ta.pivothigh/pivotlow(n, n): valor en la vela t si la vela t-n es el extremo de [t-2n, t]."""
    N = len(h)
    ph = np.full(N, np.nan)
    pl = np.full(N, np.nan)
    for t in range(2 * n, N):
        c = t - n
        wh = h[t - 2 * n:t + 1]
        wl = l[t - 2 * n:t + 1]
        if h[c] == wh.max() and (wh == h[c]).sum() == 1:
            ph[t] = h[c]
        if l[c] == wl.min() and (wl == l[c]).sum() == 1:
            pl[t] = l[c]
    return ph, pl


def trendcraft(df, rl, mode="BoS", sma_len=20):
    """Devuelve (estado por vela: 1 / -1 / 0, señales: +1 Buy, -1 Sell, 0)."""
    h, l, c = df.h.values, df.l.values, df.c.values
    t = secs(df.index)
    N = len(c)
    sl = pd.Series(l).rolling(sma_len).mean().values
    shh = pd.Series(h).rolling(sma_len).mean().values
    ph, pl = pivots(h, l, rl)
    bull = False
    nPh1 = nPl1 = np.nan
    pH = pL = nPh = nPl = None            # (precio, tiempo)
    prev_nPh = prev_nPl = None
    lin = []                              # x2 (tiempo) de las líneas, la más nueva primero
    mssBullVar = mssBearVar = False
    lastBosBull = lastBosBear = lastMssBull = lastMssBear = np.nan
    prevB = prevS = np.nan                # nivel de la vela anterior (ta.crossover compara con b[1])
    state = 0
    st = np.zeros(N)
    sig = np.zeros(N)
    for i in range(N):
        phPs = ph[i]
        plPs = pl[i]
        phBi = t[i - rl] if not np.isnan(phPs) else None
        plBi = t[i - rl] if not np.isnan(plPs) else None
        if not np.isnan(phPs):
            nPh = (phPs, phBi)
            if pH is None:
                pH = (phPs, phBi)
        if not np.isnan(plPs):
            nPl = (plPs, plBi)
            if pL is None:
                pL = (plPs, plBi)
        bosBull = bosBear = mssBull = mssBear = False
        tmpBosBull = tmpBosBear = tmpMssBull = tmpMssBear = np.nan
        htfClose = c[i - 1] if i > 0 else np.nan
        htfTime = t[i - 1] if i > 0 else t[i]
        highCond = h[i] if bull else htfClose
        timeHighCond = t[i] if bull else htfTime
        if (not np.isnan(plPs) and plPs > nPl1 and bull) or np.isnan(nPl1) or (not np.isnan(plPs) and plPs < nPl1):
            nPl1 = plPs
        if pH is not None and highCond > pH[0]:
            if bull:
                bosBull = True
                tmpBosBull = pH[0]
                if not mssBullVar and lin:
                    lin.pop(0)
                mssBullVar = False
            else:
                mssBull = True
                tmpMssBull = pH[0]
                mssBullVar = True
            bull = True
            mssBearVar = False
            pH = None
            pL = None
            if not np.isnan(nPl1) and nPl is not None:
                pL = (nPl[0], nPl[1])
            lin.insert(0, timeHighCond)
        lowCond = htfClose if bull else l[i]
        timeLowCond = htfTime if bull else t[i]
        if (not np.isnan(phPs) and phPs < nPh1 and not bull) or np.isnan(nPh1) or (not np.isnan(phPs) and phPs > nPh1):
            nPh1 = phPs
        if pL is not None and lowCond < pL[0]:
            if not bull:
                bosBear = True
                tmpBosBear = pL[0]
                if not mssBearVar and lin:
                    lin.pop(0)
                mssBearVar = False
            else:
                mssBear = True
                tmpMssBear = pL[0]
                mssBearVar = True
            bull = False
            mssBullVar = False
            pH = None
            pL = None
            if not np.isnan(nPh1) and nPh is not None:
                pH = (nPh[0], nPh[1])
            lin.insert(0, timeLowCond)
        # actualización de swings
        if pH is not None and nPh is not None and prev_nPh is not None and lin and not bull \
                and nPh[0] != prev_nPh[0] and nPh[1] <= lin[0]:
            pH = (nPh[0], nPh[1])
        if pL is not None and nPl is not None and prev_nPl is not None and lin and bull \
                and nPl[0] != prev_nPl[0] and nPl[1] <= lin[0]:
            pL = (nPl[0], nPl[1])
        # reinicio de pivotes
        if nPh is not None and h[i] > nPh[0]:
            nPh = None
        if nPl is not None and l[i] < nPl[0]:
            nPl = None
        prev_nPh, prev_nPl = nPh, nPl
        if bosBull and not np.isnan(tmpBosBull):
            lastBosBull = tmpBosBull
        if bosBear and not np.isnan(tmpBosBear):
            lastBosBear = tmpBosBear
        if mssBull and not np.isnan(tmpMssBull):
            lastMssBull = tmpMssBull
        if mssBear and not np.isnan(tmpMssBear):
            lastMssBear = tmpMssBear
        lvB = lastBosBull if mode == "BoS" else lastMssBull
        lvS = lastBosBear if mode == "BoS" else lastMssBear
        buy = (not np.isnan(lvB) and not np.isnan(prevB) and c[i] > lvB and c[i - 1] <= prevB and c[i] > shh[i] and c[i] > sl[i])
        sell = (not np.isnan(lvS) and not np.isnan(prevS) and c[i] < lvS and c[i - 1] >= prevS and c[i] < shh[i] and c[i] < sl[i])
        prevB, prevS = lvB, lvS
        buySig = buy and state != 1
        sellSig = sell and state != -1
        if buySig:
            state = 1
            sig[i] = 1
        if sellSig:
            state = -1
            sig[i] = -1
        if sl[i] < c[i] < shh[i]:
            state = 0
        st[i] = state
    return st, sig


# ───────── 2) Zero Lag Liquidity ─────────
def rsi_pine(x, n):
    d = np.diff(x, prepend=x[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    return np.where(dn == 0, 100.0, 100 - 100 / (1 + up / np.where(dn == 0, 1, dn)))


def wick_poc(df, f5, top, bot, bins=7):
    """POC del volumen dentro de la mecha con velas de 5m de esa vela (como request.security_lower_tf)."""
    t = secs(df.index)
    step = t[1] - t[0]
    t5 = secs(f5.index)
    c5, v5 = f5.c.values, f5.v.values
    out = np.full(len(t), np.nan)
    for i in np.flatnonzero(~np.isnan(top)):
        a = np.searchsorted(t5, t[i])
        b = np.searchsorted(t5, t[i] + step)
        if b <= a or top[i] == bot[i]:
            continue
        seg = (top[i] - bot[i]) / bins
        bounds = bot[i] + seg * np.arange(bins + 1)
        vols = np.zeros(bins)
        for j in range(a, b):
            for k in range(bins):
                if bounds[k] <= c5[j] <= bounds[k + 1]:
                    vols[k] += v5[j]
        k = int(np.argmax(vols))             # sin volumen dentro: el primer tramo (como array.indexof del máximo 0)
        out[i] = (bounds[k] + bounds[k + 1]) / 2
    return out


@njit(cache=True)
def _liq_levels(c, h, l, hl2, poc, typ, maxn):
    N = len(c)
    lv = np.zeros(maxn)
    ty = np.zeros(maxn, np.int64)
    x1 = np.zeros(maxn, np.int64)
    n = 0
    trend = np.zeros(N)
    brk = np.zeros(N)
    rej = np.zeros(N)
    tr = 0
    for i in range(N):
        if typ[i] != 0 and not np.isnan(poc[i]):
            # nuevo nivel al principio (unshift); si pasa de maxn se quita el más viejo
            for k in range(min(n, maxn - 1), 0, -1):
                lv[k] = lv[k - 1]
                ty[k] = ty[k - 1]
                x1[k] = x1[k - 1]
            lv[0] = poc[i]
            ty[0] = typ[i]
            x1[0] = i
            n = min(n + 1, maxn)
        b = 0
        r = 0
        k = n - 1
        while k >= 0:
            done = (c[i] > lv[k] and c[i - 1] > lv[k]) if ty[k] == 1 else (c[i] < lv[k] and c[i - 1] < lv[k])
            if i > 0 and done:
                b = ty[k]
                tr = ty[k]
                for m in range(k, n - 1):
                    lv[m] = lv[m + 1]
                    ty[m] = ty[m + 1]
                    x1[m] = x1[m + 1]
                n -= 1
            else:
                if i > 0 and h[i - 1] > lv[k] and l[i - 1] < lv[k] and (i - 1) - x1[k] > 0:   # la línea ya se alargó
                    if hl2[i] > lv[k] and ty[k] == -1:
                        r = 1
                    elif hl2[i] < lv[k] and ty[k] == 1:
                        r = -1
            k -= 1
        trend[i] = tr
        brk[i] = b
        rej[i] = r
    return trend, brk, rej


def zero_lag(df, f5):
    o, h, l, c, v = df.o.values, df.h.values, df.l.values, df.c.values, df.v.values
    vol = rsi_pine(v, 14)
    lw = np.minimum(c, o) - l
    uw = h - np.maximum(c, o)
    aw = (lw + uw) / 2
    aW = pd.Series(aw).rolling(21).mean().values
    hi = (vol > 60) & (aw > aW * 2.0) & (uw != lw)
    red = hi & (lw > uw)
    green = hi & (uw > lw)
    top = np.where(red, np.minimum(c, o), np.where(green, h, np.nan))
    bot = np.where(red, l, np.where(green, np.maximum(c, o), np.nan))
    poc = wick_poc(df, f5, top, bot)
    typ = np.where(green, 1, np.where(red, -1, 0)).astype(np.int64)
    return _liq_levels(c, h, l, (h + l) / 2, poc, typ, 500)


# ───────── 1) FVG grandes ─────────
def big_fvgs(df, vola_len=50, mult=1.5):
    h, l = df.h.values, df.l.values
    N = len(h)
    gaps = []
    out = np.zeros(N)
    for i in range(2, N):
        bb, be = l[i] > h[i - 2], h[i] < l[i - 2]
        if bb or be:
            gaps.insert(0, l[i] - h[i - 2] if bb else l[i - 2] - h[i])
            del gaps[vola_len:]
        if gaps:
            vola = sum(gaps) / len(gaps)
            if bb and (l[i] - h[i - 2]) > vola * mult:
                out[i] = 1
            elif be and (l[i - 2] - h[i]) > vola * mult:
                out[i] = -1
    return out


def event_returns(df, ev, hold, cost=COST):
    """Entrar al cierre de la vela del evento en su dirección y salir tras `hold` velas."""
    c = df.c.values
    t = df.index
    rs = []
    for i in np.flatnonzero(ev != 0):
        if i + hold < len(c):
            rs.append((t[i], ev[i] * (c[i + hold] / c[i] - 1) - 2 * cost))
    return rs


def show_rule(label, df, tf, pos, fund=True, start=None):
    start = start or df.index[300]
    out = []
    for var, p in (("LF", np.where(pos > 0, 1.0, 0.0)), ("LS", pos), ("SO", np.where(pos < 0, -1.0, 0.0))):
        res = run(df, p, tf, COST, fund)
        i, o, a = metrics(res, start, SPLIT), metrics(res, SPLIT), metrics(res, start)
        rb = random_benchmark(res, start, None, n=150)["pct"] if var != "SO" else float("nan")
        out.append(f"  {label:34s} {var} | IS {fmt_pct(i['ret']):>7s} sh {i['sharpe']:5.2f} | OOS {fmt_pct(o['ret']):>7s} sh {o['sharpe']:5.2f} "
                   f"dd {o['dd'] * 100:4.0f}% | total {fmt_pct(a['ret']):>7s} oper {a['trades']:5d} gana {a['win'] * 100:3.0f}% | azar {rb * 100:3.0f}%")
    print("\n".join(out))
    sys.stdout.flush()


def main():
    f15 = load("fut_15m")
    f5 = load("fut_5m")
    sets = {"15m": f15, "30m": resample(f15, "30min"), "1h": resample(f15, "1h"), "4h": resample(f15, "4h"),
            "1d": resample(f15, "1D")}
    for tf, df in sets.items():
        start = df.index[300]
        h = hold_metrics(df, tf, start, SPLIT), hold_metrics(df, tf, SPLIT, None), hold_metrics(df, tf, start, None)
        print(f"\n=== {tf} === aguantar: IS sh {h[0]['sharpe']:.2f} | OOS {fmt_pct(h[1]['ret'])} sh {h[1]['sharpe']:.2f} dd {h[1]['dd'] * 100:.0f}% | "
              f"total {fmt_pct(h[2]['ret'])}")
        show_rule("EMA 10/50", df, tf, ema_cross(df))
        for mode in ("BoS", "MSS"):
            st, sig = trendcraft(df, RL[tf], mode)
            show_rule(f"TrendCraft {mode} (estado)", df, tf, st)
            nb, ns = int((sig > 0).sum()), int((sig < 0).sum())
            print(f"    señales TrendCraft {mode}: {nb} Buy · {ns} Sell")
        if tf in ("15m", "30m", "1h", "4h"):
            trend, brk, rej = zero_lag(df, f5)
            show_rule("Liquidez Zero Lag (tendencia)", df, tf, trend)
            for name, ev in (("ruptura de liquidez", brk), ("rechazo de liquidez", rej), ("FVG grande", big_fvgs(df))):
                for hold in (4, 16):
                    rs = event_returns(df, ev, hold)
                    for per, f in (("IS", lambda x: x < SPLIT), ("OOS", lambda x: x >= SPLIT)):
                        r = np.array([x for tt, x in rs if f(tt)])
                        if len(r):
                            print(f"    {name:20s} {hold:2d} velas {per}: n={len(r):5d} gana {np.mean(r > 0) * 100:3.0f}% "
                                  f"media {r.mean() * 1e4:+6.1f}pb (sin comisiones {r.mean() * 1e4 + 12:+6.1f}pb)")


if __name__ == "__main__":
    main()
