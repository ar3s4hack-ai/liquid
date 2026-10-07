"""Coinalyze (opcional, clave gratis en coinalyze.net): Open Interest con histórico y liquidaciones por vela
de los exchanges que no tenemos.

- API gratis: 40 consultas por minuto por clave; cada mercado pedido cuenta como una (hasta 20 por petición).
  Aquí se gastan como mucho 34 por minuto, en segundo plano, nunca al abrir la web.
- Guarda unos 1.500-2.000 puntos por temporalidad intradía (en 5 min, unos 5-7 días): de sobra para el gráfico.
- Para el modelo: histórico de OI de Hyperliquid, Bitget, Deribit y BitMEX (en vez de esperar a grabarlo minuto
  a minuto) y de los mercados de BTC con más OI que no tenemos (Gate, HTX, Kraken…).
- Para el panel: liquidaciones por vela de los mercados que no escuchamos, y de los nuestros en las velas en las que
  el servidor no estuvo escuchando. Sin precio: no entran en la validación.
- Coinalyze pide citarles con enlace donde se enseñen sus datos (la web lo hace).
"""
import os
import re
import threading
import time
from collections import defaultdict

import requests

KEY = os.getenv("COINALYZE_API_KEY", "").strip()
BASE = os.getenv("COINALYZE_BASE", "https://api.coinalyze.net/v1").rstrip("/")
BUDGET = int(os.getenv("COINALYZE_BUDGET", "34"))        # consultas por minuto (el límite es 40)
MAX_EXTRA = int(os.getenv("COINALYZE_EXTRA", "12"))       # mercados extra para el modelo
IV = {"5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "daily"}
STEP = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
SPAN = {"5m": 900, "15m": 800, "1h": 600, "4h": 240, "1d": 120}        # velas que enseña la web
REFRESH = {"5m": 180, "15m": 600, "1h": 1800, "4h": 3600, "1d": 10800}
DISCOVER_EVERY = 12 * 3600

# mercados que ya tenemos con histórico propio (no se piden para el modelo)
OWN_OI = {("binance", "BTCUSDT"), ("binance", "BTCUSDC"), ("binance", "BTCUSDPERP"), ("bybit", "BTCUSDT"),
          ("okx", "BTCUSDTSWAP"), ("okx", "BTCUSDSWAP")}
# fuentes que solo grabamos minuto a minuto: Coinalyze trae su histórico
SNAP_OI = {("hyperliquid", "BTC"): "hyperliquid", ("bitget", "BTCUSDT"): "bitget",
           ("deribit", "BTCPERPETUAL"): "deribit", ("bitmex", "XBTUSD"): "bitmex"}
# mercados cuyas liquidaciones escuchamos nosotros (con precio)
OWN_LIQ = {("binance", "BTCUSDT"), ("bybit", "BTCUSDT"), ("okx", "BTCUSDTSWAP"), ("deribit", "BTCPERPETUAL"),
           ("bitmex", "XBTUSD"), ("bitget", "BTCUSDT"), ("gate", "BTCUSDT"), ("htx", "BTCUSDT")}
EX_ALIAS = {"gateio": "gate", "gate": "gate", "huobi": "htx", "htx": "htx", "okex": "okx", "bybit": "bybit",
            "binance": "binance", "okx": "okx", "deribit": "deribit", "bitmex": "bitmex", "bitget": "bitget",
            "hyperliquid": "hyperliquid"}

_http = requests.Session()
_lock = threading.Lock()
STATE = {"markets": [], "oi": [], "liq": [], "names": {}, "discovered": None, "data": {},
         "errors": 0, "last_error": None, "calls": [], "check": None}
_started = False


def norm_ex(name):
    n = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return EX_ALIAS.get(n, n)


def norm_sym(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


# ───────────── Límite de consultas ─────────────
def _spend(n):
    """Espera hasta poder gastar n consultas sin pasar de BUDGET por minuto."""
    while True:
        now = time.time()
        with _lock:
            STATE["calls"] = [t for t in STATE["calls"] if now - t < 60]
            if len(STATE["calls"]) + n <= BUDGET:
                STATE["calls"] += [now] * n
                return
            wait = 60 - (now - STATE["calls"][0]) + 0.1
        time.sleep(max(0.2, wait))


def _get(path, params=None, cost=1):
    _spend(cost)
    for attempt in range(2):
        r = _http.get(f"{BASE}{path}", params=params or {}, headers={"api_key": KEY}, timeout=20)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(60.0, float(r.headers.get("Retry-After") or 10)))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("429")


# ───────────── Mercados ─────────────
def pick_markets(exchanges, markets, oi_now):
    """Elige qué pedir: el OI de los que solo grabamos y de los MAX_EXTRA con más OI que no tenemos;
    las liquidaciones de los nuestros (para rellenar huecos) y de los que no escuchamos (hasta 20 en total)."""
    names = {e.get("code"): norm_ex(e.get("name")) for e in exchanges if isinstance(e, dict)}
    shown = {e.get("code"): str(e.get("name") or "") for e in exchanges if isinstance(e, dict)}   # «HTX», «WOO X», «dYdX»
    btc = []
    for m in markets:
        if not isinstance(m, dict) or not m.get("is_perpetual") or str(m.get("base_asset", "")).upper() not in ("BTC", "XBT"):
            continue
        ex = names.get(m.get("exchange")) or norm_ex(m.get("exchange"))
        key = (ex, norm_sym(m.get("symbol_on_exchange")))
        btc.append({"symbol": m.get("symbol"), "ex": ex, "key": key,
                    "label": f"{shown.get(m.get('exchange')) or ex.capitalize()} {m.get('symbol_on_exchange')}",
                    "oi": float(oi_now.get(m.get("symbol")) or 0)})
    btc.sort(key=lambda x: -x["oi"])
    repl = [m for m in btc if m["key"] in SNAP_OI]
    extra = [m for m in btc if m["key"] not in OWN_OI and m["key"] not in SNAP_OI and m["oi"] > 0][:MAX_EXTRA]
    own_liq = [m for m in btc if m["key"] in OWN_LIQ]
    other_liq = [m for m in btc if m["key"] not in OWN_LIQ and m["oi"] > 0]
    liq = own_liq + other_liq[:max(0, 20 - len(own_liq))]
    for m in repl:
        m["name"] = SNAP_OI[m["key"]]
    for k, m in enumerate(extra):
        m["name"] = f"cz{k + 1}"
    return {"all": btc, "oi": (repl + extra)[:20], "liq": liq[:20]}


def discover():
    ex = _get("/exchanges")
    mk = _get("/future-markets")
    names = {e.get("code"): norm_ex(e.get("name")) for e in ex if isinstance(e, dict)}
    cand = [m.get("symbol") for m in mk if isinstance(m, dict) and m.get("is_perpetual")
            and str(m.get("base_asset", "")).upper() in ("BTC", "XBT") and m.get("symbol")]
    oi_now = {}
    for k in range(0, len(cand), 20):                      # OI actual para ordenar por tamaño
        part = cand[k:k + 20]
        rows = _get("/open-interest", {"symbols": ",".join(part), "convert_to_usd": "true"}, cost=len(part))
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("symbol"):
                oi_now[r["symbol"]] = r.get("value") or 0
    sel = pick_markets(ex, mk, oi_now)
    with _lock:
        STATE.update(markets=sel["all"], oi=sel["oi"], liq=sel["liq"], names=names, discovered=int(time.time()))
    return sel


# ───────────── Históricos ─────────────
def _history(path, symbols, tf, t_from, t_to):
    rows = _get(path, {"symbols": ",".join(symbols), "interval": IV[tf], "from": int(t_from), "to": int(t_to),
                       "convert_to_usd": "true"}, cost=len(symbols))
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("symbol"):
            out[r["symbol"]] = r.get("history") or []
    return out


def refresh(tf, now=None):
    """Pide lo nuevo de una temporalidad (OI y liquidaciones) y lo junta con lo que ya había."""
    now = now or time.time()
    step = STEP[tf]
    with _lock:
        oi_m, liq_m = list(STATE["oi"]), list(STATE["liq"])
        old = STATE["data"].get(tf) or {"oi": {}, "liq": {}, "at": 0}
    start = now - (SPAN[tf] + 2) * step

    def since(kind, symbols):
        """Desde dónde pedir: solo lo nuevo, salvo que haya un mercado que aún no tenemos (entonces todo)."""
        have = old[kind]
        if not have or any(not have.get(x) for x in symbols):
            return start
        return max(start, min(max(have[x]) for x in symbols) - 3 * step)

    oi, liq = {k: dict(v) for k, v in old["oi"].items()}, {k: dict(v) for k, v in old["liq"].items()}
    if oi_m:
        syms = [m["symbol"] for m in oi_m]
        for sym, hist in _history("/open-interest-history", syms, tf, since("oi", syms), now).items():
            d = oi.setdefault(sym, {})
            for h in hist:
                t, c = int(h.get("t") or 0), float(h.get("c") or 0)
                if t and c > 0:
                    d[t - t % step] = c                # OI al cierre de la vela, como lo que grabamos minuto a minuto
    if liq_m:
        syms = [m["symbol"] for m in liq_m]
        for sym, hist in _history("/liquidation-history", syms, tf, since("liq", syms), now).items():
            d = liq.setdefault(sym, {})
            for h in hist:
                t = int(h.get("t") or 0)
                if t:
                    d[t - t % step] = [float(h.get("l") or 0), float(h.get("s") or 0)]   # l: largos · s: cortos
    for d in list(oi.values()) + list(liq.values()):
        for t in [t for t in d if t < start]:
            del d[t]
    with _lock:
        STATE["data"][tf] = {"oi": oi, "liq": liq, "at": now}
    return STATE["data"][tf]


# ───────────── Para el servidor ─────────────
def oi_sources(tf):
    """{nombre de fuente: {vela: USD}} y {nombre: etiqueta}. Los nombres hyperliquid/bitget/deribit/bitmex
    sustituyen a la grabación propia; cz1, cz2… son mercados nuevos."""
    with _lock:
        d = STATE["data"].get(tf)
        oi_m = list(STATE["oi"])
    if not d:
        return {}, {}
    out, labels = {}, {}
    for m in oi_m:
        series = d["oi"].get(m["symbol"])
        if series and len(series) >= 3:
            out[m["name"]] = dict(series)
            labels[m["name"]] = m["label"]
    return out, labels


def labels(tf="5m"):
    """{nombre de fuente: etiqueta} de los mercados con OI ya descargado (sin copiar los datos)."""
    with _lock:
        d = STATE["data"].get(tf)
        return {m["name"]: m["label"] for m in STATE["oi"] if d and len(d["oi"].get(m["symbol"]) or ()) >= 3}


def liq_split(tf):
    """Liquidaciones por vela: (de los mercados que no escuchamos, de los nuestros) como {vela: [largos, cortos]}."""
    with _lock:
        d = STATE["data"].get(tf)
        liq_m = list(STATE["liq"])
    ext, own = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    if not d:
        return {}, {}, 0
    n_ext = 0
    for m in liq_m:
        series = d["liq"].get(m["symbol"]) or {}
        target = own if m["key"] in OWN_LIQ else ext
        if m["key"] not in OWN_LIQ and series:
            n_ext += 1
        for t, (L, S) in series.items():
            target[t][0] += L
            target[t][1] += S
    return dict(ext), dict(own), n_ext


def status():
    if not KEY:
        return None
    with _lock:
        st = {k: STATE[k] for k in ("discovered", "errors", "last_error", "check")}
        st["oi_markets"] = [m["label"] for m in STATE["oi"]]
        st["liq_markets"] = len(STATE["liq"])
        st["btc_markets"] = len(STATE["markets"])
        st["all"] = [f"{m['label']} [{m['key'][0]}:{m['key'][1]}] {round(m.get('oi') or 0) / 1e9:.2f}B" for m in STATE["markets"]]
        st["updated"] = {tf: int(v["at"]) for tf, v in STATE["data"].items()}
        st["calls_last_min"] = len([t for t in STATE["calls"] if time.time() - t < 60])
    return st


def set_check(value):
    with _lock:
        STATE["check"] = value


def _loop():
    while True:
        try:
            now = time.time()
            if not STATE["discovered"] or now - STATE["discovered"] > DISCOVER_EVERY:
                discover()
            for tf in ("5m", "15m", "1h", "4h", "1d"):
                d = STATE["data"].get(tf)
                if not d or time.time() - d["at"] >= REFRESH[tf]:
                    refresh(tf)
        except Exception as e:
            with _lock:
                STATE["errors"] += 1
                STATE["last_error"] = str(e)[:160]
            auth = isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403)
            time.sleep(900 if auth else 60)      # clave mala: no insistir
        time.sleep(10)


def start():
    global _started
    if _started or not KEY or os.getenv("COLLECT", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="coinalyze").start()
