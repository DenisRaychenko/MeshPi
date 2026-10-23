// Налаштування автоматики (Система): правила автовідповідача й заплановані оголошення. Усе вимкнено, доки не ввімкнете.
(function () {
  const form = document.getElementById('autoform');
  if (!form) return;
  const $ = id => document.getElementById(id);
  let st = null;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const num = (v, w, min, max) => { const i = el('input', 'tg-input'); i.type = 'number'; i.value = v ?? ''; i.style.width = w; if (min != null) i.min = min; if (max != null) i.max = max; return i; };
  const txt = (v, w, ph) => { const i = el('input', 'tg-input'); i.type = 'text'; i.value = v || ''; i.style.width = w; if (ph) i.placeholder = ph; return i; };
  const lab = (t, i) => { const l = el('label'); l.append(t + ' ', i); l.style.marginRight = '12px'; return l; };
  const say = (t, bad) => { $('automsg').textContent = t || ''; $('automsg').style.color = bad ? 'var(--crit)' : ''; };
  let R, A;

  function ruleRow(r) {
    const row = el('div', 'row'); row.style.marginBottom = '6px';
    const w = txt(r.word, '8em', 'слово'), t = txt(r.text, '34em', 'текст відповіді, до ' + st.limits.text_bytes + ' байт');
    const del = el('button', 'check-btn', '✕'); del.type = 'button'; del.onclick = () => row.remove();
    row.append(w, t, del); row._get = () => ({word: w.value, text: t.value }); return row;
  }
  function itemRow(it, id) {
    const row = el('div', 'row'); row.style.marginBottom = '6px';
    const t = txt(it.text, '26em', 'текст оголошення'), ev = num(it.every, '5em', 60, 10080), ch = num(it.ch ?? 0, '3.5em', 0, 7), qf = num(it.quiet_from, '3.5em', 0, 23), qt = num(it.quiet_to, '3.5em', 0, 23);
    const del = el('button', 'check-btn', '✕'); del.type = 'button'; del.onclick = () => row.remove();
    row.append(t, lab('кожні, хв', ev), lab('канал', ch), lab('тиша з', qf), lab('до', qt), del);
    row._get = () => ({id, text: t.value, every: +ev.value, ch: +ch.value, quiet_from: qf.value === '' ? null : +qf.value, quiet_to: qt.value === '' ? null : +qt.value});
    return row;
  }

  function draw() {
    form.replaceChildren();
    const c = st.cfg;
    R = {}; A = {};
    const h1 = el('h3', null, 'Автовідповідач'); h1.style.cssText = 'margin:6px 0;font-size:.9rem';
    R.on = el('input'); R.on.type = 'checkbox'; R.on.checked = c.reply.on;
    const on = el('label'); on.append(R.on, ' ввімкнено'); on.style.fontWeight = '600';
    R.scope = el('select', 'tg-input'); for (const [v, t] of [['dm', 'лише особисті'], ['channel', 'лише канал'], ['both', 'і особисті, і канал']]) R.scope.append(new Option(t, v)); R.scope.value = c.reply.scope;
    R.cooldown = num(c.reply.cooldown, '6em', 60, 86400); R.per_hour = num(c.reply.per_hour, '4em', 1, 30); R.cut = num(c.reply.chan_cutoff, '4em', 0, 100);
    const opts = el('div', 'row'); opts.style.margin = '6px 0';
    opts.append(lab('відповідати', R.scope), lab('пауза для одного вузла, с', R.cooldown), lab('не більше на годину', R.per_hour), lab('не передавати при зайнятості ефіру, % (0 вимкнено)', R.cut));
    const rules = el('div'); R.rules = rules; for (const r of c.reply.rules) rules.append(ruleRow(r));
    const add = el('button', 'check-btn', '＋ правило'); add.type = 'button'; add.onclick = () => { if (rules.children.length < 10) rules.append(ruleRow({word: '', text: ''})); };
    const hint = el('p', 'hint', 'Підстановки: ' + st.placeholders.map(p => '{' + p + '}').join(' ') + '. Слово береться з початку повідомлення (регістр неважливий).');
    form.append(h1, on, opts, rules, add, hint);

    const h2 = el('h3', null, 'Заплановані оголошення'); h2.style.cssText = 'margin:14px 0 6px;font-size:.9rem';
    A.on = el('input'); A.on.type = 'checkbox'; A.on.checked = c.announce.on;
    const on2 = el('label'); on2.append(A.on, ' ввімкнено'); on2.style.fontWeight = '600';
    A.cut = num(c.announce.chan_cutoff, '4em', 0, 100);
    const items = el('div'); A.items = items; let n = 0; for (const it of c.announce.items) items.append(itemRow(it, it.id)); n = Math.max(0, ...c.announce.items.map(i => i.id));
    const add2 = el('button', 'check-btn', '＋ оголошення'); add2.type = 'button';
    add2.onclick = () => { if (items.children.length < 5) items.append(itemRow({text: '', every: 360, ch: 0}, ++n)); };
    const o2 = el('div', 'row'); o2.style.margin = '6px 0'; o2.append(lab('не передавати при зайнятості ефіру, % (0 вимкнено)', A.cut));
    const hint2 = el('p', 'hint', `Не частіше за раз на 60 хв; перше оголошення після ввімкнення піде через заданий інтервал. Передано за останню годину (відповіді й оголошення разом): ${st.sent_last_hour}.`);
    form.append(h2, on2, o2, items, add2, hint2);
  }

  const body = () => ({
    reply: {on: R.on.checked, scope: R.scope.value, cooldown: +R.cooldown.value, per_hour: +R.per_hour.value, chan_cutoff: +R.cut.value, rules: [...R.rules.children].map(r => r._get())},
    announce: {on: A.on.checked, chan_cutoff: +A.cut.value, items: [...A.items.children].map(r => r._get())},
  });
  $('autosave').onclick = async () => {
    say('Зберігаю…');
    try { st = await api('/api/auto', body()); draw(); say('Збережено.'); } catch (e) { say(e.message, true); }
  };
  api('/api/auto').then(r => { st = r; draw(); }).catch(() => say('не вдалося прочитати налаштування', true));
})();
