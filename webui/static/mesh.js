// Карта мережі: кола відстані від цього вузла (типово 1, 3 і 5 км), вузли за справжнім азимутом і відстанню,
// стрілки зв'язків: «вгору» (до нас) — червоні, «вниз» (від нас) — зелені. Дані — з /api/mesh, архів по днях.
// Значення з мережі вставляємо лише через textContent.
(function () {
  const svg = document.getElementById('meshsvg');
  if (!svg) return;
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  };
  const store = {
    get(k, d) { try { const v = localStorage.getItem('mesh_' + k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('mesh_' + k, v); } catch (e) { /* без сховища — ок */ } },
  };
  const C = 450, MAXR = 420, HOME = {x: 0, y: 0, w: 900};
  const ui = {day: $('#mday'), prev: $('#mprev'), next: $('#mnext'), rings: $('#mrings'), via: $('#mvia'),
              mqtt: $('#mmqtt'), swap: $('#mswap'), reset: $('#mreset'), note: $('#mnote'), info: $('#minfo'),
              body: $('#mlinks'), legend: $('#mlegend')};
  let data = null, sel = null, view = {...HOME};

  ui.rings.value = store.get('rings', '1,3,5');
  for (const k of ['via', 'mqtt', 'swap']) ui[k].checked = store.get(k, k === 'via' ? '1' : '0') === '1';

  const ringsKm = () => {
    const r = [...new Set(ui.rings.value.split(/[\s,;]+/).map(Number).filter(v => v > 0 && isFinite(v)))].sort((a, b) => a - b);
    return (r.length ? r : [1, 3, 5]).slice(0, 6);
  };
  const rad = d => d * Math.PI / 180;
  function haversine(a, b) {  // км
    const dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 12742 * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function bearing(a, b) {  // радіани від півночі за годинниковою
    const dLon = rad(b.lon - a.lon);
    return Math.atan2(Math.sin(dLon) * Math.cos(rad(b.lat)),
      Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) - Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(dLon));
  }
  const hasPos = n => n && typeof n.lat === 'number' && typeof n.lon === 'number' && !(n.lat === 0 && n.lon === 0);
  const fmtKm = d => d < 1 ? Math.round(d * 1000) + ' м' : d.toFixed(d < 10 ? 1 : 0) + ' км';
  const nodeName = n => n.short || n.name || n.id;

  // Видимий радіус: кожен проміжок між кільцями займає однакову ширину (перше коло — 1 км, друге — 3 км…).
  function layout(nodes, origin, rings) {
    const k = rings.length, S = MAXR / (k + 1.9), pos = {};
    const radius = d => {
      let prev = 0;
      for (let i = 0; i < k; i++) {
        if (d <= rings[i]) return i * S + (d - prev) / (rings[i] - prev) * S;
        prev = rings[i];
      }
      return k * S + Math.min(1, Math.log(d / rings[k - 1]) / Math.log(4)) * 0.9 * S;  // далі за останнє коло — стиснуто
    };
    const unknown = [];
    for (const n of nodes) {
      if (n.me) { pos[n.id] = {x: C, y: C, dist: 0, th: 0, r: 0}; continue; }
      if (origin && hasPos(n)) {
        const d = haversine(origin, n), th = bearing(origin, n), r = Math.max(radius(d), 0.3 * S);
        pos[n.id] = {x: C + r * Math.sin(th), y: C - r * Math.cos(th), dist: d, th, r};
      } else unknown.push(n);
    }
    unknown.sort((a, b) => (a.hops ?? 99) - (b.hops ?? 99) || (a.id < b.id ? -1 : 1));
    unknown.forEach((n, i) => {
      const th = (i + 0.5) / unknown.length * 2 * Math.PI, r = (k + 1.5) * S;
      pos[n.id] = {x: C + r * Math.sin(th), y: C - r * Math.cos(th), dist: null, th, r};
    });
    // розсуваємо накладені вузли вздовж кола (відстань від центру при цьому не міняється)
    const list = Object.values(pos).filter(p => p.r > 0);
    if (list.length < 300) {
      for (let it = 0; it < 60; it++) {
        let moved = false;
        for (let a = 0; a < list.length; a++) for (let b = a + 1; b < list.length; b++) {
          const p = list[a], q = list[b], dx = q.x - p.x, dy = q.y - p.y, d = Math.hypot(dx, dy);
          if (d >= 26) continue;
          const push = (26 - d) / 2 / Math.max(p.r, q.r, 1) * (it % 2 ? -1 : 1) + 0.002;
          p.th -= push; q.th += push; moved = true;
          for (const z of [p, q]) { z.x = C + z.r * Math.sin(z.th); z.y = C - z.r * Math.cos(z.th); }
        }
        if (!moved) break;
      }
    }
    return {pos, S, k, hasUnknown: unknown.length > 0};
  }

  function draw() {
    svg.replaceChildren();
    if (!data) return;
    const rings = ringsKm(), up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    const showVia = ui.via.checked;
    const L = layout(data.nodes, data.origin, rings), pos = L.pos;
    const defs = el('defs');
    for (const [id, col] of [['arUp', up], ['arDown', down]]) {
      const m = el('marker', {id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: 'userSpaceOnUse', orient: 'auto'});
      m.append(el('path', {d: 'M0,0L10,5L0,10z', style: `fill:${col}`}));
      defs.append(m);
    }
    svg.append(defs);

    // кола відстані
    const g0 = el('g'); svg.append(g0);
    rings.forEach((km, i) => {
      g0.append(el('circle', {cx: C, cy: C, r: (i + 1) * L.S, fill: 'none', stroke: 'var(--line)', 'stroke-width': 1}));
      g0.append(el('text', {x: C + 4, y: C - (i + 1) * L.S - 3, fill: 'var(--text-low)', 'font-size': 11}, km + ' км'));
    });
    g0.append(el('circle', {cx: C, cy: C, r: (L.k + 0.95) * L.S, fill: 'none', stroke: 'var(--line)', 'stroke-dasharray': '2 6'}));
    g0.append(el('text', {x: C + 4, y: C - (L.k + 0.95) * L.S - 3, fill: 'var(--text-low)', 'font-size': 11}, '> ' + rings[L.k - 1] + ' км'));
    if (L.hasUnknown) {
      g0.append(el('circle', {cx: C, cy: C, r: (L.k + 1.5) * L.S, fill: 'none', stroke: 'var(--line)', 'stroke-dasharray': '1 5'}));
      g0.append(el('text', {x: C + 4, y: C - (L.k + 1.5) * L.S - 3, fill: 'var(--text-low)', 'font-size': 11}, 'без координат'));
    }

    // зв'язки: об'єднуємо різні види одного напрямку; пунктир — лише коли шлях іде через невідомі вузли
    const merged = new Map();
    for (const l of data.links) {
      if (l.kind === 'via' && !showVia) continue;
      if (!pos[l.a] || !pos[l.b]) continue;
      const key = `${l.a}|${l.b}|${l.flow}`, m = merged.get(key);
      if (m) { m.n += l.n; m.via = m.via && l.kind === 'via'; m.snr = m.snr ?? l.snr; } else merged.set(key, {...l, via: l.kind === 'via'});
    }
    const both = new Set([...merged.values()].map(l => `${l.a}|${l.b}`));
    const gl = el('g'); svg.append(gl);
    for (const l of merged.values()) {
      const p = pos[l.a], q = pos[l.b], dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
      const off = both.has(`${l.b}|${l.a}`) || merged.has(`${l.b}|${l.a}|${l.flow === 'up' ? 'down' : 'up'}`) ? 7 : 0;
      const ux = dx / len, uy = dy / len, sx = p.x + ux * 12, sy = p.y + uy * 12, ex = q.x - ux * 15, ey = q.y - uy * 15;
      const cx = (sx + ex) / 2 - uy * off * 2, cy = (sy + ey) / 2 + ux * off * 2;
      const dim = sel && l.a !== sel && l.b !== sel;
      const path = el('path', {d: `M${sx.toFixed(1)},${sy.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`,
        fill: 'none', stroke: l.flow === 'up' ? up : down, 'stroke-width': (1.2 + Math.log2(l.n + 1) * 0.55).toFixed(1),
        'stroke-opacity': dim ? 0.1 : 0.8, 'marker-end': `url(#${l.flow === 'up' ? 'arUp' : 'arDown'})`});
      if (l.via) path.setAttribute('stroke-dasharray', '6 4');
      path.append(el('title', {}, `${nameOf(l.a)} → ${nameOf(l.b)} · ${l.flow === 'up' ? 'вгору' : 'вниз'} · ${l.n} пак.` +
        (l.snr != null ? ` · SNR ${l.snr}` : '') + (l.via ? ' · через невідомі вузли' : '')));
      gl.append(path);
    }

    // вузли
    const gn = el('g'); svg.append(gn);
    for (const n of data.nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const g = el('g', {style: 'cursor:pointer', opacity: sel && !linked(n.id) ? 0.35 : 1});
      const fill = n.me ? 'var(--signal)' : n.repeater ? 'var(--warn)' : 'var(--text-mid)';
      if (n.repeater && !n.me) g.append(el('rect', {x: p.x - 8, y: p.y - 8, width: 16, height: 16, transform: `rotate(45 ${p.x} ${p.y})`, fill}));
      else g.append(el('circle', {cx: p.x, cy: p.y, r: n.me ? 12 : 8, fill}));
      if (n.id === sel) g.append(el('circle', {cx: p.x, cy: p.y, r: 17, fill: 'none', stroke: 'var(--text-hi)', 'stroke-width': 2}));
      g.append(el('text', {x: p.x, y: p.y + (n.me ? 25 : 21), 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--text-hi)'}, nodeName(n)));
      g.append(el('title', {}, `${n.name} ${n.id}${p.dist != null ? ' · ' + fmtKm(p.dist) : ''}`));
      g.onclick = ev => { ev.stopPropagation(); sel = sel === n.id ? null : n.id; draw(); info(); };
      gn.append(g);
    }
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.w}`);
    info.pos = pos;
  }

  const nameOf = id => { const n = data && data.nodes.find(x => x.id === id); return n ? nodeName(n) : id; };
  const linked = id => id === sel || data.links.some(l => (l.a === sel && l.b === id) || (l.b === sel && l.a === id));

  function info() {
    const n = sel && data.nodes.find(x => x.id === sel);
    if (!n) { ui.info.textContent = 'Натисніть вузол, щоб підсвітити його зв\'язки.'; return; }
    const p = (info.pos || {})[n.id], mine = data.links.filter(l => l.a === n.id || l.b === n.id);
    const sum = f => mine.filter(f).reduce((s, l) => s + l.n, 0);
    ui.info.textContent = `${n.name} · ${n.id} · ${n.repeater ? 'ретранслятор' : n.role}` +
      (n.hops != null ? ` · ${n.hops} стрибків` : '') + (p && p.dist != null ? ` · ${fmtKm(p.dist)} від нас` : ' · координат немає') +
      ` · вгору ${sum(l => l.flow === 'up')} пак., вниз ${sum(l => l.flow === 'down')} пак.`;
  }

  function table() {
    ui.body.replaceChildren();
    const rows = [...data.links].sort((a, b) => b.n - a.n).slice(0, 300);
    const KIND = {direct: 'пряме', relay: 'через ретранслятор', via: 'через невідомі вузли'};
    for (const l of rows) {
      const tr = ui.body.insertRow();
      tr.insertCell().textContent = nameOf(l.a);
      tr.insertCell().textContent = nameOf(l.b);
      const d = tr.insertCell();
      d.textContent = l.flow === 'up' ? '▲ вгору' : '▼ вниз';
      d.style.color = (l.flow === 'up') !== ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
      tr.insertCell().textContent = KIND[l.kind] || l.kind;
      tr.insertCell().textContent = l.n;
      tr.insertCell().textContent = l.snr != null ? l.snr : '—';
      tr.insertCell().textContent = ago(Date.now() / 1000 - l.t);
    }
  }

  function fillDays(cur) {
    const days = data.days.map(d => d.day);
    if (!days.includes(data.today)) days.unshift(data.today);
    if (!days.includes(cur)) days.push(cur);
    days.sort().reverse();
    ui.day.replaceChildren(...days.map(d => {
      const o = document.createElement('option'), s = data.days.find(x => x.day === d);
      o.value = d; o.textContent = d + (d === data.today ? ' · сьогодні' : '') + (s ? ` · ${s.packets} пак.` : ' · порожньо');
      return o;
    }));
    ui.day.value = cur;
    ui.prev.disabled = ui.day.selectedIndex >= days.length - 1;
    ui.next.disabled = ui.day.selectedIndex <= 0;
  }

  async function load() {
    try {
      const day = ui.day.value || '';
      const r = await api(`/api/mesh?day=${encodeURIComponent(day)}${ui.mqtt.checked ? '&mqtt=1' : ''}`);
      data = r;
      if (sel && !data.nodes.some(n => n.id === sel)) sel = null;
      fillDays(r.day);
      const pk = data.links.reduce((s, l) => s + l.n, 0);
      ui.note.textContent = data.links.length
        ? `${data.nodes.length} вузлів · ${data.links.length} напрямків зв'язку · ${pk} пакетів за ${r.day}.` +
          (data.origin ? '' : ' Задайте розташування в «Налаштуваннях» — тоді вузли стануть за справжньою відстанню.')
        : `За ${r.day} зв'язків ще немає: вони накопичуються з кожного прийнятого пакета.`;
      draw(); info(); table();
    } catch (e) { ui.note.textContent = e.message; }
  }

  // масштаб колесом і перетягування
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const rc = svg.getBoundingClientRect(), f = ev.deltaY < 0 ? 0.85 : 1 / 0.85, w = Math.min(1800, Math.max(120, view.w * f));
    const mx = view.x + (ev.clientX - rc.left) / rc.width * view.w, my = view.y + (ev.clientY - rc.top) / rc.height * view.w;
    view = {x: mx - (mx - view.x) * w / view.w, y: my - (my - view.y) * w / view.w, w};
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.w}`);
  }, {passive: false});
  let drag = null;
  svg.addEventListener('pointerdown', ev => { drag = {x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y, moved: false}; });
  window.addEventListener('pointermove', ev => {
    if (!drag) return;
    const rc = svg.getBoundingClientRect(), dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    view.x = drag.vx - dx / rc.width * view.w; view.y = drag.vy - dy / rc.height * view.w;
    svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.w}`);
  });
  window.addEventListener('pointerup', () => { drag = null; });
  svg.addEventListener('click', () => { if (sel && !(drag && drag.moved)) { sel = null; draw(); info(); } });

  ui.reset.onclick = () => { view = {...HOME}; draw(); };
  ui.day.onchange = load;
  ui.prev.onclick = () => { ui.day.selectedIndex += 1; load(); };
  ui.next.onclick = () => { ui.day.selectedIndex -= 1; load(); };
  ui.rings.onchange = () => { store.set('rings', ui.rings.value); draw(); };
  ui.via.onchange = ui.swap.onchange = () => { store.set('via', ui.via.checked ? '1' : '0'); store.set('swap', ui.swap.checked ? '1' : '0'); legend(); draw(); table(); };
  ui.mqtt.onchange = () => { store.set('mqtt', ui.mqtt.checked ? '1' : '0'); load(); };

  function legend() {
    const up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    ui.legend.replaceChildren();
    for (const [col, txt] of [[up, '▲ вгору — до нас: що вузли передають нам'], [down, '▼ вниз — від нас: наші передачі, шлях «туди» з «Маршруту»']]) {
      const s = document.createElement('span'); s.textContent = txt; s.style.color = col; ui.legend.append(s);
    }
    const t = document.createElement('span'); t.className = 'hint';
    t.textContent = '◆ ретранслятор · ● вузол · пунктир — через невідомі вузли · товщина — кількість пакетів';
    ui.legend.append(t);
  }
  legend();

  load();
  setInterval(() => { if (!document.hidden && (!data || ui.day.value === data.today)) load(); }, 60000);
})();
