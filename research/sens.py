"""Sensibilidad de la regla de unanimidad a sus parámetros + cortos en 0/3."""
import numpy as np
import pandas as pd

from common import COST, COST_SPOT, SPLIT, ema_fast, fmt_pct, hold_metrics, load, metrics, resample, run, sma
from trend_rules import state

def sig(h4, d, e_len=100, s=1.0, f=50, sl=200, band_len=35):
    c, h, l = d.c.values, d.h.values, d.l.values
    s1 = (c > ema_fast(c, e_len)).astype(float); s1[:e_len] = np.nan
    hl2 = (h + l) / 2
    ewo = np.nan_to_num(sma(hl2, 5) - sma(hl2, 35))
    up = ema_fast(np.maximum(ewo, 0), band_len); lo = ema_fast(np.minimum(ewo, 0), band_len)
    s2 = (state(ewo > up * s, ewo < 0, ewo < lo * s, ewo > 0) > 0).astype(float); s2[:40] = np.nan
    ds = pd.DataFrame({"a": s1, "b": s2}, index=d.index + pd.Timedelta(hours=20)).reindex(h4.index).ffill()
    c4 = h4.c.values
    s3 = (ema_fast(c4, f) > ema_fast(c4, sl)).astype(float); s3[:sl] = np.nan
    return ds.a.values + ds.b.values + s3

for name, src, cost, fund, start in (("futuros", "fut_15m", COST, True, "2020-08-01"), ("spot", "spot_1h", COST_SPOT, False, "2018-01-01")):
    raw = load(src); h4 = resample(raw, "4h"); d = resample(raw, "1D"); st = pd.Timestamp(start, tz="UTC")
    hold = hold_metrics(h4, "4h", st, None, cost, fund)
    print(f"\n== {name}: mantener ALL {fmt_pct(hold['ret'])} dd {fmt_pct(hold['dd'])} sh {hold['sharpe']:.2f}")
    for kw in ({}, {"e_len": 80}, {"e_len": 120}, {"e_len": 150}, {"s": 0.8}, {"s": 1.2}, {"band_len": 20}, {"band_len": 50},
               {"f": 40, "sl": 160}, {"f": 60, "sl": 240}, {"f": 50, "sl": 150}):
        sc = sig(h4, d, **kw)
        pos = np.where(sc >= 3, 1.0, 0.0)
        res = run(h4, pos, "4h", cost, fund)
        a, i, o = metrics(res, st), metrics(res, st, SPLIT), metrics(res, SPLIT)
        print(f"  {str(kw):28s} ALL {fmt_pct(a['ret']):>7s} dd {fmt_pct(a['dd'])} sh {a['sharpe']:.2f} | IS sh {i['sharpe']:.2f} | OOS {fmt_pct(o['ret'])} dd {fmt_pct(o['dd'])} sh {o['sharpe']:.2f}")
    if fund:
        sc = sig(h4, d)
        for lab, cond in (("corto si 0/3", sc == 0), ("corto si ≤1/3", sc <= 1)):
            pos = np.where(cond, -1.0, 0.0)
            res = run(h4, pos, "4h", cost, fund)
            a, i, o = metrics(res, st), metrics(res, st, SPLIT), metrics(res, SPLIT)
            print(f"  {lab:28s} ALL {fmt_pct(a['ret']):>7s} dd {fmt_pct(a['dd'])} sh {a['sharpe']:.2f} | IS {fmt_pct(i['ret'])} | OOS {fmt_pct(o['ret'])} tr {a['trades']} gana {a['win']*100:.0f}%")
