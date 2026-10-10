"""Simulador de las pruebas SMC/ICT/Wyckoff (octubre de 2026, segunda tanda).

- Entrada a mercado (apertura de la vela siguiente a la señal) o con orden límite.
- Salida recorriendo velas de 5m. Si stop y objetivo caen en la misma vela de 5m, cuenta el stop. En la vela de 5m
  en la que se llena una orden límite solo cuenta el recorrido que pudo pasar después de llenarse (si el precio bajaba
  hasta la compra, el mínimo de esa vela; el máximo pudo ser antes): para la compra, el stop; para su espejo, el objetivo.
- Costes: entrada a mercado 0,06 %, límite 0,02 %; objetivo (orden límite) 0,02 %; stop o salida por tiempo 0,06 %.
- Espejo: la misma operación en sentido contrario, mismo momento y mismas distancias. Si la dirección de la señal no
  aporta nada, real y espejo dan lo mismo antes de comisiones (≈ 0 R).
- Dentro de muestra: hasta 2023. Fuera de muestra: 2024-2026."""
import math
import sys

import numpy as np
import pandas as pd
from numba import njit

from common import COST, MAKER, SPLIT, secs

BUF = 0.0002            # margen del stop más allá del extremo (0,02 %)
SPLIT_T = int(SPLIT.timestamp())


@njit(cache=True)
def fill_at(h5, l5, k0, end, side, lim):
    """Primera vela de 5m (desde k0) que toca el precio límite; -1 si no llega."""
    for j in range(k0, end + 1):
        if (side > 0 and l5[j] <= lim) or (side < 0 and h5[j] >= lim):
            return j
    return -1


@njit(cache=True)
def walk_from(h5, l5, c5, j0, end, side, stop, tp, fill_dir):
    """Devuelve (precio de salida, motivo 0 stop / 1 objetivo / 2 tiempo, vela).
    fill_dir = 0: entrada a mercado (en la primera vela cuentan stop y objetivo; si caen los dos, stop).
    fill_dir = ±1: orden límite llenada con el precio bajando (+1) o subiendo (−1) hasta ella. En esa vela de 5m solo
    cuenta lo que pudo pasar después de llenarse: el recorrido en la dirección en que venía el precio (mínimo si bajaba,
    máximo si subía). Así se trata igual a la operación real y a su espejo."""
    for j in range(j0, end + 1):
        first = fill_dir != 0 and j == j0
        lo_ok = not first or fill_dir > 0
        hi_ok = not first or fill_dir < 0
        if side > 0:
            if lo_ok and l5[j] <= stop:
                return stop, 0, j
            if hi_ok and h5[j] >= tp:
                return tp, 1, j
        else:
            if hi_ok and h5[j] >= stop:
                return stop, 0, j
            if lo_ok and l5[j] <= tp:
                return tp, 1, j
    return c5[end], 2, end


class Fine:
    """Velas de 5m para recorrer las salidas."""

    def __init__(self, df5):
        self.t = secs(df5.index)
        self.o, self.h, self.l, self.c = df5.o.values, df5.h.values, df5.l.values, df5.c.values

    def at(self, ts):
        return int(np.searchsorted(self.t, ts))


def trade(F, ts_entry, side, stop, ts_end, R=2.0, tp_px=None, limit=None, meta=None, min_R=None):
    """Una operación y su espejo. ts_entry: momento en que se puede entrar (apertura de la vela siguiente a la señal,
    o inicio de la vela en la que se llena la orden límite). tp_px: objetivo fijo (si no, R veces el riesgo)."""
    k0 = F.at(ts_entry)
    end = F.at(ts_end) - 1
    if k0 >= len(F.t) or end < k0:
        return None
    if limit is None:
        j0, entry, ecost, fdir = k0, F.o[k0], COST, 0
    else:
        j0 = fill_at(F.h, F.l, k0, end, side, limit)
        if j0 < 0:
            return None
        entry, ecost, fdir = limit, MAKER, side       # una compra límite se llena con el precio bajando
    risk = (entry - stop) * side
    if risk <= 0:
        return None
    if tp_px is not None:
        if (tp_px - entry) * side <= 0:
            return None
        if min_R is not None and (tp_px - entry) * side < min_R * risk:
            return None
        dist = abs(tp_px - entry)
    else:
        dist = R * risk
    out = {"t": int(F.t[j0]), "side": side, "meta": meta or {}}
    for name, s in (("real", side), ("espejo", -side)):
        st = entry - s * risk
        tp = entry + s * dist
        px, why, j = walk_from(F.h, F.l, F.c, j0, end, s, st, tp, fdir)
        gross = s * (px / entry - 1)
        fee = ecost + (MAKER if why == 1 else COST)
        rr = risk / entry
        out[name] = {"R": (gross - fee) / rr, "gR": gross / rr, "ret": gross - fee, "risk": rr, "why": int(why),
                     "bars": int(j - j0 + 1)}
    out["drift"] = side * (F.c[end] / entry - 1)
    return out


# ───────── hora de Nueva York (con horario de verano) ─────────
def ny_clock(t):
    """t en segundos UTC -> (día de NY como entero, hora, minuto, día de la semana 0=lunes)."""
    idx = pd.to_datetime(t, unit="s", utc=True).tz_convert("America/New_York")
    day = (idx.normalize().tz_localize(None) - pd.Timestamp("1970-01-01")).days.values.astype(np.int64)
    return day, idx.hour.values.astype(np.int64), idx.minute.values.astype(np.int64), idx.weekday.values.astype(np.int64)


def ny_ts(day, hour, minute=0):
    """Segundos UTC del instante day (entero de NY) hh:mm hora de NY."""
    ts = pd.Timestamp("1970-01-01") + pd.Timedelta(days=int(day), hours=hour, minutes=minute)
    return int(ts.tz_localize("America/New_York", nonexistent="shift_forward", ambiguous=True).tz_convert("UTC").timestamp())


# ───────── resumen ─────────
def _stats(sel, key):
    if not sel:
        return None
    R = np.array([x[key]["R"] for x in sel])
    g = np.array([x[key]["gR"] for x in sel])
    se = R.std(ddof=1) / math.sqrt(len(R)) if len(R) > 1 else float("nan")
    return {"n": len(R), "win": float((R > 0).mean()), "R": float(R.mean()), "gR": float(g.mean()),
            "t": float(R.mean() / se) if se and se > 0 else float("nan"),
            "risk": float(np.median([x[key]["risk"] for x in sel]))}


def summary(trs):
    out = {}
    for per, f in (("IS", lambda x: x["t"] < SPLIT_T), ("OOS", lambda x: x["t"] >= SPLIT_T), ("todo", lambda x: True)):
        sel = [x for x in trs if f(x)]
        out[per] = {"real": _stats(sel, "real"), "espejo": _stats(sel, "espejo")}
    return out


def show(label, trs, results=None):
    s = summary(trs)
    parts = []
    for per in ("IS", "OOS"):
        r, m = s[per]["real"], s[per]["espejo"]
        if r is None:
            parts.append(f"{per}: —")
            continue
        parts.append(f"{per}: n={r['n']:5d} gana {r['win'] * 100:4.1f}% neto {r['R']:+.2f}R (t={r['t']:+.1f}) "
                     f"bruto {r['gR']:+.2f}R espejo {m['gR']:+.2f}R riesgo {r['risk'] * 100:.2f}%")
    print(f"{label:52s} " + " || ".join(parts))
    sys.stdout.flush()
    if results is not None:
        results[label] = s
    return s
