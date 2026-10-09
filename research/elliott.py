"""Elliott mecánico: entrada en el inicio de la onda 3 (zigzag por porcentaje).
Onda 1 = L0→H1, onda 2 = H1→L2 con L2 > L0 y retroceso 38,2-78,6 %. Se compra cuando el cierre supera H1
(stop en L2, objetivo L2 + 1,618 × onda 1). Al revés para cortos. Salidas recorriendo velas de 5m."""
import numpy as np

from common import load, resample, secs
from ict import Fine, show, summary, trade


def zigzag(h, l, th):
    """Pivotes confirmados: lista de (índice del pivote, precio, tipo +1 máximo / -1 mínimo, índice de confirmación)."""
    piv = []
    d = 1
    ext, ext_i = h[0], 0
    for k in range(1, len(h)):
        if d > 0:
            if h[k] > ext:
                ext, ext_i = h[k], k
            elif l[k] <= ext * (1 - th):
                piv.append((ext_i, ext, 1, k))
                d, ext, ext_i = -1, l[k], k
        else:
            if l[k] < ext:
                ext, ext_i = l[k], k
            elif h[k] >= ext * (1 + th):
                piv.append((ext_i, ext, -1, k))
                d, ext, ext_i = 1, h[k], k
    return piv


def wave3(df, F, th, ext=1.618, max_mult=3.0):
    t = secs(df.index)
    h, l, c = df.h.values, df.l.values, df.c.values
    step = t[1] - t[0]
    piv = zigzag(h, l, th)
    out = []
    for a in range(len(piv) - 2):
        p0, p1, p2 = piv[a], piv[a + 1], piv[a + 2]
        side = 1 if p0[2] == -1 else -1      # empieza en mínimo → impulso alcista
        x0, x1, x2 = p0[1], p1[1], p2[1]
        w1 = (x1 - x0) * side
        if w1 <= 0 or (x2 - x0) * side <= 0:
            continue
        ret = (x1 - x2) * side / w1
        if not (0.382 <= ret <= 0.786):
            continue
        k2 = p2[3]                                   # L2 confirmado aquí
        if (c[k2] - x1) * side > 0:
            continue                                 # ya había superado H1: no se persigue
        limit = int(k2 + max_mult * (p1[0] - p0[0]) + 1)
        for k in range(k2, min(len(c), limit)):
            if (l[k] <= x2 and side > 0) or (h[k] >= x2 and side < 0):
                break                                # invalidada
            if (c[k] - x1) * side > 0:
                tp = x2 + side * ext * w1
                if (tp - c[k]) * side <= 0:
                    break
                R = abs(tp - c[k]) / abs(c[k] - x2)
                end = int(t[k] + step + max_mult * (p1[0] - p0[0]) * step)
                r = trade(F, int(t[k] + step), side, c[k], x2, R, end, meta={"R": R})
                if r:
                    out.append(r)
                break
    return out


def main():
    f5 = load("fut_5m")
    f15 = load("fut_15m")
    F = Fine(f5)
    for name, df, ths in (("15m", f15, (0.01, 0.02)), ("1h", resample(f15, "1h"), (0.02, 0.04)),
                          ("4h", resample(f15, "4h"), (0.04, 0.08))):
        for th in ths:
            trs = wave3(df, F, th)
            lab = f"Onda 3 Elliott {name} zigzag {th * 100:.0f}%"
            show(lab, summary(trs, lab))
            Rs = [x["meta"]["R"] for x in trs]
            print(f"   (objetivo medio {np.mean(Rs):.2f}R)")


if __name__ == "__main__":
    main()
