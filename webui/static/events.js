// Журнал подій: нові вузли та обрані (★ на вузлі), що пропали й повернулися. Дані з /api/events; тексти вставляємо лише
// через textContent (назви вузлів — чужі дані). Сповіщення (звук, системне вікно у фоні, лічильник на вкладці) вмикає
// прапорець у самій вкладці; стан «що вже переглянуто» живе лише в цьому браузері.
(function () {
  const rows = document.getElementById('evrows');
  const tab = document.querySelector('#tabbar [data-tab="events"]');
  if (!rows || !tab) return;
  const $id = id => document.getElementById(id);
  const LABEL = {new: 'Новий', fav_off: 'Пропав', fav_on: 'Повернувся', bat_low: 'Заряд', chan_high: 'Ефір', reboot: 'Перезапуск', link_lost: "Зв'язок втрачено", link_ok: "Зв'язок є", geo_in: 'У зоні', geo_out: 'Із зони'};  // підпис-таблетка: колір і слово, а не емодзі
  const TITLE = {new: 'Новий вузол', fav_off: 'Обраний пропав', fav_on: 'Обраний повернувся', bat_low: 'Низький заряд', chan_high: 'Ефір перевантажений', reboot: 'Вузол перезапустився', link_lost: "Зв'язок втрачено", link_ok: "Зв'язок відновлено", geo_in: 'Вузол увійшов у зону', geo_out: 'Вузол вийшов із зони'};
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* без сховища — ок */ } },
  };
  let owner = null, top = 0, list = [], first = true;
  const notify = $id('evnotify');
  notify.checked = store.get('evnotify', '1') === '1';
  notify.onchange = () => {
    store.set('evnotify', notify.checked ? '1' : '0');
    if (notify.checked && window.Notification && Notification.permission === 'default') Notification.requestPermission();
  };

  const active = () => tab.classList.contains('active');
  const seenKey = () => 'evseen:' + owner;
  const seenId = () => Number(store.get(seenKey(), '0')) || 0;

  function badge() {
    const n = active() ? 0 : list.filter(e => e.id > seenId()).length;
    $id('evbadge').textContent = n ? ` ${n > 99 ? '99+' : n}` : '';
    $id('evbadge').style.color = n ? 'var(--warn, #e3b341)' : '';
  }
  function markSeen() { if (list.length) store.set(seenKey(), String(list[0].id)); badge(); }

  const rel = t => {  // «7 хв тому»: точний час — у підказці
    const s = Math.max(0, Date.now() / 1000 - t);
    return s < 90 ? 'щойно' : s < 3600 ? `${Math.round(s / 60)} хв тому` : s < 86400 ? `${Math.round(s / 3600)} год тому` : `${Math.round(s / 86400)} д тому`;
  };
  const dayName = d => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
    return diff === 0 ? 'Сьогодні' : diff === 1 ? 'Вчора' : d.toLocaleDateString('uk-UA', {day: 'numeric', month: 'long'});
  };

  function render() {
    rows.replaceChildren();
    $id('evempty').hidden = list.length > 0;
    const fresh = seenId();
    let day = '';
    for (const e of list) {
      const when = new Date(e.t * 1000), d = when.toDateString();
      if (d !== day) {  // заголовок доби
        day = d;
        const h = rows.insertRow(); h.className = 'evday';
        const c = h.insertCell(); c.colSpan = 3; c.textContent = dayName(when);
      }
      const tr = rows.insertRow();
      const time = tr.insertCell();
      time.textContent = rel(e.t); time.title = when.toLocaleString('uk-UA'); time.style.whiteSpace = 'nowrap';
      const k = tr.insertCell(), pill = document.createElement('span');
      pill.className = 'evk ' + e.kind; pill.textContent = LABEL[e.kind] || 'Подія'; k.append(pill);
      const c = tr.insertCell(); c.textContent = e.text;
      if (e.id > fresh && !active()) { c.style.fontWeight = '600'; tr.classList.add('evnew'); }
    }
  }

  function alertNew(fresh) {
    if (!notify.checked) return;
    if (typeof chime === 'function') chime();
    if (document.hidden && window.Notification && Notification.permission === 'granted') {
      const e = fresh[0];
      new Notification(fresh.length > 1 ? `Meshpi: ${fresh.length} події` : `Meshpi: ${TITLE[e.kind] || 'Подія'}`, {body: e.text});
    }
  }

  async function refresh() {
    let r;
    try { r = await api('/api/events?limit=200'); } catch (e) { return; }
    if (r.owner !== owner) { owner = r.owner; top = 0; first = true; }  // інший вузол: свій журнал, без сповіщень про старе
    const fresh = r.events.filter(e => e.id > top);
    list = r.events;
    if (list.length) top = Math.max(top, list[0].id);
    if (!first && fresh.length) alertNew(fresh);
    first = false;
    render();
    if (active()) markSeen(); else badge();
  }

  new MutationObserver(() => { if (active()) { markSeen(); render(); } else badge(); })
    .observe(tab, {attributes: true, attributeFilter: ['class']});
  refresh();
  // у фоновій вкладці — раз на хвилину, якщо сповіщення ввімкнено (як для повідомлень)
  let n = 0;
  setInterval(() => { if (!document.hidden || (notify.checked && ++n % 4 === 0)) refresh(); }, 15000);
})();
