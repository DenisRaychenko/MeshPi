// Дії над вузлом у «Вузлах» (як на екрані вузла в застосунку Meshtastic): обране, ігнорувати, заглушити, забути,
// запит і надсилання позиції. Обране/ігнор/заглушення записуються у список вузлів на самому вузлі.
// Значення з мережі вставляємо лише через textContent.
(function () {
  const idEl = document.getElementById('ndid'), panel = document.getElementById('nd'), out = document.getElementById('ndout');
  if (!idEl || !panel || !out) return;
  const bar = document.createElement('div');
  bar.className = 'row'; bar.id = 'ndact'; bar.style.margin = '0 0 10px';
  out.before(bar);
  let cur = null, busy = false;

  const note = t => { out.textContent = t; };
  async function run(label, body, then) {
    if (busy) { note('Дочекайтеся завершення попередньої операції.'); return; }
    busy = true; note(label + '…');
    try {
      const r = await runJob({target: 'local', ...body}, (j, ms) => note(`${label}… ${Math.round(ms / 1000)} с (радіо)`));
      note(then ? then(r) : 'Готово.');
    } catch (e) { note(e.message); }
    finally { busy = false; draw(); }
  }

  async function homePos() {  // моя позиція: задане розташування плати, інакше геолокація браузера
    try { const h = (await api('/api/location')).home; if (h) return {lat: h.lat, lon: h.lon}; } catch (e) { /* без розташування */ }
    return new Promise((ok, no) => {
      if (!navigator.geolocation) return no(new Error('Задайте розташування плати у вкладці «Вузол» (або дозвольте геолокацію)'));
      navigator.geolocation.getCurrentPosition(p => ok({lat: p.coords.latitude, lon: p.coords.longitude}),
        () => no(new Error('Задайте розташування плати у вкладці «Вузол» (або дозвольте геолокацію)')), {timeout: 10000});
    });
  }

  async function draw() {
    bar.replaceChildren();
    if (!cur) return;
    let n = {};
    try { n = (await api('/api/nodes')).find(x => x.id === cur) || {}; } catch (e) { /* без прапорців */ }
    const add = (text, title, fn, cls) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = cls || 'check-btn';
      b.textContent = text; b.title = title; b.onclick = fn; bar.append(b);
    };
    const act = (name, extra) => ({op: 'action', name, args: {node: cur}, ...extra});
    add(n.fav ? '★ Обраний' : '☆ В обрані', 'Обрані вузли (записується в список вузлів на вашому вузлі): прошивка їх не забуває і ретранслює пакети від них',
      () => run('Записую', act(n.fav ? 'unfavorite' : 'favorite'), () => n.fav ? 'Прибрано з обраних.' : 'Додано в обрані.'));
    add(n.ignored ? '✓ Не ігнорувати' : '🚫 Ігнорувати', 'Ігноровані вузли не потрапляють у список і не ретранслюються',
      () => run('Записую', act(n.ignored ? 'unignore' : 'ignore'), () => n.ignored ? 'Вузол більше не ігнорується.' : 'Вузол ігнорується.'));
    add(n.muted ? '🔔 Увімкнути звук' : '🔕 Заглушити', 'Заглушені вузли не лунають сповіщеннями на самому вузлі',
      () => run('Записую', act('mute'), () => n.muted ? 'Звук від вузла увімкнено.' : 'Вузол заглушено.'));
    add('📍 Запросити позицію', 'Попросити вузол надіслати свою позицію (передача в ефір)',
      () => run('Запитую позицію', {op: 'request_position', node: cur},
        r => `Позиція: ${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}${r.alt != null ? `, ${r.alt} м` : ''}`));
    add('📤 Надіслати йому мою позицію', 'Надіслати цьому вузлу позицію вашої плати (задане розташування)', async () => {
      try {
        const p = await homePos();
        await run('Надсилаю позицію', {op: 'send_position', node: cur, lat: p.lat, lon: p.lon}, () => 'Позицію надіслано.');
      } catch (e) { note(e.message); }
    });
    add('🗑 Забути', 'Видалити вузол зі списку вузлів на вашому вузлі (з\'явиться знову, коли його почують)', () => {
      if (!confirm('Забути цей вузол у списку вузлів на вашому вузлі?')) return;
      run('Видаляю', act('remove_node', {confirm: true}), () => 'Вузол видалено зі списку.');
    }, 'reboot-btn');
  }

  new MutationObserver(() => {
    const id = idEl.textContent.trim();
    if (id && id !== cur) { cur = id; draw(); }
  }).observe(idEl, {childList: true, characterData: true, subtree: true});
  document.getElementById('ndx')?.addEventListener('click', () => { cur = null; bar.replaceChildren(); });
})();
