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
    note.textContent = rows.length ? `Показано ${rows.length} останніх пакетів (пам'ять панелі, зникає при перезапуску).` : 'Пакетів ще не було.';
  }
  async function load() {
    try { render(await api('/api/packets?limit=' + lim.value)); } catch (e) { note.textContent = e.message; }
  }
  go.onclick = load;
  lim.onchange = load;
  auto.onchange = () => {
    clearInterval(timer);
    if (auto.checked) { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 3000); }
  };
  load();
})();
