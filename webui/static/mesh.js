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
  const C = 450, HOME = {x: 0, y: 0, w: 900};
  const ui = {day: $('#mday'), prev: $('#mprev'), next: $('#mnext'), via: $('#mvia'),
              mqtt: $('#mmqtt'), swap: $('#mswap'), reset: $('#mreset'), zin: $('#mzin'), zout: $('#mzout'), note: $('#mnote'), info: $('#minfo'),
              body: $('#mlinks'), legend: $('#mlegend')};
  const up = () => ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = () => ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
  let data = null, sel = null, view = {...HOME};
  let auto = true;  // автомасштаб: вмістити всі вузли; вимикається ручним масштабом чи зсувом, вмикається кнопкою «авто»

  function fit(pos) {
    const pts = Object.values(pos);
    if (!pts.length) return {...HOME};
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = Math.max(240, Math.max(x1 - x0, y1 - y0) + 300);  // поле під підписи, що виходять за вузли
    return {x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - w / 2, w};
  }
  const setView = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.w}`);
  function zoomAt(fx, fy, f) {  // fx, fy — точка в частках вікна (0..1), яка лишається на місці
    auto = false;
    const w = Math.min(8000, Math.max(80, view.w * f)), mx = view.x + fx * view.w, my = view.y + fy * view.w;
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
  const nodeName = n => n.name || n.short || n.id;

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
  // Щоб значки не налазили: радіус кола росте з кількістю вузлів на ньому, сусіди на колі розсуваються за кутом,
  // а на щільному колі кожен другий зсунутий по радіусу (зигзаг).
  // Остаточно значки й підписи не накладаються: після розкладки по колах кожен вузол розглядається як прямокутник
  // «значок + підпис» (підпис іде від центру назовні), а накладені прямокутники розсуваються — переважно вздовж
  // кола, трохи й по радіусу. Якщо місця на колі не вистачає, воно розширюється й розкладка повторюється.
  const GAP = 46, RING_STEP = 80, RING_STEPS = {1: 190, 2: 160}, TWO_PI = 2 * Math.PI;
  const LABEL_CH = 7, LABEL_MAX = 22;
  const labelWidth = n => Math.min((n.name || n.short || n.id).length, LABEL_MAX) * LABEL_CH;
  const ME_BOX = [C - 40, C - 16, C + 40, C + 42];  // центральний вузол із підписом під ним
  const overlap = (a, b, mx, my) => [Math.min(a[2], b[2]) - Math.max(a[0], b[0]) + mx, Math.min(a[3], b[3]) - Math.max(a[1], b[1]) + my];

  function layout(nodes, origin, hops) {
    const groups = new Map(), pos = {};
    for (const n of nodes) {
      if (n.me) { pos[n.id] = {x: C, y: C, dist: 0, th: 0, r: 0, brg: 0}; continue; }
      const h = hops[n.id], key = h == null ? 'x' : Math.min(h, RINGS), known = origin && hasPos(n);
      if (!groups.has(key)) groups.set(key, []);
      const th = known ? bearing(origin, n) : hashAngle(n.id);
      groups.get(key).push({n, th, brg: th, dist: known ? haversine(origin, n) : null, hops: h, key, lw: labelWidth(n)});
    }
    const extra = {};  // скільки докинули до радіуса кола, коли на ньому не вистачило місця
    let R = {}, items = [];
    for (let attempt = 0; attempt < 12; attempt++) {
      const need = key => { const m = (groups.get(key) || []).length; return m * GAP / TWO_PI / (m > 6 ? 1.6 : 1); };
      // пропорції: ближні кола просторіші (там найбільше вузлів), далі крок менший
      R = {0: Math.max(95, need(0)) + (extra[0] || 0)};
      for (let h = 1; h <= RINGS; h++) R[h] = Math.max(R[h - 1] + (RING_STEPS[h] || RING_STEP), need(h)) + (extra[h] || 0);
      R.x = Math.max(R[RINGS] + RING_STEP, need('x')) + (extra.x || 0);
      items = [];
      for (const [key, list] of groups) {
        const r = R[key], m = list.length, min = Math.min(GAP / r * (m > 6 ? 0.62 : 1), TWO_PI / m);  // мін. кут між сусідами
        for (const q of list) q.th = q.brg;  // кожна спроба — від справжнього азимута
        for (let it = 0; it < 300 && m > 1; it++) {
          for (const q of list) q.th = (q.th % TWO_PI + TWO_PI) % TWO_PI;
          list.sort((a, b) => a.th - b.th);
          let moved = false;
          for (let i = 0; i < m; i++) {
            const a = list[i], b = list[(i + 1) % m], d = b.th - a.th + (i === m - 1 ? TWO_PI : 0);
            if (d < min - 1e-6) { const push = (min - d) / 2; a.th -= push; b.th += push; moved = true; }
          }
          if (!moved) break;
        }
        list.sort((a, b) => a.th - b.th).forEach((q, i) => {
          q.r0 = r; q.r = r + (m > 6 ? (i % 2 ? 9 : -9) : 0);
          q.band = key === 0 ? 26 : 34;  // на скільки вузол може відійти від свого кола
          items.push(q);
        });
      }
      const bad = relax(items);
      if (!bad.size) break;
      for (const key of bad) extra[key] = (extra[key] || 0) + 32;  // тісно: розширюємо ці кола й пробуємо знову
    }
    for (const q of items) {
      pos[q.n.id] = {x: C + q.r * Math.sin(q.th), y: C - q.r * Math.cos(q.th), dist: q.dist, th: q.th, r: q.r, brg: q.brg, hops: q.hops};
    }
    return {pos, R, hasDirect: groups.has(0), hasUnknown: groups.has('x')};
  }

  // Розсуває накладені прямокутники; повертає ключі кіл, де накладання лишилися.
  function relax(items) {
    const put = q => { q.x = C + q.r * Math.sin(q.th); q.y = C - q.r * Math.cos(q.th); };
    const boxOf = q => q.x >= C ? [q.x - 13, q.y - 12, q.x + 19 + q.lw, q.y + 12] : [q.x - 19 - q.lw, q.y - 12, q.x + 13, q.y + 12];
    const MX = 4, MY = 3;
    for (let it = 0; it < 250; it++) {
      items.forEach(put);
      const boxes = items.map(boxOf), dx = items.map(() => 0), dy = items.map(() => 0);
      let any = false;
      for (let a = 0; a < items.length; a++) {
        const me = overlap(boxes[a], ME_BOX, MX, MY);  // від центрального вузла відштовхуємо лише цей
        if (me[0] > 0 && me[1] > 0) { any = true; const ca = (boxes[a][0] + boxes[a][2]) / 2, cb = (boxes[a][1] + boxes[a][3]) / 2; if (me[0] < me[1]) dx[a] += (ca >= C ? 1 : -1) * (me[0] + 1); else dy[a] += (cb >= C ? 1 : -1) * (me[1] + 1); }
        for (let b = a + 1; b < items.length; b++) {
          const [ox, oy] = overlap(boxes[a], boxes[b], MX, MY);
          if (ox <= 0 || oy <= 0) continue;
          any = true;
          if (ox < oy) {  // легше розвести по горизонталі
            const s = ox / 2 + 0.5, sg = (boxes[b][0] + boxes[b][2]) >= (boxes[a][0] + boxes[a][2]) ? 1 : -1;
            dx[a] -= sg * s; dx[b] += sg * s;
          } else {
            const s = oy / 2 + 0.5, sg = (boxes[b][1] + boxes[b][3]) >= (boxes[a][1] + boxes[a][3]) ? 1 : -1;
            dy[a] -= sg * s; dy[b] += sg * s;
          }
        }
      }
      if (!any) return new Set();
      items.forEach((q, i) => {  // назад у полярні координати: кут вільний, радіус — у межах смуги свого кола
        const x = q.x + dx[i], y = q.y + dy[i];
        q.th = Math.atan2(x - C, C - y);
        q.r = Math.min(q.r0 + q.band, Math.max(q.r0 - q.band, 40, Math.hypot(x - C, y - C)));
      });
    }
    items.forEach(put);  // що лишилося — кола, на яких і після розсування тісно
    const boxes = items.map(boxOf), bad = new Set();
    for (let a = 0; a < items.length; a++) {
      const me = overlap(boxes[a], ME_BOX, MX, MY);
      if (me[0] > 0 && me[1] > 0) bad.add(items[a].key);
      for (let b = a + 1; b < items.length; b++) {
        const [ox, oy] = overlap(boxes[a], boxes[b], 0, 0);
        if (ox > 0 && oy > 0) { bad.add(items[a].key); bad.add(items[b].key); }
      }
    }
    return bad;
  }

  // Ролі вузлів Meshtastic: підпис, колір і своя форма значка
  const ROLES = {
    CLIENT: ['Клієнт', '#8fa3c4'], CLIENT_MUTE: ['Клієнт Mute (не ретранслює)', '#7d8dab'],
    CLIENT_HIDDEN: ['Клієнт прихований', '#7d8dab'], CLIENT_BASE: ['Базовий клієнт', '#5ec2e6'],
    ROUTER: ['Роутер', '#ffb454'], ROUTER_CLIENT: ['Роутер-клієнт', '#ffb454'], ROUTER_LATE: ['Роутер (пізня ретрансляція)', '#ffb454'],
    REPEATER: ['Репітер', '#ff8a3d'], TRACKER: ['Трекер', '#c77dff'], SENSOR: ['Датчик', '#4ade80'],
    TAK: ['TAK', '#7bd88f'], TAK_TRACKER: ['TAK-трекер', '#7bd88f'], LOST_AND_FOUND: ['Загублені речі', '#ff7a90'],
  };
  const roleOf = n => ROLES[n.role] ? n.role : 'CLIENT';
  const ROUTERISH = new Set(['ROUTER', 'ROUTER_CLIENT', 'ROUTER_LATE', 'REPEATER']);
  const MQTT_COLOR = '#b07cff';
  function icon(role, x, y) {
    const c = ROLES[role][1], g = el('g'), add = (t, a) => g.append(el(t, a));
    const solid = {fill: c}, ring = {fill: 'none', stroke: c, 'stroke-width': 2}, cut = 'var(--sky-900)';
    const diamond = `M${x},${y - 10} L${x + 10},${y} L${x},${y + 10} L${x - 10},${y} Z`;
    switch (role) {
      case 'CLIENT_MUTE':
        add('circle', {cx: x, cy: y, r: 8, ...ring}); add('line', {x1: x - 6, y1: y + 6, x2: x + 6, y2: y - 6, stroke: c, 'stroke-width': 2}); break;
      case 'CLIENT_HIDDEN': add('circle', {cx: x, cy: y, r: 8, ...ring, 'stroke-dasharray': '3 2'}); break;
      case 'CLIENT_BASE': add('path', {d: `M${x - 9},${y} L${x},${y - 9} L${x + 9},${y} L${x + 9},${y + 8} L${x - 9},${y + 8} Z`, ...solid}); break;
      case 'ROUTER': add('path', {d: diamond, ...solid}); break;
      case 'ROUTER_CLIENT': add('path', {d: diamond, ...solid}); add('circle', {cx: x, cy: y, r: 3, fill: cut}); break;
      case 'ROUTER_LATE': add('path', {d: diamond, ...ring}); add('line', {x1: x - 4, y1: y, x2: x + 4, y2: y, stroke: c, 'stroke-width': 2}); break;
      case 'REPEATER': add('polygon', {points: `${x - 5},${y - 9} ${x + 5},${y - 9} ${x + 10},${y} ${x + 5},${y + 9} ${x - 5},${y + 9} ${x - 10},${y}`, ...solid}); break;
      case 'TRACKER':
        add('path', {d: `M${x},${y + 11} C${x - 12},${y - 2} ${x - 8},${y - 11} ${x},${y - 11} C${x + 8},${y - 11} ${x + 12},${y - 2} ${x},${y + 11} Z`, ...solid});
        add('circle', {cx: x, cy: y - 3, r: 3, fill: cut}); break;
      case 'SENSOR':
        add('rect', {x: x - 7, y: y - 7, width: 14, height: 14, rx: 2, ...solid});
        add('polyline', {points: `${x - 4},${y + 1} ${x - 2},${y - 3} ${x},${y + 3} ${x + 2},${y - 3} ${x + 4},${y + 1}`, fill: 'none', stroke: cut, 'stroke-width': 1.5}); break;
      case 'TAK': add('path', {d: `M${x - 8},${y - 8} H${x + 8} V${y + 2} L${x},${y + 10} L${x - 8},${y + 2} Z`, ...solid}); break;
      case 'TAK_TRACKER': add('path', {d: `M${x - 8},${y - 8} H${x + 8} V${y + 2} L${x},${y + 10} L${x - 8},${y + 2} Z`, ...ring}); break;
      case 'LOST_AND_FOUND': add('circle', {cx: x, cy: y, r: 9, ...ring}); add('circle', {cx: x, cy: y, r: 4, ...solid}); break;
      default: add('circle', {cx: x, cy: y, r: 8, ...solid});
    }
    return g;
  }
  const isRelay = n => n.repeater && !ROUTERISH.has(roleOf(n));  // клієнт, що ретранслював пакети за даними

  // легенда в лівому нижньому куті карти: лише те, що є на карті
  function drawKey(nodes) {
    const key = $('#mkey');
    key.replaceChildren();
    const items = [];
    if (nodes.some(n => n.me)) items.push([() => el('circle', {cx: 0, cy: 0, r: 9, fill: 'var(--signal)'}), 'Цей вузол']);
    for (const role of Object.keys(ROLES)) {
      if (nodes.some(n => !n.me && roleOf(n) === role)) items.push([() => icon(role, 0, 0), ROLES[role][0]]);
    }
    if (nodes.some(n => !n.me && isRelay(n))) items.push([() => el('circle', {cx: 0, cy: 0, r: 10, fill: 'none', stroke: 'var(--warn)', 'stroke-width': 1.5}), 'Ретранслював пакети']);
    if (nodes.some(n => n.mqtt)) items.push([() => el('circle', {cx: 0, cy: 0, r: 10, fill: 'none', stroke: MQTT_COLOR, 'stroke-width': 1.5, 'stroke-dasharray': '2 3'}), 'Через MQTT']);
    key.hidden = !items.length;
    for (const [make, label] of items) {
      const row = document.createElement('div'), s = el('svg', {viewBox: '-13 -13 26 26', width: 18, height: 18});
      row.style.cssText = 'display:flex;align-items:center;gap:6px;white-space:nowrap';
      s.append(make());
      const t = document.createElement('span'); t.textContent = label;
      row.append(s, t); key.append(row);
    }
  }

  function draw() {
    hideTip();  // вузли перемальовуються — pointerleave для старих не прийде
    svg.replaceChildren();
    if (!data) return;
    const up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    const showVia = ui.via.checked;
    nextHop = buildRoutes();
    prevHop = buildDown();
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
    const ringLine = (r, label, dash) => {
      g0.append(el('circle', {cx: C, cy: C, r, fill: 'none', stroke: 'var(--line)', 'stroke-width': 1, ...(dash ? {'stroke-dasharray': dash} : {})}));
      g0.append(el('text', {x: C + 4, y: C - r - 3, fill: 'var(--text-low)', 'font-size': 12}, label));
    };
    if (L.hasDirect) ringLine(L.R[0], 'напряму', '2 5');
    for (let i = 1; i <= RINGS; i++) ringLine(L.R[i], `${i} ${hopWord(i)}`);
    if (L.hasUnknown) ringLine(L.R.x, 'стрибки невідомі', '1 5');

    // зв'язки: об'єднуємо різні види одного напрямку; пунктир — лише коли шлях іде через невідомі вузли
    const merged = new Map();
    for (const l of data.links) {
      if (l.kind === 'via' && !showVia) continue;
      if (!pos[l.a] || !pos[l.b]) continue;
      const key = `${l.a}|${l.b}|${l.flow}`, m = merged.get(key);
      if (m) { m.n += l.n; m.via = m.via && l.kind === 'via'; m.snr = m.snr ?? l.snr; } else merged.set(key, {...l, via: l.kind === 'via'});
    }
    const both = new Set([...merged.values()].map(l => `${l.a}|${l.b}`));
    const gl = el('g'), gb = el('g'); svg.append(gl, gb);
    // шляхи вибраного вузла: вгору (до нас) і вниз (від нас) можуть бути різними — кроки яскраві й пронумеровані
    const routeUp = sel ? routeOf(sel) : null, routeDown = sel ? routeDownOf(sel) : null;
    const route = [...(routeUp || []), ...(routeDown || [])];
    const step = new Map([...(routeUp || []).map((l, i) => [`${l.a}|${l.b}|up`, i + 1]), ...(routeDown || []).map((l, i) => [`${l.a}|${l.b}|down`, i + 1])]);
    for (const l of merged.values()) {
      const p = pos[l.a], q = pos[l.b], dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
      const off = both.has(`${l.b}|${l.a}`) || merged.has(`${l.b}|${l.a}|${l.flow === 'up' ? 'down' : 'up'}`) ? 7 : 0;
      const ux = dx / len, uy = dy / len, sx = p.x + ux * 12, sy = p.y + uy * 12, ex = q.x - ux * 15, ey = q.y - uy * 15;
      const cx = (sx + ex) / 2 - uy * off * 2, cy = (sy + ey) / 2 + ux * off * 2;
      const hot = step.has(`${l.a}|${l.b}|${l.flow}`), touch = l.a === sel || l.b === sel;
      const opacity = !sel ? 0.75 : hot ? 1 : touch ? 0.45 : 0.06;
      const path = el('path', {d: `M${sx.toFixed(1)},${sy.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`,
        fill: 'none', stroke: l.flow === 'up' ? up : down, 'stroke-width': (1 + Math.log2(l.n + 1) * 0.4 + (hot ? 2 : 0)).toFixed(1),
        'stroke-opacity': opacity, 'marker-end': `url(#${l.flow === 'up' ? 'arUp' : 'arDown'})`});
      if (hot) {  // номер кроку на лінії маршруту
        const bx = (sx + 2 * cx + ex) / 4, by = (sy + 2 * cy + ey) / 4;
        gb.append(el('circle', {cx: bx, cy: by, r: 9, fill: l.flow === 'up' ? up : down}));
        gb.append(el('text', {x: bx, y: by + 4, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: 'var(--sky-900)'}, step.get(`${l.a}|${l.b}|${l.flow}`)));
      }
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
      const g = el('g', {style: 'cursor:pointer', opacity: sel && !linked(n.id) && !(route || []).some(l => l.a === n.id || l.b === n.id) ? 0.3 : 1});
      if (n.me) g.append(el('circle', {cx: p.x, cy: p.y, r: 12, fill: 'var(--signal)'}));
      else g.append(icon(roleOf(n), p.x, p.y));
      if (!n.me && isRelay(n)) g.append(el('circle', {cx: p.x, cy: p.y, r: 14, fill: 'none', stroke: 'var(--warn)', 'stroke-width': 1.5}));
      if (n.mqtt) g.append(el('circle', {cx: p.x, cy: p.y, r: 14, fill: 'none', stroke: MQTT_COLOR, 'stroke-width': 1.5, 'stroke-dasharray': '2 3'}));
      if (n.id === sel) g.append(el('circle', {cx: p.x, cy: p.y, r: 17, fill: 'none', stroke: 'var(--text-hi)', 'stroke-width': 2}));
      const full = n.name || n.short || n.id, text = full.length > 22 ? full.slice(0, 21) + '…' : full;
      const right = p.x >= C;  // підпис йде від центру назовні, обведення фоном читається поверх ліній
      g.append(el('text', {x: p.x + (n.me ? 0 : right ? 17 : -17), y: p.y + (n.me ? 27 : 4), 'text-anchor': n.me ? 'middle' : right ? 'start' : 'end',
        'font-size': 12, fill: 'var(--text-hi)', stroke: 'var(--sky-900)', 'stroke-width': 3.5, 'stroke-linejoin': 'round', 'paint-order': 'stroke'}, text));
      g.addEventListener('pointerenter', ev => tip(n, p, ev));
      g.addEventListener('pointermove', ev => placeTip(ev));
      g.addEventListener('pointerleave', hideTip);
      g.onclick = ev => { ev.stopPropagation(); sel = sel === n.id ? null : n.id; draw(); info(); routeTable(); };
      gn.append(g);
    }
    drawKey(data.nodes.filter(n => pos[n.id]));
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
      ['Роль', n.me ? 'цей вузол' : ROLES[roleOf(n)][0] + (ROLES[n.role] || !n.role ? '' : ` (${n.role})`) + (isRelay(n) ? ', ретранслював пакети' : '')],
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

  // Маршрут вузла до нас: найкоротший шлях за зв'язками «вгору»; серед рівних беремо той, де більше пакетів.
  function buildRoutes() {
    const radj = {}, next = {}, seen = {[data.me]: true}, queue = [data.me];
    for (const l of data.links) if (l.flow === 'up' && (l.kind !== 'via' || ui.via.checked)) (radj[l.b] = radj[l.b] || []).push(l);
    for (let i = 0; i < queue.length; i++) {
      for (const l of (radj[queue[i]] || []).slice().sort((x, y) => y.n - x.n)) {
        if (!seen[l.a]) { seen[l.a] = true; next[l.a] = l; queue.push(l.a); }
      }
    }
    return next;
  }
  let nextHop = {}, prevHop = {};
  function routeOf(id) {
    const out = [], seen = new Set();
    let cur = id;
    while (cur !== data.me && nextHop[cur] && !seen.has(cur)) { seen.add(cur); out.push(nextHop[cur]); cur = nextHop[cur].b; }
    return cur === data.me && out.length ? out : null;
  }
  // Шлях «вниз» (від нас до вузла): за зв'язками «вниз» від нас уперед. Відомий лише для вузлів, до яких ходив «Маршрут».
  function buildDown() {
    const out = {}, prev = {}, seen = {[data.me]: true}, queue = [data.me];
    for (const l of data.links) if (l.flow === 'down' && (l.kind !== 'via' || ui.via.checked)) (out[l.a] = out[l.a] || []).push(l);
    for (let i = 0; i < queue.length; i++) {
      for (const l of (out[queue[i]] || []).slice().sort((x, y) => y.n - x.n)) {
        if (!seen[l.b]) { seen[l.b] = true; prev[l.b] = l; queue.push(l.b); }
      }
    }
    return prev;
  }
  function routeDownOf(id) {
    const out = [], seen = new Set();
    let cur = id;
    while (cur !== data.me && prevHop[cur] && !seen.has(cur)) { seen.add(cur); out.unshift(prevHop[cur]); cur = prevHop[cur].a; }
    return cur === data.me && out.length ? out : null;
  }

  const nameOf = id => { const n = data && data.nodes.find(x => x.id === id); return n ? nodeName(n) : id; };
  const linked = id => id === sel || data.links.some(l => (l.a === sel && l.b === id) || (l.b === sel && l.a === id));

  function info() {
    const n = sel && data.nodes.find(x => x.id === sel);
    ui.info.style.whiteSpace = 'pre-line';
    if (!n) { ui.info.textContent = 'Натисніть вузол (на карті або в таблиці маршрутів), щоб підсвітити його шлях до нас.'; return; }
    const p = (info.pos || {})[n.id], mine = data.links.filter(l => l.a === n.id || l.b === n.id);
    const sum = f => mine.filter(f).reduce((s, l) => s + l.n, 0);
    const r = n.me ? null : routeOf(n.id), d = n.me ? null : routeDownOf(n.id);
    ui.info.textContent = `${n.name} · ${n.id} · ${n.me ? 'цей вузол' : ROLES[roleOf(n)][0]}` +
      (n.hops != null ? ` · ${n.hops} стрибків` : '') + (p && p.dist != null ? ` · ${fmtKm(p.dist)} від нас` : ' · координат немає') +
      ` · вгору ${sum(l => l.flow === 'up')} пак., вниз ${sum(l => l.flow === 'down')} пак.` +
      (n.me ? '' : '\n▲ Вгору (до нас): ' + (r ? routeText(r) : 'за даними цієї доби невідомий') +
        '\n▼ Вниз (від нас): ' + (d ? routeText(d) : 'невідомий — повний шлях вниз дає лише «Маршрут» (вкладка «Вузли»)'));
  }
  const routeText = r => [r[0].a, ...r.map(l => l.b)].map(nameOf).join(' → ');

  function routeTable() {
    const body = $('#mroutes');
    body.replaceChildren();
    const rows = data.nodes.filter(n => !n.me).map(n => ({n, r: routeOf(n.id)}));
    rows.sort((a, b) => (a.r ? a.r.length : 99) - (b.r ? b.r.length : 99) || (a.n.name || a.n.id).localeCompare(b.n.name || b.n.id));
    for (const {n, r} of rows) {
      const tr = body.insertRow();
      tr.style.cursor = 'pointer';
      if (n.id === sel) tr.style.background = 'var(--btn-signal-hover-bg)';
      tr.onclick = () => { sel = sel === n.id ? null : n.id; draw(); info(); routeTable(); };
      const name = tr.insertCell(); name.textContent = n.name || n.id;
      const dn = routeDownOf(n.id), c1 = tr.insertCell(), c2 = tr.insertCell();
      c1.textContent = r ? routeText(r) : 'невідомий';
      c2.textContent = dn ? routeText(dn) : 'невідомий';
      c1.style.color = up(); c2.style.color = dn ? down() : 'var(--text-low)';
      for (const c of [c1, c2]) { c.style.whiteSpace = 'normal'; c.style.minWidth = '200px'; }  // довгі шляхи переносяться, а не ховаються за прокруткою
      const weakest = r ? Math.min(...r.map(l => l.n)) : null;
      tr.insertCell().textContent = r ? weakest : '—';
      const snrs = r ? r.map(l => l.snr).filter(v => v != null) : [];
      tr.insertCell().textContent = snrs.length ? Math.min(...snrs) : '—';
    }
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
      const vw = r.view && r.view.on ? `Погляд вузла «${r.view.name}»: ` : '';
      ui.note.textContent = vw + (data.links.length
        ? `${data.nodes.length} вузлів · ${data.links.length} напрямків зв'язку · ${pk} пакетів за ${r.day}.` +
          (data.origin ? '' : ' Задайте розташування в «Налаштуваннях» — тоді вузли стануть за справжньою відстанню.')
        : `За ${r.day} зв'язків ще немає: вони накопичуються з кожного прийнятого пакета.`);
      draw(); info(); routeTable(); table();
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
  svg.addEventListener('click', () => { if (sel && !(drag && drag.moved)) { sel = null; draw(); info(); routeTable(); } });

  ui.reset.onclick = () => { auto = true; draw(); };
  ui.zin.onclick = () => zoomAt(0.5, 0.5, 0.7);
  ui.zout.onclick = () => zoomAt(0.5, 0.5, 1 / 0.7);
  ui.day.addEventListener('change', () => { auto = true; });  // інша доба — знову вмістити все
  ui.day.onchange = load;
  ui.prev.onclick = () => { ui.day.selectedIndex += 1; load(); };
  ui.next.onclick = () => { ui.day.selectedIndex -= 1; load(); };
  ui.via.onchange = ui.swap.onchange = () => { store.set('via', ui.via.checked ? '1' : '0'); store.set('swap', ui.swap.checked ? '1' : '0'); legend(); draw(); info(); routeTable(); table(); };
  ui.mqtt.onchange = () => { store.set('mqtt', ui.mqtt.checked ? '1' : '0'); load(); };

  function legend() {
    const up = ui.swap.checked ? 'var(--signal)' : 'var(--crit)', down = ui.swap.checked ? 'var(--crit)' : 'var(--signal)';
    ui.legend.replaceChildren();
    for (const [col, txt] of [[up, '▲ вгору — до нас: що вузли передають нам'], [down, '▼ вниз — від нас: наші пакети, які сусіди ретранслювали, і «Маршрут»']]) {
      const s = document.createElement('span'); s.textContent = txt; s.style.color = col; ui.legend.append(s);
    }
    const t = document.createElement('span'); t.className = 'hint';
    t.textContent = 'пунктирна лінія — через невідомі вузли · товщина — кількість пакетів · значки ролей — у куті карти';
    ui.legend.append(t);
  }
  legend();

  // автоматичні маршрути: перемикач і стан черги
  const trace = $('#mtrace'), tstat = $('#mtstat');
  async function loadTrace() {
    try {
      const r = await api('/api/autotrace');
      trace.checked = r.on;
      if (!r.on) { tstat.textContent = 'Авто-маршрути вимкнені.'; return; }
      const parts = [`Авто-маршрути: раз на ${Math.round(r.every / 3600)} год і для нових вузлів, до ${r.max_hops} стрибків, по одному`];
      if (r.current) parts.push(`зараз запитую ${nameOf(r.current)}`);
      if (r.queued) parts.push(`у черзі ${r.queued}`);
      if (r.next_cycle) parts.push(`наступне коло ${new Date(r.next_cycle * 1000).toLocaleTimeString('uk-UA', {hour: '2-digit', minute: '2-digit'})}`);
      if (r.last) parts.push(`останній: ${r.last.name} ${r.last.ok ? '✓' : '✗ без відповіді'}`);
      tstat.textContent = parts.join(' · ');
    } catch (e) { tstat.textContent = e.message; }
  }
  trace.onchange = async () => {
    try { await api('/api/autotrace', {on: trace.checked}); } catch (e) { tstat.textContent = e.message; }
    loadTrace();
  };

  load();
  loadTrace();
  setInterval(() => { if (!document.hidden) loadTrace(); }, 15000);
  setInterval(() => { if (!document.hidden && (!data || ui.day.value === data.today)) load(); }, 60000);
  window.addEventListener('meshpi:mesh-reload', load);  // «Джерело даних карти» змінило центр
})();
