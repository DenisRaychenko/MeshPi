// Джерело даних карти: «очима вузла» (/api/netview). Центр карти — вибраний вузол, зокрема доступний лише по радіо.
// Назви вузлів — чужі дані: textContent.
(function () {
  const box = document.getElementById('viewbox');
  if (!box) return;
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const say = (t, bad) => { $('viewnote').textContent = t || ''; $('viewnote').style.color = bad ? 'var(--crit)' : ''; };
  let st = null;

  function draw() {
    $('viewon').checked = st.on;
    const sel = $('viewnode'); sel.replaceChildren();
    for (const c of st.candidates) sel.append(new Option(`${c.fav ? '★ ' : ''}${c.name}${c.short && c.short !== c.name ? ' (' + c.short + ')' : ''}`, c.id));
    if (st.node && !st.candidates.some(c => c.id === st.node)) sel.append(new Option(st.node, st.node));
    const pre = st.node || (st.candidates.find(c => /chrt|router/i.test(c.name + ' ' + (c.short || '')))?.id) || (st.candidates[0] || {}).id;
    if (pre) sel.value = pre;
    const out = $('viewinfo'); out.replaceChildren();
    if (st.on && st.known) {
      const row = el('div', 'hfind ' + (st.known.direct ? 'ok' : 'warn'));
      row.append(el('span', 'lv', st.known.direct ? 'гаразд' : 'увага'), el('span', null, `Карта показує мережу очима вибраного вузла. Сьогодні про нього відомо: прямих сусідів ${st.known.direct}, зв'язків ${st.known.links}, пакетів ${st.known.packets}.` +
        (st.known.direct ? '' : ' Прямих сусідів ще не видно: натисніть «Запитати маршрут і дані» або зачекайте, поки вузол щось передасть через ретранслятори.')));
      out.append(row);
    }
  }
  async function load() {
    try { st = await api('/api/netview'); draw(); } catch (e) { say(e.message, true); }
  }
  const reload = () => window.dispatchEvent(new Event('meshpi:mesh-reload'));

  $('viewsave').onclick = async () => {
    say('Застосовую…');
    try { st = await api('/api/netview', {on: $('viewon').checked, node: $('viewnode').value || null}); draw(); say(st.on ? 'Карта тепер очима вибраного вузла.' : 'Карта знову з погляду вашого вузла.'); reload(); }
    catch (e) { say(e.message, true); }
  };
  $('viewask').onclick = async () => {
    const to = $('viewnode').value;
    if (!to) { say('Оберіть вузол', true); return; }
    say('Надсилаю запити по радіо…');
    const res = [];
    for (const [kind, label] of [['trace', 'маршрут'], ['device', 'показники']]) {
      try { await api('/api/ask', {to, kind}); res.push(label + ': запит надіслано'); }
      catch (e) { res.push(label + ': ' + e.message); }
    }
    say(res.join(' · ') + '. Відповідь може йти до хвилини; карта оновиться сама.');
    setTimeout(() => { load(); reload(); }, 20000);
  };
  $('viewnbr').onclick = async () => {
    const to = $('viewnode').value;
    if (!to) { say('Оберіть вузол', true); return; }
    say('Читаю налаштування NeighborInfo по радіо…');
    try {
      const r = await runJob({op: 'read', section: 'neighbor_info', target: to}, (j, ms) => say(`Читаю NeighborInfo… ${Math.round(ms / 1000)} с (радіо)`));
      const v = r.values || {};
      const on = v.enabled === true, every = v.update_interval || v.updateInterval;
      say(on ? `NeighborInfo увімкнено на вузлі${every ? ` (раз на ${Math.round(every / 60)} хв)` : ''}${v.transmit_over_lora === false ? ', але без передачі в ефір' : ''}.`
             : 'NeighborInfo на вузлі вимкнено: він не повідомляє про своїх сусідів. Увімкнути можна у «Керуванні» (це зміна налаштувань вузла, панель її сама не робить).');
    } catch (e) { say(e.message, true); }
  };
  load();
})();
