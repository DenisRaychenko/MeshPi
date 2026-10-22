// Керування вузлами: налаштування свого вузла й віддалених по радіо (форми будуються з /api/admin/schema).
// Значення з мережі вставляємо лише через textContent / value.
(function () {
  const box = document.getElementById('adminbox');
  if (!box) return;
  const targetSel = $('#atarget'), stat = $('#astat'), nav = $('#asecs'), pane = $('#apane');
  const SEC_LABEL = {
    device: 'Пристрій', position: 'Позиція (GPS)', power: 'Живлення', network: 'Мережа (Wi-Fi)', display: 'Екран',
    lora: 'LoRa (радіо)', bluetooth: 'Bluetooth', security: 'Безпека (лише читання)', mqtt: 'MQTT', serial: 'Серійний порт',
    external_notification: 'Зовнішні сповіщення', store_forward: 'Store & Forward', range_test: 'Тест дальності',
    telemetry: 'Телеметрія', canned_message: 'Готові повідомлення', audio: 'Аудіо', remote_hardware: 'Віддалені GPIO',
    neighbor_info: 'Інфо про сусідів', ambient_lighting: 'Підсвітка', detection_sensor: 'Датчик виявлення',
    paxcounter: 'Лічильник людей (Pax)', statusmessage: 'Статус-повідомлення', traffic_management: 'Керування трафіком', tak: 'TAK',
  };
  const FIELD_LABEL = {
    region: 'Регіон', modem_preset: 'Пресет модему', use_preset: 'Використовувати пресет', tx_power: 'Потужність, дБм',
    hop_limit: 'Hop limit', channel_num: 'Слот частоти', tx_enabled: 'Передача дозволена', role: 'Роль',
    rebroadcast_mode: 'Режим ретрансляції', node_info_broadcast_secs: 'Період NodeInfo, с', ignore_mqtt: 'Ігнорувати MQTT-пакети',
    ok_to_mqtt: 'Дозволити MQTT-міст', enabled: 'Увімкнено', address: 'Адреса', username: 'Користувач', password: 'Пароль',
    root: 'Корінь теми', proxy_to_client_enabled: 'Проксі через клієнта', map_reporting_enabled: 'Звіт на карту',
    gps_mode: 'Режим GPS', fixed_position: 'Фіксована позиція', position_broadcast_secs: 'Період позиції, с',
    is_power_saving: 'Енергозбереження', wifi_enabled: 'Wi-Fi', wifi_ssid: 'Назва Wi-Fi', wifi_psk: 'Пароль Wi-Fi',
    screen_on_secs: 'Екран світить, с', device_update_interval: 'Період телеметрії пристрою, с',
    environment_update_interval: 'Період довкілля, с', environment_measurement_enabled: 'Вимірювати довкілля',
    admin_key: 'Ключі адміністраторів', is_managed: 'Керований вузол', serial_enabled: 'Серійна консоль',
    debug_log_api_enabled: 'Журнал налагодження по API',
  };
  const POS_PRECISION = [[0, 'не ділитися'], [10, '≈ 23 км'], [11, '≈ 12 км'], [12, '≈ 5,8 км'], [13, '≈ 2,9 км'],
    [14, '≈ 1,5 км'], [15, '≈ 730 м'], [16, '≈ 365 м'], [17, '≈ 180 м'], [18, '≈ 90 м'], [19, '≈ 45 м'], [32, 'точно']];
  let schemas = null, sections = null, section = null, nodes = [];
  const lab = n => FIELD_LABEL[n] || n.replace(/_/g, ' ');

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const say = (t, bad) => { stat.textContent = t || ''; stat.style.color = bad ? 'var(--crit)' : ''; };
  const target = () => targetSel.value || 'local';
  const isRemote = () => target() !== 'local';

  // ---- завдання: запуск і опитування стану ----
  async function job(body) {
    const {job: id} = await api('/api/admin/job', {...body, target: target()});
    const t0 = Date.now();
    for (;;) {
      await new Promise(r => setTimeout(r, 1200));
      const j = await api('/api/admin/job?id=' + id);
      if (j.state === 'done') return j.result;
      if (j.state === 'error') throw new Error(j.error);
      say(`Виконується… ${Math.round((Date.now() - t0) / 1000)} с${j.progress ? ' · ' + j.progress : ''}` +
        (isRemote() ? ' (віддалений вузол по радіо може відповідати десятки секунд)' : ''));
      if (Date.now() - t0 > 15 * 60 * 1000) throw new Error('Завдання виконується надто довго');
    }
  }
  let busy = false;
  async function guarded(fn) {
    if (busy) { say('Дочекайтеся завершення поточної операції.', true); return; }
    busy = true; box.classList.add('busy');
    try { await fn(); } catch (e) { say(e.message, true); } finally { busy = false; box.classList.remove('busy'); }
  }

  // ---- побудова форми з опису полів ----
  function field(f, v) {
    const wrap = el('label', 'afield'), name = el('span', 'aname', lab(f.name));
    name.title = f.name;
    if (f.type === 'message' && !f.repeated) {
      const fs = el('fieldset', 'afs'), lg = el('legend', null, lab(f.name));
      lg.title = f.name; fs.append(lg);
      const sub = fields(f.fields, v || {});
      fs.append(sub.el);
      return {el: fs, get: sub.get};
    }
    let input, get;
    if (f.repeated) {  // повторювані поля: значення через кому; повідомлення — JSON
      input = f.type === 'message' ? el('textarea', 'tg-input') : el('input', 'tg-input');
      input.value = f.type === 'message' ? JSON.stringify(v || [], null, 1) : (v || []).join(', ');
      if (f.type === 'message') input.rows = 4;
      get = () => {
        if (f.type === 'message') return JSON.parse(input.value || '[]');
        const parts = input.value.split(',').map(s => s.trim()).filter(Boolean);
        return f.type === 'int' || f.type === 'float' ? parts.map(Number) : parts;
      };
    } else if (f.type === 'bool') {
      input = el('input'); input.type = 'checkbox'; input.checked = !!v;
      get = () => input.checked;
    } else if (f.type === 'enum') {
      input = el('select', 'tg-input');
      for (const o of f.options) { const op = el('option', null, o); op.value = o; input.append(op); }
      input.value = v ?? f.options[0];
      get = () => input.value;
    } else if (f.type === 'int' || f.type === 'float') {
      input = el('input', 'tg-input'); input.type = 'number';
      if (f.type === 'int') { input.step = 1; if (f.max < 2 ** 31) { input.min = f.min; input.max = f.max; } } else input.step = 'any';
      input.value = v ?? 0;
      get = () => { const n = Number(input.value); if (!Number.isFinite(n)) throw new Error(`«${lab(f.name)}»: потрібне число`); return n; };
    } else {
      input = el('input', 'tg-input'); input.type = f.name.includes('password') || f.name.endsWith('psk') && f.type === 'string' ? 'password' : 'text';
      input.value = v ?? ''; input.autocomplete = 'off';
      get = () => input.value;
    }
    wrap.append(name, input);
    return {el: wrap, get};
  }
  function fields(list, values) {
    const root = el('div', 'afields'), parts = [];
    for (const f of list) {
      const x = field(f, values[f.name]);
      root.append(x.el); parts.push([f.name, x.get]);
    }
    return {el: root, get: () => Object.fromEntries(parts.map(([n, g]) => [n, g()]))};
  }

  // ---- розділи конфігурації ----
  function buildNav() {
    nav.replaceChildren();
    const add = (id, text, group) => {
      const b = el('button', 'tab-link', text); b.type = 'button'; b.dataset.id = id;
      b.onclick = () => open(id);
      group.append(b);
    };
    const mkGroup = title => { const g = el('div', 'agroup'); g.append(el('div', 'agtitle', title)); nav.append(g); return g; };
    const g0 = mkGroup('Вузол');
    add('node', 'Назва, позиція, дії', g0); add('channels', 'Канали', g0);
    const g1 = mkGroup('Налаштування');
    for (const s of sections.config) add(s, SEC_LABEL[s] || s, g1);
    const g2 = mkGroup('Модулі');
    for (const s of sections.module) add(s, SEC_LABEL[s] || s, g2);
  }
  function mark(id) { for (const b of nav.querySelectorAll('button')) b.classList.toggle('active', b.dataset.id === id); }
  function open(id) {
    section = id; mark(id); pane.replaceChildren();
    if (id === 'node') return nodePane();
    if (id === 'channels') return channelsPane();
    return configPane(id);
  }

  function configPane(name) {
    const ro = sections.readonly.includes(name);
    const head = el('div', 'row'), title = el('h3', null, SEC_LABEL[name] || name);
    const read = el('button', 'check-btn', '⟳ Прочитати'); read.type = 'button';
    const write = el('button', 'check-btn', '✓ Записати на вузол'); write.type = 'button';
    write.hidden = ro;
    head.append(title, read, write);
    const note = el('p', 'hint', ro ? 'Розділ лише для читання: тут ключі вузла. Змінюйте їх у застосунку Meshtastic або CLI.'
      : 'Спершу натисніть «Прочитати», змініть потрібне й «Записати». Записуються всі поля розділу.');
    const holder = el('div'); let form = null;
    pane.append(head, note, holder);
    read.onclick = () => guarded(async () => {
      say('Читаю…');
      const r = await job({op: 'read', section: name});
      form = fields(schemas[name], r.values);
      holder.replaceChildren(form.el);
      say(`Прочитано (${new Date().toLocaleTimeString('uk-UA')}).`);
    });
    if (!isRemote()) setTimeout(() => read.click(), 0);  // свій вузол читається миттєво й без ефіру; віддалений — лише за кнопкою
    write.onclick = () => guarded(async () => {
      if (!form) { say('Спершу прочитайте розділ.', true); return; }
      const values = form.get();
      const risky = name === 'lora' ? ' Невідповідні регіон чи пресет можуть обірвати зв\'язок із вузлом.' : '';
      if (!confirm(`Записати розділ «${SEC_LABEL[name] || name}» на ${isRemote() ? 'віддалений вузол ' + targetSel.selectedOptions[0].textContent : 'цей вузол'}?${risky}`)) return;
      say('Записую…');
      const r = await job({op: 'write', section: name, values});
      say(r.note || 'Записано.');
    });
  }

  // ---- канали ----
  function channelsPane() {
    const head = el('div', 'row'), title = el('h3', null, 'Канали');
    const read = el('button', 'check-btn', '⟳ Прочитати канали'); read.type = 'button';
    head.append(title, read);
    const holder = el('div', 'achans');
    pane.append(head, el('p', 'hint', 'Канал 0 — основний. PSK — ключ шифрування в base64 (16 або 32 байти; «нові ключі» створює випадковий). ' +
      'Точність позиції — наскільки точно канал ділиться вашим розташуванням. Вимкнений канал (DISABLED) не використовується.'), holder);
    read.onclick = () => guarded(async () => {
      say('Читаю канали…');
      const r = await job({op: 'channels'});
      holder.replaceChildren(...r.channels.map(chanCard));
      say(`Прочитано ${r.channels.length} каналів.`);
    });
    if (!isRemote()) setTimeout(() => read.click(), 0);
  }
  function chanCard(ch) {
    const card = el('section', 'achan'), h = el('h4', null, `Канал ${ch.index}${ch.index === 0 ? ' (основний)' : ''}`);
    const mk = (label, input) => { const w = el('label', 'afield'); w.append(el('span', 'aname', label), input); return w; };
    const role = el('select', 'tg-input');
    for (const r of ['DISABLED', 'PRIMARY', 'SECONDARY']) { const o = el('option', null, r); o.value = r; role.append(o); }
    role.value = ch.role;
    const name = el('input', 'tg-input'); name.value = ch.name; name.maxLength = 11;
    const psk = el('input', 'tg-input'); psk.value = ch.psk; psk.autocomplete = 'off';
    const gen = el('button', 'check-btn', 'нові ключі'); gen.type = 'button';
    gen.onclick = () => { const b = crypto.getRandomValues(new Uint8Array(32)); psk.value = btoa(String.fromCharCode(...b)); };
    const up = el('input'); up.type = 'checkbox'; up.checked = ch.uplink_enabled;
    const down = el('input'); down.type = 'checkbox'; down.checked = ch.downlink_enabled;
    const muted = el('input'); muted.type = 'checkbox'; muted.checked = ch.is_muted;
    const prec = el('select', 'tg-input');
    const known = POS_PRECISION.some(p => p[0] === ch.position_precision);
    for (const [v, t] of known ? POS_PRECISION : [...POS_PRECISION, [ch.position_precision, `${ch.position_precision} біт`]]) {
      const o = el('option', null, `${v} — ${t}`); o.value = v; prec.append(o);
    }
    prec.value = ch.position_precision;
    const save = el('button', 'check-btn', '✓ Записати канал'); save.type = 'button';
    save.onclick = () => guarded(async () => {
      if (!confirm(`Записати канал ${ch.index} на ${isRemote() ? 'віддалений' : 'цей'} вузол? Зміна ключа відріже вузли зі старим ключем.`)) return;
      say('Записую канал…');
      await job({op: 'write_channel', channel: {index: ch.index, role: role.value, name: name.value, psk: psk.value,
        uplink_enabled: up.checked, downlink_enabled: down.checked, is_muted: muted.checked, position_precision: +prec.value}});
      say(`Канал ${ch.index} записано.`);
    });
    const pskRow = mk('PSK (base64)', psk); pskRow.append(gen);
    card.append(h, mk('Роль', role), mk('Назва (до 11 байт)', name), pskRow,
      mk('Вивантаження в MQTT (uplink)', up), mk('Завантаження з MQTT (downlink)', down), mk('Вимкнути звук', muted),
      mk('Точність позиції', prec), save);
    return card;
  }

  // ---- назва, позиція, дії ----
  function nodePane() {
    const sec = (t) => { const s = el('section', 'achan'); s.append(el('h4', null, t)); pane.append(s); return s; };
    const row = (parent, label, input) => { const w = el('label', 'afield'); w.append(el('span', 'aname', label), input); parent.append(w); return input; };
    const btn = (parent, text, fn, cls) => { const b = el('button', cls || 'check-btn', text); b.type = 'button'; b.onclick = () => guarded(fn); parent.append(b, ' '); return b; };

    const own = sec('Назва вузла');
    const ln = row(own, 'Назва (до 39 байт)', el('input', 'tg-input')), sn = row(own, 'Коротка (до 4 байт)', el('input', 'tg-input'));
    ln.maxLength = 39; sn.maxLength = 4;
    btn(own, '⟳ Прочитати', async () => { say('Читаю…'); const r = await job({op: 'owner'}); ln.value = r.long_name || ''; sn.value = r.short_name || ''; say('Прочитано.'); });
    btn(own, '✓ Записати', async () => { say('Записую…'); await job({op: 'set_owner', long_name: ln.value, short_name: sn.value}); say('Назву записано.'); });

    const posi = sec('Фіксована позиція');
    const la = row(posi, 'Широта', el('input', 'tg-input')), lo = row(posi, 'Довгота', el('input', 'tg-input')), al = row(posi, 'Висота, м', el('input', 'tg-input'));
    la.type = lo.type = al.type = 'number'; la.step = lo.step = 'any'; al.value = 0;
    btn(posi, '✓ Задати позицію', async () => {
      say('Записую позицію…'); await job({op: 'fixed_position', lat: +la.value, lon: +lo.value, alt: +al.value}); say('Позицію задано.');
    });
    btn(posi, 'Прибрати фіксовану', async () => { say('Прибираю…'); await job({op: 'action', name: 'remove_fixed_position'}); say('Фіксовану позицію прибрано.'); }, 'reboot-btn');

    const info = sec('Про вузол');
    const out = el('pre', null, ''); out.style.cssText = 'white-space:pre-wrap;margin:8px 0 0';
    btn(info, '⟳ Версія прошивки, плата', async () => {
      say('Читаю…'); const r = await job({op: 'metadata'});
      out.textContent = Object.entries(r.metadata).map(([k, v]) => `${k}: ${v}`).join('\n'); say('Прочитано.');
    });
    info.append(out);

    const act = sec('Дії');
    const where = () => isRemote() ? 'ВІДДАЛЕНИЙ вузол ' + targetSel.selectedOptions[0].textContent : 'цей вузол';
    btn(act, '⟲ Перезавантажити', async () => {
      if (!confirm(`Перезавантажити ${where()}?`)) return; say('Надсилаю…'); const r = await job({op: 'action', name: 'reboot', args: {secs: 5}}); say(r.note || 'Вузол перезавантажиться за кілька секунд.');
    });
    btn(act, '🕐 Синхронізувати час', async () => { say('Надсилаю…'); await job({op: 'action', name: 'set_time'}); say('Час на вузлі встановлено з цього комп\'ютера.'); });
    btn(act, '⏻ Вимкнути', async () => {
      if (!confirm(`Вимкнути ${where()}? Увімкнути його знову можна лише фізично.`)) return;
      say('Надсилаю…'); const r = await job({op: 'action', name: 'shutdown', confirm: true, args: {secs: 5}}); say(r.note || 'Команду вимкнення прийнято.');
    }, 'reboot-btn');
    btn(act, '🧹 Очистити список вузлів', async () => {
      if (!confirm(`Очистити список відомих вузлів (NodeDB) на ${where()}?`)) return;
      say('Надсилаю…'); await job({op: 'action', name: 'nodedb_reset', confirm: true}); say('Список вузлів очищено.');
    }, 'reboot-btn');
    btn(act, '⚠ Скинути налаштування', async () => {
      if (!confirm(`СКИНУТИ усі налаштування на ${where()} до заводських (канали, ключі, радіо)? Це незворотно.`)) return;
      say('Надсилаю…'); await job({op: 'action', name: 'factory_reset_config', confirm: true}); say('Команду скидання прийнято.');
    }, 'reboot-btn');
  }

  // ---- вибір вузла ----
  async function loadTargets() {
    try {
      nodes = await api('/api/nodes');
      const st = await api('/api/status');
      const me = (st.node || {}).id;
      const keep = targetSel.value;
      targetSel.replaceChildren();
      const first = el('option', null, `Цей вузол — ${(st.node || {}).longName || me || 'підключений до панелі'}`); first.value = 'local';
      targetSel.append(first);
      for (const n of nodes.filter(x => x.id && x.id !== me && !x.mqtt)) {
        const o = el('option', null, `${n.long || n.short || n.id}${n.hops != null ? ` · ${n.hops} стрибк.` : ''} · по радіо`);
        o.value = n.id; targetSel.append(o);
      }
      if ([...targetSel.options].some(o => o.value === keep)) targetSel.value = keep;
    } catch (e) { say(e.message, true); }
  }

  (async function init() {
    try {
      const s = await api('/api/admin/schema');
      schemas = s.schemas; sections = s.sections;
    } catch (e) { say(e.message, true); return; }
    buildNav(); await loadTargets(); open('lora');
  })();
  targetSel.onchange = () => { say(''); if (section) open(section); };
  $('#areload').onclick = loadTargets;
})();
