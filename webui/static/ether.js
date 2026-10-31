// «Ефір»: усі текстові повідомлення, що дійшли до панелі, одним потоком: з усіх каналів і всі приватні (також адресовані не нам,
// якщо вузол їх передав). Дані ті самі, що у «Повідомленнях» (/api/messages); тут лише перегляд із фільтрами. Тексти вставляємо
// лише через textContent: імена й текст чужі.
(function () {
  const tab = document.querySelector('#tabbar [data-tab="ether"]');
  const rows = document.getElementById('etrows');
  if (!tab || !rows) return;
  const $id = id => document.getElementById(id);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* без сховища — ок */ } },
  };
  let msgs = [], names = new Map(), chans = new Map(), raw = '', me = null;
  const chSel = $id('etch'), kindSel = $id('etkind'), q = $id('etq'), who = $id('etwho');
  kindSel.value = store.get('etkind', 'all');
  if (!kindSel.value) kindSel.value = 'all';

  const active = () => tab.classList.contains('active');
  const nm = id => id === '^all' ? 'усі' : (names.get(id) || id || '?');
  const isDm = m => !!m.to && m.to !== '^all';
  const chName = c => chans.get(c) ? `${c} · ${chans.get(c)}` : String(c);

  function fillChannels(list) {  // канали: ті, що відомі вузлу, і ті, де вже були повідомлення
    const keep = chSel.value, set = new Set([...chans.keys(), ...list.map(m => m.ch)]);
    chSel.replaceChildren(new Option('Усі канали', ''));
    for (const c of [...set].sort((a, b) => a - b)) chSel.append(new Option('Канал ' + chName(c), String(c)));
    chSel.value = [...chSel.options].some(o => o.value === keep) ? keep : '';
  }

  function render() {
    const needle = q.value.trim().toLowerCase(), kind = kindSel.value, ch = chSel.value, only = who.value;
    const list = msgs.filter(m => !m.emoji
      && (!ch || String(m.ch) === ch)
      && (kind === 'all' || (kind === 'dm') === isDm(m))
      && (only !== 'foreign' || (isDm(m) && !m.out && me && m.to !== me && m.from !== me))
      && (!needle || (m.text || '').toLowerCase().includes(needle) || nm(m.from).toLowerCase().includes(needle)));
    rows.replaceChildren();
    $id('etempty').hidden = list.length > 0;
    $id('etcount').textContent = list.length ? `${list.length} повідомлень` : '';
    let day = '';
    for (const m of list.slice(-500).reverse()) {
      const when = new Date(m.t * 1000), d = when.toDateString();
      if (d !== day) {
        day = d;
        const h = rows.insertRow(); h.className = 'evday';
        const c = h.insertCell(); c.colSpan = 4;
        c.textContent = when.toLocaleDateString('uk-UA', {day: 'numeric', month: 'long'});
      }
      const tr = rows.insertRow();
      const t = tr.insertCell(); t.textContent = when.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}); t.style.whiteSpace = 'nowrap';
      const k = tr.insertCell(), pill = document.createElement('span');
      pill.className = 'evk ' + (isDm(m) ? 'fav_on' : 'new'); pill.textContent = isDm(m) ? '🔒 приватне' : 'к ' + chName(m.ch);
      pill.title = isDm(m) ? 'Приватне повідомлення, канал ' + chName(m.ch) : 'Канал ' + chName(m.ch);
      k.append(pill);
      const f = tr.insertCell(); f.style.whiteSpace = 'nowrap';
      f.textContent = (m.out ? 'Ви' : (m.name || nm(m.from))) + (isDm(m) ? ' → ' + (m.to === me ? 'Ви' : nm(m.to)) : '');
      tr.insertCell().textContent = m.text || '';
    }
  }

  async function refresh() {
    let m;
    try { m = await api('/api/messages'); } catch (e) { return; }
    const sig = JSON.stringify(m);
    if (sig === raw && names.size) return;
    raw = sig; msgs = m;
    try {
      for (const n of await api('/api/nodes')) names.set(n.id, n.long || n.short || n.id);
      const st = await api('/api/status');
      me = (st.node && st.node.id) || me;
      for (const c of (st.mqtt && st.mqtt.channels) || []) chans.set(c.index, c.name);
    } catch (e) { /* без імен — показуємо ID */ }
    fillChannels(msgs);
    render();
  }

  kindSel.onchange = () => { store.set('etkind', kindSel.value); render(); };
  for (const e of [chSel, who]) e.onchange = render;
  q.addEventListener('input', render);
  new MutationObserver(() => { if (active()) refresh(); }).observe(tab, {attributes: true, attributeFilter: ['class']});
  setInterval(() => { if (active() && !document.hidden) refresh(); }, 5000);
  if (active()) refresh();
})();
