"""Números finales de la señal publicada, calculados con el MISMO código que usa el servidor (trend.py del repo).
Comprueba también que coincide con ensemble.py (la versión de la investigación)."""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # trend.py del servidor
import trend as TR  # noqa: E402

from common import COST, COST_SPOT, SPLIT, hold_metrics, load, metrics, resample, run, secs, trades_of, wilson  # noqa: E402
from ensemble import build  # noqa: E402


def rows(df):
    t = secs(df.index)
    return [(int(t[i]), float(df.o.iat[i]), float(df.h.iat[i]), float(df.l.iat[i]), float(df.c.iat[i])) for i in range(len(df))]


def study(raw, cost, fund, start, label):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    sc, dv, hv = TR.series(rows(d), rows(h4))
    score = np.array([np.nan if s is None else s for _, s in sc], float)
    # comprobación: igual que la investigación
    g = build(h4, d)
    ref = g["score"].values
    both = ~np.isnan(score) & ~np.isnan(ref)
    mism = int((score[both] != ref[both]).sum())
    print(f"{label}: velas 4h {len(score)}, comparadas {both.sum()}, distintas {mism}, "
          f"válidas trend.py {np.isfinite(score).sum()} vs investigación {np.isfinite(ref).sum()}")
    pos = np.where(score == 3, 1.0, 0.0)
    res = run(h4, pos, "4h", cost, fund)
    st = pd.Timestamp(start, tz="UTC")
    a, o = metrics(res, st), metrics(res, SPLIT)
    ha, ho = hold_metrics(h4, "4h", st, None, cost, fund), hold_metrics(h4, "4h", SPLIT, None, cost, fund)
    m = res["idx"] >= st
    tr = trades_of(res, m)
    rets = np.array([x[3] for x in tr])
    dur = np.array([(x[1] - x[0] + 1) * 4 / 24 for x in tr])
    out = {"from": start[:7], "ret": a["ret"], "hold": ha["ret"], "dd": a["dd"], "hold_dd": ha["dd"], "trades": len(rets),
           "win": float((rets > 0).mean()), "avg_win": float(rets[rets > 0].mean()), "avg_loss": float(rets[rets <= 0].mean()),
           "best": float(rets.max()), "worst": float(rets.min()), "days_med": float(np.median(dur)),
           "days_win": float(np.median(dur[rets > 0])), "days_loss": float(np.median(dur[rets <= 0])),
           "expo": a["expo"], "sharpe": a["sharpe"], "hold_sharpe": ha["sharpe"],
           "oos_ret": o["ret"], "oos_hold": ho["ret"], "oos_dd": o["dd"], "oos_hold_dd": ho["dd"]}
    # probabilidades: estado al cierre del día (vela de 4h de las 20:00) → precio 7 y 30 días después
    ts = pd.to_datetime(np.array([t for t, _ in sc]), unit="s", utc=True)
    s_ser = pd.Series(score, index=ts)
    daily = s_ser[s_ser.index.hour == 20]
    daily.index = daily.index.normalize()
    c = d.c.reindex(daily.index)
    for N in (7, 30):
        fwd = c.shift(-N) / c - 1
        mk = (daily.index >= st) & fwd.notna().values & daily.notna().values
        base = fwd[mk]
        p = {"base": float((base > 0).mean()), "n": int(mk.sum())}
        for lab, cond in (("compra", daily == 3), ("espera", (daily == 1) | (daily == 2)), ("venta", daily == 0)):
            x = fwd[mk & cond.values]
            k = int((x > 0).sum())
            lo, hi = wilson(k, len(x))
            p[lab] = float(k / len(x))
            p[lab + "_n"] = int(len(x))
            p[lab + "_ci"] = [round(lo, 3), round(hi, 3)]
            p[lab + "_mu"] = float(x.mean())
        out[f"p{N}"] = p
    if fund:
        sh = run(h4, np.where(score == 0, -1.0, 0.0), "4h", cost, fund)
        out["shorts"] = metrics(sh, st)["ret"]
        sh2 = run(h4, np.where(score <= 2, -1.0, 0.0), "4h", cost, fund)
        out["shorts_le2"] = metrics(sh2, st)["ret"]
    yrs = {}
    for y in sorted(set(res["idx"].year)):
        mm = (res["idx"].year == y) & m
        if mm.any():
            hold_y = h4.c[mm].iloc[-1] / h4.c[mm].iloc[0] - 1
            yrs[int(y)] = [float(np.prod(1 + res["pnl"][mm]) - 1), float(hold_y)]
    out["years"] = yrs
    print(json.dumps(out, indent=1, default=float))
    return out


fut = study(load("fut_15m"), COST, True, "2020-08-01", "futuros")
spot = study(load("spot_1h"), COST_SPOT, False, "2018-01-01", "spot")
json.dump({"fut": fut, "spot": spot}, open("final_stats.json", "w"), default=float, indent=1)
