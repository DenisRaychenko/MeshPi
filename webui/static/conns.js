// Кілька вузлів: перемикач у шапці й список підключень у «Системі» (дані з /api/conns).
// Значення з мережі й від користувача вставляємо лише через textContent.
(function () {
  const sel = $('#nodesel'), rows = $('#connrows'), msg = $('#connmsg'), form = $('#connform');
  if (!sel || !rows) return;
  const f = {name: $('#cname'), host: $('#chost'), port: $('#cport'), go: $('#cgo'), cancel: $('#ccancel')};
  let data = null, editing = null;

  const say = (t, bad) => { msg.textContent = t || ''; msg.style.color = bad ? 'var(--crit)' : ''; };

  function draw() {
    sel.replaceChildren(...data.conns.map(c => {
      const o = document.createElement('option');
      o.value = c.id; o.textContent = (c.online ? '● ' : '○ ') + c.name;
      return o;
    }));
    sel.value = data.current;
    rows.replaceChildren();
    for (const c of data.conns) {
      const tr = rows.insertRow();
      tr.insertCell().textContent = c.name + (c.id === data.current ? '  (вибраний)' : '');
      tr.insertCell().textContent = `${c.host}:${c.port}`;
      const st = tr.insertCell();
      st.textContent = c.online ? 'на зв\'язку' : 'немає зв\'язку' + (c.error ? ` — ${c.error}` : '');
      st.style.color = c.online ? 'var(--signal)' : 'var(--crit)';
      const act = tr.insertCell();
      const mk = (txt, title, fn, cls) => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = cls || 'check-btn'; b.textContent = txt; b.title = title; b.onclick = fn;
        act.append(b, ' ');
      };
      if (c.id !== data.current) mk('Показати', 'Перемкнути панель на цей вузол', () => choose(c.id));
      mk('✎', 'Змінити назву чи адресу', () => edit(c));
      if (data.conns.length > 1) mk('✕', 'Прибрати вузол зі списку (історія в базі лишається)', () => remove(c), 'reboot-btn');
    }
  }

  async function load() {
    try { data = await api('/api/conns'); draw(); } catch (e) { say(e.message, true); }
  }
  async function choose(id) {
    try { await api('/api/conns', {action: 'select', id}); location.reload(); } catch (e) { say(e.message, true); }
  }
  function edit(c) {
    editing = c.id;
    f.name.value = c.name; f.host.value = c.host; f.port.value = c.port;
    f.go.textContent = '✓ Зберегти'; f.cancel.hidden = false; f.name.focus();
  }
  function reset() {
    editing = null; form.reset(); f.port.value = 4403;
    f.go.textContent = '＋ Додати вузол'; f.cancel.hidden = true;
  }
  async function remove(c) {
    if (!confirm(`Прибрати «${c.name}» зі списку? Історія в базі лишається.`)) return;
    try { data = await api('/api/conns', {action: 'remove', id: c.id}); say(`«${c.name}» прибрано.`); draw(); if (c.id === sel.value) location.reload(); }
    catch (e) { say(e.message, true); }
  }

  sel.onchange = () => choose(sel.value);
  f.cancel.onclick = () => { reset(); say(''); };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = {action: editing ? 'update' : 'add', name: f.name.value, host: f.host.value, port: +f.port.value};
    if (editing) body.id = editing;
    try {
      data = await api('/api/conns', body);
      say(editing ? 'Збережено.' : 'Вузол додано: панель підключається, за кілька секунд він з\'явиться «на зв\'язку».');
      reset(); draw();
    } catch (e) { say(e.message, true); }
  };

  load();
  setInterval(() => { if (!document.hidden) load(); }, 10000);
})();
