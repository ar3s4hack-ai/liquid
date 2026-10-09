// Ejecuta static/index.html con un navegador simulado (DOM, Lightweight Charts, canvas, fetch, WebSocket y temporizadores).
const fs = require('fs'), vm = require('vm'), path = require('path');
const [, , REPO, OUT] = process.argv;
const html = fs.readFileSync(path.join(REPO, 'static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const load = (f) => JSON.parse(fs.readFileSync(path.join(OUT, f), 'utf8'));
const ok = (m) => console.log('✓ ' + m);
const flush = () => new Promise(r => setImmediate(r));
async function settle() { for (let i = 0; i < 6; i++) await flush(); }

function makeEnv(opts) {
  const calls = { fill: 0, rects: [], clips: [], texts: [], arcs: 0, textPos: [], fetches: [], sockets: [], titles: [], removedLines: 0, markers: [],
    images: [], putImages: [], strokes: [], fills: [] };
  const timers = [];
  let tid = 0;
  const ctx2d = () => ({
    fillStyle: '', strokeStyle: '', lineWidth: 1, font: '', globalAlpha: 1, imageSmoothingEnabled: true,
    setTransform() {}, clearRect() {}, fillRect(x, y, w, h) { calls.fill++; calls.rects.push([x, y, w, h, this.fillStyle]); }, save() {}, restore() {},
    beginPath() { this._pts = 0; }, rect(x, y, w, h) { calls.clips.push(h); }, clip() {}, arc() { calls.arcs++; }, fill() { calls.fills.push([this.fillStyle, this._pts]); },
    stroke() { calls.strokes.push([this.strokeStyle, this._pts]); }, closePath() {},
    moveTo() { this._pts = 1; }, lineTo() { this._pts = (this._pts || 0) + 1; }, setLineDash() {}, fillText(t, x, y) { calls.texts.push(t); calls.textPos.push([t, x, y]); },
    measureText: (t) => ({ width: String(t).length * 6 }),
    createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    putImageData(img) { calls.putImages.push(img); },
    drawImage(...a) { calls.images.push([this.globalAlpha, this.imageSmoothingEnabled, ...a.slice(1)]); }
  });
  function El(id, tag) {
    const e = {
      id, tag, style: {}, dataset: {}, children: [], kids: [], clientWidth: 420, clientHeight: 700, offsetHeight: 92,
      width: 0, height: 0, textContent: '', value: '', placeholder: '', checked: false, onclick: null, oninput: null, onchange: null,
      className: '', _h: '',
      get innerHTML() { return this._h; }, set innerHTML(v) { this._h = v; this.kids = []; this.children = []; },
      classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, f) { const on = f === undefined ? !this._s.has(c) : !!f; on ? this._s.add(c) : this._s.delete(c); return on; }, contains(c) { return this._s.has(c); } },
      appendChild(c) { this.kids.push(c); this.children.push(c); c.parent = this; return c; },
      prepend(c) { this.children.unshift(c); c.parent = this; },
      get lastChild() { return this.children[this.children.length - 1]; },
      remove() { if (this.parent) { this.parent.children = this.parent.children.filter(x => x !== this); this.parent.kids = this.parent.kids.filter(x => x !== this); } },
      getContext: () => e._ctx || (e._ctx = ctx2d()), addEventListener() {}
    };
    return e;
  }
  const els = {};
  const get = (id) => els[id] || (els[id] = El(id));
  const tfBtns = ['5m', '15m', '1h', '4h', '1d'].map(t => Object.assign(El('tf' + t), { dataset: { tf: t } }));
  const poolCbs = ['3', '5', '10', '25', '50', '100'].map(t => Object.assign(El('cb' + t), { dataset: { pool: t } }));
  const pv = {}; ['3', '5', '10', '25', '50', '100'].forEach(t => { pv[t] = El('pv' + t); });

  // ── Lightweight Charts simulado (guarda datos y valida el orden de update como el real)
  let payloadRef = null;
  const px2y = (p) => 300 - (p - payloadRef.price) * (300 / (payloadRef.price * 0.02));
  const mkSeries = (kind, o) => {
    const s = {
      kind, opts: { ...o }, data: [], updates: [], lines: [],
      setData(d) { this.data = d.slice(); },
      update(b) {
        const last = this.data.length ? this.data[this.data.length - 1].time : -Infinity;
        if (b.time < last) throw new Error(`Cannot update oldest data (${kind}): ${b.time} < ${last}`);
        if (b.time === last) this.data[this.data.length - 1] = b; else this.data.push(b);
        this.updates.push(b);
      },
      applyOptions(x) { Object.assign(this.opts, x); },
      priceScale() { return { applyOptions: (x) => { s.scale = x; } }; },
      priceToCoordinate: (p) => px2y(p),
      coordinateToPrice: (y) => payloadRef.price + (300 - y) * (payloadRef.price * 0.02 / 300),
      createPriceLine(lo) { calls.titles.push(lo.title); const pl = { ...lo, applyOptions(x) { Object.assign(pl, x); } }; s.lines.push(pl); return pl; },
      removePriceLine() { calls.removedLines++; },
      setMarkers(m) { calls.markers.push(m); }
    };
    return s;
  };
  const allSeries = [];
  const scales = {};
  const tscale = {
    height: () => 28, options: () => ({ barSpacing: 4 }), applyOptions() {},
    timeToCoordinate: (t) => { const cs = payloadRef.candles; const tf = (cs[1].time - cs[0].time); return 360 + (t - cs[cs.length - 1].time) / tf * 0.4; },
    setVisibleLogicalRange(r) { calls.range = r; }
  };
  const chart = {
    addCandlestickSeries(o) { const s = mkSeries('velas', o); allSeries.push(s); chart.main = s; return s; },
    addHistogramSeries(o) { const s = mkSeries('hist:' + o.priceScaleId + (o.color && o.color.includes('255,159') ? ':L' : o.color ? ':S' : ''), o); allSeries.push(s); return s; },
    priceScale: (id) => scales[id] || (scales[id] = { id, applyOptions(x) { this.opts = x; }, width: () => 62 }),
    timeScale: () => tscale,
    subscribeCrosshairMove(fn) { calls.crosshair = fn; }
  };
  const store = { ...(opts.storage || {}) };
  const sandbox = {
    document: {
      getElementById: get,
      createElement: (tag) => El('nuevo', tag),
      querySelectorAll: (sel) => sel.includes('data-pool') ? poolCbs : tfBtns,
      querySelector: (sel) => pv[(sel.match(/data-pv="(\d+)"/) || [])[1]] || El('x')
    },
    window: { devicePixelRatio: 2, innerWidth: opts.innerWidth || 1000, addEventListener() {}, ResizeObserver: class { observe() { calls.ro = true; } } },
    localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } },
    location: { search: '' }, URLSearchParams,
    LightweightCharts: { createChart: (el, o) => { calls.chartOpts = o; return chart; } },
    fetch: async (u) => { calls.fetches.push(u); const v = opts.route(u); return { json: async () => v }; },
    requestAnimationFrame: (cb) => { calls.raf = cb; },
    setInterval: (fn, ms) => { timers.push({ fn, ms, every: true, id: ++tid }); return tid; },
    setTimeout: (fn, ms) => { timers.push({ fn, ms, id: ++tid }); return tid; },
    clearTimeout() {}, clearInterval() {},
    WebSocket: class { constructor(u) { this.url = u; this.readyState = 0; calls.sockets.push(this); } close() { this.readyState = 3; } },
    console, Date, Math, JSON, Number, String, Object, Array, Map, Set, Promise, parseFloat, encodeURIComponent, isFinite, Uint8ClampedArray
  };
  sandbox.ResizeObserver = sandbox.window.ResizeObserver;
  vm.createContext(sandbox);
  return {
    calls, els, get, timers, chart, allSeries, scales, sandbox, poolCbs, pv, tfBtns, store,
    setPayload(p) { payloadRef = p; },
    run() { vm.runInContext(script, sandbox); },
    frame() { if (calls.raf) { const cb = calls.raf; calls.raf = null; cb(); } },
    fireTimeouts(maxMs) { const due = timers.filter(t => !t.every && t.ms <= maxMs); for (const t of due) { timers.splice(timers.indexOf(t), 1); t.fn(); } },
    js(code) { return vm.runInContext(code, sandbox); }
  };
}

(async () => {
  const P = load('payload_auto.json'), PV = load('payload_vol.json'), PO = load('payload_oi.json');
  // datos más variados para los paneles: ΔOI con salidas y liquidaciones en muchas velas (no en las 10 últimas)
  P.doi = P.doi.map(([t, v], i) => [t, i % 3 === 0 ? -v * 0.7 : v]);
  const have = new Set(P.liqs.hist.map(x => x[0]));
  P.liqs.hist = P.liqs.hist.concat(P.candles.slice(0, -10).filter((c, i) => i % 4 === 0 && !have.has(c.time))
    .map((c, i) => [c.time, (i % 5) * 20000 + 3000, (i % 7) * 15000])).sort((a, b) => a[0] - b[0]);
  const VAL = { tf: '5m', models: { auto: { hit: 0.42, base: 0.2, lift: 2.1, prec: 0.3, prec_base: 0.1, prec_lift: 3, events: 150, usd: 3e6, touches: 40, tol_points: 50, coverage_hours: 30, enough: true } },
    calib: { status: 'ok', params: { tiers: [25, 50, 100], half_life_h: null, spike_z: 0, mmr: 0.004 }, tested: 72, events: 150, hours: 30, score: 2, default_score: 2 },
    collectors: { binance: { connected: true }, bybit: { connected: true }, okx: { connected: true }, deribit: { connected: true }, bitmex: { connected: false },
      bitget: { connected: true }, gate: { connected: true }, htx: { connected: false } }, db: { events: 150, hours_listening: 30 } };
  const last = P.candles[P.candles.length - 1].time, step = P.candles[1].time - P.candles[0].time;
  // funding cobrado cada 8 h con los dos signos (la última vela usa el previsto: ctx.funding = 0,01 %)
  const c0 = P.candles[0].time;
  for (const X of [P, PV, PO]) X.funding = [[c0 - 7200, 0.0001], [c0 + 6 * 3600, -0.00005], [c0 + 14 * 3600, 0.0002], [c0 + 22 * 3600, 0.00008]];
  // mapa de liquidez simulado: 60 últimas velas, muro (nivel 7) 300 $ por encima del precio y libro normal alrededor
  const S = 40, k0 = Math.floor(P.price * 0.97 / S), nk = Math.floor(P.price * 1.03 / S) - k0 + 1, kw = Math.floor((P.price + 300) / S) - k0;
  const BM = { S, k0, nk, levels: [1e5, 2e5, 4e5, 8e5, 1.6e6, 3.2e6, 6.4e6], since: P.candles[P.candles.length - 60].time,
    rows: P.candles.map((c, i) => i < P.candles.length - 60 ? null : [kw - 20, '1234321' + '2'.repeat(13) + '7' + '12'.repeat(5)]) };
  let current = P, liveQueue = [], bmOverride;
  const route = (u) => {
    if (u.startsWith('/api/data')) return current && current.candles && /book=1/.test(u) ? Object.assign({}, current, { book_map: bmOverride === undefined ? BM : bmOverride }) : current;
    if (u.startsWith('/api/validate')) return VAL;
    if (u.startsWith('/api/live')) {
      const after = +((u.match(/after=(\d+)/) || [])[1] || 0);
      if (after === 0) return { cursor: P.liqs.rowid, events: [] };
      const evs = liveQueue.filter(e => e[0] > after); liveQueue = [];
      return { cursor: evs.length ? evs[evs.length - 1][0] : after, events: evs };
    }
    return { error: 'ruta no simulada ' + u };
  };
  // ajustes antiguos (v7) con la vista «Limpia» y la paleta fría: deben migrar
  const env = makeEnv({ route, storage: { liq_cfg: JSON.stringify({ ver: 7, view: 'clean', palette: 'cool', minPct: 24, model: 'auto', levs: [25, 50, 100], intensity: 1.5, ex: [], group: null }) } });
  env.setPayload(P);
  env.run();
  await settle();
  const cfg = env.js('cfg');
  if (cfg.view !== 'pro' || 'palette' in cfg || cfg.panes !== true || cfg.intensity !== 1.5 || cfg.ver !== 9 || cfg.paneList.join() !== 'doi,liq,cvd' ||
      cfg.hl !== true || cfg.hlLevs.join() !== '3,5,10,25,50,100' || cfg.map !== 'liq') throw new Error('migración de ajustes: ' + JSON.stringify(cfg));
  ok('ajustes v7 migrados (vista Limpia → Pro, paleta fuera, intensidad conservada, 3 paneles con CVD, capa HL, mapa de liquidaciones)');

  // ── primera carga
  const st = env.get('st').innerHTML, hdr = env.get('hdr').textContent, bias = env.get('bias').innerHTML;
  if (!/estimado<\/span> · 10 fuentes/.test(st) || !/OI \d/.test(st) || !/% 24h\)/.test(st)) throw new Error('estado: ' + st);
  if (!hdr.startsWith('BINANCE:BTCUSDT · 5m · Pools 25X+50X+100X · Total ≥ ')) throw new Error('cabecera: ' + hdr);
  if (!/▲ cortos \d+%/.test(bias) || !/▼ largos \d+%/.test(bias)) throw new Error('sesgo: ' + bias);
  if (!env.calls.fetches[0].includes('bin=0.05')) throw new Error('la vista Pro debe pedir tramos de 0,05 %');
  if (!/&v=2&want=cvd(&|$)/.test(env.calls.fetches[0])) throw new Error('debe pedir solo lo que se ve (CVD sí; burbujas, gasolina y muros no): ' + env.calls.fetches[0]);
  if (!env.timers.some(t => !t.every && t.ms === 60000 && t.fn === env.js('load'))) throw new Error('en 5m se recarga cada 60 s');
  ok('estado (10 fuentes, OI total y 24 h), cabecera Pro y sesgo: ' + bias.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim());
  const imán = /Imán/.test(bias);
  if (env.allSeries.length !== 1) throw new Error('solo debe haber la serie de velas (paneles en lienzo)');
  const mo = env.chart.main.opts;
  if (mo.lastValueVisible !== false || mo.priceLineVisible !== false) throw new Error('etiqueta de precio duplicada en el eje');
  const nD = env.js('doiMap.size'), nL = env.js('liqHist.size');
  if (nD !== P.doi.length || nL !== P.liqs.hist.length) throw new Error('paneles sin datos');
  if (!env.js('[...liqHist.values()].every(v => v[0] >= 0 && v[1] >= 0)')) throw new Error('liquidaciones negativas');
  const sm = env.chart.main.scale.scaleMargins;
  if (Math.abs(sm.bottom - 0.50) > 1e-9 || sm.top !== 0.06) throw new Error('las velas deben dejar sitio a los paneles: ' + JSON.stringify(sm));
  ok(`paneles: ${nD} velas con ΔOI y ${nL} con liquidaciones; una sola etiqueta de precio`);

  // ── autoscale incluye las zonas
  const ap = env.chart.main.opts.autoscaleInfoProvider;
  const zr = env.js('zoneRange');
  const res = ap(() => ({ priceRange: { minValue: P.price - 10, maxValue: P.price + 10 }, margins: { above: 0, below: 0 } }));
  if (!zr || res.priceRange.minValue > zr[0] || res.priceRange.maxValue < zr[1]) throw new Error('encuadre de zonas: ' + JSON.stringify([zr, res]));
  ok(`encuadre automático incluye las zonas fuertes (${Math.round(zr[0])}–${Math.round(zr[1])})` + (imán ? ' · imán visible' : ''));

  // ── dibujo con recorte a la zona de precio + marcos de paneles
  env.frame();
  const h = 700, ph = h - 28, yMax = ph * (1 - 0.48), hh = (ph - yMax) / 3, yB = yMax + hh, yC = yMax + 2 * hh, xMax = 420 - 62;
  if (env.calls.fill < 50) throw new Error('no dibuja el mapa: ' + env.calls.fill);
  if (!env.calls.clips.some(x => Math.abs(x - yMax) < 0.01)) throw new Error('falta el recorte de los paneles: ' + env.calls.clips.slice(0, 4));
  if (!env.calls.texts.some(t => t.startsWith('Δ Open Interest')) || !env.calls.texts.some(t => t.startsWith('Liquidaciones reales')) ||
      !env.calls.texts.some(t => t.startsWith('CVD'))) throw new Error('rótulos de paneles: ' + env.calls.texts.filter(t => /^(Δ|Liq|CVD)/.test(t)));
  ok(`mapa dibujado (${env.calls.fill} rectángulos) sin invadir los paneles (recorte a ${Math.round(yMax)} px)`);
  const COL = { doiUp: 'rgba(38,166,154,0.9)', doiDn: 'rgba(239,83,80,0.9)', liqS: 'rgba(46,196,255,0.95)', liqL: 'rgba(255,159,28,0.95)' };
  const bars = (c) => env.calls.rects.filter(r => r[4] === c);
  for (const [k, c] of Object.entries(COL)) {
    const b = bars(c);
    if (!b.length) throw new Error('panel sin barras: ' + k);
    const [lo, hi] = k.startsWith('doi') ? [yMax, yB] : [yB, yC];
    const bad = b.filter(r => r[1] < lo || r[1] + r[3] > hi + 0.01 || r[0] < 0 || r[0] + r[2] > xMax + 0.01 || (r[2] < 1.4 && Math.abs(r[0] + r[2] - xMax) > 0.01) || r[3] < 1.5);
    if (bad.length) throw new Error('barras fuera de su panel: ' + k + ' ' + JSON.stringify(bad.slice(0, 3)) + ' de ' + b.length + ' · panel ' + [lo, hi]);
  }
  const titles = env.calls.textPos.filter(([t]) => /^(Δ|Liq|CVD)/.test(t));
  if (titles.length !== 3 || titles.some(([t]) => t.length * 6 > xMax - 14)) throw new Error('títulos de paneles que pisan el eje: ' + titles.map(x => x[0]));
  if (titles.some(([, , y], i) => Math.abs(y - ([yMax, yB, yC][i] + 13)) > 0.01)) throw new Error('cada título en su panel: ' + titles.map(x => x[2]));
  const cvdLines = env.calls.strokes.filter(x => x[0] === '#e8e8e8' || x[0] === '#b388ff');
  if (cvdLines.length !== 2 || cvdLines.some(x => x[1] < 50)) throw new Error('líneas de CVD: ' + JSON.stringify(cvdLines));
  const maxes = env.calls.textPos.filter(([t]) => /^máx /.test(t));
  if (maxes.length !== 2 || maxes.some(([, x]) => x !== xMax + 4)) throw new Error('máximos de los paneles: ' + JSON.stringify(maxes));
  ok(`paneles dibujados: ${Object.values(COL).map(c => bars(c).length).join('/')} barras (ΔOI ▲▼ · liq ▲▼) y 2 líneas de CVD; títulos «${titles.map(x => x[0]).join('», «')}»`);

  // ── escala log: una cascada enorme no aplasta las barras normales
  const spikeT = P.candles[P.candles.length - 30].time, prevV = env.js(`liqHist.get(${spikeT})`);
  env.js(`liqHist.set(${spikeT}, [5e8, ${prevV ? prevV[1] : 0}]); version++;`);
  env.calls.rects.length = 0; env.frame();
  const hL = bars(COL.liqL).map(r => r[3]).sort((a, b) => a - b);
  const ratio = hL[Math.floor(hL.length / 2)] / hL[hL.length - 1];
  if (!(ratio > 0.15)) throw new Error('con una cascada de 500M la barra mediana es invisible: ' + ratio.toFixed(3));
  if (!env.calls.textPos.some(([t]) => t === 'máx 500.0M')) throw new Error('máximo del panel con la cascada');
  env.js(prevV ? `liqHist.set(${spikeT}, [${prevV[0]}, ${prevV[1]}]); version++;` : `liqHist.delete(${spikeT}); version++;`);
  ok(`con una cascada de 500M la barra mediana mide el ${Math.round(ratio * 100)} % de la más alta (antes < 1 %)`);

  // ── velas finas (< 3 px): columnas de 3 px que suman varias velas; velas anchas: una barra por vela
  const wide = bars(COL.liqL);
  if (wide.some(r => Math.abs(r[2] - 2.8) > 1e-9 && r[0] + r[2] < xMax - 0.01)) throw new Error('ancho de barra por vela: ' + wide[0]);
  const tsOpts = env.chart.timeScale().options;
  env.chart.timeScale().options = () => ({ barSpacing: 1.2 });
  env.calls.rects.length = 0; env.frame();
  const thin = bars(COL.liqL);
  const xOf = (t) => 360 + (t - last) / step * 0.4;   // como el eje simulado; lo que pasa de xMax no se pinta
  const nCols = new Set(P.liqs.hist.filter(x => x[1] > 0 && xOf(x[0]) <= xMax).map(x => Math.floor(xOf(x[0]) / 3))).size;
  if (thin.some(r => r[0] % 3 !== 0 || Math.abs(r[2] - 2.4) > 1e-9 && r[0] + r[2] < xMax - 0.01) || thin.length !== nCols) {
    const drawn = new Set(thin.map(r => r[0] / 3));
    const want = [...new Set(P.liqs.hist.filter(x => x[1] > 0 && xOf(x[0]) <= xMax).map(x => Math.floor(xOf(x[0]) / 3)))];
    throw new Error(`columnas de 3 px: ${thin.length} vs ${nCols} · faltan ${want.filter(k => !drawn.has(k))} · sobran ${[...drawn].filter(k => !want.includes(k))} · liqHist ${env.js('liqHist.size')} vs ${P.liqs.hist.length}`);
  }
  env.chart.timeScale().options = tsOpts;
  env.calls.rects.length = 0; env.frame();
  ok(`velas finas: ${thin.length} columnas de 3 px (suman ~7 velas cada una); velas anchas: una barra por vela`);

  // ── ancho: en escritorio el título largo cabe
  env.get('wrap').clientWidth = 1400; env.calls.textPos.length = 0; env.frame();
  if (!env.calls.textPos.some(([t]) => t.startsWith('Liquidaciones reales (8 exchanges) · escala log'))) throw new Error('título largo en escritorio');
  if (!env.calls.textPos.some(([t]) => /^CVD \(compras − ventas a mercado\) · blanco: futuros [+−]\S+ · violeta: spot [+−]\S+$/.test(t))) throw new Error('título CVD en escritorio: ' + env.calls.textPos.map(x => x[0]).filter(t => /^CVD/.test(t)));
  env.get('wrap').clientWidth = 420; env.frame();
  ok('títulos: completos en escritorio y cortos en móvil');

  // ── feed en directo: cursor inicial, evento grande (pulso + aviso), pequeño, antiguo y repetido
  if (env.js('liveCursor') !== P.liqs.rowid) throw new Error('cursor inicial');
  const r0 = P.liqs.rowid;
  liveQueue = [
    [r0 + 1, (last + 10) * 1000, 'bybit', 1, P.price - 100, 180000],       // dentro de la zona de precio (con 3 paneles)
    [r0 + 2, (last + 11) * 1000, 'binance', 1, P.price - 110, 120000],
    [r0 + 3, (last + 12) * 1000, 'okx', -1, P.price + 200, 3000],
    [r0 + 4, (last - 5 * step + 7) * 1000, 'deribit', -1, P.price + 150, 9000]
  ];
  const nItems = env.js('data.liqs.items.length');
  await env.js('pollLive()'); await settle();
  const hv = (t) => env.js(`liqHist.get(${t}) || [0, 0]`);
  const base = (P.liqs.hist.find(x => x[0] === last) || [0, 0, 0]);
  const oldBase = (P.liqs.hist.find(x => x[0] === last - 5 * step) || [0, 0, 0]);
  if (Math.round(hv(last)[0]) !== base[1] + 300000) throw new Error('largos en directo: ' + JSON.stringify([hv(last), base]));
  if (Math.round(hv(last)[1]) !== base[2] + 3000) throw new Error('cortos en directo: ' + JSON.stringify(hv(last)));
  if (Math.round(hv(last - 5 * step)[1]) !== oldBase[2] + 9000) throw new Error('evento de una vela anterior no se suma');
  if (env.js('data.liqs.items.length') !== nItems + 3) throw new Error('burbujas en directo (≥ 5.000 $)');
  const pulses = env.get('wrap').children.filter(c => /pulse/.test(c.className));
  if (pulses.length !== 2) throw new Error('pulsos: ' + pulses.length);
  env.fireTimeouts(2500);
  const toasts = env.get('toasts').children;
  if (toasts.length !== 1 || !/300K/.test(toasts[0].innerHTML) || !/largos/.test(toasts[0].innerHTML) || !/2 órdenes · Bybit, Binance/.test(toasts[0].innerHTML)) throw new Error('aviso: ' + (toasts[0] && toasts[0].innerHTML));
  ok('directo: barras por vela, burbujas, 2 pulsos y 1 aviso «⚡ 300K en largos · 2 órdenes · Bybit, Binance»');

  // ── recarga: lo llegado en directo se vuelve a aplicar sin duplicar ni repetir avisos
  current = JSON.parse(JSON.stringify(P));
  await env.js('load()'); await settle();
  if (Math.round(hv(last)[0]) !== base[1] + 300000) throw new Error('recarga: barra duplicada o perdida ' + hv(last)[0]);
  env.fireTimeouts(2500);
  if (env.get('toasts').children.length !== 1) throw new Error('la recarga no debe repetir avisos');
  current = JSON.parse(JSON.stringify(P)); current.liqs.rowid = r0 + 4;   // el servidor ya incluye esos eventos
  current.liqs.hist = current.liqs.hist.map(x => x[0] === last ? [x[0], x[1] + 300000, x[2] + 3000] : x);
  await env.js('load()'); await settle();
  if (Math.round(hv(last)[0]) !== base[1] + 300000) throw new Error('recarga con eventos incluidos: doble conteo');
  ok('recarga: los eventos en directo se reaplican una sola vez');

  // ── lectura del cursor (pools + paneles)
  const yOf = (p) => 300 - (p - P.price) * (300 / (P.price * 0.02));
  const below = P.heat.active.filter(a => a[0] < P.price && a[1] > 0).sort((a, b) => b[0] - a[0])[0];   // el más cercano
  env.get('bPanes').onclick();                        // con 3 paneles ese nivel cae debajo: se mira sin paneles
  env.calls.crosshair({ point: { x: 100, y: yOf(below[0]) }, time: last });
  if (!/^Si llega aquí: ≈.+ largos$/.test(env.get('pCum').textContent)) throw new Error('acumulado: ' + env.get('pCum').textContent);
  if (!/ΔOI [+−]/.test(env.get('pAt').textContent) || !/liquidado: largos/.test(env.get('pAt').textContent)) throw new Error('lectura de paneles: ' + env.get('pAt').textContent);
  env.get('bPanes').onclick();
  env.calls.crosshair({ point: { x: 100, y: 690 }, time: last });
  if (env.get('pTot').textContent !== '–') throw new Error('en los paneles no debe leer precio');
  ok('cursor: «' + env.get('pAt').textContent + '»');

  // ── capas: Paneles, Zonas, Liq, Asia; vista Completa; temporalidad; WebSocket sin forceOrder
  env.get('bPanes').onclick();
  if (env.chart.main.scale.scaleMargins.bottom !== 0.08) throw new Error('ocultar paneles');
  env.calls.clips.length = 0; env.calls.textPos.length = 0; env.frame();
  if (!env.calls.clips.includes(700)) throw new Error('sin paneles el mapa usa todo el alto');
  if (env.calls.textPos.some(([t]) => /^(Δ|Liq|máx)/.test(t))) throw new Error('paneles ocultos pero dibujados');
  env.get('bPanes').onclick();
  env.get('bZones').onclick();
  if (!env.calls.titles.some(t => /^(largos|cortos) ≈.+ pts$/.test(String(t)))) throw new Error('rótulos de zonas');
  if (env.get('zones').style.display !== 'block') throw new Error('panel de zonas');
  const a0 = env.calls.arcs, nfL = env.calls.fetches.length; env.get('bLiq').onclick(); env.frame();
  if (env.calls.arcs <= a0) throw new Error('burbujas');
  if (!env.calls.fetches.slice(nfL).some(u => /want=[^&]*liq/.test(u))) throw new Error('al encender Liq se piden las burbujas');
  await settle();
  env.get('bAsia').onclick();
  const viewChips = env.get('cView').kids;
  if (viewChips.length !== 2 || viewChips.map(c => c.textContent).join() !== 'Pro,Completa') throw new Error('vistas: ' + viewChips.map(c => c.textContent));
  const nf = env.calls.fetches.length;
  viewChips[1].onclick(); await settle();
  if (env.calls.fetches.slice(nf).find(u => u.startsWith('/api/data')).includes('bin=')) throw new Error('la vista Completa no pide tramos de Pro');
  env.frame();
  ok('capas Paneles/Zonas/Liq/Asia y vista Completa');
  const ns = env.calls.sockets.length;
  env.tfBtns[2].onclick(); await settle();
  const sock = env.calls.sockets[env.calls.sockets.length - 1];
  if (env.calls.sockets.length !== ns + 1 || !sock.url.endsWith('@aggTrade/btcusdt@kline_1h') || sock.url.includes('forceOrder')) throw new Error('WebSocket: ' + sock.url);
  sock.readyState = 1; sock.onopen();
  const lc = P.candles[P.candles.length - 1];
  sock.onmessage({ data: JSON.stringify({ data: { e: 'kline', k: { t: lc.time * 1000, i: '1h', o: '1', h: '2', l: '0.5', c: '1.5' } } }) });
  sock.onmessage({ data: JSON.stringify({ data: { e: 'aggTrade', p: String(lc.close + 20), T: (lc.time + 5) * 1000 } }) });
  if (!/price-live/.test(env.get('st').innerHTML)) throw new Error('LIVE');
  if (!env.timers.some(t => !t.every && t.ms === 120000 && t.fn === env.js('load'))) throw new Error('en 1h se recarga cada 120 s');
  ok('temporalidad 1h: WebSocket solo con precio y velas (las liquidaciones vienen del servidor) y recarga cada 2 min');

  // ── modelo Volumen y modelo OI con exchange elegido
  current = PV; await env.js('load()'); await settle();
  if (!/volumen/.test(env.get('st').innerHTML) || env.js('doiMap.size') !== 0) throw new Error('modelo Volumen');
  env.frame();
  if (!env.calls.texts.some(t => /no disponible con el modelo Volumen/.test(t))) throw new Error('aviso ΔOI con Volumen');
  current = PO; await env.js('load()'); await settle();
  ok('modelos Volumen y Open Interest por exchange');

  // ── Grupo: aviso cuando las líneas quedan finísimas
  env.get('cView').kids[0].onclick(); await settle();   // de vuelta a la vista Pro (la que usa el Grupo)
  if (env.js('cfg.view') !== 'pro') throw new Error('volver a Pro');
  const pgEl = env.get('pGroup');
  pgEl.value = '1'; pgEl.onchange({ target: pgEl });
  if (!pgEl.classList.contains('low') || !/Grupo muy bajo/.test(pgEl.title)) throw new Error('Grupo 1 sin aviso');
  if (!env.calls.fetches[env.calls.fetches.length - 1].includes('bin=0.0100')) throw new Error('Grupo 1: tramo ' + env.calls.fetches[env.calls.fetches.length - 1]);
  await settle();
  pgEl.value = '8'; pgEl.onchange({ target: pgEl });
  if (pgEl.classList.contains('low')) throw new Error('Grupo 8 no debe avisar');
  await settle();
  pgEl.value = ''; pgEl.onchange({ target: pgEl });
  if (pgEl.classList.contains('low') || !/^auto \d/.test(pgEl.placeholder)) throw new Error('Grupo automático');
  await settle();
  ok('Grupo: aviso amarillo con 1, sin aviso con 8 o automático (' + pgEl.placeholder + ')');

  // ── v2: mapa de liquidez (libro de órdenes en el tiempo)
  const mapChips = env.get('cMap').kids;
  if (mapChips.map(c => c.textContent).join() !== 'Liquidaciones,Liquidez (libro),Las dos' || env.js('cfg.map') !== 'liq') throw new Error('selector de mapa');
  if (env.calls.fetches.some(u => /book=1/.test(u))) throw new Error('sin el mapa de liquidez no se pide el libro');
  let nf2 = env.calls.fetches.length;
  mapChips[1].onclick(); await settle();
  const fb = env.calls.fetches.slice(nf2).find(u => u.startsWith('/api/data'));
  if (!fb || !/book=1/.test(fb) || env.js('cfg.map') !== 'book') throw new Error('modo libro: ' + fb);
  if (env.get('bias').innerHTML !== '' || env.js('zoneRange') !== null) throw new Error('con solo el libro no van el sesgo ni el encuadre de zonas de liquidación');
  const img = env.calls.putImages[env.calls.putImages.length - 1];
  const bi = env.js('({n: bookImg.n, nk: bookImg.nk, last: bookImg.lastRow, S: bookImg.S})');
  if (!img || img.width !== P.candles.length || img.height !== bi.nk || bi.last !== P.candles.length - 1) throw new Error('imagen del libro: ' + JSON.stringify(bi));
  const wallPx = ((bi.nk - 1 - kw) * bi.n + (P.candles.length - 1)) * 4;
  if (img.data[wallPx] !== 255 || img.data[wallPx + 3] !== 255) throw new Error('el muro (nivel 7) debe ser blanco');
  const lowPx = ((bi.nk - 1 - (kw + 1)) * bi.n + (P.candles.length - 1)) * 4;     // justo encima del muro, nivel 1: azul oscuro y transparente
  if (img.data[lowPx + 2] !== 96 || img.data[lowPx + 3] !== Math.round(0.30 * 255)) throw new Error('nivel 1 del libro: ' + Array.from(img.data.slice(lowPx, lowPx + 4)));
  const emptyPx = ((bi.nk - 1 - kw) * bi.n + 5) * 4;
  if (img.data[emptyPx + 3] !== 0) throw new Error('velas sin libro deben quedar vacías');
  env.js("var __heatDraws = 0; (function () { const f = drawTime; drawTime = function (...a) { __heatDraws++; return f.apply(this, a); }; })();");
  env.get('wrap').clientWidth = 1400;                                  // con sitio a la derecha: el libro de ahora se alarga
  env.calls.images.length = 0; env.calls.rects.length = 0; env.frame();
  const dImg = env.calls.images;
  if (dImg.length !== 2 || dImg[0][0] !== 1 || dImg[0][1] !== false || dImg[1][0] !== 0.6 || dImg[1][2] !== bi.last || dImg[1][4] !== 1) throw new Error('dibujo del libro: ' + JSON.stringify(dImg));
  const xa = 360 - (P.candles.length - 1) * 0.4;                       // eje de tiempo simulado: 0,4 px por vela
  if (Math.abs(dImg[0][6] - (xa - 0.2)) > 1e-6 || Math.abs(dImg[0][8] - P.candles.length * 0.4) > 1e-6) throw new Error('el libro no cuadra con las velas: ' + dImg[0]);
  const yTopB = 300 - ((k0 + nk) * S - P.price) * (300 / (P.price * 0.02));
  if (Math.abs(dImg[0][7] - yTopB) > 1e-6) throw new Error('el libro no cuadra con el precio: ' + dImg[0][7] + ' vs ' + yTopB);
  env.get('wrap').clientWidth = 420;
  if (env.calls.rects.some(r => ['#008f8f', '#0fbf0f', '#f0e60a', '#e8100a'].includes(r[4])) || env.js('__heatDraws')) throw new Error('en modo libro no se pintan liquidaciones');
  if (!/Mapa de liquidez · libro de órdenes · tramos de 40 \$ · desde /.test(env.get('hdr').textContent)) throw new Error('cabecera libro: ' + env.get('hdr').textContent);
  const wallY = 300 - ((k0 + kw + 0.5) * S - P.price) * (300 / (P.price * 0.02));
  env.calls.crosshair({ point: { x: 200, y: wallY }, time: last });
  if (env.get('pBook').textContent !== 'Libro ≈ 6.4M+') throw new Error('lectura del libro: ' + env.get('pBook').textContent);
  const lowY = 300 - ((k0 + kw + 1 + 0.5) * S - P.price) * (300 / (P.price * 0.02));
  env.calls.crosshair({ point: { x: 200, y: lowY }, time: last });
  if (env.get('pBook').textContent !== 'Libro ≈ 100K–200K') throw new Error('lectura del nivel 1: ' + env.get('pBook').textContent);
  env.calls.crosshair({ point: { x: 200, y: wallY }, time: P.candles[10].time });
  if (env.get('pBook').textContent !== '') throw new Error('sin libro en esa vela');
  env.get('cMap').kids[2].onclick(); await settle();
  env.js('__heatDraws = 0');
  env.calls.images.length = 0; env.calls.rects.length = 0; env.frame();
  if (env.calls.images[0][0] !== 0.8 || !env.js('__heatDraws')) throw new Error('modo «las dos»: libro con transparencia y liquidaciones encima');
  if (!/· \+ libro de órdenes/.test(env.get('hdr').textContent)) throw new Error('cabecera las dos: ' + env.get('hdr').textContent);
  // sin libro grabado aún (el servidor da null) o con error: aviso en la cabecera y nada dibujado
  bmOverride = null; await env.js('load()'); await settle();
  env.calls.images.length = 0; env.frame();
  if (env.calls.images.length || !/· \+ libro: grabando \(aún sin datos\)$/.test(env.get('hdr').textContent)) throw new Error('libro sin datos: ' + env.get('hdr').textContent);
  bmOverride = { error: 'fallo' }; env.get('cMap').kids[1].onclick(); await settle(); await env.js('load()'); await settle();
  if (!/Mapa de liquidez · libro: error$/.test(env.get('hdr').textContent)) throw new Error('libro con error: ' + env.get('hdr').textContent);
  bmOverride = undefined;
  env.get('cMap').kids[0].onclick(); await settle();
  env.calls.images.length = 0; env.frame();
  if (env.calls.images.length) throw new Error('en modo liquidaciones no se pinta el libro');
  env.calls.crosshair({ point: { x: 200, y: wallY }, time: last });
  if (env.get('pBook').textContent !== '') throw new Error('en modo liquidaciones no se lee el libro');
  if (!/cortos \d+%/.test(env.get('bias').innerHTML) || !env.js('zoneRange')) throw new Error('de vuelta a liquidaciones: sesgo y encuadre');
  ok(`mapa de liquidez: imagen ${bi.n}×${bi.nk} (velas × tramos de ${bi.S} $) alineada con velas y precio, muro en blanco, modos libro / las dos / liquidaciones y lectura «Libro ≈ 6.4M+»`);

  // ── v2: paneles elegibles (hasta 3) y CVD en directo
  const paneChips = () => env.get('cPanes').kids;
  if (paneChips().map(c => c.textContent).join() !== 'ΔOI,Liquidaciones,CVD,Funding,Gasolina') throw new Error('chips de paneles: ' + paneChips().map(c => c.textContent));
  paneChips()[2].onclick();                                      // fuera el CVD: 2 paneles
  if (env.js('paneList().join()') !== 'doi,liq' || Math.abs(env.chart.main.scale.scaleMargins.bottom - 0.38) > 1e-9) throw new Error('2 paneles');
  env.calls.strokes.length = 0; env.frame();
  if (env.calls.strokes.some(x => x[0] === '#e8e8e8')) throw new Error('CVD apagado pero dibujado');
  paneChips()[0].onclick(); paneChips()[1].onclick();           // sin ninguno: el mapa usa todo el alto
  if (env.js('paneList().length') !== 0 || env.chart.main.scale.scaleMargins.bottom !== 0.08) throw new Error('sin paneles');
  paneChips()[2].onclick();                                      // solo CVD
  if (env.js('paneList().join()') !== 'cvd' || Math.abs(env.chart.main.scale.scaleMargins.bottom - 0.24) > 1e-9) throw new Error('1 panel');
  paneChips()[0].onclick(); paneChips()[1].onclick();            // los 3, siempre en el mismo orden
  if (env.js('paneList().join()') !== 'doi,liq,cvd') throw new Error('orden de paneles: ' + env.js('paneList().join()'));
  // CVD en directo: el mensaje de vela trae compras a mercado (Q) y volumen (q) en USD
  const lastI = P.candles.length - 1, before = env.js(`cvdCum[0][${lastI}]`), d0 = env.js(`(cvdD.get(${last}) || [0])[0]`);
  const s2 = env.calls.sockets[env.calls.sockets.length - 1];
  s2.readyState = 1; s2.onopen();
  s2.onmessage({ data: JSON.stringify({ data: { e: 'kline', k: { t: last * 1000, i: env.js('tf'), o: '1', h: '2', l: '0.5', c: '1.5', q: '3000000', Q: '2000000' } } }) });
  const after = env.js(`cvdCum[0][${lastI}]`);
  if (Math.round(after - before) !== Math.round(1000000 - d0)) throw new Error('CVD en directo: ' + [before, after, d0]);
  env.calls.crosshair({ point: { x: 100, y: 690 }, time: last });
  const ro = env.get('pAt').textContent;
  if (!/delta: fut\. \+1\.0M · spot [+−]/.test(ro)) throw new Error('lectura del CVD: ' + ro);
  ok('paneles: elegibles en ⚙ (0 a 3, siempre ΔOI · liquidaciones · CVD) y CVD en directo con la vela de Binance: «' + ro + '»');

  // ── v2: funding y gasolina (como mucho 3 paneles: al añadir uno sale el más antiguo)
  paneChips()[3].onclick();                                       // + Funding: sale el CVD, el primero que se puso
  if (env.js('paneList().join()') !== 'doi,liq,fund' || paneChips()[2].classList.contains('on') || !paneChips()[3].classList.contains('on'))
    throw new Error('máximo 3: ' + env.js('cfg.paneList.join()'));
  env.calls.rects.length = 0; env.calls.textPos.length = 0; env.frame();
  const FUP = 'rgba(255,159,28,0.85)', FDN = 'rgba(46,196,255,0.85)';
  const geoF = env.js('paneGeom()').panes[2];
  const fUp = env.calls.rects.filter(r => r[4] === FUP), fDn = env.calls.rects.filter(r => r[4] === FDN);
  if (!fUp.length || !fDn.length) throw new Error('funding: barras ▲ ' + fUp.length + ' ▼ ' + fDn.length);
  if ([...fUp, ...fDn].some(r => r[1] < geoF.y0 || r[1] + r[3] > geoF.y1 + 0.01)) throw new Error('funding fuera de su panel');
  const zeroF = Math.round((geoF.y0 + 18 + geoF.y1 - 3) / 2), halfF = (geoF.y1 - 3 - geoF.y0 - 18) / 2;
  const tallUp = Math.max(...fUp.map(r => r[3])), tallDn = Math.max(...fDn.map(r => r[3]));
  if (Math.abs(tallUp - halfF) > 0.01 || Math.abs(tallDn - halfF * 0.25) > 0.01) throw new Error('funding en escala normal (0,02 % llena, −0,005 % un cuarto): ' + [tallUp, tallDn, halfF]);
  if (!env.calls.textPos.some(([t]) => /^Funding .*· ahora 0\.0100 %$/.test(t)) || !env.calls.textPos.some(([t]) => t === 'máx 0.020%'))
    throw new Error('títulos del funding: ' + env.calls.textPos.map(x => x[0]).filter(t => /Funding|máx/.test(t)));
  // velas finas: la columna es la media del funding, no la suma
  env.chart.timeScale().options = () => ({ barSpacing: 1.2 });
  env.calls.textPos.length = 0; env.frame();
  if (!env.calls.textPos.some(([t]) => t === 'máx 0.020%')) throw new Error('funding con velas finas: ' + env.calls.textPos.map(x => x[0]).filter(t => /máx/.test(t)));
  env.chart.timeScale().options = tsOpts;
  const nfF = env.calls.fetches.length;
  paneChips()[4].onclick();                                       // + Gasolina: sale ΔOI
  if (!env.calls.fetches.slice(nfF).some(u => /want=[^&]*fuel/.test(u))) throw new Error('al poner Gasolina se pide su dato');
  await settle();
  if (env.js('paneList().join()') !== 'liq,fund,fuel') throw new Error('gasolina: ' + env.js('cfg.paneList.join()'));
  env.calls.fills.length = 0; env.calls.textPos.length = 0; env.frame();
  // (las burbujas usan los mismos colores, pero son arcos sin puntos de línea)
  const fuelFills = env.calls.fills.filter(x => (x[0] === 'rgba(46,196,255,0.55)' || x[0] === 'rgba(255,159,28,0.55)') && x[1] > 0);
  const nVis = env.js('visibleIdx(358).length');
  if (fuelFills.length !== 2 || fuelFills.some(x => x[1] !== nVis + 2)) throw new Error('gasolina: áreas ' + JSON.stringify(fuelFills) + ' para ' + nVis + ' velas');
  const FU = env.js('data.fuel'), fz = FU[FU.length - 1];
  const fuelTitle = env.calls.textPos.find(([t]) => /^Gasolina/.test(t));
  if (!fuelTitle || !fuelTitle[0].endsWith(`ahora ▲ ${env.js(`fmtM(${fz[2]})`)} ▼ ${env.js(`fmtM(${fz[1]})`)} (${Math.round(100 * fz[1] / (fz[1] + fz[2]))} % largos)`))
    throw new Error('título de la gasolina: ' + (fuelTitle && fuelTitle[0]));
  env.calls.crosshair({ point: { x: 100, y: 690 }, time: P.candles[30].time });     // 2,5 h después de la primera vela: periodo de −0,005 %
  const ro2 = env.get('pAt').textContent;
  const f150 = FU.find(x => x[0] === P.candles[30].time);
  if (!/funding -0\.0050 %/.test(ro2) || !ro2.includes(`gasolina ▲ ${env.js(`fmtM(${f150[2]})`)} ▼ ${env.js(`fmtM(${f150[1]})`)}`) || /delta/.test(ro2))
    throw new Error('lectura de paneles nuevos: ' + ro2);
  env.calls.crosshair({ point: { x: 100, y: 690 }, time: last });
  if (!/funding 0\.0100 %/.test(env.get('pAt').textContent)) throw new Error('funding previsto en la vela abierta: ' + env.get('pAt').textContent);
  paneChips()[3].onclick(); paneChips()[4].onclick(); paneChips()[0].onclick(); paneChips()[2].onclick();
  if (env.js('paneList().join()') !== 'doi,liq,cvd') throw new Error('volver a ΔOI + liquidaciones + CVD: ' + env.js('cfg.paneList.join()'));
  ok('paneles nuevos: funding (barras en escala normal, media en velas finas, «ahora 0.0100 %») y gasolina (2 áreas); lectura «' + ro2 + '»');

  // ── respuesta compacta (velas en listas) y una respuesta vieja que llega tarde no pisa a la nueva
  const PC = JSON.parse(JSON.stringify(current));
  PC.candles = PC.candles.map(c => [c.time, c.open, c.high, c.low, c.close]);
  current = PC; await env.js('load()'); await settle();
  const c5 = env.js('data.candles[5]');
  if (c5.time !== P.candles[5].time || c5.close !== P.candles[5].close || c5.high !== P.candles[5].high || env.chart.main.data[5].open !== P.candles[5].open)
    throw new Error('velas en listas: ' + JSON.stringify(c5));
  const PA = JSON.parse(JSON.stringify(P)), PB = JSON.parse(JSON.stringify(P));
  PA.price = 11111; PB.price = 22222;
  let release; current = new Promise(r => { release = r; });
  const slow = env.js('load()');
  current = PB; await env.js('load()');
  release(PA); await slow; await settle();
  if (env.js('data.price') !== 22222) throw new Error('una respuesta vieja pisó a la nueva: ' + env.js('data.price'));
  current = P; await env.js('load()'); await settle();
  ok('respuesta compacta: velas en listas convertidas y una respuesta que llega tarde no pisa a la más nueva');

  // ── v2: Hyperliquid real (capa con las posiciones más grandes y modelo propio)
  const PH = JSON.parse(JSON.stringify(P));
  PH.hl = { positions: 37, tracked: 900, long_usd: 9e7, short_usd: 6e7, long_pct: 0.6, coverage: 0.42, top: [
    [P.price * 0.998, 1, 2.4e6, 40, P.price], [P.price * 1.004, -1, 1.1e6, 20, P.price * 0.99], [P.price * 1.0042, -1, 9e5, 25, P.price],
    [P.price * 0.95, 1, 5e4, 3, P.price], [P.price * 0.97, 1, 3e6, 10, P.price]] };          // el de 0,97 cae en los paneles: no se pinta
  current = PH; await env.js('load()'); await settle();
  env.calls.textPos.length = 0; env.calls.strokes.length = 0; env.frame();
  const hlTxt = env.calls.textPos.filter(([t]) => /^HL /.test(t)).map(x => x[0]);
  if (hlTxt.sort().join('|') !== 'HL corto 1.1M · 20x|HL largo 2.4M · 40x') throw new Error('rótulos HL: ' + hlTxt);   // el de 900K va pegado: solo línea; el de 50K no
  const hlLines = env.calls.strokes.filter(x => (x[0] === '#ff9f1c' || x[0] === '#2ec4ff') && x[1] === 2);   // las burbujas también usan esos colores (arcos)
  if (hlLines.length !== 3) throw new Error('líneas HL: ' + JSON.stringify(hlLines));
  env.get('bHl').onclick(); env.calls.textPos.length = 0; env.frame();
  if (env.calls.textPos.some(([t]) => /^HL /.test(t)) || env.js('cfg.hl') !== false) throw new Error('capa HL apagada');
  env.get('bHl').onclick();
  // modelo real: cabecera, estado y pools que filtran sus posiciones (sin pasar al modelo OI)
  const PR = JSON.parse(JSON.stringify(PH));
  Object.assign(PR, { model: 'hl', used: ['hyperliquid_real'], levs: [3, 5, 10, 25, 50, 100], calib: null });
  current = PR;
  const mchips = env.get('cModel').kids;
  if (mchips.map(c => c.textContent).join() !== 'Auto (calibrado),Open Interest,Volumen,Hyperliquid real') throw new Error('modelos: ' + mchips.map(c => c.textContent));
  let nf3 = env.calls.fetches.length;
  mchips[3].onclick(); await settle();
  if (!env.calls.fetches.slice(nf3).some(u => /model=hl&lev=3,5,10,25,50,100/.test(u))) throw new Error('petición del modelo real: ' + env.calls.fetches.slice(nf3));
  if (!/Hyperliquid real \(37 posiciones\)$/.test(env.get('hdr').textContent) || !/real · Hyperliquid<\/span> · 37 posiciones/.test(env.get('st').innerHTML)) throw new Error('cabecera/estado real: ' + env.get('hdr').textContent + ' | ' + env.get('st').innerHTML);
  const hlFuelT = env.js('PANE_DRAW.fuel(500, 600, 358, 4).titles[0]');
  if (!/^Gasolina real de Hyperliquid \(posiciones por liquidar\)/.test(hlFuelT)) throw new Error('gasolina con el modelo real: ' + hlFuelT);
  nf3 = env.calls.fetches.length;
  env.poolCbs[0].checked = false; env.poolCbs[0].onchange(); await settle();
  if (env.js('cfg.model') !== 'hl' || env.js('cfg.hlLevs.join()') !== '5,10,25,50,100' || env.js('cfg.levs.join()') !== '25,50,100') throw new Error('pools en el modelo real');
  if (!env.calls.fetches.slice(nf3).some(u => /model=hl&lev=5,10,25,50,100/.test(u))) throw new Error('petición con pools del modelo real');
  ok('Hyperliquid: 3 líneas reales (rótulos sin solaparse), capa HL apagable, modelo real con cabecera y pools propios');

  // ── v2: Coinalyze (fuentes con nombre, «otros», cita con enlace y panel con sus mercados)
  const PZ = JSON.parse(JSON.stringify(P));
  Object.assign(PZ, { labels: { cz1: 'Gate BTC_USDT', cz2: 'Kraken PF_XBTUSD', hyperliquid: 'Hyperliquid BTC' }, credits: ['coinalyze'],
    used: P.used.concat(['cz1', 'cz2']), exchanges: P.exchanges.includes('otros') ? P.exchanges : P.exchanges.concat(['otros']) });
  PZ.liqs.cz_markets = 3;
  current = PZ; env.js("cfg.model = 'auto'"); await env.js('load()'); await settle();
  if (!/<a href="https:\/\/coinalyze\.net"[^>]*>datos extra: Coinalyze<\/a>/.test(env.get('st').innerHTML)) throw new Error('cita a Coinalyze: ' + env.get('st').innerHTML);
  if (!env.get('cEx').kids.some(c => c.textContent === 'Otros (Coinalyze)')) throw new Error('chip «otros»');
  env.get('wrap').clientWidth = 1400; env.calls.textPos.length = 0; env.frame();
  if (!env.calls.textPos.some(([t]) => t.startsWith('Liquidaciones reales (8 exchanges + 3 vía Coinalyze)'))) throw new Error('título con Coinalyze');
  env.get('wrap').clientWidth = 420; env.frame();
  current = P; await env.js('load()'); await settle();
  if (env.get('cEx').kids.some(c => c.textContent === 'Otros (Coinalyze)') || /Coinalyze/.test(env.get('st').innerHTML)) throw new Error('sin Coinalyze no se enseña');
  ok('Coinalyze: fuentes con nombre, chip «Otros» solo si hay datos, cita con enlace y panel «8 exchanges + 3 vía Coinalyze»');

  // ── validez y errores
  env.get('bVal').onclick(); await settle();
  const vh = env.get('val').innerHTML;
  for (const t of ['Acierto', 'Calibración automática', 'Fuentes de Open Interest', 'Deribit', 'BitMEX', 'OKX', 'Bitget', 'Gate', 'HTX',
    'Recogida de liquidaciones reales (8 exchanges)', 'Mejor de 72 combinaciones', 'margen de mantenimiento <b>0,4 %</b>']) if (!vh.includes(t)) throw new Error('validez sin: ' + t);
  if (/Mapa de liquidez/.test(vh)) throw new Error('sin estado del libro no se enseña');
  VAL.book = { synced: true, levels: 23456, bid_low: 1000, ask_high: 97000, coverage: 0.64, range_pct: 6, rows: { book5: 12, book60: 1 }, since: { book5: 1791349200 } };
  await env.js('loadVal()'); await settle();
  const vb = env.get('val').innerHTML;
  if (!/Mapa de liquidez \(libro de Binance\)/.test(vb) || !/sincronizado · 23\.456 niveles · cubre el <b>64%<\/b> de los tramos de 10 \$ a ±6 %<\/div>/.test(vb) || !/Grabando desde .* · 12 bloques de 5 min/.test(vb) ||
      !/no liquidaciones/.test(vb)) throw new Error('estado del libro: ' + vb.slice(vb.indexOf('Mapa de liquidez'), vb.indexOf('Mapa de liquidez') + 400));
  VAL.book = { synced: false, levels: 0, rows: { book5: 0, book60: 0 }, since: {} };
  await env.js('loadVal()'); await settle();
  if (!/sin sincronizar · 0 niveles<\/div>/.test(env.get('val').innerHTML) || !/Aún sin bloques guardados/.test(env.get('val').innerHTML)) throw new Error('libro sin sincronizar');
  VAL.book = { error: 'falló <b>x</b>' };
  await env.js('loadVal()'); await settle();
  if (!/<h4>Mapa de liquidez<\/h4><div class="bad">Error: falló &lt;b&gt;x&lt;\/b&gt;<\/div>/.test(env.get('val').innerHTML)) throw new Error('libro con error');
  delete VAL.book;
  VAL.calib.params.mmr = 0.005; await env.js('loadVal()'); await settle();
  if (!env.get('val').innerHTML.includes('margen de mantenimiento <b>0,5 %</b>')) throw new Error('MMR 0,5 %');
  VAL.calib.params.mmr = 0.004;
  if (/Hyperliquid real<\/h4>|<h4>Coinalyze/.test(vh)) throw new Error('sin datos no salen Hyperliquid ni Coinalyze');
  // Hyperliquid y Coinalyze en la validez
  VAL.hl = { enabled: true, positions: 37, tracked: 900, long_pct: 0.6, coverage: 0.42 };
  VAL.hl_compare = { match: 0.58, base: 0.21, lift: 2.76, usd: 1.5e8 };
  VAL.models.hl = { hit: 0.5, base: 0.2, lift: 2.5, prec: 0.4, prec_base: 0.1, prec_lift: 4, events: 150, usd: 3e6, touches: 12, tol_points: 50, coverage_hours: 30, enough: true };
  VAL.coinalyze = null;
  await env.js('loadVal()'); await settle();
  const vhl = env.get('val').innerHTML;
  if (!/<b>37<\/b> posiciones de BTC con su precio de liquidación exacto · largos <b>60%<\/b> · cubren el <b>42%<\/b> del OI de Hyperliquid/.test(vhl)) throw new Error('bloque Hyperliquid: ' + vhl);
  if (!/¿El mapa estimado marca dónde están\? <b>58%<\/b> <span class="muted">\(azar: 21%\)<\/span> · <span class="ok">✓ 2,8× mejor que el azar/.test(vhl)) throw new Error('comparación con lo real');
  if (!/Modelo Hyperliquid real \(posiciones de verdad\)/.test(vhl) || !/Sin clave \(es gratis en coinalyze\.net\)/.test(vhl)) throw new Error('modelo real o aviso de Coinalyze');
  if (!/Con 150\.0M de liquidaciones reales cerca del precio/.test(vhl)) throw new Error('importe de la comparación');
  VAL.hl_compare = { match: 0.4, base: 0.5, lift: 0.8, usd: 530000 };
  VAL.models.hl.enough = false; VAL.models.hl.since = 1791407426;
  await env.js('loadVal()'); await settle();
  const vlow = env.get('val').innerHTML;
  if (!/Aún hay poco real cerca del precio \(530K\)/.test(vlow) || /no mejora al azar \(0,8×\)/.test(vlow)) throw new Error('poco real: sin veredicto');
  if (!/grabando desde \d\d [a-z]+\.?,? \d\d:\d\d/.test(vlow) || !/Aún pocos datos para este modelo/.test(vlow)) throw new Error('modelo real con pocos datos: ' + vlow.slice(vlow.indexOf('Modelo Hyperliquid'), vlow.indexOf('Modelo Hyperliquid') + 600));
  VAL.coinalyze = { oi_markets: ['Hyperliquid BTC', 'Gate BTC_USDT'], liq_markets: 14, last_error: '<b>401</b>' };
  await env.js('loadVal()'); await settle();
  const vcz = env.get('val').innerHTML;
  if (!/OI con histórico: Hyperliquid BTC · Gate BTC_USDT/.test(vcz) || !/de 14 mercados/.test(vcz) || !/Último error: &lt;b&gt;401&lt;\/b&gt;/.test(vcz) || !/href="https:\/\/coinalyze\.net"/.test(vcz)) throw new Error('bloque Coinalyze: ' + vcz);
  delete VAL.hl; delete VAL.hl_compare; delete VAL.models.hl; delete VAL.coinalyze;
  ok('validez: 8 exchanges, MMR de la calibración, estado del libro, Hyperliquid real (posiciones, cobertura y coincidencia con el estimado) y Coinalyze (o cómo conseguir la clave)');
  // ── manual: botón, 11 apartados, todo lo que se puede elegir explicado y con los mismos colores que el gráfico
  {
    if (env.get('help').style.display === 'block') throw new Error('el manual empieza cerrado');
    env.get('bCfg').onclick();                                   // con ⚙ abierto: al abrir el manual se cierra
    if (env.get('cfg').style.display !== 'block') throw new Error('abrir ⚙');
    env.get('bHelp').onclick();
    if (env.get('help').style.display !== 'block' || env.get('cfg').style.display !== 'none' || env.get('val').style.display === 'block') throw new Error('abrir el manual');
  }
  const hm = env.get('help').innerHTML;
  const secs = (hm.match(/<summary>[^<]+<\/summary>/g) || []).map(x => x.replace(/<\/?summary>/g, ''));
  if (secs.join('|') !== 'Lo básico|Mapa de liquidaciones|Perfil, sesgo e imán|Liquidaciones reales|Hyperliquid real|Mapa de liquidez (libro)|Paneles de abajo|Barra de arriba|⚙ Ajustes y capas|✓ Validez|Palabras clave')
    throw new Error('apartados: ' + secs);
  const items = (hm.match(/<div class="it">/g) || []).length;
  const must = [...Object.values(env.js('PANE_NAME')).map(x => `<b>${x}</b>`),
    'Perfil (barras', 'Paneles (los de abajo)', 'Calor / Lado (', ' Zonas ·', ' Asia ·', 'Liq (burbujas)', 'Libro (muros', 'HL (Hyperliquid)',   // las 8 capas de ⚙
    ...env.js('Object.values(MODEL_NAME)').map(x => x.replace(' (calibrado)', '') + ':'),
    'Liquidaciones, Liquidez (libro) o Las dos', 'Pro: 4 colores', 'Completa: todos', '<b>Grupo</b>', '<b>Imán</b>', '<b>Intensidad</b>', '«Total ≥»', 'maxHeat',
    '<b>Burbujas (capa «Liq»)</b>', '<b>Pulso</b>', '<b>Aviso ⚡</b>', '<b>Velas</b>', '<b>Perfil</b>', '<b>Curvas</b>', '<b>Acierto</b>', '<b>Azar</b>', '<b>Puntos (pts)</b>',
    'no es una recomendación ni una señal de entrada'];
  const miss = must.filter(t => !hm.includes(t));
  if (miss.length) throw new Error('el manual no explica: ' + miss.join(' | '));
  // los colores de los dibujos son los del gráfico
  const COLS = [...env.js('PRO_COLS'), ...Object.values(env.js('TIER_COL')), '#ff9f1c', '#2ec4ff', '#e8e8e8', '#b388ff', '#26a69a', '#ef5350', '#9be9a8', '#ff9bb3',
    'rgba(38,166,154,0.9)', 'rgba(239,83,80,0.9)', 'rgba(46,196,255,0.95)', 'rgba(255,159,28,0.95)', 'rgba(255,159,28,0.85)', 'rgba(46,196,255,0.85)',
    'rgba(46,196,255,0.55)', 'rgba(255,159,28,0.55)', '#ff5a5a', '#3ddc84', ...env.js('BOOK_PAL.slice(1).map(c => `rgba(${c.join(",")})`)')];
  const missC = COLS.filter(c => !hm.includes(c));
  if (missC.length) throw new Error('colores del manual distintos del gráfico: ' + missC);
  if (!/^#[0-9a-fA-F]{6}$/.test(String(hm.match(/--c:([^"]+)"/)[1])) || new Set((hm.match(/--c:[^"]+"/g) || [])).size !== 11) throw new Error('un color por apartado');
  // horas de la sesión asiática en la hora de quien mira
  const asia = env.js('data.asia'), hhm = (t) => new Date(t * 1000).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
  if (env.get('helpAsia').textContent !== `de ${hhm(asia.start)} a ${hhm(asia.end)}, tu hora`) throw new Error('horas de Asia: ' + env.get('helpAsia').textContent);
  env.get('hClose').onclick();
  if (env.get('help').style.display !== 'none') throw new Error('cerrar con ✕');
  env.get('bHelp').onclick(); env.get('bHelp').onclick();
  if (env.get('help').style.display !== 'none') throw new Error('el botón abre y cierra');
  env.get('bHelp').onclick(); env.get('bVal').onclick(); await settle();
  if (env.get('help').style.display !== 'none' || env.get('val').style.display !== 'block') throw new Error('✓ Validez cierra el manual');
  env.js('closeVal()');
  ok(`manual: 11 apartados y ${items} explicaciones (todos los paneles, capas, modelos y botones), colores iguales al gráfico y horas de Asia en tu hora («${env.get('helpAsia').textContent}»)`);
  current = { error: 'fallo simulado' }; await env.js('load()'); await settle();
  if (!/Error: fallo simulado/.test(env.get('st').innerHTML)) throw new Error('error no mostrado');
  ok('panel de validez y errores visibles');
  console.log('TODA LA WEB OK');
})().catch(e => { console.error('FALLO:', e.message); process.exit(1); });
