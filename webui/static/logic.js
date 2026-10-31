// Чиста логіка сторінок (без DOM) — її перевіряє test_logic.py через node.
// Лише function/var: так функції видно і в браузері, і в тестовому контексті node.

// Скільки часу минуло: «12 с», «5 хв», «3 год», «2 д»
function ago(t, nowSec) {
  if (!t) return '—';
  const s = Math.max(0, (nowSec ?? Date.now() / 1000) - t) | 0;
  return s < 90 ? s + ' с' : s < 5400 ? (s / 60 | 0) + ' хв' : s < 129600 ? (s / 3600 | 0) + ' год' : (s / 86400 | 0) + ' д';
}

// Час роботи вузла: raw — секунди від вузла, at — коли отримано (мс); «цокає» між оновленнями
function uptimeStr(raw, at, nowMs) {
  if (raw == null) return '—';
  const t = raw + ((nowMs ?? Date.now()) - at) / 1000 | 0, d = t / 86400 | 0, h = t % 86400 / 3600 | 0, mi = t % 3600 / 60 | 0;
  return d ? `${d}д ${h}г` : `${h}г ${mi}хв`;
}

// Якість сигналу: [добре від, середньо від]; нижче — погано. Межі під LongFast (SF11 приймає до SNR ≈ −17 дБ).
var SNR = [0, -10], RSSI = [-100, -115];
function quality([good, fair], v) { return v >= good ? 'signal' : v >= fair ? 'warn' : 'crit'; }

// Прошивка шле 101 %, коли вузол живиться від мережі (USB), а не від батареї
function bat(b) { return b == null ? null : b > 100 ? 'мережа' : b + '%'; }

// Відстань по великому колу (гаверсинус), м; null — немає координат у вузла чи «розташування плати»
function distance(n, home) {
  if (!home || n.lat == null || n.lon == null) return null;
  const r = Math.PI / 180, a = Math.sin((n.lat - home.lat) * r / 2) ** 2 +
    Math.cos(home.lat * r) * Math.cos(n.lat * r) * Math.sin((n.lon - home.lon) * r / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}
function distStr(m) { return m == null ? null : m < 1000 ? `${Math.round(m)} м` : `${(m / 1000).toFixed(m < 100000 ? 1 : 0)} км`; }

// Сортування вузлів: {key, dir}; назва — без регістру й з числами за значенням («Вузол 2» перед «Вузол 10»);
// порожні значення завжди внизу
function sortVal(n, k) { return k === 'name' ? (n.long || n.short || '').toLowerCase() || null : n[k]; }
function sorted(list, {key, dir}) {
  return [...list].sort((a, b) => {
    const x = sortVal(a, key), y = sortVal(b, key);
    if (x == null || y == null) return (x == null) - (y == null);
    if (key === 'name') return x.localeCompare(y, 'uk', {numeric: true}) * dir;
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
}

// Фільтр «почуті за»: age — секунди (0 — усі); свій вузол і обрані показуються завжди
function heardRecently(n, age, nowSec, myId, favs) {
  return !age || n.id === myId || favs.has(n.id) || n.heard > nowSec - age;
}

// «50.4501, 30.5234», «50.4501 30.5234» або посилання Google Карт з «@50.4501,30.5234,15z» -> {lat, lon} | null
function parseLoc(t) {
  const m = t.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/) || t.match(/(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)/);
  return m && {lat: +m[1], lon: +m[2]};
}

// Реакції-емодзі (reply_id + emoji, як у застосунку) до повідомлень зі списку: pid → Map(емодзі → {n, who}).
// Реакція на повідомлення, якого в списку немає, сюди не потрапляє — її показують окремим рядком.
function reactionsOf(list) {
  const own = new Set(list.filter(m => m.pid && !m.emoji).map(m => m.pid)), out = new Map();
  for (const m of list) {
    if (!m.emoji || !own.has(m.reply)) continue;
    const g = out.get(m.reply) || new Map(), r = g.get(m.text) || {n: 0, who: []};
    out.set(m.reply, g); g.set(m.text, r); r.n++; r.who.push(m.name || m.from || '?');
  }
  return out;
}

// RTTTL («назва:d=4,o=5,b=120:c,8e.,p,a#6») -> {name, notes: [{f — Гц (0 — пауза), ms}]} | null.
// Типові d=4, o=6, b=63 (специфікація); A4 = 440 Гц. Обмеження: до 200 нот і 30 с — це сигнал, а не концерт.
var RT_DUR = [1, 2, 4, 8, 16, 32], RT_NOTE = {c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11, h: 11};
function parseRtttl(s) {
  const parts = typeof s === 'string' && s.length <= 1000 ? s.split(':') : [];
  if (parts.length !== 3) return null;
  const def = {d: 4, o: 6, b: 63};
  for (const kv of parts[1].split(',')) {
    if (!kv.trim()) continue;
    const m = kv.trim().toLowerCase().match(/^([dob])\s*=\s*(\d{1,3})$/);
    if (!m) return null;
    def[m[1]] = +m[2];
  }
  if (!RT_DUR.includes(def.d) || def.o < 3 || def.o > 8 || def.b < 10 || def.b > 900) return null;
  const notes = [];
  let total = 0;
  for (const raw of parts[2].split(',')) {
    const t = raw.trim().toLowerCase();
    if (!t) continue;
    const m = t.match(/^(\d{1,2})?([a-hp])(#?)(\.?)([3-8])?(\.?)$/), d = m && (m[1] ? +m[1] : def.d);
    if (!m || !RT_DUR.includes(d)) return null;
    const ms = 60000 / def.b * 4 / d * (m[4] || m[6] ? 1.5 : 1), o = m[5] ? +m[5] : def.o;
    const f = m[2] === 'p' ? 0 : 440 * 2 ** ((12 * (o + 1) + RT_NOTE[m[2]] + (m[3] ? 1 : 0) - 69) / 12);
    notes.push({f, ms});
    total += ms;
    if (notes.length > 200 || total > 30000) return null;
  }
  return notes.length ? {name: parts[0].trim(), notes} : null;
}

// Текст повідомлення → частини [рядок, посилання?]: лише http(s):// і www.; кінцеві розділові знаки — не частина адреси.
// Повертає дані, а не HTML: сторінка сама робить <a> з href, тож ні javascript:, ні розмітка з ефіру не пройдуть
function linkParts(text) {
  const out = [], re = /\b(?:https?:\/\/|www\.)[^\s<>"]+/gi;
  let at = 0, m;
  while ((m = re.exec(text))) {
    let url = m[0];
    const extra = u => u.endsWith(')') && u.split(')').length > u.split('(').length;  // «)» без пари — з тексту
    while (/[.,;:!?'»\]]$/.test(url) || extra(url)) url = url.slice(0, -1);
    if (m.index > at) out.push([text.slice(at, m.index), false]);
    out.push([url, true]);
    at = m.index + url.length; re.lastIndex = at;
  }
  if (at < text.length) out.push([text.slice(at), false]);
  return out;
}

// Цитата для відповіді (reply_id без emoji): «Ім'я: початок тексту…», не довше n символів (емодзі не ріжемо навпіл)
function quoteText(orig, n = 80) {
  if (!orig) return 'повідомлення, якого вже немає в журналі';
  const s = `${orig.name || orig.from || '?'}: ${orig.text || ''}`, cp = [...s];
  return cp.length > n ? cp.slice(0, n - 1).join('') + '…' : s;
}

// Вкладки повідомлень: '' — канали (усім), інакше ID вузла приватної розмови
const dmPeer = m => (m.to && m.to !== '^all' ? (m.out ? m.to : m.from) : '') || '';
// Приватні розмови — від найсвіжішої; extra — відкриті вручну (клік по вузлу), ще без повідомлень
function dmTabs(list, extra = []) {
  const last = new Map();
  for (const m of list) { const p = dmPeer(m); if (p) last.set(p, Math.max(last.get(p) || 0, m.t)); }
  for (const p of extra) if (p && !last.has(p)) last.set(p, Infinity);
  return [...last].sort((a, b) => b[1] - a[1]).map(([p]) => p);
}
// Скільки вхідних (не реакцій) у вкладці новіші за since
// Вкладки чату: кожен канал окремо ('c0', 'c1', …: лише повідомлення в канал) і приватні розмови (ID вузла)
const isChanTab = t => /^c\d+$/.test(t);
const inTab = (m, t) => isChanTab(t) ? !dmPeer(m) && 'c' + (m.ch || 0) === t : dmPeer(m) === t;
const unreadIn = (list, tab, since) => list.filter(m => !m.out && !m.emoji && inTab(m, tab) && m.t > since).length;

// Потужність на антені за налаштуванням чипа: дБм → [дБм, мВт, оцінка?]; для кожного модуля з підсилювачем — своя таблиця
// E22-400M33S — за графіком Ebyte «E22-M Series» v1.5 (VCC 5 В, 25 °C): точки 1, 4, 7, 11, 16, 21 дБм, між ними —
// лінійно; 22 — оцінка (графік закінчується на 21, максимум за описом ~33,5 дБм)
const E22_ANT = {1: [18.7, 74], 2: [19.6, 91], 3: [20.5, 112], 4: [21.5, 141], 5: [22.4, 174], 6: [23.4, 219],
  7: [24.4, 275], 8: [25.2, 331], 9: [25.9, 389], 10: [26.7, 468], 11: [27.5, 562], 12: [28.1, 646], 13: [28.8, 759],
  14: [29.4, 871], 15: [30, 1000], 16: [30.7, 1175], 17: [31.2, 1318], 18: [31.7, 1479], 19: [32.2, 1660],
  20: [32.7, 1862], 21: [33.2, 2089], 22: [33.5, 2239, 1]};
// Виміри власника на його E22 (для порівняння в таблиці налаштувань; на ~0,5–1 дБ нижчі за графік виробника)
const E22_MEAS = {12: [27, 513], 13: [28, 615], 14: [29, 720], 15: [29, 856], 16: [30, 1020], 17: [30.7, 1180],
  18: [31.36, 1370], 19: [32, 1580], 20: [32.52, 1800], 21: [32.95, 1970], 22: [33.29, 2140]};
// RFM98PW-433S2: виміри власника в мВт, дБм пораховано з них
const RFM98PW_ANT = {12: [24.7, 295], 13: [25.5, 358], 14: [26.3, 423], 15: [27, 496], 16: [27.8, 598], 17: [28.5, 716],
  18: [29.3, 845], 19: [29.8, 958], 20: [30.3, 1070], 21: [30.6, 1160], 22: [30.9, 1230]};
// Таблиця модуля з конфігу meshtasticd (назва файлу з /api/power); null — модуль без підсилювача або без таблиці
function antTable(radio) {
  if (/e22/i.test(radio || '')) return {name: 'E22-400M33S', rows: E22_ANT, meas: E22_MEAS};
  if (/rfm98pw/i.test(radio || '')) return {name: 'RFM98PW-433S2', rows: RFM98PW_ANT};
  return null;
}
// Що виходить в антену: з таблиці модуля (null поза нею); Ra-01/02 без підсилювача — те саме, що на чипі
function antennaPower(chip, radio) {
  if (chip == null) return null;
  const t = antTable(radio);
  if (t) { const r = t.rows[chip]; return r ? {dbm: r[0], mw: r[1], est: !!r[2]} : null; }
  if (/ra-01/i.test(radio || '')) return {dbm: chip, mw: Math.round(10 ** (chip / 10)), est: false};
  return null;
}
// Приватні вкладки без закритих: закрита (closed[id] = час останнього повідомлення на момент закриття) знову
// з'являється, щойно в розмові є новіше повідомлення; keep — відкриті зараз (активна, клік по вузлу) — завжди
function shownTabs(list, keep = [], closed = {}) {
  const last = {};
  for (const m of list) { const p = dmPeer(m); if (p) last[p] = Math.max(last[p] || 0, m.t); }
  return dmTabs(list, keep).filter(id => keep.includes(id) || !(id in closed) || (last[id] || 0) > closed[id]);
}
