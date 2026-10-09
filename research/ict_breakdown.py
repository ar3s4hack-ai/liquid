import json, numpy as np
from common import SPLIT
d = json.load(open("ict_trades.json"))
S = SPLIT.timestamp()
def grp(x):
    h = x["meta"].get("hour", -1)
    return "Londres 07-11" if 7 <= h < 12 else "NY 12-16" if 12 <= h < 17 else "tarde 17-21" if 17 <= h < 22 else "otra"
def line(sel):
    if not sel: return "     —                      "
    g = np.mean([x["real"]["gR"] for x in sel]); n = np.mean([x["real"]["R"] for x in sel])
    w = np.mean([x["real"]["ret"] > 0 for x in sel])
    return f"n={len(sel):4d} gana {w*100:3.0f}% bruto {g:+.2f}R neto {n:+.2f}R"
for key in ["Asia 15m N=0 TP=2.0R", "Asia 15m N=4 TP=2.0R", "Asia 15m N=4 TP=opuesta≥2R", "Asia 5m N=4 TP=2.0R",
            "PDH/PDL 15m N=4 TP=2.0R", "PDH/PDL 15m N=4 TP=opuesta≥2R"]:
    trs = d[key]
    print(f"\n== {key}")
    combos = {}
    for x in trs:
        wk = "laborable" if x["meta"]["wd"] < 5 else "finde"
        al = "a favor tend." if x["meta"].get("trend", 0) == x["side"] else "en contra"
        for k in [grp(x), wk, al, grp(x) + " · " + wk, grp(x) + " · " + wk + " · " + al]:
            combos.setdefault(k, []).append(x)
    for k in sorted(combos):
        v = combos[k]
        a = [x for x in v if x["t"] < S]; b = [x for x in v if x["t"] >= S]
        print(f"  {k:45s} IS {line(a)} | OOS {line(b)}")
