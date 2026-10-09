"""Reglas de posición: EMA200, cruce 50/200, EMA100 diaria, EWO (Elliott), bandas de ruptura del EWO,
Bollinger (reversión y ruptura) y estructura SMC (BOS/CHoCH). Largo/fuera, largo/corto y solo cortos.
Muestra dentro de muestra (2020-23) y fuera de muestra (2024-26), comparación con mantener y con azar."""
import json
import sys

import numpy as np
import pandas as pd

from common import (COST, COST_SPOT, SPLIT, ema_fast, fmt_pct, hold_metrics, load, metrics, random_benchmark,
                    resample, run, sma, years_table)
from smc import lux_structure


def state(enter_long, exit_long, enter_short, exit_short):
    """Máquina de estados: entra/sale según condiciones (arrays booleanos)."""
    n = len(enter_long)
    pos = np.zeros(n)
    p = 0
    for i in range(n):
        if p == 1 and exit_long[i]:
            p = 0
        elif p == -1 and exit_short[i]:
            p = 0
        if p == 0:
            if enter_long[i]:
                p = 1
            elif enter_short[i]:
                p = -1
        pos[i] = p
    return pos


def rules(df, tf):
    c, h, l = df.c.values, df.h.values, df.l.values
    hl2 = (h + l) / 2
    e200, e50, e100 = ema_fast(c, 200), ema_fast(c, 50), ema_fast(c, 100)
    out = {}
    out["Precio > EMA200"] = np.where(c > e200, 1.0, -1.0)
    out["Cruce EMA50/200"] = np.where(e50 > e200, 1.0, -1.0)
    if tf == "1d":
        out["Precio > EMA100 (diaria)"] = np.where(c > e100, 1.0, -1.0)
    ewo = np.nan_to_num(sma(hl2, 5) - sma(hl2, 35))
    out["EWO 5/35 > 0"] = np.where(ewo > 0, 1.0, np.where(ewo < 0, -1.0, 0.0))
    up = ema_fast(np.maximum(ewo, 0), 35)
    lo = ema_fast(np.minimum(ewo, 0), 35)
    for s in (1.0, 0.8):
        out[f"EWO bandas ruptura ×{s}"] = state(ewo > up * s, ewo < 0, ewo < lo * s, ewo > 0)
    m = sma(c, 20)
    sd = pd.Series(c).rolling(20).std(ddof=0).values
    ub, lb = m + 2 * sd, m - 2 * sd
    out["Bollinger reversión"] = state(c < lb, c > m, c > ub, c < m)
    out["Bollinger ruptura"] = state(c > ub, c < m, c < lb, c > m)
    out["Bollinger reversión + EMA200"] = state((c < lb) & (c > e200), (c > m) | (c < e200 * 0.97),
                                                (c > ub) & (c < e200), (c < m) | (c > e200 * 1.03))
    tr50 = lux_structure(h, l, c, 50)[0].astype(float)
    out["SMC estructura (swing 50)"] = tr50
    tr20 = lux_structure(h, l, c, 20)[0].astype(float)
    out["SMC estructura (swing 20)"] = tr20
    for k in out:
        out[k][:210] = 0
    return out


def evaluate(df, tf, cost, fund, label, rand_n=300):
    rows = []
    start = df.index[210]
    hold = {p: hold_metrics(df, tf, a, b, cost, fund) for p, (a, b) in
            {"IS": (start, SPLIT), "OOS": (SPLIT, None), "ALL": (start, None)}.items()}
    rows.append({"tf": label, "rule": "Mantener (comprar y aguantar)", "var": "hold",
                 **{f"{p}_{k}": hold[p][k] for p in hold for k in ("ret", "dd", "sharpe", "trades", "win", "expo")}})
    for name, pos in rules(df, tf).items():
        variants = {"LF": np.where(pos > 0, 1.0, 0.0)}
        if not fund is None and fund:
            variants["LS"] = pos
            variants["SO"] = np.where(pos < 0, -1.0, 0.0)
        for var, p in variants.items():
            res = run(df, p, tf, cost, fund)
            row = {"tf": label, "rule": name, "var": var}
            for per, (a, b) in {"IS": (start, SPLIT), "OOS": (SPLIT, None), "ALL": (start, None)}.items():
                mt = metrics(res, a, b)
                for k in ("ret", "dd", "sharpe", "trades", "win", "expo"):
                    row[f"{per}_{k}"] = mt[k]
            if var != "SO" and rand_n:
                rb = random_benchmark(res, start, None, n=rand_n)
                ro = random_benchmark(res, SPLIT, None, n=rand_n, seed=2)
                row["rand_all_pct"], row["rand_oos_pct"] = rb["pct"], ro["pct"]
                row["rand_all_med"], row["rand_oos_med"] = rb["median"], ro["median"]
            row["years"] = years_table(res)
            rows.append(row)
    return rows


def main():
    f15 = load("fut_15m")
    sets = [("15m", f15, "15m"), ("1h", resample(f15, "1h"), "1h"), ("4h", resample(f15, "4h"), "4h"),
            ("1d", resample(f15, "1D"), "1d")]
    allrows = []
    for label, df, tf in sets:
        rows = evaluate(df, tf, COST, True, label, rand_n=200 if tf in ("15m", "1h") else 400)
        allrows += rows
        print_rows(rows)
    s1d = load("spot_1d")
    rows = evaluate(s1d, "1d", COST_SPOT, False, "1d spot 2017+", rand_n=400)
    allrows += rows
    print_rows(rows)
    with open("trend_rules.json", "w") as fh:
        json.dump(allrows, fh, default=float)


def print_rows(rows):
    print(f"\n=== {rows[0]['tf']} ===")
    print(f"{'regla':34s} var | {'IS ret':>8s} {'IS dd':>6s} {'IS sh':>5s} | {'OOS ret':>8s} {'OOS dd':>6s} {'OOS sh':>6s} "
          f"{'tr':>4s} {'win':>4s} | {'ALL ret':>9s} {'ALL dd':>6s} {'sh':>5s} | azar% all/oos")
    for r in rows:
        rp = f"{r.get('rand_all_pct', float('nan')) * 100:4.0f}/{r.get('rand_oos_pct', float('nan')) * 100:3.0f}" \
            if "rand_all_pct" in r else ""
        print(f"{r['rule'][:34]:34s} {r['var']:3s} | {fmt_pct(r['IS_ret']):>8s} {fmt_pct(r['IS_dd']):>6s} "
              f"{r['IS_sharpe']:5.2f} | {fmt_pct(r['OOS_ret']):>8s} {fmt_pct(r['OOS_dd']):>6s} {r['OOS_sharpe']:6.2f} "
              f"{r['OOS_trades']:4.0f} {r['OOS_win'] * 100 if r['OOS_win'] == r['OOS_win'] else 0:4.0f} | "
              f"{fmt_pct(r['ALL_ret']):>9s} {fmt_pct(r['ALL_dd']):>6s} {r['ALL_sharpe']:5.2f} | {rp}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
