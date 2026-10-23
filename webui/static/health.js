// Здоров'я мережі: висновки й графіки співвідношень за LocalStats вибраного вузла (/api/netstats). Тексти лише через textContent.
(function () {
  const box = document.getElementById('healthbox');
  if (!box) return;
  const $ = id => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const LEVEL = {ok: 'гаразд', info: 'до уваги', warn: 'увага', crit: 'проблема'};
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svg = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs || {}) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };

  function spark(title, pts, fmt, warnAt) {
    const card = el('div', 'metric'); card.style.padding = '10px';
    const W = 320, H = 100, L = 40, R = 8, T = 20, B = 14;
    const s = svg('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': title});
    s.append(svg('text', {x: 0, y: 12, 'font-size': 11, fill: 'var(--text-mid)'}, title));
    if (pts.length < 2) {
      s.append(svg('text', {x: W / 2, y: H / 2 + 6, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--text-low)'}, 'мало даних'));
      card.append(s); return card;
    }
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let lo = Math.min(0, ...ys), hi = Math.max(...ys, warnAt || 0);
    if (hi - lo < 1e-9) hi = lo + 1;
    const X = t => L + (x1 > x0 ? (t - x0) / (x1 - x0) : .5) * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    if (warnAt != null) s.append(svg('line', {x1: L, x2: W - R, y1: Y(warnAt), y2: Y(warnAt), stroke: 'var(--warn)', 'stroke-dasharray': '4 3', 'stroke-width': 1}));
    s.append(svg('polyline', {points: pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' '), fill: 'none', stroke: 'var(--signal)', 'stroke-width': 2}));
    s.append(svg('text', {x: L - 4, y: Y(hi) + 4, 'font-size': 10, 'text-anchor': 'end', fill: 'var(--text-mid)'}, fmt(hi)));
    s.append(svg('text', {x: L - 4, y: Y(lo) + 4, 'font-size': 10, 'text-anchor': 'end', fill: 'var(--text-mid)'}, fmt(lo)));
    s.append(svg('text', {x: W - R, y: 12, 'font-size': 12, 'font-weight': 700, 'text-anchor': 'end', fill: 'var(--text-hi)'}, fmt(ys[ys.length - 1])));
    card.append(s); return card;
  }

  async function refresh() {
    let r;
    try { r = await api('/api/netstats?hours=' + $('healthhours').value); }
    catch (e) { $('healthnote').textContent = 'немає зв\'язку з вузлом'; return; }
    const t = r.totals;
    $('healthnote').textContent = r.samples >= 2 ? `за вікно: прийнято ${t.rx}, передано ${t.tx}, дублікатів ${t.dup}, з помилками ${t.bad}, ретрансляцій ${t.rel}` : '';
    const f = $('healthfind'); f.replaceChildren();
    for (const x of r.findings) { const row = el('div', 'hfind ' + x.level); row.append(el('span', 'lv', LEVEL[x.level] || x.level), el('span', null, x.text)); f.append(row); }
    const g = $('healthcharts'); g.replaceChildren();
    const pct = v => (v * 100).toFixed(0) + '%';
    g.append(spark('Дублікати, % прийнятого', r.series.dup, pct, 0.15), spark('Пакети з помилками, % прийнятого', r.series.bad, pct, 0.20),
             spark('Зайнятість каналу, %', r.series.chan, v => v.toFixed(0) + '%', 25), spark('Рівень шуму, дБм', r.series.noise, v => v.toFixed(0), -100));
  }
  $('healthhours').onchange = refresh;
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
})();
