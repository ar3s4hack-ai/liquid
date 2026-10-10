"""¿Aguanta el spring/upthrust de poco volumen en 15m (lo único de Wyckoff con números positivos)?
Vecinos de los ajustes (rango de 30-60 velas, volumen < 0,8-1,2 veces la media, perforación ≤ 0,5-1 ATR), 30m,
compras y ventas por separado, y prueba contra el azar: la misma regla con el volumen de otra vela al azar del rango
(si el volumen bajo no aporta nada, sale lo mismo)."""
import sys

import numpy as np

from common import load, resample
from sim import Fine, show, summary
from wyckoff import springs


def line(label, trs):
    s = summary(trs)
    a = s["todo"]["real"]
    if a is None:
        print(f"  {label:44s} sin operaciones")
        return None
    R = np.array([x["real"]["R"] for x in trs])
    show(label, trs)
    return a["R"], len(R)


def main():
    f15 = load("fut_15m")
    F = Fine(load("fut_5m"))
    m30 = resample(f15, "30min")
    print("### Vecinos (15m, objetivo el otro lado del rango)")
    for N in (30, 40, 60):
        for vlow in (0.8, 1.0, 1.2):
            for pen in (0.5, 0.75, 1.0):
                line(f"15m N={N} vol<{vlow} perf≤{pen}ATR", springs(f15, F, N=N, vol="bajo", vlow=vlow, pen=pen))
    print("\n### Compras (spring) y ventas (upthrust) por separado, ajustes base")
    line("15m solo spring (compra)", springs(f15, F, vol="bajo", sides=(1,)))
    line("15m solo upthrust (venta)", springs(f15, F, vol="bajo", sides=(-1,)))
    print("\n### 30m")
    for vlow in (0.8, 1.0, 1.2):
        line(f"30m vol<{vlow}", springs(m30, F, vol="bajo", vlow=vlow))
    print("\n### Contra el azar: volumen de otra vela del rango (200 sorteos, 15m base)")
    base = springs(f15, F, vol="bajo")
    act = np.mean([x["real"]["R"] for x in base])
    rng = np.random.default_rng(5)
    v0 = f15.v.values.copy()
    sims = []
    for s in range(200):
        df = f15.copy()
        df["v"] = np.roll(v0, int(rng.integers(1000, len(v0) - 1000)))
        trs = springs(df, F, vol="bajo")
        sims.append(np.mean([x["real"]["R"] for x in trs]) if trs else np.nan)
        if s % 50 == 49:
            print(f"  {s + 1} sorteos…")
            sys.stdout.flush()
    sims = np.array(sims)
    print(f"  real {act:+.2f}R · azar mediana {np.nanmedian(sims):+.2f}R · igual o mejor que el real "
          f"{np.nanmean(sims >= act) * 100:.0f}% de las veces")


if __name__ == "__main__":
    main()
