"""Descarga los datos extra del estudio (research/extra.py) a research/data/extra/.

Fear & Greed (alternative.me), on-chain gratuito de Coin Metrics (MVRV, flujos y saldo de exchanges, hash rate…),
stablecoins (DefiLlama), macro (FRED: dólar, tipos reales, balance de la Fed, cuenta del Tesoro, repos inversos,
S&P 500…), Coinbase BTC-USD (prima de Coinbase), volatilidad implícita DVOL (Deribit), visitas a «Bitcoin» en Wikipedia,
métricas de futuros de Binance (OI y largos/cortos, desde dic-2021) y flujos de los ETF (Farside).

Uso: python3 research/get_extra.py   (unos 25 MB; tarda unos minutos)"""
import datetime as dt, gzip, io, json, os, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "extra")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (research)"}
def get(url, tries=5, binary=False):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                b = r.read()
                return b if binary else b.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                print("HTTP", e.code, url[:120]); return None
        except Exception as e:
            print("error", type(e).__name__, url[:120])
        time.sleep(2 + 3 * i)
    return None
def save(name, text):
    if text is None:
        print("SIN DATOS:", name); return
    with gzip.open(f"{OUT}/{name}.gz", "wt") as f:
        f.write(text)
    print("ok", name, len(text))
# 1) Fear & Greed (alternative.me)
save("fng.json", get("https://api.alternative.me/fng/?limit=0&format=json"))
# 2) CoinMetrics community: cada métrica por separado (algunas no son gratis)
for m in ["CapMVRVCur", "CapMrktCurUSD", "CapRealUSD", "AdrActCnt", "TxCnt", "HashRate", "FlowInExUSD",
          "FlowOutExUSD", "SplyExNtv", "NVTAdj", "SplyCur", "IssTotUSD", "RevUSD"]:
    rows, url = [], ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=" + m +
                     "&frequency=1d&page_size=10000&start_time=2014-01-01")
    while url:
        t = get(url)
        if t is None: break
        j = json.loads(t)
        rows += j.get("data", [])
        url = j.get("next_page_url")
    save(f"cm_{m}.json", json.dumps(rows) if rows else None)
# 3) Stablecoins (DefiLlama)
save("stablecoins_all.json", get("https://stablecoins.llama.fi/stablecoincharts/all"))
# 4) FRED (CSV sin clave)
for s in ["DTWEXBGS", "DFII10", "WALCL", "RRPONTSYD", "WTREGEN", "M2SL", "SP500", "VIXCLS", "NASDAQCOM", "DGS2", "T10Y2Y"]:
    save(f"fred_{s}.csv", get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}"))
# 5) Coinbase BTC-USD diario y horario (prima de Coinbase)
def coinbase(gran, start, name):
    rows, t = {}, start
    end_all = dt.datetime.utcnow()
    step = dt.timedelta(seconds=gran * 300)
    while t < end_all:
        e = min(t + step, end_all)
        u = (f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity={gran}"
             f"&start={t.isoformat()}Z&end={e.isoformat()}Z")
        txt = get(u)
        if txt:
            for r in json.loads(txt):
                rows[r[0]] = r
        t = e
        time.sleep(0.15)
    save(name, json.dumps([rows[k] for k in sorted(rows)]))
coinbase(86400, dt.datetime(2017, 8, 1), "coinbase_1d.json")
coinbase(3600, dt.datetime(2019, 9, 1), "coinbase_1h.json")
# 6) Deribit DVOL (volatilidad implícita de BTC), por tramos
dv, start_ms = {}, int(dt.datetime(2021, 3, 1).timestamp() * 1000)
now_ms = int(time.time() * 1000)
while start_ms < now_ms:
    end_ms = min(start_ms + 900 * 86400 * 1000, now_ms)
    t = get(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&start_timestamp={start_ms}&end_timestamp={end_ms}&resolution=1D")
    if t:
        for r in json.loads(t).get("result", {}).get("data", []):
            dv[r[0]] = r
    start_ms = end_ms
save("deribit_dvol.json", json.dumps([dv[k] for k in sorted(dv)]) if dv else None)
# 7) Wikipedia: visitas al artículo «Bitcoin» (atención)
save("wiki_bitcoin.json", get("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/20150701/20261231"))
# 8) Binance: métricas diarias de futuros (OI y ratios largos/cortos, cada 5 min) desde dic-2021
days, d = [], dt.date(2021, 12, 1)
while d < dt.date.today():
    days.append(d); d += dt.timedelta(days=1)
def met(day):
    b = get(f"https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{day.isoformat()}.zip", tries=3, binary=True)
    if not b: return []
    z = zipfile.ZipFile(io.BytesIO(b))
    out = []
    for n in z.namelist():
        for line in io.TextIOWrapper(z.open(n), encoding="utf-8"):
            if line[:1].isdigit():
                out.append(line.strip())
    return out
with ThreadPoolExecutor(12) as ex:
    allrows = [r for rs in ex.map(met, days) for r in rs]
save("binance_metrics.csv", "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n" + "\n".join(allrows) if allrows else None)
# 9) Flujos de los ETF de BTC (Farside, tabla HTML)
save("farside_btc_etf.html", get("https://farside.co.uk/bitcoin-etf-flow-all-data/"))
# 10) Hash rate (blockchain.com)
save("bc_hashrate.json", get("https://api.blockchain.info/charts/hash-rate?timespan=all&format=json&sampled=false"))
