// Перевірка безпеки (Система): знахідки з /api/security. Назви вузлів — чужі дані, вставляємо лише через textContent.
(function () {
  const box = document.getElementById('secbox');
  if (!box) return;
  const $ = id => document.getElementById(id);
  const LEVEL = {ok: 'гаразд', info: 'до уваги', warn: 'увага', crit: 'проблема'};
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  async function run() {
    $('secnote').textContent = 'перевіряю…';
    const out = $('secfind'); out.replaceChildren();
    let r;
    try { r = await api('/api/security'); } catch (e) { $('secnote').textContent = e.message; return; }
    $('secnote').textContent = `вузлів ${r.nodes}, з ключем ${r.keyed}`;
    for (const f of r.findings) {
      const row = el('div', 'hfind ' + f.level), body = el('div');
      body.append(el('div', null, f.text));
      if (f.items.length) body.append(el('div', 'hint', f.items.slice(0, 12).join(', ') + (f.items.length > 12 ? ` та ще ${f.items.length - 12}` : '')));
      row.append(el('span', 'lv', LEVEL[f.level] || f.level), body);
      out.append(row);
    }
  }
  $('secrun').onclick = run;
})();
