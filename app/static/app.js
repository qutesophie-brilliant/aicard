/* 쉬운 말 · 큰 글씨 · 읽어주기. 상태는 이 브라우저에만 기억한다. */
(function () {
  const root = document.documentElement;
  const safeGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const safeSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

  if (safeGet('aicard.easy') === 'on') root.dataset.easy = 'on';
  if (safeGet('aicard.size') === 'large') root.dataset.size = 'large';

  function sync() {
    document.querySelectorAll('[data-toggle="easy"]').forEach(b => b.setAttribute('aria-pressed', root.dataset.easy === 'on'));
    document.querySelectorAll('[data-toggle="size"]').forEach(b => b.setAttribute('aria-pressed', root.dataset.size === 'large'));
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-toggle]');
    if (!b) return;
    if (b.dataset.toggle === 'easy') {
      root.dataset.easy = root.dataset.easy === 'on' ? '' : 'on';
      safeSet('aicard.easy', root.dataset.easy || 'off');
    } else if (b.dataset.toggle === 'size') {
      root.dataset.size = root.dataset.size === 'large' ? '' : 'large';
      safeSet('aicard.size', root.dataset.size || 'normal');
    } else if (b.dataset.toggle === 'speak') {
      speak(b);
    }
    sync();
  });

  function speak(btn) {
    if (!('speechSynthesis' in window)) { btn.textContent = '이 브라우저는 읽어주기를 지원하지 않아요'; return; }
    if (speechSynthesis.speaking) { speechSynthesis.cancel(); btn.setAttribute('aria-pressed', 'false'); btn.textContent = '읽어주기'; return; }
    const target = document.querySelector(btn.dataset.target || '.label');
    if (!target) return;
    // 쉬운 말 모드면 쉬운 말 문장만 읽는다
    const easy = root.dataset.easy === 'on';
    const nodes = target.querySelectorAll(easy ? '.easy-only, .label-head h2' : '.plain-only, .label-head h2, .label h3, .label .txt, .label .summary');
    let text = Array.from(nodes).map(n => n.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean).join('. ');
    if (!text) text = target.innerText;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ko-KR'; u.rate = easy ? 0.85 : 0.95;
    u.onend = () => { btn.setAttribute('aria-pressed', 'false'); btn.textContent = '읽어주기'; };
    btn.setAttribute('aria-pressed', 'true'); btn.textContent = '읽기 멈추기';
    speechSynthesis.speak(u);
  }

  sync();
})();
