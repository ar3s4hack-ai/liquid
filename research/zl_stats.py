"""Liquidez Zero Lag de 4h con el MISMO código del servidor (trend.py): comprueba que coincide con pine.py/zl_check.py
y calcula los números que enseña la web (LIQ_STUDY)."""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import trend as TR  # noqa: E402

from common import COST, COST_SPOT, SPLIT, load, metrics, resample, run, secs, trades_of  # noqa: E402
from ensemble import build  # noqa: E402
from zl_check import zl  # noqa: E402


def rows6(df):
    t = secs(df.index)
    return [(int(t[i]), float(df.o.iat[i]), float(df.h.iat[i]), float(df.l.iat[i]), float(df.c.iat[i]), float(df.v.iat[i]))
            for i in range(len(df))]


def pocs_for(h4rows, small):
    ts = secs(small.index)
    cs, vs = small.c.values, small.v.values
    out = {}
    for i, _, top, bot in TR.zl_marked(h4rows):
        t0 = h4rows[i][0]
        a, b = np.searchsorted(ts, t0), np.searchsorted(ts, t0 + TR.H4)
        if b > a:
            out[t0] = TR.poc_from(list(zip(cs[a:b], vs[a:b])), top, bot)
    return out


res = {}
for mk, raw, small, cost, fund, start in (("fut", load("fut_15m"), load("fut_5m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC")),
                                          ("spot", load("spot_1h"), None, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC"))):
    h4 = resample(raw, "4h")
    sm = small if small is not None else raw
    rows = rows6(h4)
    srv = np.array(TR.liquidity(rows, pocs_for(rows, sm), full=True), float)
    ref = zl(h4, sm)
    print(f"{mk}: velas {len(srv)}, distintas del estudio {int((np.sign(srv) != np.sign(ref)).sum())}")
    d = resample(raw, "1D")
    g = build(h4, d)
    sc = np.nan_to_num(g["score"].values)
    ok = ~np.isnan(g["score"].values)
    liq = srv > 0
    r = run(h4, np.where(liq, 1.0, 0.0), "4h", cost, fund)
    a, i_, o = metrics(r, start), metrics(r, start, SPLIT), metrics(r, SPLIT)
    hold = metrics(run(h4, np.ones(len(h4)), "4h", cost, fund), start)
    ro = run(h4, np.where((ok & (sc >= 3)) | liq, 1.0, 0.0), "4h", cost, fund)
    ao = metrics(ro, start)
    yrs = {}
    for y in sorted(set(r["idx"].year)):
        m = (r["idx"].year == y) & (r["idx"] >= start)
        if m.any():
            yrs[int(y)] = [float(np.prod(1 + r["pnl"][m]) - 1), float(np.prod(1 + ro["pnl"][m]) - 1)]
    tr = np.array([t[3] for t in trades_of(r, r["idx"] >= start)])
    c = h4.c
    fwd = (c.shift(-42) / c - 1).values
    mm = (h4.index >= start) & ~np.isnan(fwd) & (h4.index.hour == 20)
    x = fwd[mm & liq]
    res[mk] = {"from": str(start.date())[:7], "ret": a["ret"], "dd": a["dd"], "hold": hold["ret"], "hold_dd": hold["dd"],
               "sharpe_is": i_["sharpe"], "sharpe_oos": o["sharpe"], "trades": len(tr), "win": float((tr > 0).mean()),
               "p7": float((x > 0).mean()), "base7": float((fwd[mm] > 0).mean()), "or_ret": ao["ret"], "or_dd": ao["dd"],
               "years": yrs}
    print(json.dumps(res[mk], indent=1, default=float))
json.dump(res, open("zl_stats.json", "w"), default=float, indent=1)
