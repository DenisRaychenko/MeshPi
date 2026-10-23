// Налаштування сповіщень поза браузером (Система): Telegram, ntfy, вебхук, які події слати й пороги тривог.
// Токени сюди не приходять (лише «задано»); порожнє поле лишає збережений токен, «Стерти» видаляє канал.
(function () {
  const form = document.getElementById('notifyform');
  if (!form) return;
  const $ = id => document.getElementById(id);
  let cfg = null;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const field = (label, input) => { const l = el('label'); l.append(label + ' ', input); l.style.marginRight = '12px'; return l; };
  const input = (type, value, ph, w) => { const i = el('input', 'tg-input'); i.type = type; i.value = value || ''; if (ph) i.placeholder = ph; i.style.width = w || '14em'; i.autocomplete = 'off'; return i; };
  const say = (t, bad) => { $('notifymsg').textContent = t || ''; $('notifymsg').style.color = bad ? 'var(--crit)' : ''; };
  let F = {};

  function box(title, key, extra) {
    const b = el('div'); b.style.cssText = 'border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 10px';
    const on = el('input'); on.type = 'checkbox'; on.checked = cfg[key].on;
    const head = el('label'); head.style.fontWeight = '600'; head.append(on, ' ' + title);
    const clear = el('button', 'check-btn', 'Стерти'); clear.type = 'button'; clear.style.marginLeft = '12px';
    clear.onclick = async () => {
      if (!confirm(`Стерти налаштування каналу «${title}» разом із токеном?`)) return;
      try { cfg = await api('/api/notify', {clear: [key]}); draw(); say('Стерто.'); } catch (e) { say(e.message, true); }
    };
    const row = el('div', 'row'); row.style.marginTop = '8px';
    b.append(head, clear, row);
    F[key] = {on, row};
    extra(row);
    form.append(b);
  }

  function draw() {
    form.replaceChildren(); F = {};
    box('Telegram', 'telegram', row => {
      F.telegram.token = input('password', '', cfg.telegram.token_set ? 'токен задано' : '123456789:AA… від @BotFather', '18em');
      F.telegram.chat = input('text', cfg.telegram.chat, '-100… або @канал', '11em');
      row.append(field('токен бота', F.telegram.token), field('chat id', F.telegram.chat));
    });
    box('ntfy', 'ntfy', row => {
      F.ntfy.url = input('text', cfg.ntfy.url, 'https://ntfy.sh', '14em'); F.ntfy.topic = input('text', cfg.ntfy.topic, 'meshpi-моя-тема', '12em');
      F.ntfy.token = input('password', '', cfg.ntfy.token_set ? 'токен задано' : 'за потреби', '10em');
      row.append(field('сервер', F.ntfy.url), field('тема', F.ntfy.topic), field('токен', F.ntfy.token));
    });
    box('Вебхук (JSON)', 'webhook', row => {
      F.webhook.url = input('text', cfg.webhook.url, 'http://хост:порт/шлях', '20em'); F.webhook.token = input('password', '', cfg.webhook.token_set ? 'токен задано' : 'за потреби', '10em');
      row.append(field('адреса', F.webhook.url), field('Bearer-токен', F.webhook.token));
    });
    const kinds = el('div', 'row'); kinds.style.margin = '4px 0 8px'; F.kinds = {};
    kinds.append(el('span', 'hint', 'Слати події:'));
    for (const [k, label] of Object.entries(cfg.labels)) {
      const c = el('input'); c.type = 'checkbox'; c.checked = cfg.kinds[k]; F.kinds[k] = c;
      const l = el('label'); l.append(c, ' ' + label); kinds.append(l);
    }
    const rules = el('div', 'row'); F.rules = {};
    rules.append(field('низький заряд, % (0 вимкнено)', F.rules.battery = input('number', cfg.rules.battery, '', '5em')),
                 field('перевантажений ефір, % (0 вимкнено)', F.rules.chan = input('number', cfg.rules.chan, '', '5em')));
    form.append(kinds, rules);
  }

  function body() {
    const b = {telegram: {on: F.telegram.on.checked, chat: F.telegram.chat.value, token: F.telegram.token.value},
               ntfy: {on: F.ntfy.on.checked, url: F.ntfy.url.value, topic: F.ntfy.topic.value, token: F.ntfy.token.value},
               webhook: {on: F.webhook.on.checked, url: F.webhook.url.value, token: F.webhook.token.value},
               kinds: {}, rules: {battery: +F.rules.battery.value, chan: +F.rules.chan.value}};
    for (const [k, c] of Object.entries(F.kinds)) b.kinds[k] = c.checked;
    return b;
  }

  $('notifysave').onclick = async () => {
    say('Зберігаю…');
    try { cfg = await api('/api/notify', body()); draw(); say('Збережено.'); } catch (e) { say(e.message, true); }
  };
  $('notifytest').onclick = async () => {
    say('Надсилаю…');
    try {
      const r = await api('/api/notify/test', {});
      say(r.map(x => `${x.channel}: ${x.ok ? 'доставлено' : 'помилка (' + x.why + ')'}`).join(' · '), r.some(x => !x.ok));
    } catch (e) { say(e.message, true); }
  };
  api('/api/notify').then(c => { cfg = c; draw(); }).catch(() => say('не вдалося прочитати налаштування', true));
})();
