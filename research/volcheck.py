"""Robustez del filtro de volatilidad realizada (el único casi aprobado): otros umbrales y ventanas."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, load, resample
from ensemble import build
from extra import daily, pct_rank_flag, test

for mk, raw, cost, fund, start in (("spot", load("spot_1h"), COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC")),
                                   ("futuros", load("fut_15m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    lr = np.log(d.c).diff()
    print(f"== {mk}")
    for win in (14, 30, 60):
        for q in (0.8, 0.9, 0.95):
            rv = daily(lr.rolling(win).std())
            fl = pct_rank_flag(rv, q=q)
            fl = fl.astype("float").where(fl.notna())
            o = test(h4, g, fl, cost, fund, start, f"rv{win} q{q}", n_rot=200)
            print(f"  ventana {win:2d}d, fuera si > percentil {int(q*100)}: IS sh {o['base_IS'][2]:.2f}→{o['F_IS'][2]:.2f} dd {o['base_IS'][1]*100:.0f}→{o['F_IS'][1]*100:.0f} | "
                  f"OOS sh {o['base_OOS'][2]:.2f}→{o['F_OOS'][2]:.2f} dd {o['base_OOS'][1]*100:.0f}→{o['F_OOS'][1]*100:.0f} | azar {o['p_F']*100:.0f}%")
