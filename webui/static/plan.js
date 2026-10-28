// Планувальник лінка й прогноз сонячного вузла. Зовнішні запити (open-meteo.com, forecast.solar) йдуть лише з кнопок.
// Назви вузлів — чужі дані: textContent.
(function () {
  if (!document.getElementById('planbox')) return;
  const $ = id => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svg = (tag, a, text) => { const e = document.createElementNS(NS, tag); for (const k in a || {}) e.setAttribute(k, a[k]); if (text != null) e.textContent = text; return e; };
  const VERDICT = {clear: ['ok', 'Пряма видимість вільна, перша зона Френеля чиста'], marginal: ['warn', 'Видимість є, але перша зона Френеля закрита більш ніж на 40%: сигнал буде слабшим'], blocked: ['crit', 'Пряма видимість перекрита рельєфом']};

  // ---- планувальник ----
  async function fillNodes() {
    try {
      const nodes = (await api('/api/nodes')).filter(n => n.lat != null && n.lon != null && n.id);
      for (const sel of [$('plana'), $('planb')]) { sel.replaceChildren(); for (const n of nodes) sel.append(new Option(n.short || n.long || n.id, n.id)); }
      if (nodes.length > 1) $('planb').selectedIndex = 1;
      $('plannote').textContent = nodes.length < 2 ? 'потрібні два вузли з відомою позицією' : '';
    } catch (e) { /* без вузлів - порожні списки */ }
  }
  function drawProfile(r) {
    const pts = r.profile.points, W = 720, H = 260, L = 46, R = 12, T = 12, B = 28;
    const lo = Math.min(...pts.map(p => p.terrain)) - 5, hi = Math.max(...pts.map(p => Math.max(p.terrain + p.bulge, p.los + p.f1 * 0))) + 15;
    const X = d => L + d / r.profile.dist_km * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const s = svg('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'Профіль лінка'});
    const poly = (arr, close) => arr.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ');
    s.append(svg('polygon', {points: poly([[0, lo], ...pts.map(p => [p.d, p.terrain + p.bulge]), [r.profile.dist_km, lo]]), fill: 'var(--sky-700)', stroke: 'var(--line)'}));
    s.append(svg('polygon', {points: poly([...pts.map(p => [p.d, p.los + p.f1 * 0.6]), ...[...pts].reverse().map(p => [p.d, p.los - p.f1 * 0.6])]), fill: 'var(--signal)', 'fill-opacity': .12, stroke: 'none'}));
    s.append(svg('line', {x1: X(0), y1: Y(pts[0].los), x2: X(r.profile.dist_km), y2: Y(pts[pts.length - 1].los), stroke: 'var(--signal)', 'stroke-width': 2}));
    for (const v of [lo + 5, (lo + hi) / 2, hi - 10]) s.append(svg('text', {x: L - 4, y: Y(v) + 3, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--text-mid)'}, v.toFixed(0) + ' м'));
    for (const f of [0, .5, 1]) s.append(svg('text', {x: X(f * r.profile.dist_km), y: H - 10, 'text-anchor': f ? (f === 1 ? 'end' : 'middle') : 'start', 'font-size': 10, fill: 'var(--text-mid)'}, (f * r.profile.dist_km).toFixed(1) + ' км'));
    s.append(svg('text', {x: X(0) + 4, y: Y(pts[0].los) - 6, 'font-size': 11, fill: 'var(--text-hi)'}, r.a.name), svg('text', {x: X(r.profile.dist_km) - 4, y: Y(pts[pts.length - 1].los) - 6, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--text-hi)'}, r.b.name));
    return s;
  }
  $('plango').onclick = async () => {
    $('plannote').textContent = 'запитую висоти рельєфу…'; $('planres').replaceChildren();
    const q = new URLSearchParams({a: $('plana').value, b: $('planb').value, h1: $('planh1').value, h2: $('planh2').value, freq: $('planf').value, tx: $('plantx').value, g1: $('plang').value, g2: $('plang').value});
    let r;
    try { r = await api('/api/linkplan?' + q); } catch (e) { $('plannote').textContent = e.message; return; }
    $('plannote').textContent = r.source;
    const [lvl, text] = VERDICT[r.profile.verdict];
    const out = $('planres'), v = el('div', 'hfind ' + lvl); v.append(el('span', 'lv', lvl === 'ok' ? 'гаразд' : lvl === 'warn' ? 'увага' : 'проблема'), el('span', null, text + `. Найменший запас над перешкодою ${r.profile.min_clear} м на ${r.profile.worst_d} км від ${r.a.name}.`));
    const b = r.budget, mg = el('div', 'hfind ' + (b.margin >= 10 ? 'ok' : b.margin >= 0 ? 'warn' : 'crit'));
    mg.append(el('span', 'lv', 'бюджет'), el('span', null, `Відстань ${r.profile.dist_km} км. Втрати у вільному просторі ${b.fspl} дБ, рівень сигналу ≈ ${b.prx} дБм, чутливість ≈ ${b.sensitivity} дБм (смуга ${r.params.bw} кГц, поріг SNR ${r.params.snr} дБ), запас ≈ ${b.margin} дБ. ` + (r.profile.verdict === 'clear' ? '' : 'Перешкода додасть втрат, яких тут немає.')));
    out.append(v, mg, drawProfile(r));
  };
  fillNodes();

  // ---- сонячний вузол ----
  const F = {on: $('solon'), lat: $('sollat'), lon: $('sollon'), dec: $('soldec'), az: $('solaz'), kwp: $('solkwp'), cap_wh: $('solcap'), load_w: $('solload'), eff: $('soleff')};
  let nodePos = null;
  const say = (t, bad) => { $('solnote').textContent = t || ''; $('solnote').style.color = bad ? 'var(--crit)' : ''; };
  const when = t => new Date(t * 1000).toLocaleString('uk-UA', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
  function show(r) {
    const c = r.cfg; nodePos = r.node_pos;
    F.on.checked = c.on; for (const k of ['lat', 'lon', 'dec', 'az', 'kwp', 'cap_wh', 'load_w', 'eff']) F[k].value = c[k] ?? '';
    if (!r.external) say('Зовнішні запити вимкнено на сервері (MESHPI_NO_EXTERNAL=1)', true);
    const out = $('solres'); out.replaceChildren();
    const f = r.forecast;
    if (!f) return;
    if (!f.ok) { const row = el('div', 'hfind crit'); row.append(el('span', 'lv', 'помилка'), el('span', null, f.error || 'немає даних')); out.append(row); return; }
    const days = Object.entries(f.days || {}).map(([d, wh]) => `${d}: ${(wh / 1000).toFixed(2)} кВт·год`).join(' · ');
    const bal = el('div', 'hfind ' + (f.balance_wh >= 0 ? 'ok' : 'warn')); bal.append(el('span', 'lv', 'баланс'), el('span', null, `До ${when(f.until)}: виробіток ${f.gen_wh} Вт·год, споживання ${f.load_wh} Вт·год, різниця ${f.balance_wh} Вт·год. Прогноз за добу: ${days || '—'}.`));
    out.append(bal);
    if (f.min_pct != null) {
      const row = el('div', 'hfind ' + (f.risk ? 'crit' : f.min_pct < 20 ? 'warn' : 'ok'));
      row.append(el('span', 'lv', 'акумулятор'), el('span', null, f.risk ? `Акумулятор розрядиться близько ${when(f.empty_at)} (стартовий заряд ${r.battery ?? 100}%). Збільште панель чи акумулятор або зменште споживання.`
        : `Найнижчий заряд ≈ ${f.min_pct}% (${when(f.min_at)}), до кінця прогнозу ≈ ${f.end_pct}%.`));
      out.append(row);
    } else { const row = el('div', 'hfind info'); row.append(el('span', 'lv', 'до уваги'), el('span', null, 'Вкажіть ємність акумулятора, щоб побачити запас.')); out.append(row); }
  }
  async function load(forecast) {
    try { show(await api('/api/solar' + (forecast ? '?forecast=1' : ''))); } catch (e) { say(e.message, true); }
  }
  $('solpos').onclick = () => { if (nodePos) { F.lat.value = nodePos.lat; F.lon.value = nodePos.lon; } else say('у вузла немає позиції', true); };
  $('solsave').onclick = async () => {
    const b = {on: F.on.checked};
    for (const k of ['lat', 'lon', 'dec', 'az', 'kwp', 'cap_wh', 'load_w', 'eff']) if (F[k].value !== '') b[k] = +F[k].value;
    say('Зберігаю…');
    try { await api('/api/solar', b); say(F.on.checked ? 'Запитую прогноз…' : 'Збережено.'); await load(true); if (!$('solnote').style.color) say(''); } catch (e) { say(e.message, true); }
  };
  load(false);
})();
