"""Estrategias de la captura del bot: lectura por votos (RSI, medias, MACD, Bollinger, VWAP, compras a mercado),
EQH/EQL como imán de liquidez y «Rango 9h» (vela de las 9 h de España y rango nocturno 00-09 h)."""
import sys

import numpy as np
import pandas as pd

from common import COST, SPLIT, ema_fast, load, secs, sma
from ict import Fine, trade, summary, show


def rsi(c, n):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


# ───────── 1) lectura por votos (velas de 5m, como el bot) ─────────
def votes(f5):
    c, h, l, v, tbv = f5.c.values, f5.h.values, f5.l.values, f5.v.values, f5.tbv.values
    e20, e50, e200 = ema_fast(c, 20), ema_fast(c, 50), ema_fast(c, 200)
    r9 = rsi(c, 9)
    h1 = f5.c.resample("1h", label="left", closed="left").last().dropna()
    r1h = pd.Series(rsi(h1.values, 14), index=h1.index + pd.Timedelta("1h"))   # conocido al cerrar la hora
    r1h = r1h.reindex(f5.index + pd.Timedelta("5min"), method="ffill").values
    macd = ema_fast(c, 12) - ema_fast(c, 26)
    hist = macd - ema_fast(macd, 9)
    tp = (h + l + c) / 3
    vw = pd.Series(tp * v).rolling(288).sum().values / pd.Series(v).rolling(288).sum().values
    buy = pd.Series(tbv).rolling(12).sum().values / pd.Series(v).rolling(12).sum().values
    m = sma(c, 20)
    sd = pd.Series(c).rolling(20).std(ddof=0).values
    pb = (c - (m - 2 * sd)) / (4 * sd)
    sig = {
        "RSI9 5m": np.sign(r9 - 50),
        "RSI 1h": np.sign(r1h - 50),
        "Medias alineadas": np.where((e20 > e50) & (e50 > e200), 1, np.where((e20 < e50) & (e50 < e200), -1, 0)),
        "Precio vs EMA20": np.sign(c - e20),
        "MACD": np.sign(hist),
        "VWAP 24h": np.sign(c - vw),
        "Compras a mercado 1h": np.sign(buy - 0.5),
        "Bollinger %B": np.sign(pb - 0.5),
    }
    return sig


def vote_study(f5):
    sig = votes(f5)
    c = f5.c.values
    score = np.nansum(np.vstack(list(sig.values())), axis=0)
    t = f5.index
    out = {}
    for hz, nb in (("1h", 12), ("4h", 48), ("24h", 288)):
        fwd = np.full(len(c), np.nan)
        fwd[:-nb] = c[nb:] / c[:-nb] - 1
        rows = []
        for per, mk in (("IS", (t < SPLIT)), ("OOS", (t >= SPLIT))):
            ok = mk & np.isfinite(fwd) & (np.arange(len(c)) > 300)
            base = (fwd[ok] > 0).mean()
            for lo, hi, lab in ((-9, -4, "≤-4 (muy bajista)"), (-3, -1, "-3..-1"), (0, 0, "0"), (1, 3, "+1..+3"),
                                (4, 9, "≥+4 (muy alcista)")):
                s = ok & (score >= lo) & (score <= hi)
                rows.append((per, lab, int(s.sum()), float((fwd[s] > 0).mean()), float(np.mean(fwd[s]) * 1e4), base))
            # cada indicador por separado: P(sube | dice alcista) - P(sube | dice bajista)
            for k, v in sig.items():
                a = ok & (v > 0)
                b = ok & (v < 0)
                rows.append((per, "· " + k, int(a.sum()), float((fwd[a] > 0).mean() - (fwd[b] > 0).mean()),
                             float((np.mean(fwd[a]) - np.mean(fwd[b])) * 1e4), base))
        out[hz] = rows
        print(f"\n== Lectura por votos → precio {hz} después (P sube / media pb; para indicadores: diferencia alcista-bajista)")
        for per, lab, n, p, mu, base in rows:
            print(f"  {per:3s} {lab:28s} n={n:7d}  P={p * 100:5.1f}%  media {mu:+6.1f}pb   (base {base * 100:.1f}%)")
    # regla operable: largo si ≥+4, corto si ≤-4, mantener 1 h, costes 0,06 %/lado
    for thr in (4, 6):
        res = []
        i = 300
        n = len(c)
        while i < n - 12:
            s = score[i]
            if abs(s) >= thr:
                side = 1 if s > 0 else -1
                r = side * (c[i + 12] / c[i] - 1) - 2 * COST
                res.append((t[i], r))
                i += 12
            else:
                i += 1
        for per, f in (("IS", lambda x: x < SPLIT), ("OOS", lambda x: x >= SPLIT)):
            rr = np.array([r for tt, r in res if f(tt)])
            print(f"  Regla votos |score|≥{thr}, 1 h, {per}: n={len(rr)} gana {np.mean(rr > 0) * 100:.1f}% "
                  f"media {rr.mean() * 1e4:+.1f}pb (bruto {rr.mean() * 1e4 + 12:+.1f}pb)")
    return out


# ───────── 2) EQH/EQL como imán ─────────
def eq_study(f15, L=3, tol=0.001, look=288, horizon=672):
    """Cada hora: nivel EQH más cercano por encima y EQL más cercano por debajo (dos máximos/mínimos de giro
    a menos de tol entre sí en las últimas `look` velas, sin barrer aún). ¿Cuál se toca antes?
    Hipótesis nula (paseo aleatorio): P(arriba primero) = d_abajo / (d_arriba + d_abajo).
    Control: lo mismo con máximos/mínimos de giro sueltos (no iguales)."""
    h, l, c = f15.h.values, f15.l.values, f15.c.values
    n = len(c)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    for i in range(L, n - L):
        if h[i] == h[i - L:i + L + 1].max():
            ph[i] = True
        if l[i] == l[i - L:i + L + 1].min():
            pl[i] = True
    hi_idx = np.flatnonzero(ph)
    lo_idx = np.flatnonzero(pl)
    t = f15.index
    res = {"EQ": [], "suelto": []}
    for k in range(look + L, n - horizon, 4):
        cur = c[k]
        # giros confirmados (i + L <= k) en la ventana y sin barrer después
        hs = hi_idx[(hi_idx >= k - look) & (hi_idx + L <= k)]
        ls = lo_idx[(lo_idx >= k - look) & (lo_idx + L <= k)]
        ups, dns = {"EQ": [], "suelto": []}, {"EQ": [], "suelto": []}
        for arr, side, store in ((hs, 1, ups), (ls, -1, dns)):
            vals = (h if side > 0 else l)[arr]
            for a_i, a in enumerate(arr):
                lvl = vals[a_i]
                if side > 0 and (lvl <= cur or h[a + 1:k + 1].max(initial=-np.inf) > lvl):
                    continue
                if side < 0 and (lvl >= cur or l[a + 1:k + 1].min(initial=np.inf) < lvl):
                    continue
                twin = np.any((np.abs(vals - lvl) <= tol * lvl) & (arr != a))
                store["EQ" if twin else "suelto"].append(lvl)
        for kind in ("EQ", "suelto"):
            if not ups[kind] or not dns[kind]:
                continue
            U = min(ups[kind])
            Dn = max(dns[kind])
            du, dd = (U - cur) / cur, (cur - Dn) / cur
            if du > 0.03 or dd > 0.03:
                continue
            fu = np.flatnonzero(h[k + 1:k + 1 + horizon] >= U)
            fd = np.flatnonzero(l[k + 1:k + 1 + horizon] <= Dn)
            a = fu[0] if len(fu) else 10**9
            b = fd[0] if len(fd) else 10**9
            if a == b:
                continue   # misma vela o ninguno: sin resolver
            res[kind].append((t[k], dd / (du + dd), 1 if a < b else 0))
    for kind, v in res.items():
        print(f"\n== {kind}: niveles por encima/debajo, ¿cuál se toca antes? (n={len(v)})")
        for per, f in (("IS", lambda x: x < SPLIT), ("OOS", lambda x: x >= SPLIT)):
            arr = np.array([(p, y) for tt, p, y in v if f(tt)])
            if not len(arr):
                continue
            pred, act = arr[:, 0], arr[:, 1]
            # el nivel más cercano: ¿se toca antes de lo que dice el paseo aleatorio?
            near_up = pred > 0.5
            p_near = np.where(near_up, pred, 1 - pred)
            hit_near = np.where(near_up, act, 1 - act)
            print(f"  {per}: P(el más cercano primero) real {hit_near.mean() * 100:.1f}% vs azar {p_near.mean() * 100:.1f}%  "
                  f"| P(arriba primero) real {act.mean() * 100:.1f}% vs azar {pred.mean() * 100:.1f}%")
    return res


# ───────── 3) Rango 9 h ─────────
def rango9(f15, F, mode="vela9", R=None, stop_mode="opuesto", exit_local=22):
    loc = f15.index.tz_convert("Europe/Madrid")
    t = secs(f15.index)
    h, l, c = f15.h.values, f15.l.values, f15.c.values
    dates = loc.normalize()
    out = []
    n = len(c)
    hour = loc.hour.values
    day_codes, first = np.unique(np.asarray(dates.asi8), return_index=True)
    bounds = list(first) + [n]
    for di in range(len(first)):
        i0, i1 = bounds[di], bounds[di + 1]
        hh = hour[i0:i1]
        if mode == "vela9":
            m = hh == 9
            w0 = 10
        else:
            m = hh < 9
            w0 = 9
        if m.sum() < (4 if mode == "vela9" else 36):
            continue
        hi, lo = h[i0:i1][m].max(), l[i0:i1][m].min()
        for k in range(i0, i1):
            if hour[k] < w0 or hour[k] >= exit_local - 1:
                continue
            side = 1 if c[k] > hi else -1 if c[k] < lo else 0
            if side == 0:
                continue
            entry = c[k]
            if stop_mode == "opuesto":
                stop = lo if side > 0 else hi
            else:
                stop = (hi + lo) / 2
            if (entry - stop) * side <= 0:
                break
            # fin del día local a las exit_local h
            end_ts = int((pd.Timestamp(dates[i0]) + pd.Timedelta(hours=exit_local)).tz_convert("UTC").timestamp())
            RR = R if R is not None else 1000.0
            r = trade(F, int(t[k] + 900), side, entry, stop, RR, end_ts,
                      meta={"hour": int(hour[k]), "wd": int(loc[k].weekday()), "range": (hi - lo) / entry})
            if r:
                out.append(r)
            break
    return out


def main():
    f5 = load("fut_5m")
    f15 = load("fut_15m")
    F = Fine(f5)
    which = sys.argv[1:] or ["votos", "eq", "rango"]
    if "votos" in which:
        vote_study(f5)
    if "rango" in which:
        print("\n### Rango 9 h (hora de España): ruptura al cierre de vela de 15m, una por día, salida 22 h")
        for mode in ("vela9", "noche"):
            for stop_mode in ("opuesto", "mitad"):
                for R in (1.0, 2.0, None):
                    lab = f"{'Vela 9-10h' if mode == 'vela9' else 'Rango 00-09h'} stop {stop_mode} TP {'fin día' if R is None else str(R) + 'R'}"
                    show(lab, summary(rango9(f15, F, mode, R, stop_mode), lab))
    if "eq" in which:
        eq_study(f15)


if __name__ == "__main__":
    main()
