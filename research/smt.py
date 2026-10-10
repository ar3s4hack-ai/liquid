"""Divergencia SMT de ICT entre BTC y ETH (futuros de Binance, 15m y 1h, 2020-2026; ver sim.py).

Regla fijada antes de mirar: giros de 5 velas a cada lado en los dos (el mismo giro si están a ≤ 3 velas). SMT bajista:
uno de los dos supera su último máximo de giro y el otro no (los dos niveles seguían intactos). Variante «cierra
dentro»: el que lo supera cierra de vuelta por debajo. Se vende BTC a la apertura siguiente con stop por encima del
máximo de BTC (la mecha o su giro) y objetivo 2R; salida a las 48 velas. SMT alcista: lo mismo en los mínimos.
Caso 1: BTC barre y ETH no. Caso 2: ETH barre y BTC no."""
import json

import numpy as np
from numba import njit

from common import load, resample, secs
from sim import BUF, Fine, show, trade
from smc2 import last_confirmed, pivots

RES = {}


@njit(cache=True)
def smt_signals(hb, lb, cb, he, le, ce, phb, phe, plb, ple, close_back, maxlag):
    n = len(cb)
    side = np.zeros(n, np.int8)
    stop = np.full(n, np.nan)
    case = np.zeros(n, np.int8)
    ib0 = ie0 = jb0 = je0 = -1
    mxb = mxe = -np.inf
    mnb = mne = np.inf
    for k in range(1, n):
        ib, ie, jb, je = phb[k - 1], phe[k - 1], plb[k - 1], ple[k - 1]
        if ib != ib0:
            ib0 = ib
            mxb = -np.inf
            for j in range(ib + 1, k):
                mxb = max(mxb, hb[j])
        if ie != ie0:
            ie0 = ie
            mxe = -np.inf
            for j in range(ie + 1, k):
                mxe = max(mxe, he[j])
        if jb != jb0:
            jb0 = jb
            mnb = np.inf
            for j in range(jb + 1, k):
                mnb = min(mnb, lb[j])
        if je != je0:
            je0 = je
            mne = np.inf
            for j in range(je + 1, k):
                mne = min(mne, le[j])
        s = 0
        st = np.nan
        cs = 0
        if ib >= 0 and ie >= 0 and abs(ib - ie) <= maxlag and mxb <= hb[ib] and mxe <= he[ie]:
            tb, te = hb[k] > hb[ib], he[k] > he[ie]
            if tb and not te and (not close_back or cb[k] < hb[ib]):
                s, st, cs = -1, hb[k] * (1 + BUF), 1
            elif te and not tb and (not close_back or ce[k] < he[ie]):
                s, st, cs = -1, hb[ib] * (1 + BUF), 2
        if jb >= 0 and je >= 0 and abs(jb - je) <= maxlag and mnb >= lb[jb] and mne >= le[je]:
            tb, te = lb[k] < lb[jb], le[k] < le[je]
            s2, st2, cs2 = 0, np.nan, 0
            if tb and not te and (not close_back or cb[k] > lb[jb]):
                s2, st2, cs2 = 1, lb[k] * (1 - BUF), 1
            elif te and not tb and (not close_back or ce[k] > le[je]):
                s2, st2, cs2 = 1, lb[jb] * (1 - BUF), 2
            if s2 != 0:
                if s != 0:
                    s = 0           # las dos a la vez: se ignora
                else:
                    s, st, cs = s2, st2, cs2
        side[k], stop[k], case[k] = s, st, cs
        mxb, mxe = max(mxb, hb[k]), max(mxe, he[k])
        mnb, mne = min(mnb, lb[k]), min(mne, le[k])
    return side, stop, case


def smt(b, e, F, L=5, close_back=True, maxlag=3, R=2.0, max_bars=48):
    t = secs(b.index)
    step = t[1] - t[0]
    hb, lb, cb = b.h.values, b.l.values, b.c.values
    he, le, ce = e.h.values, e.l.values, e.c.values
    pb, qb = pivots(hb, lb, L)
    pe, qe = pivots(he, le, L)
    side, stop, case = smt_signals(hb, lb, cb, he, le, ce, last_confirmed(pb, L), last_confirmed(pe, L),
                                   last_confirmed(qb, L), last_confirmed(qe, L), close_back, maxlag)
    out = []
    for k in np.flatnonzero(side != 0):
        if (cb[k] - stop[k]) * side[k] <= 0:
            continue
        r = trade(F, int(t[k] + step), int(side[k]), stop[k], int(t[k] + step + max_bars * step), R=R,
                  meta={"caso": int(case[k])})
        if r:
            out.append(r)
    return out


def main():
    b5, e5 = load("fut_5m"), load("eth_fut_5m")
    common_idx = b5.index.intersection(e5.index)
    b5, e5 = b5.loc[common_idx], e5.loc[common_idx]
    F = Fine(load("fut_5m"))
    print("\n### Divergencia SMT BTC/ETH")
    for tfn in ("15m", "1h"):
        b, e = resample(b5, tfn.replace("m", "min")), resample(e5, tfn.replace("m", "min"))
        idx = b.index.intersection(e.index)
        b, e = b.loc[idx], e.loc[idx]
        for cb_ in (True, False):
            trs = smt(b, e, F, close_back=cb_)
            lab = f"SMT {tfn}{' cierra dentro' if cb_ else ''}"
            show(lab, trs, RES)
            for cs in (1, 2):
                show(f"   caso {cs} ({'BTC barre, ETH no' if cs == 1 else 'ETH barre, BTC no'})",
                     [x for x in trs if x["meta"]["caso"] == cs], RES)
    with open("smt.json", "w") as fh:
        json.dump(RES, fh, default=float, indent=1)


if __name__ == "__main__":
    main()
