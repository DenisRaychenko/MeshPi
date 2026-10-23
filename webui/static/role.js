// Роль користувача: у режимі «лише перегляд» ховаємо вкладки керування й поле надсилання (сервер усе одно забороняє дії).
(function () {
  api('/api/whoami').then(w => {
    if (w.role !== 'viewer') return;
    document.body.classList.add('viewer');
    for (const t of ['admin', 'radio', 'node', 'system']) {
      const b = document.querySelector(`#tabbar [data-tab="${t}"]`); if (b) b.hidden = true;
      const p = document.querySelector(`.tabpane[data-tab="${t}"]`); if (p) p.remove();
    }
    const text = document.getElementById('text'), form = text && text.closest('form'); if (form) form.hidden = true;
    const bar = document.createElement('div'); bar.className = 'strip'; bar.style.marginBottom = '12px';
    bar.textContent = 'Режим «лише перегляд»: карта, чат, події й графіки доступні, змінювати нічого не можна.';
    document.getElementById('tabbar').after(bar);
    const cur = document.querySelector('#tabbar .active');
    if (!cur || cur.hidden) window.showTab('overview');
  }).catch(() => {});
})();
