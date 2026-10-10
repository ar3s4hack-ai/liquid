"""SMC / ICT, segunda tanda (octubre de 2026): lo que faltaba por probar después de ict.py y pine.py.

Reglas fijadas antes de mirar los resultados (hora de Nueva York, con horario de verano; BTCUSDT futuros 2020-2026):

1. Modelo 2022 (5m): liquidez en reposo = máximo/mínimo del día anterior (día de NY), rango asiático (20-24 h NY) y,
   para Nueva York, rango de Londres (02-05 h NY). En las killzones de Londres (02-05 h) y Nueva York (07-10 h): barrida
   de un nivel → cambio de estructura (cierre más allá del último giro de 5 velas) con desplazamiento (deja un FVG)
   antes de 2 h → orden límite en el FVG (borde o mitad) durante 1 h → stop más allá del extremo barrido → objetivo 2R
   o la liquidez del otro lado (si da ≥ 2R). Una operación por killzone; cierre a las 16 h NY.
2. Silver Bullet (5m): ventanas 03-04, 10-11 y 14-15 h NY. Barrida del máximo/mínimo de la hora anterior y FVG en
   sentido contrario dentro de la ventana → límite en el FVG, stop más allá de la barrida, 2R. Variante sin barrida:
   el primer FVG de la ventana. Cierre una hora después de la ventana.
3. OTE (15m, 1h, 4h): tras una ruptura de estructura (giros de 10 velas), compra/venta límite en el 70,5 % del
   retroceso del tramo; stop más allá del origen (100 %); objetivo el extremo del tramo (0 %, ≈ 2,4R) o la extensión
   −27 % (≈ 3,3R). Variante: solo si el tramo dejó un FVG.
4. Breaker (15m, 1h): order block (última vela contraria antes de la ruptura) que el precio rompe al cierre → se opera
   la vuelta a la zona en sentido contrario; stop al otro lado de la zona, 2R.
5. FVG invertido (IFVG, 5m y 15m): FVG que se cierra por completo al otro lado → límite en el borde al volver,
   stop en el extremo del tramo, 2R.
6. Máximos/mínimos débiles y fuertes de LuxAlgo (5m con swing 70, 15m y 1h con swing 50): ¿el «débil» se toca antes
   de lo que daría el azar por distancia?
7. Order blocks de swing de LuxAlgo (5m, swing 70): la vela de mínimo más bajo entre el giro roto y la ruptura (con su
   filtro de volatilidad); límite en el borde al primer retroceso (24 h), stop al otro lado, 2R.
8. CISD (5m): barrida de liquidez en killzone y cierre más allá de la apertura de la última serie de velas contrarias
   → entrada a mercado, stop más allá del extremo, 2R o liquidez opuesta.
9. Turtle soup (1h, 4h): nuevo mínimo/máximo de 20 velas (el anterior, de hace ≥ 4) que cierra de vuelta dentro.
10. Judas swing / Power of 3 (5m): el precio va contra la apertura de medianoche de NY (≥ 0,1 %) antes de las 05 h y
   vuelve a cerrar al otro lado antes de las 10 h → a favor de la vuelta; stop en el extremo, 2R o máximo/mínimo del
   día anterior; cierre a las 16 h NY.
11. Descuento/premium (4h, 1D): con estructura alcista, comprar en descuento (bajo la mitad del rango) frente a
   comprar siempre.

Sesgo opcional en 1, 2, 8 y 10: los 3 votos de la señal de la web (≥ 2 votos solo largos; ≤ 1 solo cortos).
Todo con comisiones y contra la misma operación al revés (espejo). Ver sim.py."""
import json
import sys

import numpy as np
import pandas as pd
from numba import njit

from common import COST, COST_SPOT, SPLIT, atr, load, metrics, resample, run, secs
from ensemble import build
from sim import BUF, Fine, ny_clock, ny_ts, show, trade
from smc import fvgs, lux_structure

RES = {}


# ───────── utilidades ─────────
@njit(cache=True)
def pivots(h, l, L):
    """Giros de L velas a cada lado: ph[i]/pl[i] (se conocen en i + L)."""
    n = len(h)
    ph = np.zeros(n, np.bool_)
    pl = np.zeros(n, np.bool_)
    for i in range(L, n - L):
        okh = True
        okl = True
        for j in range(i - L, i + L + 1):
            if j == i:
                continue
            if h[j] >= h[i]:
                okh = False
            if l[j] <= l[i]:
                okl = False
        ph[i] = okh
        pl[i] = okl
    return ph, pl


def last_confirmed(flags, L):
    """Para cada vela k, índice del último giro confirmado (i + L <= k); -1 si no hay."""
    n = len(flags)
    out = np.full(n, -1, np.int64)
    idx = np.flatnonzero(flags)
    conf = idx + L
    pos = np.searchsorted(conf, np.arange(n), side="right") - 1
    ok = pos >= 0
    out[ok] = idx[pos[ok]]
    return out


@njit(cache=True)
def lux_trailing(h, l, c, L):
    """Tendencia de swing de LuxAlgo y sus extremos («trailing»): máximo desde el último máximo de giro y mínimo
    desde el último mínimo de giro. Con tendencia alcista, el máximo es «débil» y el mínimo «fuerte»; al revés si es bajista."""
    n = len(c)
    trend = np.zeros(n, np.int8)
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    os = 0
    ty = np.nan
    by = np.nan
    tcross = True
    bcross = True
    tr = 0
    tu = np.nan
    td = np.nan
    for k in range(n):
        if k >= L:
            upper = h[k - L + 1]
            lower = l[k - L + 1]
            for j in range(k - L + 2, k + 1):
                if h[j] > upper:
                    upper = h[j]
                if l[j] < lower:
                    lower = l[j]
            prev = os
            if h[k - L] > upper:
                os = 0
            elif l[k - L] < lower:
                os = 1
            if os == 0 and prev != 0:
                ty = h[k - L]
                tcross = False
                tu = ty
            if os == 1 and prev != 1:
                by = l[k - L]
                bcross = False
                td = by
        if not tcross and c[k] > ty:
            tcross = True
            tr = 1
        if not bcross and c[k] < by:
            bcross = True
            tr = -1
        if not np.isnan(tu):
            tu = max(tu, h[k])
        if not np.isnan(td):
            td = min(td, l[k])
        trend[k] = tr
        up[k] = tu
        dn[k] = td
    return trend, up, dn


@njit(cache=True)
def first_hit(h, l, k, top, bot, H):
    """1 si se toca antes `top`, 0 si antes `bot`, -1 si ninguno o los dos en la misma vela."""
    n = len(h)
    for j in range(k + 1, min(n, k + 1 + H)):
        a = h[j] >= top
        b = l[j] <= bot
        if a and b:
            return -1
        if a:
            return 1
        if b:
            return 0
    return -1


class Ctx:
    """Velas de 5m con reloj de NY, días, giros y sesgo de la señal de 3 votos."""

    def __init__(self, f5, f15):
        self.F = Fine(f5)
        t = self.F.t
        self.o, self.h, self.l, self.c = f5.o.values, f5.h.values, f5.l.values, f5.c.values
        self.day, hr, mn, self.wd = ny_clock(t)
        self.tod = hr * 60 + mn
        self.starts = np.r_[0, np.flatnonzero(np.diff(self.day)) + 1]
        self.ends = np.r_[self.starts[1:], len(t)]
        ph, pl = pivots(self.h, self.l, 2)
        self.lph = last_confirmed(ph, 2)
        self.lpl = last_confirmed(pl, 2)
        # sesgo: los 3 votos de la web, conocidos al cierre de la última vela de 4h
        h4, d = resample(f15, "4h"), resample(f15, "1D")
        g = build(h4, d)
        t4 = secs(h4.index) + 14400
        pos = np.searchsorted(t4, t, side="right") - 1
        sc = np.nan_to_num(g["score"].values, nan=-1)
        self.score = np.where(pos >= 0, sc[np.maximum(pos, 0)], -1)

    def allowed(self, k, side, bias):
        if not bias:
            return True
        s = self.score[k]
        if s < 0:
            return False
        return (s >= 2) if side > 0 else (s <= 1)

    def window(self, i0, i1, m0, m1):
        """Índices [ws, we) del día [i0, i1) entre los minutos m0 y m1 de NY."""
        tod = self.tod[i0:i1]
        return i0 + int(np.searchsorted(tod, m0)), i0 + int(np.searchsorted(tod, m1))


def bull_fvg(h, l, o, c, j):
    return j >= 2 and l[j] > h[j - 2] and c[j - 1] > o[j - 1]


def bear_fvg(h, l, o, c, j):
    return j >= 2 and h[j] < l[j - 2] and c[j - 1] < o[j - 1]


def place_limit(h, l, side, limit, tp, j0, j1):
    """Primera vela en [j0, j1) que llena la orden; -1 si antes llega al objetivo o no se llena."""
    for j in range(j0, j1):
        if (side > 0 and h[j] >= tp) or (side < 0 and l[j] <= tp):
            return -1
        if (side > 0 and l[j] <= limit) or (side < 0 and h[j] >= limit):
            return j
    return -1


def pools(X, di, kz):
    """Niveles de liquidez para el día di y killzone kz ('lon' o 'ny'): (lista de mínimos, lista de máximos,
    índice de inicio de la ventana, de fin, máximos del lado contrario para objetivo)."""
    h, l = X.h, X.l
    i0, i1 = X.starts[di], X.ends[di]
    p0, p1 = X.starts[di - 1], X.ends[di - 1]
    if X.day[i0] - X.day[p0] != 1 or i1 - i0 < 250 or p1 - p0 < 250:
        return None
    pdh, pdl = h[p0:p1].max(), l[p0:p1].min()
    am = X.tod[p0:p1] >= 20 * 60
    if am.sum() < 40:
        return None
    ah, al = h[p0:p1][am].max(), l[p0:p1][am].min()
    w = (120, 300) if kz == "lon" else (420, 600)
    ws, we = X.window(i0, i1, *w)
    if we - ws < 30:
        return None
    lows, highs = [], []
    hi_before = h[i0:ws].max() if ws > i0 else -np.inf
    lo_before = l[i0:ws].min() if ws > i0 else np.inf
    for lv in (pdl, al):
        if lo_before > lv:
            lows.append(lv)
    for lv in (pdh, ah):
        if hi_before < lv:
            highs.append(lv)
    if kz == "ny":
        ls, le = X.window(i0, i1, 120, 300)
        if le - ls >= 30:
            lh, ll = h[ls:le].max(), l[ls:le].min()
            mid_hi = h[le:ws].max() if ws > le else -np.inf
            mid_lo = l[le:ws].min() if ws > le else np.inf
            if mid_lo > ll:
                lows.append(ll)
            if mid_hi < lh:
                highs.append(lh)
    return lows, highs, ws, we, i0, i1


# ───────── 1) Modelo 2022 ─────────
def model2022(X, kz_list=("lon", "ny"), entry="edge", tp_mode="R", R=2.0, bias=False, anytime=False):
    o, h, l, c = X.o, X.h, X.l, X.c
    t = X.F.t
    out = []
    for di in range(1, len(X.starts)):
        for kz in kz_list:
            P = pools(X, di, kz)
            if P is None:
                continue
            lows, highs, ws, we, i0, i1 = P
            if anytime:   # variante: cualquier hora entre 00 y 12 h NY
                ws, we = X.window(i0, i1, 0, 720)
            side, kr = 0, -1
            for k in range(ws, we):
                lo_hit = any(l[k] < p for p in lows)
                hi_hit = any(h[k] > p for p in highs)
                if lo_hit and hi_hit:
                    break
                if lo_hit or hi_hit:
                    side, kr = (1 if lo_hit else -1), k
                    break
            if side == 0:
                continue
            ext, kx = (l[kr], kr) if side > 0 else (h[kr], kr)
            lvl = h[X.lph[kr]] if side > 0 and X.lph[kr] >= 0 else (l[X.lpl[kr]] if side < 0 and X.lpl[kr] >= 0 else np.nan)
            mss = -1
            stop_t = ny_ts(X.day[i0], 16)
            lim_k = min(kr + 25, i1 - 1, X.F.at(stop_t) - 1)
            for m in range(kr, lim_k):
                if side > 0 and l[m] < ext:
                    ext, kx = l[m], m
                    lvl = h[X.lph[m]] if X.lph[m] >= 0 else np.nan
                if side < 0 and h[m] > ext:
                    ext, kx = h[m], m
                    lvl = l[X.lpl[m]] if X.lpl[m] >= 0 else np.nan
                if m > kr and np.isfinite(lvl) and ((side > 0 and c[m] > lvl) or (side < 0 and c[m] < lvl)):
                    mss = m
                    break
            if mss < 0 or not X.allowed(mss, side, bias):
                continue
            fj = -1
            for j in range(mss, kx + 1, -1):
                if j - 2 >= kx and (bull_fvg(h, l, o, c, j) if side > 0 else bear_fvg(h, l, o, c, j)):
                    fj = j
                    break
            if fj < 0:
                continue
            top, bot = (l[fj], h[fj - 2]) if side > 0 else (l[fj - 2], h[fj])
            limit = (top if side > 0 else bot) if entry == "edge" else (top + bot) / 2
            stop = ext * (1 - BUF) if side > 0 else ext * (1 + BUF)
            risk = abs(limit - stop)
            if (limit - stop) * side <= 0:
                continue
            if tp_mode == "R":
                tp = limit + side * R * risk
            else:
                opp = [p for p in (highs if side > 0 else lows) if (p - limit) * side >= 2 * risk]
                if not opp:
                    continue
                tp = min(opp) if side > 0 else max(opp)
            fill = place_limit(h, l, side, limit, tp, mss + 1, min(mss + 13, lim_k + 12))
            if fill < 0:
                continue
            r = trade(X.F, int(t[fill]), side, stop, stop_t, tp_px=tp, limit=limit, meta={"kz": kz})
            if r:
                out.append(r)
    return out


# ───────── 2) Silver Bullet ─────────
def silver_bullet(X, sweep=True, R=2.0, bias=False):
    o, h, l, c = X.o, X.h, X.l, X.c
    t = X.F.t
    out = []
    for di in range(len(X.starts)):
        i0, i1 = X.starts[di], X.ends[di]
        if i1 - i0 < 250:
            continue
        for w0 in (180, 600, 840):
            ps, pe = X.window(i0, i1, w0 - 60, w0)
            ws, we = X.window(i0, i1, w0, w0 + 60)
            if pe - ps < 11 or we - ws < 11:
                continue
            H, L_ = h[ps:pe].max(), l[ps:pe].min()
            sl = sh = -1
            lo_ext, hi_ext = np.inf, -np.inf
            done = False
            for k in range(ws, we):
                if l[k] < L_:
                    sl = k if sl < 0 else sl
                if h[k] > H:
                    sh = k if sh < 0 else sh
                lo_ext, hi_ext = min(lo_ext, l[k]), max(hi_ext, h[k])
                for side in (1, -1):
                    if sweep:
                        s_k = sl if side > 0 else sh
                        if s_k < 0 or k - 2 < s_k:
                            continue
                    elif k - 2 < ws - 2:
                        continue
                    if not (bull_fvg(h, l, o, c, k) if side > 0 else bear_fvg(h, l, o, c, k)):
                        continue
                    if not X.allowed(k, side, bias):
                        continue
                    top, bot = (l[k], h[k - 2]) if side > 0 else (l[k - 2], h[k])
                    limit = top if side > 0 else bot
                    if sweep:
                        stop = lo_ext * (1 - BUF) if side > 0 else hi_ext * (1 + BUF)
                    else:
                        stop = l[k - 2] * (1 - BUF) if side > 0 else h[k - 2] * (1 + BUF)
                    if (limit - stop) * side <= 0:
                        continue
                    tp = limit + side * R * abs(limit - stop)
                    fill = place_limit(h, l, side, limit, tp, k + 1, we)
                    done = True
                    if fill >= 0:
                        r = trade(X.F, int(t[fill]), side, stop, int(t[min(we, len(t) - 1)]) + 3600, tp_px=tp,
                                  limit=limit, meta={"w": w0})
                        if r:
                            out.append(r)
                    break
                if done:
                    break
    return out


# ───────── 3) OTE ─────────
def ote(df, F, L=10, ext=0.0, V=48, need_fvg=False, max_bars=96):
    t = secs(df.index)
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    _, ev, top, btm, top_i, btm_i = lux_structure(h, l, c, L)
    n = len(c)
    out = []
    for k in np.flatnonzero(ev != 0):
        side = 1 if ev[k] > 0 else -1
        org = btm_i[k] if side > 0 else top_i[k]
        base = btm[k] if side > 0 else top[k]
        if org < 0 or not np.isfinite(base) or org >= k:
            continue
        if need_fvg and not any((bull_fvg(h, l, o, c, j) if side > 0 else bear_fvg(h, l, o, c, j))
                                for j in range(org + 2, k + 1)):
            continue
        ex = h[org:k + 1].max() if side > 0 else l[org:k + 1].min()
        for j in range(k + 1, min(n, k + 1 + V)):
            if (side > 0 and c[j - 1] < base) or (side < 0 and c[j - 1] > base):
                break
            limit = ex - side * 0.705 * abs(ex - base)
            if (side > 0 and l[j] <= limit) or (side < 0 and h[j] >= limit):
                stop = base * (1 - BUF) if side > 0 else base * (1 + BUF)
                tp = ex + side * ext * abs(ex - base)
                r = trade(F, int(t[j]), side, stop, int(t[j] + max_bars * step), tp_px=tp, limit=limit)
                if r:
                    out.append(r)
                break
            ex = max(ex, h[j]) if side > 0 else min(ex, l[j])
    return out


# ───────── 4) Breaker ─────────
def breakers(df, F, L=10, R=2.0, look=200, V=48, max_bars=96):
    t = secs(df.index)
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    _, ev, _, _, top_i, btm_i = lux_structure(h, l, c, L)
    n = len(c)
    out = []
    for k in np.flatnonzero(ev != 0):
        side = 1 if ev[k] > 0 else -1
        org = btm_i[k] if side > 0 else top_i[k]
        if org < 0 or org >= k:
            continue
        ob = -1
        for j in range(k - 1, org - 1, -1):
            if (side > 0 and c[j] < o[j]) or (side < 0 and c[j] > o[j]):
                ob = j
                break
        if ob < 0:
            continue
        inv = -1
        for j in range(k + 1, min(n, k + 1 + look)):
            if (side > 0 and c[j] < l[ob]) or (side < 0 and c[j] > h[ob]):
                inv = j
                break
        if inv < 0:
            continue
        bs = -side
        limit = l[ob] if side > 0 else h[ob]
        stop = h[ob] * (1 + BUF) if side > 0 else l[ob] * (1 - BUF)
        tp = limit + bs * R * abs(limit - stop)
        fill = -1
        for j in range(inv + 1, min(n, inv + 1 + V)):
            if (bs < 0 and l[j] <= tp) or (bs > 0 and h[j] >= tp):
                break
            if (bs < 0 and h[j] >= limit) or (bs > 0 and l[j] <= limit):
                fill = j
                break
        if fill < 0:
            continue
        r = trade(F, int(t[fill]), bs, stop, int(t[fill] + max_bars * step), tp_px=tp, limit=limit)
        if r:
            out.append(r)
    return out


# ───────── 5) FVG invertido ─────────
def ifvg(df, F, min_atr=0.3, look=48, V=24, R=2.0, max_bars=96):
    t = secs(df.index)
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    typ, ftop, fbot = fvgs(h, l, c, o, atr(df, 14) * min_atr)
    n = len(c)
    out = []
    for k in np.flatnonzero(typ != 0):
        side = int(typ[k])
        top, bot = ftop[k], fbot[k]
        inv = -1
        for j in range(k + 1, min(n, k + 1 + look)):
            if (side > 0 and c[j] < bot) or (side < 0 and c[j] > top):
                inv = j
                break
        if inv < 0:
            continue
        bs = -side
        if bs < 0:
            limit, stop = bot, h[k:inv + 1].max() * (1 + BUF)
        else:
            limit, stop = top, l[k:inv + 1].min() * (1 - BUF)
        if (limit - stop) * bs <= 0:
            continue
        tp = limit + bs * R * abs(limit - stop)
        fill = -1
        for j in range(inv + 1, min(n, inv + 1 + V)):
            if (bs < 0 and l[j] <= tp) or (bs > 0 and h[j] >= tp):
                break
            if (bs < 0 and h[j] >= limit) or (bs > 0 and l[j] <= limit):
                fill = j
                break
        if fill < 0:
            continue
        r = trade(F, int(t[fill]), bs, stop, int(t[fill] + max_bars * step), tp_px=tp, limit=limit)
        if r:
            out.append(r)
    return out


# ───────── 6) Máximos/mínimos débiles y fuertes ─────────
@njit(cache=True)
def weak_strong(h, l, c, trend, up, dn, every, H, start):
    n = len(c)
    m = (n - start) // every + 1
    res = np.full((m, 4), np.nan)   # tiempo-índice, P(azar arriba primero), real arriba primero, tendencia
    r = 0
    for k in range(start, n - 1, every):
        if trend[k] == 0 or np.isnan(up[k]) or np.isnan(dn[k]):
            continue
        cur = c[k]
        if not (dn[k] < cur < up[k]):
            continue
        y = first_hit(h, l, k, up[k], dn[k], H)
        if y < 0:
            continue
        res[r, 0] = k
        res[r, 1] = (cur - dn[k]) / (up[k] - dn[k])
        res[r, 2] = y
        res[r, 3] = trend[k]
        r += 1
    return res[:r]


def weak_strong_study(df, L, every, H, label):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    tr, up, dn = lux_trailing(h, l, c, L)
    res = weak_strong(h, l, c, tr, up, dn, every, H, L + 1)
    tt = t[res[:, 0].astype(int)]
    out = {}
    for per, m in (("IS", tt < SPLIT.timestamp()), ("OOS", tt >= SPLIT.timestamp())):
        r = res[m]
        bull = r[:, 3] > 0
        p_weak = np.where(bull, r[:, 1], 1 - r[:, 1])          # azar: el débil primero
        y_weak = np.where(bull, r[:, 2], 1 - r[:, 2])
        out[per] = {"n": int(len(r)), "real": float(y_weak.mean()), "azar": float(p_weak.mean()),
                    "alc_real": float(r[bull, 2].mean()), "alc_azar": float(r[bull, 1].mean()),
                    "baj_real": float(1 - r[~bull, 2].mean()), "baj_azar": float(1 - r[~bull, 1].mean())}
        print(f"  {label:22s} {per}: n={len(r):6d}  el débil se toca antes {y_weak.mean() * 100:5.1f}% (azar por distancia "
              f"{p_weak.mean() * 100:5.1f}%) | alcista {out[per]['alc_real'] * 100:5.1f}% vs {out[per]['alc_azar'] * 100:5.1f}% "
              f"| bajista {out[per]['baj_real'] * 100:5.1f}% vs {out[per]['baj_azar'] * 100:5.1f}%")
    RES["débil/fuerte " + label] = out
    sys.stdout.flush()


# ───────── 7) Order blocks de swing de LuxAlgo ─────────
def lux_obs(df, F, L=70, R=2.0, valid=288, max_bars=288):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    _, ev, _, _, top_i, btm_i = lux_structure(h, l, c, L)
    a200 = atr(df, 200)
    hv = (h - l) >= 2 * a200
    pH, pL = np.where(hv, l, h), np.where(hv, h, l)
    n = len(c)
    out = []
    for k in np.flatnonzero(ev != 0):
        side = 1 if ev[k] > 0 else -1
        piv = top_i[k] if side > 0 else btm_i[k]          # el giro que se rompe
        if piv < 0 or piv >= k:
            continue
        j = piv + int(np.argmin(pL[piv:k])) if side > 0 else piv + int(np.argmax(pH[piv:k]))
        ob_hi, ob_lo = max(pH[j], pL[j]), min(pH[j], pL[j])
        limit = ob_hi if side > 0 else ob_lo
        stop = ob_lo * (1 - BUF) if side > 0 else ob_hi * (1 + BUF)
        if (c[k] - limit) * side <= 0:
            continue
        fill = -1
        for m in range(k + 1, min(n, k + 1 + valid)):
            if (side > 0 and l[m] <= limit) or (side < 0 and h[m] >= limit):
                fill = m
                break
        if fill < 0:
            continue
        r = trade(F, int(t[fill]), side, stop, int(t[fill] + max_bars * step), R=R, limit=limit)
        if r:
            out.append(r)
    return out


# ───────── 8) CISD ─────────
def cisd(X, tp_mode="R", R=2.0, bias=False):
    o, h, l, c = X.o, X.h, X.l, X.c
    t = X.F.t
    out = []
    for di in range(1, len(X.starts)):
        for kz in ("lon", "ny"):
            P = pools(X, di, kz)
            if P is None:
                continue
            lows, highs, ws, we, i0, i1 = P
            side, kr = 0, -1
            for k in range(ws, we):
                lo_hit = any(l[k] < p for p in lows)
                hi_hit = any(h[k] > p for p in highs)
                if lo_hit and hi_hit:
                    break
                if lo_hit or hi_hit:
                    side, kr = (1 if lo_hit else -1), k
                    break
            if side == 0:
                continue
            stop_t = ny_ts(X.day[i0], 16)
            lim_k = min(kr + 13, i1 - 1, X.F.at(stop_t) - 1)

            def against(j):
                return (side > 0 and c[j] < o[j]) or (side < 0 and c[j] > o[j])

            def level_for(kx):
                """Apertura de la primera vela de la última serie de velas contrarias que llega al extremo."""
                j = kx if against(kx) else kx - 1
                if j < i0 or not against(j):
                    return np.nan
                while j - 1 >= i0 and against(j - 1):
                    j -= 1
                return o[j]

            ext, kx = (l[kr], kr) if side > 0 else (h[kr], kr)
            level = level_for(kx)
            trig = -1
            for m in range(kr + 1, lim_k):
                if (side > 0 and l[m] < ext) or (side < 0 and h[m] > ext):
                    ext, kx = (l[m] if side > 0 else h[m]), m
                    level = level_for(kx)
                    continue
                if np.isfinite(level) and ((side > 0 and c[m] > level) or (side < 0 and c[m] < level)):
                    trig = m
                    break
            if trig < 0 or not X.allowed(trig, side, bias):
                continue
            stop = ext * (1 - BUF) if side > 0 else ext * (1 + BUF)
            tp_px, mr = None, None
            if tp_mode != "R":
                opp = [p for p in (highs if side > 0 else lows) if (p - c[trig]) * side > 0]
                if not opp:
                    continue
                tp_px, mr = (min(opp) if side > 0 else max(opp)), 2.0
            r = trade(X.F, int(t[trig + 1]), side, stop, stop_t, R=R, tp_px=tp_px, min_R=mr, meta={"kz": kz})
            if r:
                out.append(r)
    return out


# ───────── 9) Turtle soup ─────────
def turtle(df, F, N=20, gap=4, R=2.0, max_bars=48):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    n = len(c)
    out = []
    for k in range(N + 1, n - 1):
        wl, wh = l[k - N:k], h[k - N:k]
        il, ih = int(np.argmin(wl)), int(np.argmax(wh))
        pl_, ph_ = wl[il], wh[ih]
        if l[k] < pl_ and N - il >= gap and c[k] > pl_ and not h[k] > ph_:
            r = trade(F, int(t[k] + step), 1, l[k] * (1 - BUF), int(t[k] + step + max_bars * step), R=R)
            if r:
                out.append(r)
        elif h[k] > ph_ and N - ih >= gap and c[k] < ph_ and not l[k] < pl_:
            r = trade(F, int(t[k] + step), -1, h[k] * (1 + BUF), int(t[k] + step + max_bars * step), R=R)
            if r:
                out.append(r)
    return out


# ───────── 10) Judas swing / Power of 3 ─────────
def judas(X, delta=0.001, tp_mode="R", R=2.0, bias=False):
    h, l, c, o = X.h, X.l, X.c, X.o
    t = X.F.t
    out = []
    for di in range(1, len(X.starts)):
        i0, i1 = X.starts[di], X.ends[di]
        p0, p1 = X.starts[di - 1], X.ends[di - 1]
        if i1 - i0 < 250 or X.tod[i0] != 0:
            continue
        O = o[i0]
        pdh, pdl = h[p0:p1].max(), l[p0:p1].min()
        _, k10 = X.window(i0, i1, 0, 600)
        stop_t = ny_ts(X.day[i0], 16)
        armed, lo, hi = 0, np.inf, -np.inf
        for k in range(i0, k10):
            lo, hi = min(lo, l[k]), max(hi, h[k])
            if armed == 0:
                if X.tod[k] >= 300:
                    break
                dn_, up_ = l[k] <= O * (1 - delta), h[k] >= O * (1 + delta)
                if dn_ and up_:
                    break
                armed = 1 if dn_ else (-1 if up_ else 0)
                continue
            if (armed > 0 and c[k] > O) or (armed < 0 and c[k] < O):
                side = armed
                if not X.allowed(k, side, bias):
                    break
                stop = lo * (1 - BUF) if side > 0 else hi * (1 + BUF)
                tp_px, mr = None, None
                if tp_mode != "R":
                    tp_px, mr = (pdh if side > 0 else pdl), 2.0
                r = trade(X.F, int(t[k + 1]), side, stop, stop_t, R=R, tp_px=tp_px, min_R=mr)
                if r:
                    out.append(r)
                break
    return out


# ───────── 11) Descuento / premium ─────────
def premium_discount(df, label, cost, fund, start, L=50):
    h, l, c = df.h.values, df.l.values, df.c.values
    tr, up, dn = lux_trailing(h, l, c, L)
    mid = (up + dn) / 2
    tf = "4h" if "4h" in label else "1d"
    always = np.where(tr > 0, 1.0, 0.0)
    disc = np.zeros(len(c))
    pos = 0.0
    for k in range(len(c)):
        if tr[k] <= 0:
            pos = 0.0
        elif pos == 0.0 and np.isfinite(mid[k]) and c[k] <= mid[k]:
            pos = 1.0
        disc[k] = pos
    rows = {}
    for name, p in (("estructura alcista", always), ("alcista + comprar en descuento", disc),
                    ("aguantar", np.ones(len(c)))):
        r = run(df, p, tf, cost, fund)
        a, i_, o_ = metrics(r, start), metrics(r, start, SPLIT), metrics(r, SPLIT)
        rows[name] = {"ret": a["ret"], "dd": a["dd"], "sh_is": i_["sharpe"], "sh_oos": o_["sharpe"], "expo": a["expo"]}
        print(f"  {label:10s} {name:32s} total {a['ret'] * 100:+8.0f}%  caída {a['dd'] * 100:4.0f}%  Sharpe hasta 2023 "
              f"{i_['sharpe']:.2f} · 2024-26 {o_['sharpe']:.2f}  comprado {a['expo'] * 100:3.0f}%")
    RES["descuento " + label] = rows
    sys.stdout.flush()


def main():
    f5, f15 = load("fut_5m"), load("fut_15m")
    X = Ctx(f5, f15)
    F = X.F
    h1, h4 = resample(f15, "1h"), resample(f15, "4h")

    print("\n### 1) Modelo 2022 (5m): barrida en killzone → cambio de estructura con FVG → límite en el FVG")
    for entry in ("edge", "ce"):
        for tpm in ("R", "pool"):
            for bias in (False, True):
                lab = f"M2022 {'borde' if entry == 'edge' else 'mitad'} {'2R' if tpm == 'R' else 'liq.opuesta'}{' +sesgo' if bias else ''}"
                show(lab, model2022(X, entry=entry, tp_mode=tpm, bias=bias), RES)
    show("M2022 borde 2R a cualquier hora 00-12", model2022(X, kz_list=("lon",), anytime=True), RES)

    print("\n### 2) Silver Bullet (5m)")
    for sw in (True, False):
        for bias in (False, True):
            show(f"SB {'con barrida' if sw else 'primer FVG'}{' +sesgo' if bias else ''}", silver_bullet(X, sweep=sw, bias=bias), RES)

    print("\n### 3) OTE (70,5 % del tramo tras ruptura)")
    for tfn, df in (("15m", f15), ("1h", h1), ("4h", h4)):
        for ext in (0.0, 0.27):
            for nf in (False, True):
                lab = f"OTE {tfn} objetivo {'0 %' if ext == 0 else '−27 %'}{' +FVG' if nf else ''}"
                show(lab, ote(df, F, 10, ext, need_fvg=nf), RES)

    print("\n### 4) Breaker")
    for tfn, df in (("15m", f15), ("1h", h1)):
        show(f"Breaker {tfn}", breakers(df, F, 10), RES)

    print("\n### 5) FVG invertido (IFVG)")
    f5df = f5
    for tfn, df in (("5m", f5df), ("15m", f15)):
        show(f"IFVG {tfn}", ifvg(df, F), RES)

    print("\n### 6) Máximos/mínimos débiles y fuertes (LuxAlgo): ¿se toca antes el débil?")
    weak_strong_study(f5, 70, 12, 2016, "5m swing 70")
    weak_strong_study(f15, 50, 4, 672, "15m swing 50")
    weak_strong_study(h1, 50, 1, 336, "1h swing 50")

    print("\n### 7) Order blocks de swing de LuxAlgo (5m, swing 70)")
    for R in (1.0, 2.0):
        show(f"OB LuxAlgo 5m swing 70 TP {R:.0f}R", lux_obs(f5, F, 70, R), RES)
    show("OB LuxAlgo 15m swing 50 TP 2R", lux_obs(f15, F, 50, 2.0, valid=96, max_bars=96), RES)

    print("\n### 8) CISD tras barrida en killzone (5m)")
    for tpm in ("R", "pool"):
        for bias in (False, True):
            show(f"CISD {'2R' if tpm == 'R' else 'liq.opuesta'}{' +sesgo' if bias else ''}", cisd(X, tpm, bias=bias), RES)

    print("\n### 9) Turtle soup")
    for tfn, df in (("1h", h1), ("4h", h4)):
        show(f"Turtle soup {tfn}", turtle(df, F), RES)

    print("\n### 10) Judas swing / Power of 3 (apertura de medianoche de NY)")
    for tpm in ("R", "pool"):
        for bias in (False, True):
            show(f"Judas {'2R' if tpm == 'R' else 'máx/mín ayer'}{' +sesgo' if bias else ''}", judas(X, tp_mode=tpm, bias=bias), RES)

    print("\n### 11) Descuento/premium con estructura alcista (posición, con comisiones)")
    s1h = load("spot_1h")
    st_f, st_s = pd.Timestamp("2020-08-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")
    premium_discount(h4, "fut 4h", COST, True, st_f)
    premium_discount(resample(f15, "1D"), "fut 1d", COST, True, st_f, L=10)
    premium_discount(resample(s1h, "4h"), "spot 4h", COST_SPOT, False, st_s)
    with open("smc2.json", "w") as fh:
        json.dump(RES, fh, default=float, indent=1)


if __name__ == "__main__":
    main()
