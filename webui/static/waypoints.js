// Точки на карті (waypoints): показ на карті «Вузлів», створення кліком, зміна й видалення (дані з /api/waypoints).
// Назви й описи — чужі дані: вставляємо лише через textContent.
(function () {
  const box = document.getElementById('wpbox');
  if (!box) return;
  const $id = id => document.getElementById(id);
  const F = {name: $id('wpname'), descr: $id('wpdescr'), icon: $id('wpicon'), lat: $id('wplat'), lon: $id('wplon'), ttl: $id('wpttl'),
             ch: $id('wpch'), lock: $id('wplock'), go: $id('wpgo'), cancel: $id('wpcancel'), msg: $id('wpmsg'), rows: $id('wprows'),
             form: $id('wpform'), icons: $id('wpicons')};
  const ICONS = ['📍', '🏠', '⛺', '🚩', '⚠️', '🔥', '💧', '🅿️', '⛽', '🏥', '📡', '🌲'];
  let map = null, layer = null, draft = null, list = [], editing = null;

  const say = (t, bad) => { F.msg.textContent = t || ''; F.msg.style.color = bad ? 'var(--crit)' : ''; };
  const left = sec => {  // скільки лишилось діяти
    if (sec == null) return 'без строку';
    const s = sec - Date.now() / 1000;
    if (s <= 0) return 'вийшов';
    return s < 5400 ? `${Math.max(1, Math.round(s / 60))} хв` : s < 129600 ? `${Math.round(s / 3600)} год` : `${Math.round(s / 86400)} д`;
  };
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };

  for (const ic of ICONS) {
    const b = el('button', ic, 'check-btn'); b.type = 'button'; b.title = 'Обрати значок'; b.onclick = () => { F.icon.value = ic; };
    F.icons.append(b);
  }

  function reset() {
    editing = null; F.form.reset(); F.icon.value = '📍'; F.ttl.value = 86400; F.ch.value = 0;
    F.go.textContent = '📤 Надіслати точку'; F.cancel.hidden = true;
    if (draft && map) { map.removeLayer(draft); draft = null; }
  }
  function setDraft(lat, lon) {
    F.lat.value = (+lat).toFixed(6); F.lon.value = (+lon).toFixed(6);
    if (!map) return;
    if (draft) draft.setLatLng([lat, lon]);
    else draft = L.circleMarker([lat, lon], {radius: 8, color: '#ffb454', weight: 3, fillOpacity: .3}).addTo(map);
  }
  function edit(w) {
    editing = w;
    F.name.value = w.name; F.descr.value = w.description; F.icon.value = w.icon; F.ch.value = w.ch || 0;
    F.lock.checked = w.locked; F.ttl.value = 86400;
    setDraft(w.lat, w.lon);
    F.go.textContent = '✓ Зберегти і надіслати'; F.cancel.hidden = false;
    say('Правка точки: строк дії рахується від зараз.');
    box.scrollIntoView({block: 'nearest'});
  }
  async function remove(w) {
    if (!confirm(`Видалити точку «${w.name}» для всіх вузлів каналу?`)) return;
    try { await api('/api/waypoints', {action: 'delete', id: w.id, ch: w.ch || 0}); say('Точку видалено.'); await load(); }
    catch (e) { say(e.message, true); }
  }

  function draw() {
    box.hidden = !map && !list.length;
    F.rows.replaceChildren();
    if (layer) layer.clearLayers();
    for (const w of list) {
      const can = !w.locked || w.mine;
      const tr = F.rows.insertRow();
      tr.insertCell().textContent = w.icon;
      tr.insertCell().textContent = w.name;
      tr.insertCell().textContent = w.description || '—';
      tr.insertCell().textContent = w.mine ? 'ви' : (w.sender_name || w.sender || '—');
      tr.insertCell().textContent = left(w.expire);
      tr.insertCell().textContent = w.ch;
      const act = tr.insertCell();
      const mk = (txt, title, fn, cls) => { const b = el('button', txt, cls || 'check-btn'); b.type = 'button'; b.title = title; b.onclick = fn; act.append(b, ' '); };
      if (map) mk('⌖', 'Показати на карті', () => { map.setView([w.lat, w.lon], Math.max(map.getZoom(), 14)); $id('map').scrollIntoView({block: 'center'}); });
      if (can) { mk('✎', 'Змінити', () => edit(w)); mk('🗑', 'Видалити', () => remove(w), 'reboot-btn'); }
      else { const l = el('span', '🔒', 'hint'); l.title = 'Заблокована за іншим вузлом'; act.append(l); }
      if (!layer) continue;
      const mark = el('div', w.icon, 'wpmark');
      const pop = el('div'), b = el('b', `${w.icon} ${w.name}`);
      pop.append(b);
      if (w.description) pop.append(el('br'), w.description);
      pop.append(el('br'), el('small', `${w.mine ? 'ви' : (w.sender_name || w.sender || '?')} · діє: ${left(w.expire)} · канал ${w.ch}${w.locked ? ' · 🔒' : ''}`));
      L.marker([w.lat, w.lon], {icon: L.divIcon({html: mark, className: '', iconSize: [30, 30], iconAnchor: [15, 28]})}).bindPopup(pop).addTo(layer);
    }
  }

  async function load() {
    try { list = await api('/api/waypoints'); draw(); } catch (e) { say(e.message, true); }
  }

  F.form.onsubmit = async ev => {
    ev.preventDefault();
    if (!F.lat.value || !F.lon.value) { say('Клацніть на карті або введіть координати.', true); return; }
    try {
      const r = await api('/api/waypoints', {action: 'send', id: editing ? editing.id : undefined, name: F.name.value, description: F.descr.value,
        icon: F.icon.value || '📍', lat: +F.lat.value, lon: +F.lon.value, ttl: +F.ttl.value, lock: F.lock.checked, ch: +F.ch.value});
      say(`Точку надіслано (id ${r.id}).`); reset(); await load();
    } catch (e) { say(e.message, true); }
  };
  F.cancel.onclick = () => { reset(); say(''); };
  for (const k of ['lat', 'lon']) F[k].onchange = () => { if (F.lat.value && F.lon.value) setDraft(+F.lat.value, +F.lon.value); };

  function ready() {
    map = window.meshMap;
    layer = L.layerGroup().addTo(map);
    map.on('click', e => { setDraft(e.latlng.lat, e.latlng.lng); say('Заповніть назву й надішліть точку.'); F.name.focus({preventScroll: true}); });
    draw();
  }
  if (window.meshMap) ready(); else window.addEventListener('meshmap', ready, {once: true});
  load();
  setInterval(() => { if (!document.hidden) load(); }, 30000);
})();
