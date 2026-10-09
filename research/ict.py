"""SMC / ICT: barrido del rango asiático, barrido del máximo/mínimo del día anterior, CHoCH interno a favor
de la estructura, FVG y order blocks. Entradas al cierre de vela (o con orden límite en FVG/OB),
salidas recorriendo velas de 5 minutos (si stop y objetivo caen en la misma vela de 5m, cuenta el stop).
Cada setup se compara con la misma operación en sentido contrario (mismas distancias): si la dirección
de la señal no aporta nada, las dos dan lo mismo."""
import json
import sys

import numpy as np
from numba import njit

from common import COST, MAKER, SPLIT, atr, ema_fast, load, secs, summarize_R
from smc import fvgs, lux_structure

BUF = 0.0002   # margen del stop más allá del extremo (0,02 %)


@njit(cache=True)
def walk(h5, l5, c5, k, end, side, stop, tp):
    """Recorre velas de 5m desde k hasta end (incluida). Devuelve (precio salida, motivo 0 stop / 1 tp / 2 tiempo, j)."""
    for j in range(k, end + 1):
        if side > 0:
            if l5[j] <= stop:
                return stop, 0, j
            if h5[j] >= tp:
                return tp, 1, j
        else:
            if h5[j] >= stop:
                return stop, 0, j
            if l5[j] <= tp:
                return tp, 1, j
    return c5[end], 2, end


class Fine:
    def __init__(self, df5):
        self.t = secs(df5.index)
        self.h, self.l, self.c = df5.h.values, df5.l.values, df5.c.values

    def at(self, ts):
        """índice de la primera vela de 5m que empieza en ts o después."""
        return int(np.searchsorted(self.t, ts))


def trade(F, ts_entry, side, entry, stop, R, ts_end, entry_cost=COST, tp_cost=COST, meta=None, tp_px=None):
    """Simula una operación y su espejo (sentido contrario, mismas distancias)."""
    k = F.at(ts_entry)
    end = F.at(ts_end) - 1
    if k >= len(F.t) or end < k:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    out = {}
    for name, s in (("real", side), ("espejo", -side)):
        st = entry - s * risk
        tp = tp_px if (tp_px is not None and name == "real") else entry + s * R * risk
        if name == "espejo" and tp_px is not None:
            tp = entry + s * abs(tp_px - entry)
        px, why, j = walk(F.h, F.l, F.c, k, end, s, st, tp)
        gross = s * (px / entry - 1)
        fee = entry_cost + (COST if why == 0 else tp_cost if why == 1 else COST)
        ret = gross - fee
        rr = risk / entry
        out[name] = {"ret": ret, "R": ret / rr, "gR": gross / rr, "fee": fee, "risk": rr,
                     "why": ("stop", "tp", "time")[why], "j": j}
    # deriva: lo que hizo el precio a favor de la señal desde la entrada hasta el final de la ventana (sin stop ni objetivo)
    out["drift"] = side * (F.c[end] / entry - 1)
    out["meta"] = meta or {}
    out["t"] = ts_entry
    out["side"] = side
    return out


def daily_trend(df5):
    """Tendencia diaria (cierre > EMA100 diaria) conocida al empezar cada día UTC: dict día -> ±1."""
    d = df5.resample("1D", label="left", closed="left").agg({"c": "last"}).dropna()
    e = ema_fast(d.c.values, 100)
    sig = np.where(d.c.values > e, 1, -1)
    days = secs(d.index) // 86400
    return {int(days[i] + 1): int(sig[i]) for i in range(100, len(days))}


# ───────── 1) barrido de rango (asiático o día anterior) ─────────
def range_sweeps(df, F, trend_d, kind="asia", N=0, R=2.0, tp_mode="R", a_start=0, a_end=7, win_end=20, exit_h=21):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    day = t // 86400
    out = []
    starts = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
    ends = np.r_[starts[1:], len(t)]
    prev = None
    for di in range(len(starts)):
        i0, i1 = starts[di], ends[di]
        d = int(day[i0])
        sod = t[i0 : i1] - d * 86400
        if kind == "asia":
            m = (sod >= a_start * 3600) & (sod < a_end * 3600)
            if m.sum() < (a_end - a_start) * 3600 // step:
                prev = (h[i0:i1].max(), l[i0:i1].min())
                continue
            hi, lo = h[i0:i1][m].max(), l[i0:i1][m].min()
            w0 = a_end * 3600
        else:
            if prev is None:
                prev = (h[i0:i1].max(), l[i0:i1].min())
                continue
            hi, lo = prev
            w0 = 0
        prev = (h[i0:i1].max(), l[i0:i1].min())
        bh = bl = -1
        eh, el = -np.inf, np.inf
        for k in range(i0, i1):
            s = t[k] - d * 86400
            if s < w0 or s >= win_end * 3600:
                continue
            sig = 0
            # máximo
            if bh < 0 and h[k] > hi:
                bh, eh = k, h[k]
            elif bh >= 0:
                eh = max(eh, h[k])
            if bl < 0 and l[k] < lo:
                bl, el = k, l[k]
            elif bl >= 0:
                el = min(el, l[k])
            if bh >= 0 and c[k] < hi and k - bh <= N:
                sig = -1
            if bl >= 0 and c[k] > lo and k - bl <= N:
                sig = 1 if sig == 0 else 0   # vela que barre los dos lados: se ignora
            if bh >= 0 and k - bh >= N and c[k] >= hi:
                pass
            if sig == 0:
                if (bh >= 0 and k - bh >= N) and (bl >= 0 and k - bl >= N):
                    break
                continue
            entry = c[k]
            stop = eh * (1 + BUF) if sig < 0 else el * (1 - BUF)
            risk = abs(entry - stop)
            tp_px = None
            if tp_mode == "opp":
                tgt = lo if sig < 0 else hi
                if abs(tgt - entry) < 2 * risk or (tgt - entry) * sig <= 0:
                    break
                tp_px = tgt
            meta = {"hour": int((t[k] + step) % 86400 // 3600), "wd": int(((t[k] // 86400) + 3) % 7),
                    "trend": trend_d.get(d, 0), "range": (hi - lo) / entry, "kind": kind}
            r = trade(F, int(t[k] + step), sig, entry, stop, R, d * 86400 + exit_h * 3600, meta=meta, tp_px=tp_px)
            if r:
                out.append(r)
            break   # una operación por día
    return out


# ───────── 2) CHoCH interno a favor de la estructura swing ─────────
def choch_aligned(df, F, L_swing=50, L_int=5, R=2.0, max_bars=96, only_choch=True):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    tr_s = lux_structure(h, l, c, L_swing)[0]
    tr_i, ev_i, top_i, btm_i, _, _ = lux_structure(h, l, c, L_int)
    out = []
    for k in np.flatnonzero(ev_i != 0):
        e = ev_i[k]
        if only_choch and abs(e) != 2:
            continue
        side = 1 if e > 0 else -1
        if tr_s[k] != side:
            continue
        stop = btm_i[k] * (1 - BUF) if side > 0 else top_i[k] * (1 + BUF)
        if not np.isfinite(stop) or (c[k] - stop) * side <= 0:
            continue
        meta = {"hour": int((t[k] + step) % 86400 // 3600), "wd": int(((t[k] // 86400) + 3) % 7)}
        r = trade(F, int(t[k] + step), side, c[k], stop, R, int(t[k] + step + max_bars * step), meta=meta)
        if r:
            out.append(r)
    return out


# ───────── 3) FVG a favor de la estructura (orden límite en el borde del hueco) ─────────
def fvg_entries(df, F, L_swing=50, R=2.0, valid=24, max_bars=96, min_atr=0.3, pd_filter=False):
    t = secs(df.index)
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    a = atr(df, 14)
    tr_s, _, top, btm, _, _ = lux_structure(h, l, c, L_swing)
    typ, ftop, fbot = fvgs(h, l, c, o, a * min_atr)
    out = []
    n = len(c)
    for k in np.flatnonzero(typ != 0):
        side = int(typ[k])
        if tr_s[k] != side:
            continue
        if pd_filter and np.isfinite(top[k]) and np.isfinite(btm[k]) and top[k] > btm[k]:
            mid = (top[k] + btm[k]) / 2
            lim = ftop[k] if side > 0 else fbot[k]
            if (side > 0 and lim > mid) or (side < 0 and lim < mid):
                continue
        lim = ftop[k] if side > 0 else fbot[k]
        stop = min(l[k - 2], l[k - 1]) * (1 - BUF) if side > 0 else max(h[k - 2], h[k - 1]) * (1 + BUF)
        risk = abs(lim - stop)
        if risk <= 0:
            continue
        tp = lim + side * R * risk
        fill = -1
        for j in range(k + 1, min(n, k + 1 + valid)):
            if (side > 0 and h[j] >= tp) or (side < 0 and l[j] <= tp):
                break   # llegó al objetivo sin volver al hueco: se cancela
            if (side > 0 and l[j] < lim) or (side < 0 and h[j] > lim):
                fill = j
                break
        if fill < 0:
            continue
        # la vela de llenado puede tocar el stop: se recorre desde su apertura en 5m
        meta = {"hour": int(t[fill] % 86400 // 3600), "wd": int(((t[fill] // 86400) + 3) % 7)}
        r = trade(F, int(t[fill]), side, lim, stop, R, int(t[fill] + max_bars * step), entry_cost=MAKER,
                  tp_cost=MAKER, meta=meta)
        if r:
            out.append(r)
    return out


# ───────── 4) order block tras ruptura de estructura ─────────
def ob_entries(df, F, L=50, R=2.0, valid=48, max_bars=96):
    t = secs(df.index)
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    tr, ev, top, btm, top_i, btm_i = lux_structure(h, l, c, L)
    out = []
    n = len(c)
    for k in np.flatnonzero(ev != 0):
        side = 1 if ev[k] > 0 else -1
        # origen del impulso: el último mínimo (alcista) o máximo (bajista) de swing
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
        lim = h[ob] if side > 0 else l[ob]
        stop = l[ob] * (1 - BUF) if side > 0 else h[ob] * (1 + BUF)
        risk = abs(lim - stop)
        if risk <= 0 or (c[k] - lim) * side <= 0:
            continue
        tp = lim + side * R * risk
        fill = -1
        for j in range(k + 1, min(n, k + 1 + valid)):
            if (side > 0 and h[j] >= tp) or (side < 0 and l[j] <= tp):
                break
            if (side > 0 and l[j] < lim) or (side < 0 and h[j] > lim):
                fill = j
                break
        if fill < 0:
            continue
        meta = {"hour": int(t[fill] % 86400 // 3600), "wd": int(((t[fill] // 86400) + 3) % 7)}
        r = trade(F, int(t[fill]), side, lim, stop, R, int(t[fill] + max_bars * step), entry_cost=MAKER,
                  tp_cost=MAKER, meta=meta)
        if r:
            out.append(r)
    return out


# ───────── resumen ─────────
def summary(trs, label, cond=None):
    rows = {}
    for per, f in (("IS", lambda x: x["t"] < SPLIT.timestamp()), ("OOS", lambda x: x["t"] >= SPLIT.timestamp())):
        sel = [x for x in trs if f(x) and (cond is None or cond(x))]
        real = summarize_R([{**x["real"]} for x in sel], label)
        mirr = summarize_R([{**x["espejo"]} for x in sel], label)
        if sel:
            real["gR"] = float(np.mean([x["real"]["gR"] for x in sel]))
            mirr["gR"] = float(np.mean([x["espejo"]["gR"] for x in sel]))
            real["drift_bp"] = float(np.mean([x["drift"] for x in sel]) * 1e4)
            real["drift_up"] = float(np.mean([x["drift"] > 0 for x in sel]))
        rows[per] = (real, mirr)
    return rows


def show(label, rows):
    parts = []
    for per in ("IS", "OOS"):
        r, m = rows[per]
        if r["n"] == 0:
            parts.append(f"{per}: —")
            continue
        parts.append(f"{per}: n={r['n']:5d} gana {r['win'] * 100:4.1f}% neto {r['avgR']:+.2f}R (t={r['t']:+.1f}) "
                     f"bruto {r['gR']:+.2f}R espejo {m['gR']:+.2f}R riesgo {r['risk_med'] * 100:.2f}% "
                     f"deriva {r['drift_bp']:+.0f}pb a favor {r['drift_up'] * 100:.0f}%")
    print(f"{label:58s} " + " || ".join(parts))
    sys.stdout.flush()


def main():
    f5 = load("fut_5m")
    f15 = load("fut_15m")
    F = Fine(f5)
    td = daily_trend(f5)
    res = {}

    print("\n### Barrido del rango asiático (00-07 UTC), una operación por día, salida máx. 21:00 UTC")
    for tfn, df in (("15m", f15), ("5m", f5)):
        for N in (0, 4):
            for tpm, R in (("R", 1.0), ("R", 2.0), ("R", 3.0), ("opp", 2.0)):
                lab = f"Asia {tfn} N={N} TP={'opuesta≥2R' if tpm == 'opp' else str(R) + 'R'}"
                trs = range_sweeps(df, F, td, "asia", N=N, R=R, tp_mode=tpm)
                res[lab] = trs
                show(lab, summary(trs, lab))
    print("\n### Barrido del máximo/mínimo del día anterior")
    for tfn, df in (("15m", f15), ("5m", f5)):
        for N in (0, 4):
            for tpm, R in (("R", 2.0), ("opp", 2.0)):
                lab = f"PDH/PDL {tfn} N={N} TP={'opuesta≥2R' if tpm == 'opp' else str(R) + 'R'}"
                trs = range_sweeps(df, F, td, "pd", N=N, R=R, tp_mode=tpm, win_end=21, exit_h=23)
                res[lab] = trs
                show(lab, summary(trs, lab))
    print("\n### CHoCH interno (5) a favor del swing")
    for tfn, df, Ls in (("15m", f15, 50), ("5m", f5, 50), ("5m", f5, 70)):
        for R in (1.0, 2.0):
            lab = f"CHoCH {tfn} swing {Ls} TP={R}R"
            trs = choch_aligned(df, F, Ls, 5, R)
            res[lab] = trs
            show(lab, summary(trs, lab))
    print("\n### FVG a favor del swing (límite en el hueco)")
    for tfn, df in (("15m", f15), ("5m", f5)):
        for pdf in (False, True):
            for R in (1.0, 2.0):
                lab = f"FVG {tfn} TP={R}R{' +descuento/premium' if pdf else ''}"
                trs = fvg_entries(df, F, 50, R, pd_filter=pdf)
                res[lab] = trs
                show(lab, summary(trs, lab))
    print("\n### Order block tras BOS/CHoCH swing (límite en el OB)")
    for tfn, df in (("15m", f15), ("5m", f5)):
        for R in (1.0, 2.0):
            lab = f"OB {tfn} TP={R}R"
            trs = ob_entries(df, F, 50, R)
            res[lab] = trs
            show(lab, summary(trs, lab))
    with open("ict_trades.json", "w") as fh:
        json.dump({k: [{"t": x["t"], "side": x["side"], "meta": x["meta"], "real": x["real"], "espejo": x["espejo"],
                        "drift": x["drift"]} for x in v] for k, v in res.items()}, fh, default=float)


if __name__ == "__main__":
    main()
