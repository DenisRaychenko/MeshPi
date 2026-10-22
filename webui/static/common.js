// Спільне для всіх сторінок. Дані з мережі вставляємо лише через textContent.
const $ = s => document.querySelector(s);
// кільце в шапці: 100% — на зв'язку, 15% — ні, 75% (типово у CSS) — до першої відповіді
const ring = ok => $('.ring').style.setProperty('--ring-pct', ok ? '100%' : '15%');
async function api(url, body) {
  const r = await fetch(url, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); throw new Error('Потрібен вхід'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function metrics(box, pairs) {  // плитки «підпис / значення [одиниця]» зі style.css
  box.replaceChildren();
  for (const [k, v, u] of pairs) {
    const d = document.createElement('div'), l = document.createElement('div'), x = document.createElement('div');
    d.className = 'metric'; l.className = 'label'; x.className = 'value' + (String(v).length > 9 ? ' text-value' : '');
    l.textContent = k; x.textContent = v;
    if (u) { const e = document.createElement('span'); e.className = 'unit'; e.textContent = u; x.append(e); }
    d.append(l, x); box.append(d);
  }
}

const themeIcon = () => { $('#theme').textContent = document.documentElement.dataset.theme === 'light' ? '☀' : '☾'; };
themeIcon();
$('#theme').onclick = () => {
  const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = t; themeIcon();
  try { localStorage.setItem('theme', t); } catch (e) { /* ок */ }
};

// Звук сповіщення: мелодія RTTTL, збережена в «Налаштуваннях» (localStorage 'rtttl'), або короткий сигнал.
// Квадратна хвиля — як п'єзопищалка вузла Meshtastic.
let audio;
function playNotes(notes) {
  try {
    audio = audio || new AudioContext();
    if (audio.state === 'suspended') audio.resume();
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = 'square'; g.gain.value = 0; o.connect(g); g.connect(audio.destination);
    let t = audio.currentTime + 0.02;
    for (const n of notes) {
      if (n.f) { o.frequency.setValueAtTime(n.f, t); g.gain.setValueAtTime(0.03, t); g.gain.setValueAtTime(0, t + n.ms * 0.9 / 1000); }
      t += n.ms / 1000;
    }
    o.start(); o.stop(t);
  } catch (e) { /* без звуку */ }
}
function chime() {
  let s = null;
  try { s = localStorage.getItem('rtttl'); } catch (e) { /* без сховища — простий сигнал */ }
  const r = s && parseRtttl(s);
  playNotes(r ? r.notes : [{f: 880, ms: 150}]);
}

// Адмін-завдання (/api/admin/job): запустити й дочекатися результату; progress(завдання, мс) — для індикатора.
async function runJob(body, progress) {
  const {job: id} = await api('/api/admin/job', body);
  const t0 = Date.now();
  for (;;) {
    await new Promise(r => setTimeout(r, 1000));
    const j = await api('/api/admin/job?id=' + id);
    if (j.state === 'done') return j.result;
    if (j.state === 'error') throw new Error(j.error);
    if (progress) progress(j, Date.now() - t0);
    if (Date.now() - t0 > 10 * 60 * 1000) throw new Error('Завдання виконується надто довго');
  }
}
