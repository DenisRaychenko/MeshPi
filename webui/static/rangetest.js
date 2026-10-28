// Вимір дальності: підсумок по відправниках і графік «відстань, SNR» із /api/rangetest. Назви вузлів — чужі дані (textContent).
(function () {
  const box = document.getElementById('rangebox');
  if (!box) return;
  const $ = id => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, a, text) => { const e = document.createElementNS(NS, tag); for (const k in a || {}) e.setAttribute(k, a[k]); if (text != null) e.textContent = text; return e; };
  const COLORS = ['var(--signal)', 'var(--warn)', '#7aa2ff', '#ff8fd0', '#b4f06a', '#e5a3ff'];

  function chart(points, names) {
    const out = $('rangechart'); out.replaceChildren();
    if (!points.length) { return; }
    const W = 640, H = 240, L = 44, R = 12, T = 14, B = 30;
    const s = svg('svg', {viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': 'SNR залежно від відстані'});
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const x1 = Math.max(...xs, 0.1); let lo = Math.min(...ys, -20) - 1, hi = Math.max(...ys, 5) + 1;
    const X = d => L + d / x1 * (W - L - R), Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    // межа демодуляції LongFast (SF11) близько -17,5 дБ: нижче пакети вже не приймаються
    if (-17.5 > lo) s.append(svg('line', {x1: L, x2: W - R, y1: Y(-17.5), y2: Y(-17.5), stroke: 'var(--crit)', 'stroke-dasharray': '4 3'}),
                             svg('text', {x: W - R, y: Y(-17.5) - 3, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--crit)'}, 'межа LongFast ≈ −17,5 дБ'));
    for (const f of [0, .25, .5, .75, 1]) {
      s.append(svg('text', {x: L + f * (W - L - R), y: H - 12, 'text-anchor': 'middle', 'font-size': 10, fill: 'var(--text-mid)'}, (f * x1).toFixed(x1 < 3 ? 2 : 1) + ' км'));
    }
    for (const v of [lo, (lo + hi) / 2, hi]) s.append(svg('text', {x: L - 4, y: Y(v) + 3, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--text-mid)'}, v.toFixed(0)));
    s.append(svg('line', {x1: L, x2: W - R, y1: H - B, y2: H - B, stroke: 'var(--line)'}), svg('line', {x1: L, x2: L, y1: T, y2: H - B, stroke: 'var(--line)'}));
    const ids = [...new Set(points.map(p => p[2]))];
    for (const p of points) {
      const c = svg('circle', {cx: X(p[0]).toFixed(1), cy: Y(p[1]).toFixed(1), r: 3, fill: COLORS[ids.indexOf(p[2]) % COLORS.length], 'fill-opacity': .75});
      c.append(svg('title', null, `${names[p[2]] || p[2]}: ${p[0].toFixed(2)} км, SNR ${p[1]} дБ`)); s.append(c);
    }
    out.append(s);
  }

  async function refresh() {
    const h = $('rangehours').value;
    $('rangecsv').href = '/api/rangetest.csv?hours=' + h;
    let r;
    try { r = await api('/api/rangetest?hours=' + h); } catch (e) { $('rangenote').textContent = e.message; return; }
    const names = {};
    const tb = $('rangerows'); tb.replaceChildren();
    for (const n of r.nodes) {
      names[n.node] = n.name;
      const tr = tb.insertRow();
      const cells = [n.name, `${n.received} з ${n.expected}`, n.loss == null ? '—' : (n.loss * 100).toFixed(0) + '%',
        n.max_km == null ? 'позиція невідома' : n.max_km.toFixed(2) + ' км' + (n.far_snr == null ? '' : ` (SNR ${n.far_snr})`),
        n.snr_min == null ? '—' : `${n.snr_min}…${n.snr_max}`, n.series];
      for (const c of cells) tr.insertCell().textContent = c;
    }
    $('rangenote').textContent = r.total ? `пакетів ${r.total}` : 'Поки немає пакетів Range Test.';
    chart(r.points, names);
  }
  $('rangehours').onchange = refresh;
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
})();
