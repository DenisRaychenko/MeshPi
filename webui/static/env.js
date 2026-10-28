// Довкілля на першій сторінці: середні показники вузлів у межах кількох стрибків (дані — з /api/env).
// Значення з мережі вставляємо лише через textContent.
(function () {
  const box = document.getElementById('envbox');
  if (!box) return;
  const grid = $('#envgrid'), note = $('#envnote'), src = $('#envsrc'), body = $('#envsrcbody');
  const SVGNS = 'http://www.w3.org/2000/svg';
  const ICON = {temperature: '🌡', humidity: '💧', pressure: '⏲', iaq: '🍃', gas: '⚗', lux: '☀', wind: '🌬', pm25: '🌫', co2: '☁'};
  const FMT = {temperature: 1, humidity: 0, pressure: 1, iaq: 0, gas: 1, lux: 0, wind: 1, pm25: 0, co2: 0};
  const num = (id, v) => (+v).toFixed(FMT[id] ?? 1);

  function spark(series) {
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 120 28'); svg.setAttribute('class', 'spark');
    if (!series || series.length < 2) return svg;
    const xs = series.map(p => p[0]), ys = series.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
    const pts = series.map(p => `${((p[0] - x0) / ((x1 - x0) || 1) * 118 + 1).toFixed(1)},${(26 - (p[1] - lo) / span * 24).toFixed(1)}`);
    const line = document.createElementNS(SVGNS, 'polyline');
    line.setAttribute('points', pts.join(' ')); line.setAttribute('fill', 'none');
    line.setAttribute('stroke', 'var(--signal)'); line.setAttribute('stroke-width', '1.6');
    svg.append(line);
    return svg;
  }

  function card(m) {
    const d = document.createElement('div'), l = document.createElement('div'), v = document.createElement('div'),
          s = document.createElement('div');
    d.className = 'metric envcard'; l.className = 'label'; v.className = 'value'; s.className = 'hint';
    l.textContent = `${ICON[m.id] || ''} ${m.label}`;
    v.textContent = num(m.id, m.avg);
    if (m.unit) { const u = document.createElement('span'); u.className = 'unit'; u.textContent = m.unit; v.append(u); }
    s.textContent = m.n === 1 ? '1 вузол' : `${m.n} вузлів · ${num(m.id, m.min)}…${num(m.id, m.max)}`;
    d.append(l, v, spark(m.series), s);
    return d;
  }

  function sources(list, metrics) {
    body.replaceChildren();
    for (const x of list) {
      const tr = body.insertRow();
      tr.insertCell().textContent = x.name || x.id;
      tr.insertCell().textContent = x.hops;
      for (const m of metrics) tr.insertCell().textContent = x.values[m.id] != null ? x.values[m.id] : '—';
      tr.insertCell().textContent = ago(Date.now() / 1000 - x.age);
    }
    const head = $('#envsrchead'); head.replaceChildren();
    for (const t of ['Вузол', 'Стрибки', ...metrics.map(m => `${m.label}${m.unit ? ', ' + m.unit : ''}`), 'Давно']) {
      const th = document.createElement('th'); th.textContent = t; head.append(th);
    }
  }

  async function refresh() {
    try {
      const r = await api('/api/env');
      $('#envtitle').textContent = `Довкілля · вузли до ${r.max_hops} стрибків`;
      grid.replaceChildren(...r.metrics.map(card));
      if (!r.metrics.length) {
        note.textContent = `Поки немає даних: жоден вузол у межах ${r.max_hops} стрибків не передавав температуру, вологість чи тиск ` +
          '(зазвичай вузол із датчиком шле їх раз на ~30 хв). Дані з\'являться тут самі.';
        src.hidden = true; return;
      }
      note.textContent = `Середнє за вузлами, що передали показники за останні ${Math.round(r.window / 3600)} год ` +
        `(${r.sources.length} ${r.sources.length === 1 ? 'вузол' : 'вузлів'}). Графік — остання доба.`;
      sources(r.sources, r.metrics); src.hidden = false;
    } catch (e) { note.textContent = e.message; }
  }

  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
})();
