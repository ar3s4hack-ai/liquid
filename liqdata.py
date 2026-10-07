"""Liquidaciones reales de BTC (Binance, Bybit, OKX, Deribit, BitMEX, Bitget, Gate y HTX): recolección, almacenamiento y validación.

- Recolectores WebSocket (y consultas REST para Bitget, Gate y HTX) que corren 24 h en hilos y guardan cada liquidación en SQLite.
- Latido por minuto para saber qué horas estuvo escuchando el servidor (cobertura).
- Open Interest grabado por minuto de los exchanges que no publican histórico.
- Agregados para el gráfico (burbujas y barras por vela) y feed en directo por cursor.
- Validación: compara las zonas estimadas (antes de cada liquidación) con lo que pasó de verdad,
  y lo compara con el azar para saber si el heatmap aporta algo.
"""
import json
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque

import requests

try:
    import websocket  # paquete websocket-client
except ImportError:  # pragma: no cover
    websocket = None

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
OKX_INST = os.getenv("OKX_INST", "BTC-USDT-SWAP")
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
RETENTION_DAYS = int(os.getenv("LIQ_RETENTION_DAYS", "30"))
LIQ_MIN_USD = float(os.getenv("LIQ_MIN_USD", "5000"))
# Binance separó sus WebSocket de futuros en 2026: liquidaciones, precio de marca, velas y operaciones van por /market
BINANCE_WS = os.getenv("BINANCE_WS", "wss://fstream.binance.com/market")

GATE_CONTRACT = os.getenv("GATE_CONTRACT", "BTC_USDT")
BITGET_SYMBOL = os.getenv("BITGET_SYMBOL", SYMBOL)
HTX_CONTRACT = os.getenv("HTX_CONTRACT", "BTC-USDT")

EXCHANGES = ("binance", "bybit", "okx", "deribit", "bitmex", "bitget", "gate", "htx")
STATUS = {ex: {"connected": False, "last_msg": 0, "events": 0, "error": None, "since": None} for ex in EXCHANGES}
MARK = {"price": None, "funding": None, "next_funding": None, "ts": 0}
_mark_hist = deque(maxlen=1200)   # (ts ms, precio de marca de Binance) de los últimos ~20 min

_db = None
_db_lock = threading.Lock()
_started = False


# ───────────── Base de datos ─────────────
def set_data_dir(path):
    """Solo para pruebas: cambia la carpeta y reinicia la conexión."""
    global DATA_DIR, _db
    with _db_lock:
        if _db is not None:
            _db.close()
        _db = None
        DATA_DIR = path


def _conn():
    global _db
    if _db is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _db = sqlite3.connect(os.path.join(DATA_DIR, "liq.db"), check_same_thread=False)
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("CREATE TABLE IF NOT EXISTS liq(ts INTEGER, ex TEXT, side INTEGER, price REAL, mkt REAL, qty REAL, usd REAL)")
        _db.execute("CREATE INDEX IF NOT EXISTS liq_ts ON liq(ts)")
        _db.execute("CREATE TABLE IF NOT EXISTS hb(ts INTEGER PRIMARY KEY)")
        _db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        _db.execute("CREATE TABLE IF NOT EXISTS oi_snap(ts INTEGER, src TEXT, usd REAL, PRIMARY KEY (ts, src))")
        _db.commit()
    return _db


def save_events(evs):
    if not evs:
        return
    with _db_lock:
        c = _conn()
        c.executemany(
            "INSERT INTO liq VALUES (?,?,?,?,?,?,?)",
            [(e["ts"], e["ex"], e["side"], e["price"], e["mkt"], e["qty"], e["usd"]) for e in evs],
        )
        c.commit()
    for e in evs:
        if e["ex"] in STATUS:
            STATUS[e["ex"]]["events"] += 1


def heartbeat(now=None):
    m = int((now or time.time()) // 60 * 60)
    with _db_lock:
        c = _conn()
        c.execute("INSERT OR IGNORE INTO hb VALUES (?)", (m,))
        c.commit()


def cleanup(now=None):
    lim = int((now or time.time()) - RETENTION_DAYS * 86400)
    with _db_lock:
        c = _conn()
        c.execute("DELETE FROM liq WHERE ts < ?", (lim * 1000,))
        c.execute("DELETE FROM hb WHERE ts < ?", (lim,))
        c.execute("DELETE FROM oi_snap WHERE ts < ?", (lim,))
        c.commit()


def events_between(t0, t1):
    """Liquidaciones entre t0 y t1 (segundos)."""
    with _db_lock:
        rows = _conn().execute(
            "SELECT ts, ex, side, price, mkt, qty, usd FROM liq WHERE ts >= ? AND ts < ? ORDER BY ts",
            (int(t0 * 1000), int(t1 * 1000)),
        ).fetchall()
    return [{"ts": r[0], "ex": r[1], "side": r[2], "price": r[3], "mkt": r[4], "qty": r[5], "usd": r[6]} for r in rows]


def events_after(rowid, limit=300):
    """Liquidaciones guardadas después del cursor (rowid). Con cursor 0 o caducado devuelve solo el cursor actual."""
    with _db_lock:
        c = _conn()
        top = c.execute("SELECT COALESCE(MAX(rowid), 0) FROM liq").fetchone()[0]
        if rowid <= 0 or rowid > top:          # primera vez, o la base de datos es nueva: empezar desde ahora
            return [], top
        rows = c.execute("SELECT rowid, ts, ex, side, price, mkt, usd FROM liq WHERE rowid > ? ORDER BY rowid LIMIT ?",
                         (int(rowid), int(limit))).fetchall()
    cursor = rows[-1][0] if rows else rowid
    return [{"rowid": r[0], "ts": r[1], "ex": r[2], "side": r[3], "price": r[4], "mkt": r[5], "usd": r[6]} for r in rows], cursor


def covered_minutes(t0, t1):
    with _db_lock:
        rows = _conn().execute("SELECT ts FROM hb WHERE ts >= ? AND ts < ?", (int(t0), int(t1))).fetchall()
    return {r[0] for r in rows}


def save_oi_snapshots(values, now=None):
    m = int((now or time.time()) // 60 * 60)
    with _db_lock:
        c = _conn()
        c.executemany("INSERT OR REPLACE INTO oi_snap VALUES (?, ?, ?)", [(m, k, float(v)) for k, v in values.items()])
        c.commit()


def oi_snapshots(src, candles, step):
    """OI grabado por minuto -> valor al cierre de cada vela (último minuto dentro de la vela)."""
    if not candles:
        return {}
    t0, t1 = candles[0]["time"], candles[-1]["time"] + step
    with _db_lock:
        rows = _conn().execute("SELECT ts, usd FROM oi_snap WHERE src = ? AND ts >= ? AND ts < ? ORDER BY ts",
                               (src, int(t0), int(t1))).fetchall()
    out = {}
    for ts, usd in rows:
        out[ts - ts % step] = usd   # en orden: se queda el último minuto de cada vela
    return out


def meta_set(k, value):
    with _db_lock:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, json.dumps(value)))
        c.commit()


def meta_get(k, default=None):
    with _db_lock:
        row = _conn().execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return json.loads(row[0]) if row else default


def db_counts():
    with _db_lock:
        c = _conn()
        n, first = c.execute("SELECT COUNT(*), MIN(ts) FROM liq").fetchone()
        hb = c.execute("SELECT COUNT(*) FROM hb").fetchone()[0]
    return {"events": n, "first_ms": first, "hours_listening": round(hb / 60, 1)}


# ───────────── Lectura de mensajes de cada exchange ─────────────
def _ev(ts, ex, side, price, mkt, qty):
    px = mkt or price
    return {"ts": int(ts), "ex": ex, "side": side, "price": float(price), "mkt": float(mkt) if mkt else None,
            "qty": float(qty), "usd": float(qty) * float(px)}


def mark_at(ts_ms, max_gap_ms=10000):
    """Precio de marca de Binance más cercano a ts_ms (None si no hay ninguno a menos de max_gap_ms).
    Sirve para situar liquidaciones que llegan con precio de quiebra o con unos segundos de retraso."""
    best = None
    for t, p in reversed(_mark_hist):
        d = abs(t - ts_ms)
        if best is None or d < best[0]:
            best = (d, p)
        if t < ts_ms - max_gap_ms:
            break
    return best[1] if best and best[0] <= max_gap_ms else None


def _mark_near(ts_ms):
    return mark_at(ts_ms)


def parse_binance(msg):
    """forceOrder (S=SELL: se liquida un largo) y markPriceUpdate (precio de marca y funding)."""
    d = msg.get("data", msg) if isinstance(msg, dict) else {}
    e = d.get("e")
    if e == "markPriceUpdate":
        if d.get("s", SYMBOL) == SYMBOL:
            MARK.update(price=float(d["p"]), funding=float(d.get("r") or 0),
                        next_funding=int(d.get("T") or 0), ts=int(d.get("E") or time.time() * 1000))
            _mark_hist.append((MARK["ts"], MARK["price"]))
        return []
    if e != "forceOrder":
        return []
    o = d.get("o") or {}
    if o.get("s") != SYMBOL:
        return []
    qty = float(o.get("z") or o.get("q") or 0)
    px = float(o.get("ap") or o.get("p") or 0)
    if qty <= 0 or px <= 0:
        return []
    side = 1 if o.get("S") == "SELL" else -1
    return [_ev(o.get("T") or d.get("E"), "binance", side, px, px, qty)]


def parse_bybit(msg):
    """allLiquidation.BTCUSDT (S=Buy: se liquida un largo; p = precio de quiebra)."""
    if not str(msg.get("topic", "")).startswith("allLiquidation."):
        return []
    out = []
    for x in msg.get("data") or []:
        if x.get("s") != SYMBOL:
            continue
        qty, px = float(x.get("v") or 0), float(x.get("p") or 0)
        if qty <= 0 or px <= 0:
            continue
        side = 1 if x.get("S") == "Buy" else -1
        ts = int(x.get("T") or msg.get("ts") or 0)
        out.append(_ev(ts, "bybit", side, px, _mark_near(ts), qty))
    return out


OKX_CT_VAL = {"v": 0.01}


def parse_okx(msg):
    """liquidation-orders SWAP (posSide long/short; en modo net, side=sell es un largo). sz en contratos."""
    arg = msg.get("arg") or {}
    if arg.get("channel") != "liquidation-orders":
        return []
    out = []
    for item in msg.get("data") or []:
        if item.get("instId") != OKX_INST:
            continue
        for dt in item.get("details") or []:
            pos = (dt.get("posSide") or "").lower()
            sd = (dt.get("side") or "").lower()
            if pos == "long":
                side = 1
            elif pos == "short":
                side = -1
            else:
                side = 1 if sd == "sell" else -1
            qty = float(dt.get("sz") or 0) * OKX_CT_VAL["v"]
            px = float(dt.get("bkPx") or 0)
            if qty <= 0 or px <= 0:
                continue
            ts = int(dt.get("ts") or item.get("ts") or 0)
            out.append(_ev(ts, "okx", side, px, _mark_near(ts), qty))
    return out


DERIBIT_INST = os.getenv("DERIBIT_INST", "BTC-PERPETUAL")


def parse_deribit(msg):
    """trades.BTC-PERPETUAL: 'liquidation' = T (el que toma la orden), M (el que la tenía puesta) o MT (ambos).
    'direction' es la del que toma: si vende y lo liquidan, era un largo. El que tenía la orden va al revés.
    'amount' viene en USD en el perpetuo."""
    params = msg.get("params") or {}
    if msg.get("method") != "subscription" or not str(params.get("channel", "")).startswith("trades."):
        return []
    out = []
    for t in params.get("data") or []:
        liq = t.get("liquidation")
        if not liq or t.get("instrument_name") != DERIBIT_INST:
            continue
        usd, px = float(t.get("amount") or 0), float(t.get("price") or 0)
        if usd <= 0 or px <= 0:
            continue
        taker_side = 1 if t.get("direction") == "sell" else -1      # quien toma vendiendo cierra un largo
        sides = []
        if "T" in liq:
            sides.append(taker_side)
        if "M" in liq:
            sides.append(-taker_side)
        mkt = float(t.get("mark_price") or px)
        for side in sides:
            out.append(_ev(int(t.get("timestamp") or 0), "deribit", side, px, mkt, usd / mkt))
    return out


BITMEX_SYMBOL = os.getenv("BITMEX_SYMBOL", "XBTUSD")


def parse_bitmex(msg):
    """Tabla liquidation de BitMEX: side=Sell es la orden que vende un largo liquidado.
    En XBTUSD cada contrato vale 1 USD (leavesQty = USD). Solo se cuenta la aparición (insert)."""
    if msg.get("table") != "liquidation" or msg.get("action") != "insert":
        return []
    out = []
    now_ms = int(time.time() * 1000)
    for x in msg.get("data") or []:
        if x.get("symbol") != BITMEX_SYMBOL:
            continue
        usd, px = float(x.get("leavesQty") or 0), float(x.get("price") or 0)
        if usd <= 0 or px <= 0:
            continue
        side = 1 if x.get("side") == "Sell" else -1
        mk = _mark_near(now_ms) or px
        out.append(_ev(now_ms, "bitmex", side, px, mk, usd / mk))
    return out


GATE_MULT = {"v": 0.0001}   # BTC por contrato de BTC_USDT (se actualiza al arrancar)


def parse_gate(rows):
    """liq_orders de Gate (REST público). size = posición liquidada (+ largo, − corto) en contratos;
    order_size − left = contratos ejecutados; order_price = precio de la orden; fill_price = precio de ejecución.
    Comprobado con datos reales: size negativo con el precio subiendo (cortos liquidados)."""
    out = []
    for x in rows if isinstance(rows, list) else []:
        if x.get("contract") != GATE_CONTRACT:
            continue
        try:
            size = float(x.get("size") or 0)
            fill = float(x.get("fill_price") or 0)
            order_px = float(x.get("order_price") or 0) or fill
            done = abs(float(x.get("order_size") or size)) - abs(float(x.get("left") or 0))
            ts = int(float(x.get("time") or 0)) * 1000
        except (TypeError, ValueError):
            continue
        if size == 0 or fill <= 0 or ts <= 0:
            continue
        if done <= 0:
            done = abs(size)
        out.append(_ev(ts, "gate", 1 if size > 0 else -1, order_px, fill, done * GATE_MULT["v"]))
    return out


def parse_bitget(resp):
    """/api/v3/market/liquidations de Bitget (REST público). side = lado de la posición liquidada: «buy» = largo
    (comprobado: todo «buy» durante la cascada de largos del 07-10-2026 02:01 UTC). amount en BTC.
    price ≈ precio de quiebra: para situarla se usa el precio de marca de Binance de ese momento."""
    data = resp.get("data") if isinstance(resp, dict) else None
    out = []
    for x in (data.get("list") if isinstance(data, dict) else None) or []:
        if x.get("symbol") != BITGET_SYMBOL:
            continue
        try:
            qty, px, ts = float(x.get("amount") or 0), float(x.get("price") or 0), int(x.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or px <= 0 or ts <= 0:
            continue
        side = 1 if str(x.get("side")).lower() == "buy" else -1
        out.append(_ev(ts, "bitget", side, px, mark_at(ts), qty))
    return out


HTX_CT = {"v": 0.001}   # BTC por contrato de BTC-USDT (se actualiza al arrancar)


def parse_htx(resp):
    """Órdenes de liquidación de HTX (REST público, USDT-M). direction = la orden que cierra la posición:
    «sell» cierra un largo (largo liquidado) y «buy» un corto, igual que el forceOrder de Binance.
    amount en BTC (si falta: volume en contratos × tamaño del contrato). price ≈ precio de quiebra:
    para situarla se usa el precio de marca de Binance de ese momento."""
    if not isinstance(resp, dict):
        return []
    data = resp.get("data")
    rows = data.get("orders") if isinstance(data, dict) else data       # v3: lista · v1: {"orders": [...]}
    out = []
    for x in rows if isinstance(rows, list) else []:
        if not isinstance(x, dict) or (x.get("contract_code") or x.get("contract")) != HTX_CONTRACT:
            continue
        try:
            px = float(x.get("price") or 0)
            qty = float(x.get("amount") or 0) or float(x.get("volume") or 0) * HTX_CT["v"]
            ts = int(x.get("created_at") or 0)
        except (TypeError, ValueError):
            continue
        d = str(x.get("direction") or "").lower()
        if qty <= 0 or px <= 0 or ts <= 0 or d not in ("buy", "sell"):
            continue
        out.append(_ev(ts, "htx", 1 if d == "sell" else -1, px, mark_at(ts), qty))
    return out


def _fetch_htx(since_ms, now):
    r = requests.get("https://api.hbdm.com/linear-swap-api/v3/swap_liquidation_orders", timeout=10,
                     params={"contract": HTX_CONTRACT, "trade_type": 0})
    r.raise_for_status()
    j = r.json()
    if str(j.get("code", 200)) != "200" and j.get("status") != "ok":
        raise RuntimeError(f"HTX: {j.get('msg') or j.get('err_msg') or j.get('code')}")
    return j


def _htx_ct():
    try:
        r = requests.get("https://api.hbdm.com/linear-swap-api/v1/swap_contract_info",
                         params={"contract_code": HTX_CONTRACT}, timeout=10).json()
        HTX_CT["v"] = float(r["data"][0]["contract_size"])
    except Exception:
        pass


def _fetch_gate(since_ms, now):
    r = requests.get("https://api.gateio.ws/api/v4/futures/usdt/liq_orders", timeout=10,
                     params={"contract": GATE_CONTRACT, "from": max(int(since_ms // 1000) - 5, int(now) - 3500),
                             "to": int(now) + 5, "limit": 1000})
    r.raise_for_status()
    return r.json()


def _fetch_bitget(since_ms, now, pages=5):
    """Últimas liquidaciones; si llega una página llena de nuevas, se pide la siguiente (cascadas grandes)."""
    rows, cursor = [], None
    for _ in range(pages):
        params = {"category": "USDT-FUTURES", "symbol": BITGET_SYMBOL, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get("https://api.bitget.com/api/v3/market/liquidations", params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        if str(j.get("code")) != "00000":
            raise RuntimeError(f"Bitget: {j.get('msg')}")
        page = (j.get("data") or {}).get("list") or []
        rows += page
        cursor = (j.get("data") or {}).get("cursor")
        oldest = min((int(x.get("ts") or 0) for x in page), default=0)
        if len(page) < 100 or not cursor or oldest <= since_ms:
            break
    return {"data": {"list": rows}}


def _gate_mult():
    try:
        r = requests.get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{GATE_CONTRACT}", timeout=10).json()
        GATE_MULT["v"] = float(r["quanto_multiplier"])
    except Exception:
        pass


# ───────────── Recolectores ─────────────
class RESTCollector(threading.Thread):
    """Consulta cada pocos segundos un endpoint público de liquidaciones (Bitget, Gate y HTX).
    No repite nada: recuerda lo ya guardado (también lo de antes de un reinicio) durante 30 min."""

    def __init__(self, ex, fetch, parser, every=10, warmup_s=180):
        super().__init__(daemon=True, name=f"liq-{ex}")
        self.ex, self.fetch, self.parser, self.every, self.warmup_s = ex, fetch, parser, every, warmup_s
        self.seen = {}
        self.min_ts = None
        self.last_ts = 0

    @staticmethod
    def key(e):
        return (int(e["ts"]), int(e["side"]), round(float(e["price"]), 2), round(float(e["qty"]), 8))

    def seed(self, now=None):
        now = now or time.time()
        self.min_ts = int((now - self.warmup_s) * 1000)       # al arrancar solo se recuperan los últimos 3 min
        for e in events_between(now - 1800, now + 60):
            if e["ex"] == self.ex:
                self.seen[self.key(e)] = e["ts"]
                self.last_ts = max(self.last_ts, e["ts"])

    def poll_once(self, now=None):
        now = now or time.time()
        if self.min_ts is None:
            self.seed(now)
        evs = self.parser(self.fetch(max(self.last_ts, self.min_ts), now))
        new = []
        for e in sorted(evs, key=lambda e: e["ts"]):
            k = self.key(e)
            if e["ts"] < self.min_ts or k in self.seen:
                continue
            self.seen[k] = e["ts"]
            self.last_ts = max(self.last_ts, e["ts"])
            new.append(e)
        if len(self.seen) > 3000:
            lim = int((now - 1800) * 1000)
            self.seen = {k: t for k, t in self.seen.items() if t >= lim}
        save_events(new)
        st = STATUS[self.ex]
        st.update(connected=True, last_msg=int(now), error=None)
        if st["since"] is None:
            st["since"] = int(now)
        return new

    def run(self):
        while True:
            try:
                self.poll_once()
            except Exception as e:
                STATUS[self.ex].update(connected=False, error=str(e)[:200])
            time.sleep(self.every)


class WSCollector(threading.Thread):
    def __init__(self, ex, url, parser, subscribe=None, ping_text=None, ping_every=20):
        super().__init__(daemon=True, name=f"liq-{ex}")
        self.ex, self.url, self.parser = ex, url, parser
        self.subscribe, self.ping_text, self.ping_every = subscribe, ping_text, ping_every
        self.backoff = 5
        self._stop_ping = None

    def run(self):
        while True:
            try:
                app = websocket.WebSocketApp(self.url, on_open=self._open, on_message=self._msg,
                                             on_error=self._err, on_close=self._close)
                if self.ping_text:
                    app.run_forever()
                else:
                    app.run_forever(ping_interval=60, ping_timeout=20)
            except Exception as e:  # pragma: no cover
                STATUS[self.ex]["error"] = str(e)[:200]
            STATUS[self.ex]["connected"] = False
            time.sleep(self.backoff)
            self.backoff = min(60, self.backoff * 2)

    def _open(self, ws):
        STATUS[self.ex].update(connected=True, since=int(time.time()), error=None)
        self.backoff = 5
        if self.subscribe:
            ws.send(json.dumps(self.subscribe))
        if self.ping_text:
            stop = threading.Event()
            self._stop_ping = stop

            def pinger():
                while not stop.wait(self.ping_every):
                    try:
                        ws.send(self.ping_text)
                    except Exception:
                        return
            threading.Thread(target=pinger, daemon=True).start()

    def _msg(self, ws, raw):
        STATUS[self.ex]["last_msg"] = int(time.time())
        if raw == "pong":
            return
        try:
            msg = json.loads(raw)
        except Exception:
            return
        try:
            evs = self.parser(msg)
            if evs:
                save_events(evs)
        except Exception as e:
            STATUS[self.ex]["error"] = f"lectura: {e}"[:200]

    def _err(self, ws, err):
        STATUS[self.ex]["error"] = str(err)[:200]

    def _close(self, ws, *a):
        STATUS[self.ex]["connected"] = False
        if self._stop_ping:
            self._stop_ping.set()


def _okx_ct_val():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/instruments",
                         params={"instType": "SWAP", "instId": OKX_INST}, timeout=10).json()
        OKX_CT_VAL["v"] = float(r["data"][0]["ctVal"])
    except Exception:
        pass


def _maintenance():
    n = 0
    while True:
        try:
            heartbeat()
            if n % 60 == 0:
                cleanup()
        except Exception as e:  # pragma: no cover
            print("liq maintenance:", e, flush=True)
        n += 1
        time.sleep(60)


def start():
    global _started
    if _started or os.getenv("COLLECT", "1") == "0":
        return
    if websocket is None:
        print("websocket-client no instalado: no se recogen liquidaciones", flush=True)
        return
    _started = True
    _okx_ct_val()
    sym = SYMBOL.lower()
    WSCollector("binance", f"{BINANCE_WS}/stream?streams={sym}@forceOrder/{sym}@markPrice@1s",
                parse_binance).start()
    WSCollector("bybit", "wss://stream.bybit.com/v5/public/linear", parse_bybit,
                subscribe={"op": "subscribe", "args": [f"allLiquidation.{SYMBOL}"]},
                ping_text=json.dumps({"op": "ping"})).start()
    WSCollector("okx", "wss://ws.okx.com:8443/ws/v5/public", parse_okx,
                subscribe={"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]},
                ping_text="ping", ping_every=25).start()
    WSCollector("deribit", "wss://www.deribit.com/ws/api/v2", parse_deribit,
                subscribe={"jsonrpc": "2.0", "id": 1, "method": "public/subscribe",
                           "params": {"channels": [f"trades.{DERIBIT_INST}.100ms"]}},
                ping_text=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "public/test", "params": {}}),
                ping_every=25).start()
    WSCollector("bitmex", f"wss://ws.bitmex.com/realtime?subscribe=liquidation:{BITMEX_SYMBOL}", parse_bitmex,
                ping_text="ping", ping_every=25).start()
    RESTCollector("bitget", _fetch_bitget, parse_bitget).start()
    _gate_mult()
    RESTCollector("gate", _fetch_gate, parse_gate).start()
    _htx_ct()
    RESTCollector("htx", _fetch_htx, parse_htx).start()
    threading.Thread(target=_maintenance, daemon=True, name="liq-maint").start()


def status():
    now = int(time.time())
    out = {}
    for ex, s in STATUS.items():
        receiving = bool(s["last_msg"]) and now - s["last_msg"] < 180
        just_opened = not s["last_msg"] and s["since"] is not None and now - s["since"] < 30
        out[ex] = {
            # conectado = está llegando información (no basta con abrir la conexión)
            "connected": s["connected"] and (receiving or just_opened),
            "receiving": receiving,
            "events": s["events"],
            "last_msg_ago": (now - s["last_msg"]) if s["last_msg"] else None,
            "error": s["error"],
        }
    return out


# ───────────── Burbujas y barras por vela para el gráfico ─────────────
def chart_liqs(candles, step, min_usd=None, limit=2000, now=None):
    """items: burbujas [vela, precio medio, lado, USD] agrupadas por vela, lado y tramo de 0,05 %.
    hist: barras por vela [vela, USD largos, USD cortos]. rowid: último evento incluido (para el feed en directo)."""
    min_usd = LIQ_MIN_USD if min_usd is None else min_usd
    if not candles:
        return {"items": [], "hist": [], "rowid": 0, "long_24h": 0, "short_24h": 0, "n_24h": 0}
    t0, t1 = candles[0]["time"], candles[-1]["time"] + step
    ref = candles[-1]["close"]
    with _db_lock:      # cursor y eventos en la misma lectura: nada se cuenta dos veces con el feed en directo
        c = _conn()
        top = c.execute("SELECT COALESCE(MAX(rowid), 0) FROM liq").fetchone()[0]
        rows = c.execute("SELECT ts, side, price, mkt, usd FROM liq WHERE ts >= ? AND ts < ? AND rowid <= ?",
                         (int(t0 * 1000), int(t1 * 1000), top)).fetchall()
    agg = {}
    hist = defaultdict(lambda: [0.0, 0.0])
    for ts, side, price, mkt, usd in rows:
        e = {"ts": ts, "side": side, "price": price, "mkt": mkt, "usd": usd}
        tc = e["ts"] // 1000
        tc -= tc % step
        px = e["mkt"] or e["price"]
        key = (tc, e["side"], int(px // (ref * 0.0005)))
        a = agg.setdefault(key, [0.0, 0.0])
        a[0] += e["usd"]
        a[1] += px * e["usd"]
        hist[tc][0 if e["side"] == 1 else 1] += e["usd"]
    items = [[t, round(pw / u, 2), side, round(u)] for (t, side, _), (u, pw) in agg.items() if u >= min_usd and u > 0]
    items.sort(key=lambda x: -x[3])
    now = now or time.time()
    day = events_between(now - 86400, now + 60)
    return {
        "items": items[:limit],
        "hist": [[t, round(v[0]), round(v[1])] for t, v in sorted(hist.items())],
        "rowid": top,
        "long_24h": round(sum(e["usd"] for e in day if e["side"] == 1)),
        "short_24h": round(sum(e["usd"] for e in day if e["side"] == -1)),
        "n_24h": len(day),
    }


# ───────────── Validación ─────────────
def validate(candles, step, heat, touches, events, covered, min_ratio=0.1, tol_pct=0.06, win_pct=0.5):
    """Mide si el heatmap marcaba las liquidaciones reales ANTES de que ocurrieran.

    acierto: % (en USD) de liquidaciones reales que caen a ±tol de una zona activa del mismo lado.
    azar:    % que se acertaría colocando la liquidación en un precio cualquiera a ±win del real
             con las mismas zonas (cuánto cubren las zonas).
    mejora:  acierto / azar (1 = igual que el azar).
    confirmación: de las zonas fuertes que el precio tocó, % con liquidaciones reales del mismo lado.
    """
    size, vmax = heat["size"], heat["max"]
    price = candles[-1]["close"]
    thr = min_ratio * vmax
    tol = max(1, math.ceil(price * tol_pct / 100 / size))
    win = max(tol + 1, math.ceil(price * win_pct / 100 / size))
    t_first, t_last = candles[0]["time"], candles[-1]["time"]

    by_bin = defaultdict(list)
    for p, t0, t1, L, S in heat["segments"]:
        by_bin[math.floor(p / size)].append((t0, t1, L, S))
    tidx = {c["time"]: i for i, c in enumerate(candles)}
    FF = heat.get("F") or {}
    FL, FS = FF.get("L"), FF.get("S")

    def factor(side, tc):
        arr = FL if side == 1 else FS
        if not arr:
            return 1.0
        j = tidx.get(tc, 0) - 1
        return arr[max(0, j)]

    def active(tc, side, lo, hi):
        found = set()
        f = factor(side, tc)
        for b in range(lo, hi + 1):
            for t0, t1, L, S in by_bin.get(b, ()):
                v = (L if side == 1 else S) * f
                if t0 < tc <= t1 and v >= thr:
                    found.add(b)
                    break
        return found

    n = hits_n = 0
    w_tot = w_hit = w_base = 0.0
    ev_index = defaultdict(list)
    for e in events:
        tc = e["ts"] // 1000
        tc -= tc % step
        px = e["mkt"] or e["price"]
        b = math.floor(px / size)
        ev_index[(tc, e["side"])].append(b)
        if tc <= t_first or tc > t_last:
            continue
        act = active(tc, e["side"], b - win - tol, b + win + tol)
        hit = any(abs(k - b) <= tol for k in act)
        positions = range(b - win, b + win + 1)
        base = sum(1 for q in positions if any(abs(k - q) <= tol for k in act)) / len(positions)
        w = e["usd"] or 1.0
        n += 1
        hits_n += hit
        w_tot += w
        w_hit += w * hit
        w_base += w * base

    def is_covered(tc):
        return any(m in covered for m in range(tc - tc % 60, tc + step, 60))

    def confirmed(tc, side, b):
        for t in (tc, tc + step):
            if any(abs(x - b) <= tol for x in ev_index.get((t, side), ())):
                return True
        return False

    strong = conf = 0
    for (b, i, side), vol in touches.items():
        if vol < thr or i >= len(candles):
            continue
        tc = candles[i]["time"]
        if not is_covered(tc):
            continue
        strong += 1
        conf += confirmed(tc, side, b)

    base_hits = base_tot = 0
    for c in candles:
        tc = c["time"]
        if not is_covered(tc):
            continue
        ob = math.floor(c["open"] / size)
        lo_b, hi_b = math.floor(c["low"] / size), math.floor(c["high"] / size)
        for k in range(lo_b, min(hi_b, lo_b + 300) + 1):
            if k == ob:
                continue
            side = 1 if k < ob else -1
            base_tot += 1
            base_hits += confirmed(tc, side, k)

    hit = w_hit / w_tot if w_tot else None
    base = w_base / w_tot if w_tot else None
    prec = conf / strong if strong else None
    prec_base = base_hits / base_tot if base_tot else None
    cov_h = round(len(covered) / 60, 1)
    return {
        "events": n,
        "usd": round(w_tot),
        "hit": hit,
        "hit_count": hits_n / n if n else None,
        "base": base,
        "lift": (hit / base) if hit is not None and base else None,
        "touches": strong,
        "prec": prec,
        "prec_base": prec_base,
        "prec_lift": (prec / prec_base) if prec is not None and prec_base else None,
        "coverage_hours": cov_h,
        "enough": n >= 100 and cov_h >= 24,
        "tol_points": round(tol * size),
    }
