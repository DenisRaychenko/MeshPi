// Швидкі повідомлення (Quick chat / canned messages): готові фрази, збережені на вузлі, — кнопками під полем надсилання.
// Список читається з вузла (модуль готових повідомлень), редагується у «Керуванні» → «Назва, позиція, дії».
// Клік вставляє фразу в поле тексту; значення вставляємо лише через textContent.
(function () {
  const box = document.getElementById('quick'), text = document.getElementById('text');
  if (!box || !text) return;

  function draw(phrases) {
    box.replaceChildren();
    box.hidden = !phrases.length;
    if (!phrases.length) return;
    const label = document.createElement('span'); label.className = 'hint'; label.textContent = 'Швидкі:';
    box.append(label);
    for (const p of phrases) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'check-btn'; b.textContent = p;
      b.title = 'Вставити в поле повідомлення'; b.onclick = () => { text.value = p; text.focus(); };
      box.append(b);
    }
  }
  async function load() {
    try {
      const r = await runJob({op: 'canned', target: 'local'});
      draw((r.text || '').split('|').map(s => s.trim()).filter(Boolean));
    } catch (e) { box.hidden = true; }  // модуль вимкнено чи вузол зайнятий — просто без швидких фраз
  }
  window.reloadQuick = load;
  setTimeout(load, 4000);  // не змагаємося з першим завантаженням сторінки
})();
