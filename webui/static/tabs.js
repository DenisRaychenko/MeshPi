// Вкладки: одна сторінка, розділи показуються/ховаються. Адреса #overview, #messages, #nodes, #radio, #node, #system.
(function () {
  const buttons = [...document.querySelectorAll('#tabbar [data-tab]')];
  const panes = [...document.querySelectorAll('.tabpane')];
  const ids = buttons.map(b => b.dataset.tab);

  function show(id, push) {
    if (!ids.includes(id)) id = ids[0];
    for (const b of buttons) {
      const on = b.dataset.tab === id;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    }
    for (const p of panes) p.hidden = p.dataset.tab !== id;
    try { localStorage.setItem('tab', id); } catch (e) { /* без сховища — ок */ }
    if (push && location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
    window.dispatchEvent(new Event('resize'));  // карта Leaflet, показана з прихованої вкладки, перемальовується
    window.scrollTo(0, 0);
  }

  for (const b of buttons) b.onclick = () => show(b.dataset.tab, true);
  window.addEventListener('hashchange', () => show(location.hash.slice(1), false));
  let start = location.hash.slice(1);
  if (!ids.includes(start)) { try { start = localStorage.getItem('tab') || ''; } catch (e) { start = ''; } }
  show(start, true);
  window.showTab = id => show(id, true);
})();
