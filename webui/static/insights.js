// Аналітика: проблеми мережі, звіт про покриття, «Останні показники», геозони, пошук у повідомленнях.
// Усе лише читання з /api/issues, /api/coverage, /api/latest, /api/messages/search (геозони пишуть налаштування в /api/geofence).
// Назви вузлів і тексти повідомлень чужі, тому вставляються лише через textContent.
(function () {
  const $ = id => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svg = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs || {}) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };
  const LEVEL = {ok: 'гаразд', info: 'до уваги', warn: 'увага', crit: 'проблема'};
  const ago = t => { if (!t) return '—'; const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? 'щойно' : s < 3600 ? `${Math.round(s / 60)} хв` : s < 86400 ? `${Math.round(s / 3600)} год` : `${Math.round(s / 86400)} д`; };
  const num = (v, d = 0, u = '') => typeof v === 'number' && isFinite(v) ? v.toFixed(d) + u : '—';

  // ---- проблеми мережі ----
  async function issues() {
    const box = $('issuefind'); if (!box) return;
    let r; try { r = await api('/api/issues'); } catch (e) { $('issuenote').textContent = e.message; return; }
    box.replaceChildren();
    $('issuenote').textContent = r.issues.length ? `знайдено ${r.issues.length}` : '';
    if (!r.issues.length) { const row = el('div', 'hfind ok'); row.append(el('span', 'lv', LEVEL.ok), el('span', null, 'Явних проблем не видно.')); box.append(row); }
    for (const i of r.issues) { const row = el('div', 'hfind ' + i.level); row.append(el('span', 'lv', LEVEL[i.level] || i.level), el('span', null, i.text)); box.append(row); }
  }

  // ---- покриття: полярна діаграма «далекість за напрямком» ----
  async function coverage() {
    const box = $('covbox'); if (!box) return;
    let r; try { r = await api('/api/coverage?hours=' + $('covhours').value); } catch (e) { $('covnote').textContent = e.message; return; }
    box.replaceChildren();
    if (!r.report) { $('covnote').textContent = 'Потрібні ваші координати: вони беруться з вузла або з розділу «Розташування плати».'; return; }
    const rep = r.report;
    $('covnote').textContent = rep.n ? `вузлів із координатами: ${rep.n}` + (rep.farthest ? `; найдальший ${rep.farthest.name || '?'} ${rep.farthest.km} км` : '') +
      (rep.direct_farthest ? `; найдальший прямий ${rep.direct_farthest.name || '?'} ${rep.direct_farthest.km} км` : '') : 'У вибраному вікні немає вузлів із координатами.';
    if (!rep.n) return;
    const S = 360, C = S / 2, R = C - 34, max = Math.max(1, ...rep.sectors.map(s => s.max_km));
    const rad = km => R * Math.sqrt(km / max);   // кореневий масштаб: близькі вузли не зливаються в точку
    const s = svg('svg', {viewBox: `0 0 ${S} ${S}`, width: '100%', role: 'img', 'aria-label': 'Покриття за напрямками'}); s.style.maxWidth = '420px';
    for (const f of [0.25, 0.5, 1]) {
      s.append(svg('circle', {cx: C, cy: C, r: rad(max * f * f), fill: 'none', stroke: 'var(--line)', 'stroke-dasharray': '3 3'}));
      s.append(svg('text', {x: C + 3, y: C - rad(max * f * f) - 2, 'font-size': 9, fill: 'var(--text-low)'}, (max * f * f).toFixed(max < 10 ? 1 : 0) + ' км'));
    }
    const pt = (deg, r0) => [C + r0 * Math.sin(deg * Math.PI / 180), C - r0 * Math.cos(deg * Math.PI / 180)];
    const wedge = (s0, key, fill) => {
      const r0 = rad(s0[key]); if (!r0) return null;
      const [x1, y1] = pt(s0.center - 15, r0), [x2, y2] = pt(s0.center + 15, r0);
      return svg('path', {d: `M${C},${C} L${x1.toFixed(1)},${y1.toFixed(1)} A${r0.toFixed(1)},${r0.toFixed(1)} 0 0 1 ${x2.toFixed(1)},${y2.toFixed(1)} Z`, fill, 'fill-opacity': .55, stroke: fill});
    };
    for (const sec of rep.sectors) {
      const all = wedge(sec, 'max_km', 'var(--sky-500, #4a9eff)'), dir = wedge(sec, 'max_direct_km', 'var(--signal)');
      for (const w of [all, dir]) if (w) { const t = svg('title', null, `${sec.center}°: вузлів ${sec.count} (прямих ${sec.direct}); найдальший ${sec.max_name || '?'} ${sec.max_km} км` +
        (sec.max_direct_km ? `, прямий ${sec.max_direct_km} км` : '') + (sec.snr != null ? `, SNR ${sec.snr}` : '')); w.append(t); s.append(w); }
    }
    for (const [d, l] of [[0, 'Пн'], [90, 'Сх'], [180, 'Пд'], [270, 'Зх']]) { const [x, y] = pt(d, R + 16); s.append(svg('text', {x, y: y + 4, 'font-size': 11, 'text-anchor': 'middle', fill: 'var(--text-mid)'}, l)); }
    s.append(svg('circle', {cx: C, cy: C, r: 4, fill: 'var(--text-hi)'}));
    const legend = el('p', 'hint', 'Синій клин: найдальший вузол у цьому напрямку (з будь-яким числом стрибків). Зелений: найдальший, якого чутно напряму. Масштаб кореневий.');
    const bands = el('div', 'row'); bands.style.gap = '12px';
    let prev = 0; for (const b of rep.bands) { bands.append(el('span', 'hint', `${b.upto == null ? '> ' + prev : prev + '–' + b.upto} км: ${b.count}`)); if (b.upto != null) prev = b.upto; }
    box.append(s, legend, bands);
  }

  // ---- «Останні показники» ----
  let latest = null;
  function drawLatest() {
    const body = $('latestrows'); if (!body || !latest) return;
    body.replaceChildren();
    const favOnly = $('latestfav').checked, q = $('latestq').value.trim().toLowerCase();
    const info = latest.env_info;
    for (const n of latest.nodes) {
      if (favOnly && !n.fav && !n.me) continue;
      if (q && !`${n.name} ${n.long || ''} ${n.id}`.toLowerCase().includes(q)) continue;
      const tr = body.insertRow(), e = n.env || {};
      const cells = [(n.fav ? '★ ' : '') + (n.aircraft ? '✈ ' : '') + (n.long || n.name) + (n.me ? ' (ви)' : ''), n.role, n.bat == null ? '—' : n.bat > 100 ? 'живлення' : num(n.bat, 0, '%'),
        num(n.volt, 2, ' В'), num(n.chan, 0, '%'), num(n.air, 1, '%'), n.uptime == null ? '—' : n.uptime < 3600 ? Math.round(n.uptime / 60) + ' хв' : n.uptime < 172800 ? Math.round(n.uptime / 3600) + ' год' : Math.round(n.uptime / 86400) + ' д',
        e.temperature != null ? num(e.temperature, 1, ' °C') : '—', e.humidity != null ? num(e.humidity, 0, '%') : '—', e.pressure != null ? num(e.pressure, 0, ' гПа') : '—',
        n.snr == null ? '—' : n.snr, n.hops == null ? '—' : n.hops, ago(n.heard)];
      cells.forEach((v, k) => { const c = tr.insertCell(); c.textContent = v; c.dataset.l = $('latesthead').children[k].textContent; });
      if (n.aircraft) tr.children[0].title = `Висота ${Math.round(n.alt)} м: ймовірно літак чи дрон`;
      if (n.bat != null && n.bat <= 15) tr.children[2].style.color = 'var(--crit)';
      if (n.air != null && n.air >= 10) tr.children[5].style.color = 'var(--warn)';
    }
    $('latestcount').textContent = `${body.rows.length} з ${latest.nodes.length}`;
  }
  async function loadLatest() {
    if (!$('latestrows')) return;
    try { latest = await api('/api/latest'); } catch (e) { $('latestcount').textContent = e.message; return; }
    drawLatest();
  }

  // ---- пошук у повідомленнях ----
  function search() {
    const q = $('msgq'), box = $('msgres'); if (!q) return;
    let timer;
    const run = async () => {
      const t = q.value.trim(); box.replaceChildren();
      if (t.length < 2) { box.hidden = true; return; }
      box.hidden = false;
      let r; try { r = await api('/api/messages/search?q=' + encodeURIComponent(t)); } catch (e) { box.append(el('p', 'hint', e.message)); return; }
      if (!r.length) { box.append(el('p', 'hint', 'Нічого не знайдено.')); return; }
      box.append(el('p', 'hint', `Знайдено ${r.length}${r.length >= 100 ? '+' : ''}: найновіші першими.`));
      for (const m of r) {
        const row = el('div', 'hfind info'); row.style.cursor = 'pointer';
        const dm = m.to !== '^all', who = m.out ? 'Ви' : (m.name || m.from || '?');
        row.append(el('span', 'lv', new Date(m.t * 1000).toLocaleString('uk-UA', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'})), el('span', null, `${who}${dm ? ' (особисто)' : ' · к' + m.ch}: ${m.text}`));
        row.onclick = () => { const peer = m.out ? m.to : m.from; if (dm && peer && typeof openConv === 'function') openConv(peer); };
        box.append(row);
      }
    };
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 300); });
  }

  // ---- геозони (Система) ----
  const geo = $('geoform');
  if (geo) {
    let st = null;
    const lab = (t, i) => { const l = el('label'); l.append(t + ' ', i); l.style.marginRight = '12px'; return l; };
    const inp = (v, w, type, ph) => { const i = el('input', 'tg-input'); i.type = type || 'text'; i.value = v ?? ''; i.style.width = w; if (ph) i.placeholder = ph; if (type === 'number') i.step = 'any'; return i; };
    const say = (t, bad) => { $('geomsg').textContent = t || ''; $('geomsg').style.color = bad ? 'var(--crit)' : ''; };
    let on, zones;
    function zoneRow(z, id) {
      const row = el('div', 'row'); row.style.marginBottom = '6px';
      const name = inp(z.name, '10em', 'text', 'назва'), lat = inp(z.lat, '8em', 'number', 'широта'), lon = inp(z.lon, '8em', 'number', 'довгота'), rad = inp(z.radius_m, '6em', 'number', 'м');
      const who = el('select', 'tg-input'); for (const [v, t] of [['all', 'усі вузли'], ['fav', 'лише обрані ★']]) who.append(new Option(t, v)); who.value = z.who || 'all';
      const en = el('input'), ex = el('input'); en.type = ex.type = 'checkbox'; en.checked = z.enter !== false; ex.checked = z.exit !== false;
      const del = el('button', 'check-btn', '✕'); del.type = 'button'; del.onclick = () => row.remove();
      const le = el('label'); le.append(en, ' вхід'); const lx = el('label'); lx.append(ex, ' вихід');
      row.append(name, lab('шир.', lat), lab('довг.', lon), lab('радіус, м', rad), who, le, lx, del);
      row._get = () => ({id, name: name.value, lat: +lat.value, lon: +lon.value, radius_m: +rad.value, who: who.value, enter: en.checked, exit: ex.checked});
      return row;
    }
    function draw() {
      geo.replaceChildren();
      on = el('input'); on.type = 'checkbox'; on.checked = st.cfg.on;
      const l = el('label'); l.append(on, ' ввімкнено'); l.style.fontWeight = '600';
      zones = el('div'); let n = 0;
      for (const z of st.cfg.zones) { zones.append(zoneRow(z, z.id)); n = Math.max(n, z.id); }
      const add = el('button', 'check-btn', '＋ зона'); add.type = 'button';
      add.onclick = () => { if (zones.children.length < st.max_zones) zones.append(zoneRow({name: '', radius_m: 500}, ++n)); };
      geo.append(l, zones, add);
    }
    $('geosave').onclick = async () => {
      say('Зберігаю…');
      try { st = await api('/api/geofence', {on: on.checked, zones: [...zones.children].map(r => r._get())}); draw(); say('Збережено.'); } catch (e) { say(e.message, true); }
    };
    api('/api/geofence').then(r => { st = r; draw(); }).catch(() => say('не вдалося прочитати налаштування', true));
  }

  // ---- запуск і оновлення ----
  const bind = (id, fn) => { const e = $(id); if (e) e.onchange = fn; };
  bind('covhours', coverage); bind('latestfav', drawLatest);
  if ($('latestq')) $('latestq').addEventListener('input', drawLatest);
  if ($('issuerun')) $('issuerun').onclick = issues;
  const refresh = () => { if (document.hidden) return; issues(); coverage(); loadLatest(); };
  search(); refresh();
  setInterval(refresh, 120000);
})();
