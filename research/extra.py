"""¿Datos extra hacen más fiable la señal de tendencia (3 de 3)?

Cada dato da un «sí / no» diario conocido al cierre del día (con el retraso de publicación de cada fuente) y se prueba:
  F (filtro): comprado solo con 3/3 Y el dato a favor;
  M (mayoría): comprado con 3 de 4 votos (los 3 de tendencia + el dato).
Se compara con la señal actual en el mismo periodo (dentro de muestra hasta 2023, fuera desde 2024) y contra el azar:
el mismo dato desplazado en el tiempo una cantidad aleatoria (mismo % de días a favor y misma persistencia, pero sin
relación con el precio). Umbrales fijados antes de mirar (los habituales de cada indicador), sin optimizar.

Datos: python3 research/get_extra.py (o el workflow de la rama de investigación) los deja en research/data/extra/."""
import gzip
import io
import json
import os
import re
import sys

import numpy as np
import pandas as pd

from common import COST, COST_SPOT, D, SPLIT, load, metrics, resample, run
from ensemble import build

X = os.path.join(D, "extra")
DAY = pd.Timedelta(days=1)


def rd(name):
    p = os.path.join(X, name + ".gz")
    if not os.path.exists(p):
        return None
    with gzip.open(p, "rt") as f:
        return f.read()


def daily(s, lag_days=0):
    """Serie diaria (índice = día UTC normalizado) rellenando hacia delante; lag_days = días de retraso de publicación."""
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = s.index.normalize()
    s = s.resample("1D").last().ffill()
    if lag_days:
        s.index = s.index + pd.Timedelta(days=lag_days)
    return s


# ───────── cargadores (valor conocido al cierre del día del índice) ─────────
def fng():
    t = rd("fng.json")
    if not t:
        return None
    j = json.loads(t)["data"]
    return daily(pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC"): float(r["value"]) for r in j}))


def cm(metric, lag=1):
    t = rd(f"cm_{metric}.json")
    if not t:
        return None
    rows = json.loads(t)
    if not rows:
        return None
    return daily(pd.Series({pd.Timestamp(r["time"]): float(r[metric]) for r in rows if r.get(metric) not in (None, "")}), lag)


def stables():
    t = rd("stablecoins_all.json")
    if not t:
        return None
    j = json.loads(t)
    vals = {}
    for r in j:
        tot = r.get("totalCirculatingUSD") or r.get("totalCirculating") or {}
        v = sum(float(x) for x in tot.values()) if isinstance(tot, dict) else float(tot)
        vals[pd.Timestamp(int(r["date"]), unit="s", tz="UTC")] = v
    return daily(pd.Series(vals), 1)


def fred(sid, lag):
    t = rd(f"fred_{sid}.csv")
    if not t:
        return None
    df = pd.read_csv(io.StringIO(t))
    df.columns = ["date", "v"]
    df = df[pd.to_numeric(df.v, errors="coerce").notna()]
    return daily(pd.Series(df.v.astype(float).values, index=pd.to_datetime(df.date).dt.tz_localize("UTC")), lag)


def coinbase_premium(spot1h):
    """Prima de Coinbase (BTC-USD frente a BTC-USDT de Binance spot): media de las horas del día; antes de 2019-09, el cierre diario."""
    out = {}
    t1 = rd("coinbase_1h.json")
    bn_h = spot1h.c.copy()
    bn_h.index = bn_h.index + pd.Timedelta(hours=1)      # cierre de la vela de 1h
    if t1:
        cb = pd.Series({pd.Timestamp(r[0], unit="s", tz="UTC") + pd.Timedelta(hours=1): float(r[4]) for r in json.loads(t1)})
        prem = (cb / bn_h.reindex(cb.index) - 1).dropna()
        prem = prem[(prem.abs() < 0.05)]
        out = prem.groupby((prem.index - pd.Timedelta(seconds=1)).normalize()).mean()
    t2 = rd("coinbase_1d.json")
    if t2:
        cbd = pd.Series({pd.Timestamp(r[0], unit="s", tz="UTC"): float(r[4]) for r in json.loads(t2)})
        bnd = spot1h.c.resample("1D").last()
        pd_ = (cbd / bnd.reindex(cbd.index) - 1).dropna()
        pd_ = pd_[pd_.abs() < 0.05]
        if len(out):
            pd_ = pd_[pd_.index < out.index[0]]
            out = pd.concat([pd_, out])
        else:
            out = pd_
    return daily(out) if len(out) else None


def wiki():
    t = rd("wiki_bitcoin.json")
    if not t:
        return None
    items = json.loads(t).get("items", [])
    return daily(pd.Series({pd.Timestamp(i["timestamp"][:8], tz="UTC"): float(i["views"]) for i in items}), 1)


def binance_metrics():
    t = rd("binance_metrics.csv")
    if not t:
        return None
    df = pd.read_csv(io.StringIO(t))
    df["t"] = pd.to_datetime(df.create_time, utc=True)
    df = df.set_index("t").sort_index()
    d_ = df.resample("1D").last()
    return d_


def dvol():
    t = rd("deribit_dvol.json")
    if not t:
        return None
    rows = json.loads(t)
    return daily(pd.Series({pd.Timestamp(r[0], unit="ms", tz="UTC"): float(r[4]) for r in rows}))


def etf_flows():
    t = rd("farside_btc_etf.html")
    if not t:
        return None
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, flags=re.S)
    vals = {}
    for r in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
        if len(cells) < 3:
            continue
        try:
            day = pd.Timestamp(pd.to_datetime(cells[0], format="%d %b %Y")).tz_localize("UTC")
        except Exception:
            continue
        tot = cells[-1].replace(",", "").replace("(", "-").replace(")", "")
        try:
            vals[day] = float(tot)
        except ValueError:
            continue
    return daily(pd.Series(vals), 1) if vals else None


def pct_rank_flag(s, q=0.9, win=365, minp=180):
    """True si el valor NO está en el 10 % más alto de su último año (sin mirar al futuro)."""
    thr = s.rolling(win, min_periods=minp).quantile(q)
    return (s <= thr).where(thr.notna())


# ───────── candidatos: True = a favor de estar comprado ─────────
def candidates(spot1h, fut15):
    c = {}
    f = fng()
    if f is not None:
        c["Fear & Greed < 80 (sin euforia)"] = f < 80
    mv = cm("CapMVRVCur")
    if mv is not None:
        c["MVRV < 3,5 (sin euforia on-chain)"] = mv < 3.5
    st = stables()
    if st is not None:
        c["Stablecoins crecen (30 días)"] = st.pct_change(30) > 0
    dx = fred("DTWEXBGS", 7)
    if dx is not None:
        c["Dólar bajo su EMA50"] = dx < dx.ewm(span=50, adjust=False).mean()
    ry = fred("DFII10", 2)
    if ry is not None:
        c["Tipos reales bajan (30 días)"] = ry.diff(30) < 0
    w, tg, rr = fred("WALCL", 2), fred("WTREGEN", 2), fred("RRPONTSYD", 1)
    if w is not None and tg is not None and rr is not None:
        idx = w.index.intersection(tg.index).intersection(rr.index)
        nl = (w.reindex(idx) - tg.reindex(idx) - rr.reindex(idx) * 1000)    # WALCL y TGA en millones, RRP en miles de millones
        c["Liquidez de la Fed sube (4 semanas)"] = nl.diff(28) > 0
    sp = fred("SP500", 0)
    if sp is not None:
        c["S&P 500 sobre su media de 200"] = sp > sp.rolling(200).mean()
    cp = coinbase_premium(spot1h)
    if cp is not None:
        c["Prima de Coinbase > 0 (7 días)"] = cp.rolling(7).mean() > 0
    wk = wiki()
    if wk is not None:
        c["Atención (Wikipedia) al alza"] = wk.rolling(7).mean() > wk.rolling(30).mean()
    bm = binance_metrics()
    if bm is not None and len(bm):
        oi = daily(bm.sum_open_interest_value.astype(float))
        c["OI sin subidón (30 días)"] = pct_rank_flag(oi.pct_change(30))
        ls = daily(bm.count_long_short_ratio.astype(float))
        c["Largos/cortos sin exceso"] = pct_rank_flag(ls)
    dv = dvol()
    if dv is not None:
        c["Volatilidad implícita sin pico"] = pct_rank_flag(dv)
    fi, fo = cm("FlowInExUSD"), cm("FlowOutExUSD")
    if fi is not None and fo is not None:
        c["Salen BTC de los exchanges (7 días)"] = (fo - fi).rolling(7).sum() > 0
    se = cm("SplyExNtv")
    if se is not None:
        c["Saldo en exchanges baja (30 días)"] = se.diff(30) < 0
    hr = cm("HashRate")
    if hr is not None:
        c["Mineros sin capitular (hash 30>60)"] = hr.rolling(30).mean() > hr.rolling(60).mean()
    et = etf_flows()
    if et is not None:
        c["ETF entradas netas (7 días)"] = et.rolling(7).sum() > 0
    # de nuestros propios datos
    fr = pd.read_csv(os.path.join(D, "btcusdt_funding.csv.gz"))
    fr = pd.Series(fr.rate.values, index=pd.to_datetime(fr.ts, unit="s", utc=True)).resample("1D").sum()
    c["Funding sin exceso (7 días)"] = pct_rank_flag(daily(fr).rolling(7).mean())
    dd = resample(spot1h, "1D")
    rv = np.log(dd.c).diff().rolling(30).std()
    c["Volatilidad realizada sin pico"] = pct_rank_flag(daily(rv))
    tk = resample(fut15, "1D")
    c["Compras a mercado > 50 % (7 días)"] = daily(tk.tbv.rolling(7).sum() / tk.v.rolling(7).sum()) > 0.5
    return {k: v.astype("float").where(v.notna()) for k, v in c.items()}


def on_grid(flag, h4):
    """Dato diario (día D conocido al cerrar D) → vela de 4h: desde la que cierra con el día (D + 20h)."""
    s = flag.copy()
    s.index = s.index + pd.Timedelta(hours=20)
    return s.reindex(h4.index, method="ffill").values


def sharpe_of(res, a, b):
    return metrics(res, a, b)["sharpe"]


def test(h4, g, flag, cost, fund, start, label, n_rot=300, seed=7):
    base_score = g["score"].values
    ok = ~np.isnan(base_score)
    fl = on_grid(flag, h4)
    have = ~np.isnan(fl)
    first = h4.index[np.argmax(have)] if have.any() else None
    if first is None:
        return None
    a0 = max(start, first + pd.Timedelta(days=30))
    base = np.where(ok & (base_score >= 3), 1.0, 0.0)
    filt = np.where(ok & (base_score >= 3) & (fl == 1), 1.0, 0.0)
    maj = np.where(ok & ((np.nan_to_num(base_score) + np.nan_to_num(fl)) >= 3), 1.0, 0.0)
    out = {"label": label, "from": str(a0.date())}
    for name, pos in (("base", base), ("F", filt), ("M", maj)):
        r = run(h4, pos, "4h", cost, fund)
        for per, (x, y) in (("IS", (a0, SPLIT)), ("OOS", (SPLIT, None)), ("ALL", (a0, None))):
            if per == "IS" and a0 >= SPLIT - pd.Timedelta(days=180):
                continue
            m = metrics(r, x, y)
            out[f"{name}_{per}"] = (m["ret"], m["dd"], m["sharpe"], m["expo"])
    # azar: el mismo dato desplazado (rotado) en el tiempo
    rng = np.random.default_rng(seed)
    act = out["F_ALL"][2]
    days = flag.dropna()
    vals = days.values
    better = 0
    sims = []
    for _ in range(n_rot):
        k = int(rng.integers(60, len(vals) - 60))
        rot = pd.Series(np.roll(vals, k), index=days.index)
        fr_ = on_grid(rot, h4)
        p = np.where(ok & (base_score >= 3) & (fr_ == 1), 1.0, 0.0)
        s = sharpe_of(run(h4, p, "4h", cost, fund), a0, None)
        sims.append(s)
        better += s >= act
    out["p_F"] = better / n_rot
    out["sim_med"] = float(np.median(sims))
    out["on"] = float(np.nanmean(fl[(h4.index >= a0)]))
    return out


def fmt(t):
    if t is None:
        return "—"
    r, d_, s, e = t
    return f"{r * 100:+6.0f}% dd{d_ * 100:4.0f}% sh{s:5.2f}"


def main():
    spot1h = load("spot_1h")
    fut15 = load("fut_15m")
    cands = candidates(spot1h, fut15)
    print("candidatos con datos:", len(cands))
    for k, v in cands.items():
        vv = v.dropna()
        print(f"  {k:38s} desde {vv.index[0].date() if len(vv) else '—'}  a favor {vv.mean() * 100 if len(vv) else 0:4.0f} % del tiempo")
    res = []
    for mk, raw, cost, fund, start in (("spot", spot1h, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC")),
                                       ("futuros", fut15, COST, True, pd.Timestamp("2020-08-01", tz="UTC"))):
        h4, d_ = resample(raw, "4h"), resample(raw, "1D")
        g = build(h4, d_)
        print(f"\n=== {mk} ===  (señal actual: base; F = 3/3 y el dato; M = 3 de 4)   azar = % de desplazamientos al azar que igualan o superan a F")
        for k, flag in cands.items():
            o = test(h4, g, flag, cost, fund, start, k)
            if not o:
                continue
            o["market"] = mk
            res.append(o)
            line = f"{k[:38]:38s} desde {o['from']} | "
            for per in ("IS", "OOS"):
                if f"base_{per}" in o:
                    line += f"{per}: base {fmt(o['base_' + per])} · F {fmt(o['F_' + per])} · M {fmt(o['M_' + per])} | "
            line += f"azar {o['p_F'] * 100:3.0f}%  (a favor {o['on'] * 100:3.0f}%)"
            print(line)
            sys.stdout.flush()
    with open("extra.json", "w") as fh:
        json.dump(res, fh, default=float)


if __name__ == "__main__":
    main()
