"""Liquidez Zero Lag (4h) junto a los 3 votos actuales: ¿mejora la señal?"""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, SPLIT, fmt_pct, load, metrics, random_benchmark, resample, run, years_table
from ensemble import build
from zl_check import zl

for mk, raw, ltf, cost, fund, start in (("futuros", load("fut_15m"), load("fut_5m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC")),
                                        ("spot", load("spot_1h"), None, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    sc = g["score"].values
    ok = ~np.isnan(sc)
    z = (zl(h4, ltf if ltf is not None else raw) > 0).astype(float)
    v1, v2, v3 = np.nan_to_num(g["ema100"].values), np.nan_to_num(g["ewo"].values), np.nan_to_num(g["x4h"].values)
    rules = {
        "actual: 3 de 3": ok & (sc >= 3),
        "solo liquidez Zero Lag": z == 1,
        "3 de 3 Y liquidez": ok & (sc >= 3) & (z == 1),
        "3 de 3 O liquidez": (ok & (sc >= 3)) | (z == 1),
        "mayoría: 3 de 4 votos": ok & ((np.nan_to_num(sc) + z) >= 3),
        "4 de 4 votos": ok & ((np.nan_to_num(sc) + z) >= 4),
        "EMA100 + EWO + liquidez (3/3)": ok & (v1 + v2 + z >= 3),
        "EMA100 + liquidez (2/2)": ok & (v1 + z >= 2),
    }
    print(f"== {mk} desde {start.date()}")
    for name, cond in rules.items():
        pos = np.where(cond, 1.0, 0.0)
        r = run(h4, pos, "4h", cost, fund)
        i, o, a = metrics(r, start, SPLIT), metrics(r, SPLIT), metrics(r, start)
        rb = random_benchmark(r, start, None, n=400)["pct"]
        y = years_table(r)
        print(f"  {name:30s} IS sh {i['sharpe']:.2f} dd {i['dd']*100:4.0f}% | OOS sh {o['sharpe']:.2f} dd {o['dd']*100:4.0f}% {fmt_pct(o['ret']):>6s} | "
              f"total {fmt_pct(a['ret']):>7s} dd {a['dd']*100:4.0f}% oper {a['trades']:3d} gana {a['win']*100:3.0f}% expo {a['expo']*100:3.0f}% | azar {rb*100:3.0f}% | "
              f"2022 {y.get(2022, 0)*100:+.0f}% 2023 {y.get(2023, 0)*100:+.0f}%")
