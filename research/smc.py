"""Estructura de mercado estilo LuxAlgo SMC (swings, BOS/CHoCH), FVG y order blocks. Sin mirar al futuro."""
import numpy as np
from numba import njit


@njit(cache=True)
def lux_structure(h, l, c, L):
    """Devuelve:
    trend[k]  = dirección de la última ruptura confirmada al cierre de k (1 alcista, -1 bajista, 0 nada)
    ev[k]     = 0 sin ruptura; ±1 BOS; ±2 CHoCH (signo = dirección)
    top[k], btm[k] = último máximo/mínimo de swing confirmado (niveles), NaN si no hay
    top_i[k], btm_i[k] = índice de la vela de ese máximo/mínimo
    Igual que LuxAlgo: un máximo de swing se confirma cuando la vela de hace L tiene un máximo
    mayor que todas las L posteriores (y alterna con los mínimos)."""
    n = len(c)
    trend = np.zeros(n, np.int8)
    ev = np.zeros(n, np.int8)
    top = np.full(n, np.nan)
    btm = np.full(n, np.nan)
    top_i = np.full(n, -1, np.int64)
    btm_i = np.full(n, -1, np.int64)
    os = 0
    ty = np.nan
    by = np.nan
    ti = -1
    bi = -1
    tcross = True
    bcross = True
    tr = 0
    for k in range(n):
        if k >= L:
            upper = h[k - L + 1]
            lower = l[k - L + 1]
            for j in range(k - L + 2, k + 1):
                if h[j] > upper:
                    upper = h[j]
                if l[j] < lower:
                    lower = l[j]
            prev = os
            if h[k - L] > upper:
                os = 0
            elif l[k - L] < lower:
                os = 1
            if os == 0 and prev != 0:
                ty = h[k - L]
                ti = k - L
                tcross = False
            if os == 1 and prev != 1:
                by = l[k - L]
                bi = k - L
                bcross = False
        e = 0
        if not tcross and c[k] > ty:
            tcross = True
            e = 2 if tr == -1 else 1
            tr = 1
        if not bcross and c[k] < by:
            bcross = True
            e = -2 if tr == 1 else -1
            tr = -1
        trend[k] = tr
        ev[k] = e
        top[k] = ty
        btm[k] = by
        top_i[k] = ti
        btm_i[k] = bi
    return trend, ev, top, btm, top_i, btm_i


@njit(cache=True)
def fvgs(h, l, c, o, min_size):
    """FVG alcista en k: l[k] > h[k-2] (hueco entre la vela k-2 y la k) con vela k-1 alcista.
    Devuelve arrays por vela: tipo (1/-1/0), borde superior e inferior del hueco."""
    n = len(c)
    typ = np.zeros(n, np.int8)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    for k in range(2, n):
        if l[k] > h[k - 2] and c[k - 1] > o[k - 1] and (l[k] - h[k - 2]) >= min_size[k]:
            typ[k] = 1
            top[k] = l[k]
            bot[k] = h[k - 2]
        elif h[k] < l[k - 2] and c[k - 1] < o[k - 1] and (l[k - 2] - h[k]) >= min_size[k]:
            typ[k] = -1
            top[k] = l[k - 2]
            bot[k] = h[k]
    return typ, top, bot
