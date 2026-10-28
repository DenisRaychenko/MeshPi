// Графіки телеметрії окремого вузла в його панелі («Вузли»): батарея, напруга, зайнятість каналу, ефір, довкілля, INA.
// Дані — з /api/telemetry (накопичуються з пакетів телеметрії, які вузол передає сам, зазвичай раз на ~30 хв).
(function () {
  const idEl = document.getElementById('ndid'), out = document.getElementById('ndout');
  if (!idEl || !out) return;
  const NS = 'http://www.w3.org/2000/svg';
  const det = document.createElement('details');
  det.id = 'ndcharts'; det.style.margin = '0 0 10px';
  const sum = document.createElement('summary'); sum.textContent = '📈 Графіки телеметрії'; sum.style.cursor = 'pointer';
  const bar = document.createElement('div'); bar.className = 'row'; bar.style.margin = '8px 0';
  const hrs = document.createElement('select'); hrs.className = 'tg-input';
  for (const [v, t] of [[1, '1 год'], [6, '6 год'], [24, '24 год'], [168, '7 діб']]) { const o = document.createElement('option'); o.value = v; o.textContent = t; hrs.append(o); }
  hrs.value = 24;
  const note = document.createElement('span'); note.className = 'hint';
  bar.append(hrs, note);
  const grid = document.createElement('div'); grid.className = 'chartgrid';
  det.append(sum, bar, grid);
  out.before(det);
  let cur = null;

  const DIGITS = {bat: 0, volt: 2, chan: 1, air: 2, up: 1, p1v: 2, p2v: 2, p3v: 2, p1c: 0, p2c: 0, p3c: 0, temperature: 1, humidity: 0, pressure: 1};
  const num = (id, v) => (+v).toFixed(DIGITS[id] ?? 1);
  const when = (t, span) => { const d = new Date(t * 1000); return span > 86400 * 1.5 ? d.toLocaleDateString('uk-UA', {day: '2-digit', month: '2-digit'}) + ' ' + d.toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}) : d.toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'}); };
  const svgEl = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs || {}) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };

  function chart(s) {
    const box = document.createElement('div'); box.className = 'metric'; box.style.padding = '10px';
    const W = 320, H = 120, L = 42, R = 8, T = 22, B = 20;
    const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': s.label});
    const pts = s.points, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), span = x1 - x0;
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    if (s.id === 'bat' || s.id === 'chan' || s.id === 'air') lo = Math.max(lo, 0);
    const X = t => L + (span ? (t - x0) / span : .5) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    svg.append(svgEl('text', {x: 0, y: 12, 'font-size': 11, fill: 'var(--text-mid)'}, `${s.label}, ${s.unit}`));
    svg.append(svgEl('text', {x: W - R, y: 12, 'font-size': 12, 'font-weight': 700, 'text-anchor': 'end', fill: 'var(--text-hi)'}, `${num(s.id, ys[ys.length - 1])} ${s.unit}`));
    for (const f of [0, .5, 1]) {  // сітка й підписи осі Y
      const v = lo + (hi - lo) * f, y = Y(v);
      svg.append(svgEl('line', {x1: L, x2: W - R, y1: y, y2: y, stroke: 'var(--line)', 'stroke-width': 1}));
      svg.append(svgEl('text', {x: L - 4, y: y + 3, 'font-size': 9, 'text-anchor': 'end', fill: 'var(--text-low)'}, num(s.id, v)));
    }
    svg.append(svgEl('text', {x: L, y: H - 5, 'font-size': 9, fill: 'var(--text-low)'}, when(x0, span)));
    svg.append(svgEl('text', {x: W - R, y: H - 5, 'font-size': 9, 'text-anchor': 'end', fill: 'var(--text-low)'}, when(x1, span)));
    if (pts.length === 1) svg.append(svgEl('circle', {cx: X(xs[0]), cy: Y(ys[0]), r: 3, fill: 'var(--signal)'}));
    else svg.append(svgEl('polyline', {points: pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' '), fill: 'none',
      stroke: 'var(--signal)', 'stroke-width': 1.8, 'stroke-linejoin': 'round'}));
    const t = svgEl('title', {}, `${s.label}: від ${num(s.id, Math.min(...ys))} до ${num(s.id, Math.max(...ys))} ${s.unit}, точок: ${pts.length}`);
    svg.append(t);
    box.append(svg);
    return box;
  }

  async function load() {
    if (!cur) return;
    note.textContent = 'Завантажую…';
    try {
      const r = await api(`/api/telemetry?node=${encodeURIComponent(cur)}&hours=${hrs.value}`);
      grid.replaceChildren(...r.series.map(chart));
      note.textContent = r.series.length ? `${r.series.length} показників за ${hrs.selectedOptions[0].textContent}.`
        : 'Даних поки немає: телеметрія з\'являється, коли вузол її передає (зазвичай раз на ~30 хв) і панель її почула; історія копиться з моменту оновлення панелі.';
    } catch (e) { note.textContent = e.message; }
  }
  det.addEventListener('toggle', () => { if (det.open) load(); });
  hrs.onchange = load;

  new MutationObserver(() => {
    const id = idEl.textContent.trim();
    if (id && id !== cur) { cur = id; grid.replaceChildren(); note.textContent = ''; if (det.open) load(); }
  }).observe(idEl, {childList: true, characterData: true, subtree: true});
})();
