// Журнал пакетів (як «Debug panel / Packet log» в застосунку Meshtastic): останні прийняті пакети вибраного вузла.
// Значення з мережі вставляємо лише через textContent.
(function () {
  const body = document.getElementById('pktrows');
  if (!body) return;
  const lim = $('#pktlim'), auto = $('#pktauto'), go = $('#pktgo'), note = $('#pktnote');
  let timer = null;

  const hhmmss = t => new Date(t * 1000).toLocaleTimeString('uk-UA');
  const fmt = v => v == null ? '—' : v;
  function render(rows) {
    body.replaceChildren();
    for (const r of rows) {
      const tr = body.insertRow();
      const cells = [hhmmss(r.t), r.from_name || r.from || '—', r.to === '^all' ? 'всім' : r.to || '—', r.port, fmt(r.snr), fmt(r.rssi),
        r.hops == null ? '—' : r.hops, r.relay || '—', r.mqtt ? 'MQTT' : ''];
      for (const c of cells) tr.insertCell().textContent = c;
      if (r.port === 'ЗАШИФРОВАНО') tr.style.opacity = .6;
    }
    note.textContent = rows.length ? `Показано ${rows.length} (пам'ять панелі, зникає при перезапуску).` : 'Немає пакетів за цими умовами.';
  }
  const portSel = $('#pktport'), nodeIn = $('#pktnode'), snrIn = $('#pktsnr'), noMqtt = $('#pktnomqtt'), csv = $('#pktcsv');
  function query(withLimit) {  // ті самі фільтри для таблиці й для CSV
    const q = new URLSearchParams();
    if (withLimit) q.set('limit', lim.value);
    if (portSel.value) q.set('port', portSel.value);
    if (nodeIn.value.trim()) q.set('node', nodeIn.value.trim());
    if (snrIn.value !== '') q.set('snr_min', snrIn.value);
    if (noMqtt.checked) q.set('nomqtt', '1');
    return q.toString();
  }
  const known = new Set();
  async function load() {
    csv.href = '/api/packets.csv?' + query(false);
    try {
      const rows = await api('/api/packets?' + query(true));
      for (const r of rows) if (r.port && !known.has(r.port)) {  // типи, які вже зустрілись, у списку фільтра
        known.add(r.port); const o = document.createElement('option'); o.value = o.textContent = r.port; portSel.append(o);
      }
      render(rows);
    } catch (e) { note.textContent = e.message; }
  }
  go.onclick = load;
  for (const e of [lim, portSel, noMqtt]) e.onchange = load;
  for (const e of [nodeIn, snrIn]) e.oninput = () => { clearTimeout(e._t); e._t = setTimeout(load, 350); };
  auto.onchange = () => {
    clearInterval(timer);
    if (auto.checked) { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 3000); }
  };
  load();
})();
