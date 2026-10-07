"""CoinGlass (opcional): comprueba qué deja usar el plan de la clave COINGLASS_API_KEY.

La API de CoinGlass es de pago y cada plan abre partes distintas (según su documentación):
- Hobbyist (29 $): histórico por vela de 4h o más.        - Startup (79 $): desde 30m.
- Standard (299 $): cualquier vela y órdenes de liquidación con precio (7 días).
- Professional (699 $): su heatmap y su mapa de liquidaciones.
Aquí se prueba cada parte con una petición pequeña y se informa en /api/status y en «✓ Validez».
La clave se lee de la variable de entorno y nunca se devuelve ni se guarda.
"""
import os
import threading
import time

import requests

KEY = os.getenv("COINGLASS_API_KEY", "").strip()
BASE = os.getenv("COINGLASS_BASE", "https://open-api-v4.coinglass.com").rstrip("/")
EVERY_S = 6 * 3600     # comprobación cada 6 h
RETRY_S = 600          # sin conexión: otra vez a los 10 min
PLANS = ("Hobbyist", "Startup", "Standard", "Professional")

_PAIR = {"exchange": "Binance", "symbol": "BTCUSDT"}
# (id, qué es, ruta, parámetros, plan mínimo)
CHECKS = (
    ("key", "Clave", "/api/futures/supported-coins", {}, "Hobbyist"),
    ("hist4h", "Liquidaciones por vela 4h", "/api/futures/liquidation/history", dict(_PAIR, interval="4h", limit=2), "Hobbyist"),
    ("hist30m", "Liquidaciones por vela 30m", "/api/futures/liquidation/history", dict(_PAIR, interval="30m", limit=2), "Startup"),
    ("hist5m", "Liquidaciones por vela 5m", "/api/futures/liquidation/history", dict(_PAIR, interval="5m", limit=2), "Standard"),
    ("orders", "Órdenes de liquidación", "/api/futures/liquidation/order",
     {"exchange": "BINANCE", "symbol": "BTC", "min_liquidation_amount": 10000}, "Standard"),
    ("heatmap", "Heatmap", "/api/futures/liquidation/heatmap/model2", dict(_PAIR, range="24h"), "Professional"),
    ("map", "Mapa de liquidaciones", "/api/futures/liquidation/map", dict(_PAIR, range="1d"), "Professional"),
)

_http = requests.Session()
_lock = threading.Lock()
_state = {"checked": None, "results": {}}


def _shape(x, depth=0):
    """Forma de los datos sin sus valores (tipos y tamaños), para integrarlos luego sin adivinar el formato."""
    if isinstance(x, dict):
        return {k: _shape(v, depth + 1) for k, v in list(x.items())[:10]} if depth < 3 else "objeto"
    if isinstance(x, list):
        return [f"lista de {len(x)}", _shape(x[0], depth + 1) if x else None] if depth < 3 else f"lista de {len(x)}"
    return type(x).__name__


def _call(path, params):
    try:
        r = _http.get(BASE + path, params=params, timeout=15, headers={"CG-API-KEY": KEY, "accept": "application/json"})
    except requests.RequestException as e:
        return {"ok": False, "net": True, "msg": f"sin conexión ({type(e).__name__})"}
    try:
        body = r.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    code = str(body.get("code", ""))
    if r.status_code == 200 and code == "0":
        return {"ok": True, "msg": "ok", "shape": _shape(body.get("data"))}
    msg = body.get("msg") or body.get("message") or f"HTTP {r.status_code}"
    return {"ok": False, "http": r.status_code, "code": code or None, "msg": str(msg)[:120]}


def probe(pause=0.5):
    res = {}
    for cid, _, path, params, _ in CHECKS:
        res[cid] = _call(path, params)
        time.sleep(pause)   # el plan más barato admite 30 peticiones por minuto
    with _lock:
        _state.update(checked=int(time.time()), results=res)
    return res


def plan(res):
    """Plan más alto que confirman las pruebas, o None si la clave no da acceso a nada."""
    best = None
    for cid, _, _, _, tier in CHECKS:
        if res.get(cid, {}).get("ok") and (best is None or PLANS.index(tier) > PLANS.index(best)):
            best = tier
    return best


def summary(detail=False):
    """Lo que ven /api/status (detail=True, con la forma de los datos) y «✓ Validez». Nunca incluye la clave."""
    if not KEY:
        return None
    with _lock:
        res, checked = dict(_state["results"]), _state["checked"]
    out = {"checked": checked, "plan": plan(res) if res else None, "checks": []}
    for cid, name, _, _, tier in CHECKS:
        r = res.get(cid)
        if r is None:
            continue
        item = {"id": cid, "name": name, "tier": tier, "ok": r["ok"], "msg": r["msg"]}
        if detail and r.get("shape") is not None:
            item["shape"] = r["shape"]
        out["checks"].append(item)
    if res and not out["plan"]:
        net = [r for r in res.values() if r.get("net")]
        out["reason"] = "sin conexión con CoinGlass" if len(net) == len(res) else next(r["msg"] for r in res.values() if not r.get("net"))
    return out


def _loop():
    while True:
        try:
            res = probe()
            wait = RETRY_S if all(r.get("net") for r in res.values()) else EVERY_S
        except Exception as e:   # nunca tumbar el servidor por esto
            print("coinglass:", e, flush=True)
            wait = RETRY_S
        time.sleep(wait)


def start():
    if KEY:
        threading.Thread(target=_loop, daemon=True, name="coinglass").start()
