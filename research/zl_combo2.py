"""Variantes de «3 de 3 O liquidez» y su comportamiento por años y caídas."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, SPLIT, fmt_pct, load, metrics, random_benchmark, resample, run, years_table
from ensemble import build
from zl_check import zl

for mk, raw, ltf, cost, fund, start in (("futuros", load("fut_15m"), load("fut_5m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC")),
                                        ("spot", load("spot_1h"), None, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    sc = np.nan_to_num(g["score"].values)
    ok = ~np.isnan(g["score"].values)
    v1 = np.nan_to_num(g["ema100"].values)
    print(f"== {mk}")
    for zname, z in (("liquidez (velas pequeñas)", (zl(h4, ltf if ltf is not None else raw) > 0)),
                     ("liquidez (mitad de mecha)", (zl(h4, poc="mid") > 0))):
        rules = {
            "actual 3/3": ok & (sc >= 3),
            f"3/3 O {zname}": (ok & (sc >= 3)) | z,
            f"3/3 O ({zname} y ≥2/3)": ok & ((sc >= 3) | (z & (sc >= 2))),
            f"3/3 O ({zname} y EMA100)": ok & ((sc >= 3) | (z & (v1 == 1))),
        }
        for name, cond in rules.items():
            if zname.endswith("(mitad de mecha)") and name == "actual 3/3":
                continue
            r = run(h4, np.where(cond, 1.0, 0.0), "4h", cost, fund)
            i, o, a = metrics(r, start, SPLIT), metrics(r, SPLIT), metrics(r, start)
            rb = random_benchmark(r, start, None, n=300)["pct"]
            y = {k: v for k, v in years_table(r).items() if pd.Timestamp(f"{k}-12-31", tz="UTC") >= start}
            print(f"  {name:44s} IS sh {i['sharpe']:.2f} dd {i['dd']*100:4.0f}% | OOS sh {o['sharpe']:.2f} dd {o['dd']*100:4.0f}% | "
                  f"total {fmt_pct(a['ret']):>7s} dd {a['dd']*100:4.0f}% oper {a['trades']:3d} gana {a['win']*100:3.0f}% expo {a['expo']*100:3.0f}% azar {rb*100:3.0f}%")
            print("     años: " + " ".join(f"{k}:{v*100:+.0f}%" for k, v in y.items()))
