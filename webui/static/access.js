// Доступ і журнал дій (Система, лише адміністратор): пароль для ролі «лише перегляд» і хто що змінював.
(function () {
  const box = document.getElementById('accessbox');
  if (!box) return;
  const $ = id => document.getElementById(id);
  const say = (t, bad) => { $('accmsg').textContent = t || ''; $('accmsg').style.color = bad ? 'var(--crit)' : ''; };
  const ROLE = {admin: 'адмін', viewer: 'перегляд', '': '—'};

  async function who() {
    try {
      const w = await api('/api/whoami');
      $('accstate').textContent = !w.auth ? 'Пароля входу немає: усі мають повний доступ. Задайте пароль, щоб увімкнути ролі.'
        : w.viewer ? `Роль «лише перегляд» увімкнено: користувач «${w.viewer_user}».` : 'Роль «лише перегляд» вимкнено.';
      $('accclear').hidden = !w.viewer;
      return w.role;
    } catch (e) { say(e.message, true); }
  }
  async function log() {
    let rows;
    try { rows = await api('/api/audit'); } catch (e) { return; }
    const tb = $('auditrows'); tb.replaceChildren();
    for (const r of rows) {
      const tr = tb.insertRow();
      for (const c of [new Date(r.t * 1000).toLocaleString('uk-UA'), ROLE[r.role] ?? r.role, r.user || '—', r.ip || '—', r.action]) tr.insertCell().textContent = c;
    }
  }
  $('accsave').onclick = async () => {
    const pw = $('accpw').value;
    if (!pw) { say('Введіть пароль', true); return; }
    try { await api('/api/viewer', {password: pw}); $('accpw').value = ''; say('Збережено. Увійти можна під іменем глядача.'); who(); } catch (e) { say(e.message, true); }
  };
  $('accclear').onclick = async () => {
    if (!confirm('Вимкнути роль «лише перегляд»? Глядачів буде розлогінено.')) return;
    try { await api('/api/viewer', {clear: true}); say('Вимкнено.'); who(); } catch (e) { say(e.message, true); }
  };
  $('auditgo').onclick = log;
  who().then(role => { if (role === 'admin') log(); });  // глядачеві журнал закритий: не смикаємо маршрут і не плодимо відмов
})();
