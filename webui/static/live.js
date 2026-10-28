// Живий режим схеми мережі: кожен прийнятий пакет пролітає схемою світною кометою від відправника через ретранслятора до нашого
// вузла, вузол, що щойно передавав чи приймав, світиться, внизу стрічка останнього пакета, збоку шухляда «Трафік», а «Програти»
// повторює записане за 5-60 хв. Малює поверх схеми з mesh.js (window.meshSchema, afterDraw) і нічого в ній не змінює, поки режим вимкнений.
// Дані: /api/live (опитування раз на 2 с, лише коли режим увімкнений і вкладка видима). Лише відображення: у ефір нічого не передається.
// Значення з мережі вставляємо лише через textContent.
(function () {
  const toggle = document.getElementById('mlive');
  if (!toggle) return;
  const $id = id => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs || {}) e.setAttribute(k, attrs[k]); return e; };
  const store = {
    get(k, d) { try { const v = localStorage.getItem('live_' + k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('live_' + k, v); } catch (e) { /* без сховища — ок */ } },
  };
  const reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ui = {traffic: $id('livetraffic'), win: $id('liverepwin'), rep: $id('liverep'), stop: $id('livestop'), prog: $id('liveprog'), note: $id('livenote'),
              ticker: $id('liveticker'), drawer: $id('livedrawer'), rows: $id('liverows'), count: $id('livecount'), state: $id('livestate'),
              pause: $id('livepause'), close: $id('liveclose'), mqtt: $id('mmqtt')};
  const M = () => window.meshSchema;
  const svg = () => M() && M().svg;
  const mqttOn = () => !!(ui.mqtt && ui.mqtt.checked);
  const HOT_MS = 30000, FADE = 7000, RING = 1200;
  const hotUntil = {};  // id -> до якого часу вузол світиться
  let fx = null, flights = [], fades = [], rings = [], raf = 0, on = false;

  // ---------- шар ефектів поверх схеми ----------
  const pxScale = () => { const s = svg(); return s && s.clientWidth ? s.viewBox.baseVal.width / s.clientWidth : 1; };  // розмір 1 px екрана в одиницях схеми
  function layer() {
    if (!fx || !fx.isConnected) { fx = el('g', {'pointer-events': 'none'}); svg().append(fx); }
    return fx;
  }
  function afterDraw() {  // схему перемальовано (svg.replaceChildren): шар ефектів і кільця світіння створюємо заново
    fx = null;
    if (!on) return;
    layer();
    const pos = M().pos(), now = Date.now();
    for (const id in hotUntil) if (hotUntil[id] > now && pos[id]) fx.append(el('circle', {class: 'hotring', cx: pos[id].x, cy: pos[id].y, r: 17, 'data-hot': id}));
  }
  function pulse(id) {  // вузол, що щойно передавав чи приймав, світиться ~30 с
    if (!id || !M()) return;
    const had = (hotUntil[id] || 0) > Date.now();
    hotUntil[id] = Date.now() + HOT_MS;
    const p = M().pos()[id];
    if (!had && p && on) layer().append(el('circle', {class: 'hotring', cx: p.x, cy: p.y, r: 17, 'data-hot': id}));
    setTimeout(() => { if ((hotUntil[id] || 0) <= Date.now() && fx) fx.querySelectorAll(`[data-hot="${id}"]`).forEach(e => e.remove()); }, HOT_MS + 200);
  }

  // ---------- анімація ----------
  const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
  function frame(now) {
    raf = 0;
    if (!on) { flights = []; fades = []; rings = []; return; }
    layer();
    const k = pxScale();
    fades = fades.filter(f => {
      const u = (now - f.t0) / FADE;
      if (u >= 1) { f.line.remove(); return false; }
      if (!f.line.isConnected) layer().prepend(f.line);  // схему перемальовано посеред польоту
      f.line.setAttribute('stroke-opacity', (0.55 * (1 - u)).toFixed(2));
      return true;
    });
    flights = flights.filter(f => {
      if (now < f.t0) return true;
      if (!f.started) {
        f.started = true;
        for (let i = 1; i < f.pts.length; i++) {
          const line = el('line', {x1: f.pts[i - 1].x, y1: f.pts[i - 1].y, x2: f.pts[i].x, y2: f.pts[i].y, stroke: f.c, 'stroke-width': 1.6 * k, 'stroke-dasharray': `${6 * k} ${5 * k}`, 'stroke-opacity': 0.55});
          layer().prepend(line); fades.push({line, t0: now});
        }
        f.dots = [];
        for (let j = 0; j < 12; j++) f.dots.push(el('circle', {fill: f.c, opacity: 0}));
        f.head = el('circle', {r: 6 * k, fill: '#fff', style: `filter:drop-shadow(0 0 6px ${f.c}) drop-shadow(0 0 12px ${f.c})`});
      }
      if (!f.head.isConnected) layer().append(...f.dots, f.head);  // після перемальовування схеми елементи комети додаємо знову
      const seg = [];
      let total = 0;
      for (let i = 1; i < f.pts.length; i++) { const l = dist(f.pts[i - 1], f.pts[i]); seg.push(l); total += l; }
      const u = Math.min(1, (now - f.t0) / f.dur), d = u * total;
      const at = s => {  // точка на ламаній за пройденою відстанню s
        let rest = Math.max(0, s);
        for (let i = 0; i < seg.length; i++) {
          if (rest <= seg[i] || i === seg.length - 1) { const w = seg[i] ? Math.min(1, rest / seg[i]) : 1; return {x: f.pts[i].x + (f.pts[i + 1].x - f.pts[i].x) * w, y: f.pts[i].y + (f.pts[i + 1].y - f.pts[i].y) * w}; }
          rest -= seg[i];
        }
        return f.pts[f.pts.length - 1];
      };
      f.dots.forEach((dot, j) => {
        const p = at(d - (j + 1) * 5 * k);
        dot.setAttribute('cx', p.x); dot.setAttribute('cy', p.y); dot.setAttribute('r', 3.6 * k * (1 - j / 14)); dot.setAttribute('opacity', (0.55 * (1 - j / 12)).toFixed(2));
      });
      const h = at(d);
      f.head.setAttribute('cx', h.x); f.head.setAttribute('cy', h.y); f.head.setAttribute('r', 5.5 * k);
      if (u >= 1) {
        f.dots.forEach(e => e.remove()); f.head.remove();
        rings.push({c: f.c, at: f.pts[f.pts.length - 1], t0: now, e: null});
        return false;
      }
      return true;
    });
    rings = rings.filter(r => {
      const u = (now - r.t0) / RING;
      if (u >= 1) { if (r.e) r.e.remove(); return false; }
      if (!r.e || !r.e.isConnected) { r.e = el('circle', {fill: 'none', stroke: r.c, 'stroke-width': 2 * k, cx: r.at.x, cy: r.at.y}); layer().append(r.e); }
      r.e.setAttribute('r', (8 + 26 * u) * k); r.e.setAttribute('stroke-opacity', (0.85 * (1 - u)).toFixed(2));
      return true;
    });
    if (flights.length || fades.length || rings.length) raf = requestAnimationFrame(frame);
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };

  // ---------- пакети ----------
  const PORT = {
    TEXT_MESSAGE_APP: ['ТЕКСТ', '#5ee6c4'], POSITION_APP: ['ПОЗИЦІЯ', '#ffb454'], NODEINFO_APP: ['ВУЗОЛ', '#8fa3c4'], TELEMETRY_APP: ['ТЕЛЕМ.', '#c77dff'],
    TRACEROUTE_APP: ['МАРШРУТ', '#ff8a3d'], NEIGHBORINFO_APP: ['СУСІДИ', '#4ade80'], ROUTING_APP: ['ROUTING', '#7d8dab'], ADMIN_APP: ['АДМІН', '#ff6b6b'],
    RANGE_TEST_APP: ['RANGE', '#e3b341'], WAYPOINT_APP: ['ТОЧКА', '#7bd88f'], 'ЗАШИФРОВАНО': ['ШИФР', '#6b7a99'],
  };
  const portOf = p => PORT[p] || [String(p || '?').replace(/_APP$/, '').slice(0, 9), '#8fa3c4'];
  const hhmmss = t => new Date(t * 1000).toLocaleTimeString('uk-UA');
  const toText = pk => pk.to === '^all' || !pk.to ? 'всім' : pk.to_name || pk.to;
  const bars = snr => snr == null ? 0 : snr >= 5 ? 4 : snr >= 0 ? 3 : snr >= -7 ? 2 : 1;  // як індикатор сигналу в застосунку
  let me = null;  // наш вузол (той, що приймає), навіть коли схема показана «очима» іншого

  function fly(pk, delay) {
    const color = portOf(pk.port)[1], pos = M().pos();
    pulse(pk.from); pulse(pk.via); pulse(me);
    if (reduced || document.hidden) return;
    const pts = [pk.from, pk.via, me].map(id => id && pos[id]).filter(Boolean);  // невідомі на схемі вузли пропускаємо
    if (!pts.length) return;
    const t0 = performance.now() + delay;
    if (pts.length === 1) { rings.push({c: color, at: pts[0], t0, e: null}); kick(); return; }
    const k = pxScale(), len = pts.slice(1).reduce((s, p, i) => s + dist(pts[i], p), 0) / k;  // довжина шляху в пікселях екрана
    flights.push({pts, c: color, t0, dur: Math.min(2600, 800 + len * 1.6)});
    if (flights.length > 60) flights.splice(0, flights.length - 60);
    kick();
  }

  function ticker(pk) {  // стрічка внизу: останній пакет
    const [label, color] = portOf(pk.port);
    ui.ticker.hidden = false;
    ui.ticker.replaceChildren();
    const pill = document.createElement('span'), txt = document.createElement('span');
    pill.className = 'lpill'; pill.textContent = label; pill.style.color = color; pill.style.borderColor = color;
    txt.textContent = `${pk.from_name || pk.from || '?'} → ${toText(pk)}` + (pk.via_name ? ` · через ${pk.via_name}` : pk.hops === 0 ? ' · напряму' : '') +
      (pk.snr != null ? ` · SNR ${pk.snr} дБ` : '') + (pk.rssi != null ? ` · RSSI ${pk.rssi}` : '');
    ui.ticker.append(pill, txt);
    ui.ticker.classList.remove('flash'); void ui.ticker.offsetWidth; ui.ticker.classList.add('flash');
  }

  let total = 0;  // шухляда «Трафік»
  function drawerRow(pk) {
    const [label, color] = portOf(pk.port), li = document.createElement('li');
    const l1 = document.createElement('div'), l2 = document.createElement('div'), who = document.createElement('b'), tm = document.createElement('span');
    who.textContent = `${pk.from_name || pk.from || '?'} → ${toText(pk)}`;
    tm.textContent = hhmmss(pk.t); tm.className = 'lt';
    l1.append(who, tm);
    const pill = document.createElement('span'), rest = document.createElement('span'), sig = document.createElement('span');
    pill.className = 'lpill'; pill.textContent = label; pill.style.color = color; pill.style.borderColor = color;
    rest.textContent = [pk.hops == null ? null : pk.hops === 0 ? 'напряму' : `${pk.hops} хоп.`, pk.size != null ? pk.size + ' Б' : null,
      pk.snr != null ? `SNR ${pk.snr}` : null, pk.rssi != null ? `RSSI ${pk.rssi}` : null, pk.via_name ? `через ${pk.via_name}` : null].filter(Boolean).join(' · ');
    sig.className = 'lbars'; sig.title = 'Рівень сигналу за SNR';
    for (let i = 1; i <= 4; i++) { const s = document.createElement('i'); s.style.height = (3 + i * 3) + 'px'; if (i <= bars(pk.snr)) s.className = 'on'; sig.append(s); }
    l2.append(pill, rest, sig);
    li.append(l1, l2);
    li.onclick = () => { const p = M().pos()[pk.from]; if (p) { rings.push({c: color, at: p, t0: performance.now(), e: null}); kick(); } };  // показати, де цей вузол на схемі
    if (pk.port === 'ЗАШИФРОВАНО') li.style.opacity = 0.65;
    return li;
  }
  function drawer(pk) {
    total++;
    ui.count.textContent = `${total} пакетів`;
    if (ui.drawer.hidden) return;
    ui.rows.prepend(drawerRow(pk));
    while (ui.rows.children.length > 80) ui.rows.lastChild.remove();
  }
  ui.traffic.onclick = () => { ui.drawer.hidden = !ui.drawer.hidden; ui.traffic.setAttribute('aria-pressed', String(!ui.drawer.hidden)); };
  ui.close.onclick = () => { ui.drawer.hidden = true; ui.traffic.setAttribute('aria-pressed', 'false'); };

  function ingest(list) {
    // пачка пакетів не має злипатися: показуємо з невеликим розбігом, а якщо їх дуже багато, анімуємо лише останні
    list = list.filter(pk => !pk.mqtt || mqttOn());
    const skip = Math.max(0, list.length - 12);
    list.forEach((pk, i) => { if (i >= skip) fly(pk, (i - skip) * 160); drawer(pk); });
    if (list.length) ticker(list[list.length - 1]);
  }

  // ---------- опитування ----------
  let seq = null, paused = false, busy = false, replaying = false, timer = 0;
  const visible = () => svg() && svg().clientWidth > 0 && !document.hidden;
  async function poll() {
    if (busy || paused || replaying || !on || !visible()) return;
    busy = true;
    try {
      const r = await api('/api/live' + (seq == null ? '' : '?after=' + seq));
      seq = seq != null && r.seq < seq ? 0 : r.seq;  // панель перезапустили: нумерація почалась заново
      if (r.me) me = r.me;
      if (r.packets.length) ingest(r.packets);
      ui.state.textContent = 'Live'; ui.state.className = 'lpill live';
    } catch (e) { ui.state.textContent = 'Немає зв\'язку'; ui.state.className = 'lpill off'; }
    busy = false;
  }
  ui.pause.onclick = () => {
    paused = !paused;
    ui.pause.textContent = paused ? 'Продовжити' : 'Пауза';
    ui.state.textContent = paused ? 'Пауза' : 'Live'; ui.state.className = 'lpill ' + (paused ? 'off' : 'live');
    if (!paused) seq = null;  // що було під час паузи, не показуємо
  };

  // ---------- «Програти» ----------
  let repTimer = 0;
  function stopReplay(msg) {
    cancelAnimationFrame(repTimer); repTimer = 0;
    replaying = false; seq = null;
    ui.stop.hidden = true; ui.prog.hidden = true; ui.rep.disabled = false;
    ui.state.textContent = 'Live'; ui.state.className = 'lpill live';
    ui.note.textContent = msg || '';
  }
  ui.stop.onclick = () => stopReplay('Показ зупинено.');
  ui.rep.onclick = async () => {
    const mins = Number(ui.win.value);
    ui.rep.disabled = true;
    try {
      const r = await api(`/api/live?since=${mins * 60}&limit=1000`);
      if (r.me) me = r.me;
      const list = r.packets.filter(p => !p.mqtt || mqttOn());
      if (!list.length) { ui.rep.disabled = false; ui.note.textContent = `За останні ${mins} хв у пам'яті панелі пакетів немає.`; return; }
      replaying = true; total = 0; ui.rows.replaceChildren(); ui.count.textContent = '0 пакетів';
      ui.state.textContent = 'Показ'; ui.state.className = 'lpill rep';
      ui.stop.hidden = false; ui.prog.hidden = false; ui.prog.value = 0;
      const t0 = list[0].t, span = Math.max(1, list[list.length - 1].t - t0), speed = Math.max(1, span / 45), start = performance.now();  // уся історія вкладається у ~45 с
      let i = 0;
      const step = now => {
        const vt = t0 + (now - start) / 1000 * speed, batch = [];
        while (i < list.length && list[i].t <= vt) batch.push(list[i++]);
        if (batch.length) ingest(batch);
        ui.prog.value = i / list.length;
        if (i >= list.length) { setTimeout(() => stopReplay(`Показано ${list.length} пакетів за ${mins} хв, у ${Math.round(speed)}× швидше.`), 1500); return; }
        repTimer = requestAnimationFrame(step);
      };
      ui.note.textContent = `Показую ${list.length} пакетів за ${mins} хв у ${Math.round(speed)}× швидше.`;
      repTimer = requestAnimationFrame(step);
    } catch (e) { ui.rep.disabled = false; ui.note.textContent = e.message; }
  };

  // ---------- перемикач режиму (типово вимкнений: схема така, як була) ----------
  function apply() {
    on = toggle.checked;
    store.set('on', on ? '1' : '0');
    for (const e of [ui.traffic, ui.win, ui.rep]) e.hidden = !on;
    svg().classList.toggle('live-on', on);
    clearInterval(timer);
    if (on) {
      seq = null; paused = false; total = 0; ui.pause.textContent = 'Пауза'; ui.rows.replaceChildren(); ui.count.textContent = '0 пакетів';
      afterDraw(); poll();
      timer = setInterval(poll, 2000);
    } else {
      cancelAnimationFrame(raf); raf = 0; flights = []; fades = []; rings = [];
      if (replaying) stopReplay('');
      if (fx) { fx.remove(); fx = null; }
      ui.ticker.hidden = true; ui.drawer.hidden = true; ui.traffic.setAttribute('aria-pressed', 'false'); ui.note.textContent = '';
    }
  }
  toggle.onchange = apply;
  window.meshLive = {afterDraw, ingest, apply};  // afterDraw зове mesh.js; ingest і apply потрібні налагодженню та знімкам документації
  const start = () => { if (!M()) return setTimeout(start, 100); toggle.checked = store.get('on', '0') === '1'; apply(); };
  start();
})();
