// Карта мережі: кола стрибків (1…7) навколо цього вузла, вузли на колі за своїми стрибками й справжнім азимутом,
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
  const ui = {day: $('#mday'), prev: $('#mprev'), next: $('#mnext'), via: $('#mvia'),
              mqtt: $('#mmqtt'), swap: $('#mswap'), reset: $('#mreset'), zin: $('#mzin'), zout: $('#mzout'), note: $('#mnote'), info: $('#minfo'),
              body: $('#mlinks'), legend: $('#mlegend')};
  let data = null, sel = null, view = {...HOME};
  let auto = true;  // автомасштаб: вмістити всі вузли; вимикається ручним масштабом чи зсувом, вмикається кнопкою «авто»

  function fit(pos) {
    const pts = Object.values(pos);
    if (!pts.length) return {...HOME};
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = Math.min(HOME.w, Math.max(200, Math.max(x1 - x0, y1 - y0) + 120));  // поле під підписи
    return {x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - w / 2, w};
  }
  const setView = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.w}`);
  function zoomAt(fx, fy, f) {  // fx, fy — точка в частках вікна (0..1), яка лишається на місці
    auto = false;
    const w = Math.min(1800, Math.max(80, view.w * f)), mx = view.x + fx * view.w, my = view.y + fy * view.w;
    view = {x: mx - fx * w, y: my - fy * w, w};
    setView();
  }

  for (const k of ['via', 'mqtt', 'swap']) ui[k].checked = store.get(k, k === 'via' ? '1' : '0') === '1';

  const RINGS = 7;  // 1…7 хопів: стільки найбільше дозволяє Meshtastic
  const hopWord = n => n === 1 ? 'хоп' : n < 5 ? 'хопи' : 'хопів';
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

  // Стрибки вузла: з вузла (hopsAway), інакше з графа зв'язків — найкоротший шлях від нас мінус пряме чуття.
  function hopsMap(nodes, links, me) {
    const adj = {};
    for (const l of links) { (adj[l.a] = adj[l.a] || []).push(l.b); (adj[l.b] = adj[l.b] || []).push(l.a); }
    const dist = {[me]: 0}, queue = [me];
    for (let i = 0; i < queue.length; i++) for (const x of adj[queue[i]] || []) if (!(x in dist)) { dist[x] = dist[queue[i]] + 1; queue.push(x); }
    const out = {};
    for (const n of nodes) out[n.id] = n.hops ?? (n.id in dist ? Math.max(0, dist[n.id] - 1) : null);
    return out;
  }
  const hashAngle = id => { let h = 0; for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h % 3600 / 3600 * 2 * Math.PI; };

  // Кола — стрибки: 1…7 (hopsAway), прямі сусіди (0) — всередині першого кола. Кут — справжній азимут,
  // а без координат — стабільний за id.
  function layout(nodes, origin, hops) {
    const S = MAXR / (RINGS + 1.4), pos = {};
    let unknown = false;
    for (const n of nodes) {
      if (n.me) { pos[n.id] = {x: C, y: C, dist: 0, th: 0, r: 0, brg: 0}; continue; }
      const h = hops[n.id], known = origin && hasPos(n);
      const r = h == null ? (RINGS + 0.9) * S : h === 0 ? 0.55 * S : Math.min(h, RINGS) * S;
      if (h == null) unknown = true;
      const th = known ? bearing(origin, n) : hashAngle(n.id);
      pos[n.id] = {x: C + r * Math.sin(th), y: C - r * Math.cos(th), dist: known ? haversine(origin, n) : null, th, r, brg: th, hasBrg: !!known, hops: h};
    }
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
    return {pos, S, hasUnknown: unknown};
  }

  function draw() {
    hideTip();  // вузли перемальовуються — pointerleave для старих не прийде
    svg.replaceChildren();
    if (!data) return;
    const up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    const showVia = ui.via.checked;
    const L = layout(data.nodes, data.origin, hopsMap(data.nodes, data.links, data.me)), pos = L.pos;
    const defs = el('defs');
    for (const [id, col] of [['arUp', up], ['arDown', down]]) {
      const m = el('marker', {id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: 'userSpaceOnUse', orient: 'auto'});
      m.append(el('path', {d: 'M0,0L10,5L0,10z', style: `fill:${col}`}));
      defs.append(m);
    }
    svg.append(defs);

    // кола стрибків
    const g0 = el('g'); svg.append(g0);
    for (let i = 1; i <= RINGS; i++) {
      g0.append(el('circle', {cx: C, cy: C, r: i * L.S, fill: 'none', stroke: 'var(--line)', 'stroke-width': 1}));
      g0.append(el('text', {x: C + 4, y: C - i * L.S - 3, fill: 'var(--text-low)', 'font-size': 11}, `${i} ${hopWord(i)}`));
    }
    if (L.hasUnknown) {
      g0.append(el('circle', {cx: C, cy: C, r: (RINGS + 0.9) * L.S, fill: 'none', stroke: 'var(--line)', 'stroke-dasharray': '1 5'}));
      g0.append(el('text', {x: C + 4, y: C - (RINGS + 0.9) * L.S - 3, fill: 'var(--text-low)', 'font-size': 11}, 'стрибки невідомі'));
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
      const full = n.name || n.short || n.id;
      g.append(el('text', {x: p.x, y: p.y + (n.me ? 25 : 21), 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--text-hi)'},
        full.length > 26 ? full.slice(0, 25) + '…' : full));
      g.addEventListener('pointerenter', ev => tip(n, p, ev));
      g.addEventListener('pointermove', ev => placeTip(ev));
      g.addEventListener('pointerleave', hideTip);
      g.onclick = ev => { ev.stopPropagation(); sel = sel === n.id ? null : n.id; draw(); info(); };
      gn.append(g);
    }
    if (auto) view = fit(pos);
    setView();
    info.pos = pos;
  }

  // спливаюче вікно з даними про вузол при наведенні
  const tipBox = document.createElement('div');
  tipBox.style.cssText = 'position:fixed;z-index:50;pointer-events:none;display:none;max-width:320px;padding:10px 12px;' +
    'background:var(--sky-900);color:var(--text-hi);border:1px solid var(--signal);border-radius:8px;font-size:12px;' +
    'line-height:1.5;box-shadow:0 6px 24px rgba(0,0,0,.45)';
  document.body.append(tipBox);
  const COMPASS = ['пн', 'пн-сх', 'сх', 'пд-сх', 'пд', 'пд-зх', 'зх', 'пн-зх'];
  function tip(n, p, ev) {
    const mine = data.links.filter(l => l.a === n.id || l.b === n.id);
    const sum = f => mine.filter(f).reduce((s, l) => s + l.n, 0);
    const deg = p.dist != null && !n.me ? ((p.brg * 180 / Math.PI) % 360 + 360) % 360 : null;
    const rows = [
      ['ID', n.id + (n.short ? ` · ${n.short}` : '')],
      ['Роль', n.me ? 'цей вузол' : n.repeater ? 'ретранслятор' : n.role],
      n.hw ? ['Плата', n.hw] : null,
      p.hops != null ? ['Стрибків', p.hops + (n.hops == null ? ' (за зв\'язками)' : '')] : null,
      p.dist != null && !n.me ? ['Відстань', `${fmtKm(p.dist)}, ${Math.round(deg)}° ${COMPASS[Math.round(deg / 45) % 8]}`] : null,
      hasPos(n) ? ['Координати', `${n.lat.toFixed(5)}, ${n.lon.toFixed(5)}`] : ['Координати', 'невідомі'],
      n.snr != null ? ['SNR', n.snr + ' дБ'] : null,
      n.rssi != null ? ['RSSI', n.rssi + ' дБм'] : null,
      n.heard ? ['Востаннє чутно', ago(n.heard) + ' тому'] : null,
      ['Пакетів за добу', `▲ ${sum(l => l.flow === 'up')} · ▼ ${sum(l => l.flow === 'down')}`],
      n.mqtt ? ['Джерело', 'через MQTT-міст'] : null,
    ].filter(Boolean);
    tipBox.replaceChildren();
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:700;font-size:13px;margin-bottom:4px';
    h.textContent = n.name || n.id;
    tipBox.append(h);
    for (const [k, v] of rows) {
      const d = document.createElement('div'), a = document.createElement('span'), b = document.createElement('span');
      a.style.color = 'var(--text-mid)'; a.textContent = k + ': '; b.textContent = v;
      d.append(a, b); tipBox.append(d);
    }
    tipBox.style.display = 'block';
    placeTip(ev);
  }
  function placeTip(ev) {
    const w = tipBox.offsetWidth, h = tipBox.offsetHeight;
    const x = ev.clientX + 16 + w > innerWidth ? ev.clientX - 16 - w : ev.clientX + 16;
    const y = ev.clientY + 16 + h > innerHeight ? Math.max(4, innerHeight - h - 4) : ev.clientY + 16;
    tipBox.style.left = Math.max(4, x) + 'px'; tipBox.style.top = y + 'px';
  }
  const hideTip = () => { tipBox.style.display = 'none'; };
  svg.addEventListener('pointerleave', hideTip);

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
      tr.insertCell().textContent = ago(l.t) + ' тому';
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
    const rc = svg.getBoundingClientRect();
    zoomAt((ev.clientX - rc.left) / rc.width, (ev.clientY - rc.top) / rc.height, ev.deltaY < 0 ? 0.85 : 1 / 0.85);
  }, {passive: false});
  let drag = null;
  svg.addEventListener('pointerdown', ev => { drag = {x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y, moved: false}; });
  window.addEventListener('pointermove', ev => {
    if (!drag) return;
    const rc = svg.getBoundingClientRect(), dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; auto = false; }
    view.x = drag.vx - dx / rc.width * view.w; view.y = drag.vy - dy / rc.height * view.w;
    setView();
  });
  window.addEventListener('pointerup', () => { drag = null; });
  svg.addEventListener('click', () => { if (sel && !(drag && drag.moved)) { sel = null; draw(); info(); } });

  ui.reset.onclick = () => { auto = true; draw(); };
  ui.zin.onclick = () => zoomAt(0.5, 0.5, 0.7);
  ui.zout.onclick = () => zoomAt(0.5, 0.5, 1 / 0.7);
  ui.day.addEventListener('change', () => { auto = true; });  // інша доба — знову вмістити все
  ui.day.onchange = load;
  ui.prev.onclick = () => { ui.day.selectedIndex += 1; load(); };
  ui.next.onclick = () => { ui.day.selectedIndex -= 1; load(); };
  ui.via.onchange = ui.swap.onchange = () => { store.set('via', ui.via.checked ? '1' : '0'); store.set('swap', ui.swap.checked ? '1' : '0'); legend(); draw(); table(); };
  ui.mqtt.onchange = () => { store.set('mqtt', ui.mqtt.checked ? '1' : '0'); load(); };

  function legend() {
    const up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    ui.legend.replaceChildren();
    for (const [col, txt] of [[up, '▲ вгору — до нас: що вузли передають нам'], [down, '▼ вниз — від нас: наші пакети, які сусіди ретранслювали, і «Маршрут»']]) {
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
