/* 쉬운 말 · 큰 글씨 · 읽어주기 · 공유 · 제품 찾기. 보기 설정은 이 브라우저에만 남는다. */
(function () {
  const root = document.documentElement;
  const safeGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const safeSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

  if (window.matchMedia && window.matchMedia('(hover: none) and (pointer: coarse)').matches) root.dataset.touch = '1';
  if (safeGet('aicard.easy') === 'on') root.dataset.easy = 'on';
  if (safeGet('aicard.size') === 'large') root.dataset.size = 'large';

  function sync() {
    document.querySelectorAll('[data-toggle="easy"]').forEach(b => b.setAttribute('aria-pressed', root.dataset.easy === 'on'));
    document.querySelectorAll('[data-toggle="size"]').forEach(b => b.setAttribute('aria-pressed', root.dataset.size === 'large'));
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-toggle], [data-share], [data-maker]');
    if (!b) return;
    if (b.dataset.toggle === 'easy') {
      root.dataset.easy = root.dataset.easy === 'on' ? '' : 'on';
      safeSet('aicard.easy', root.dataset.easy || 'off');
    } else if (b.dataset.toggle === 'size') {
      root.dataset.size = root.dataset.size === 'large' ? '' : 'large';
      safeSet('aicard.size', root.dataset.size || 'normal');
    } else if (b.dataset.toggle === 'speak') {
      speak(b);
    } else if ('share' in b.dataset) {
      share(b);
    } else if ('maker' in b.dataset) {
      document.querySelectorAll('[data-maker]').forEach(x => x.setAttribute('aria-pressed', x === b));
      filter();
    }
    sync();
  });

  function speak(btn) {
    if (!('speechSynthesis' in window)) { btn.textContent = '이 브라우저는 읽어주기를 지원하지 않아요'; return; }
    if (speechSynthesis.speaking) { speechSynthesis.cancel(); btn.setAttribute('aria-pressed', 'false'); btn.textContent = '읽어주기'; return; }
    const target = document.querySelector(btn.dataset.target || '.label');
    if (!target) return;
    const easy = root.dataset.easy === 'on';
    const nodes = target.querySelectorAll(easy ? '.lb-name, .lb-big, .lb-heads li, .qa h3, .easy-only' : '.lb-name, .lb-big, .lb-heads li, .qa h3, .qa .lead.plain-only, .claims q, .claims .cond, .block h4, .block p.plain-only, .pub b, .pub p');
    let text = Array.from(nodes).map(n => n.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean).join('. ');
    if (!text) text = target.innerText;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ko-KR'; u.rate = easy ? 0.85 : 0.95;
    u.onend = () => { btn.setAttribute('aria-pressed', 'false'); btn.textContent = '읽어주기'; };
    u.onerror = () => { btn.setAttribute('aria-pressed', 'false'); btn.textContent = '읽어주기'; };
    btn.setAttribute('aria-pressed', 'true'); btn.textContent = '읽기 멈추기';
    speechSynthesis.speak(u);
  }

  async function share(btn) {
    const url = location.href.split('#')[0];
    const title = btn.dataset.share || document.title;
    const done = (msg) => { const old = btn.textContent; btn.textContent = msg; setTimeout(() => { btn.textContent = old; }, 1800); };
    if (navigator.share) {
      try { await navigator.share({ title, url }); return; } catch (e) { if (e && e.name === 'AbortError') return; }
    }
    try { await navigator.clipboard.writeText(url); done('주소를 복사했어요'); }
    catch (e) { window.prompt('이 주소를 복사하세요', url); }
  }

  // 제품 찾기: 이름·브랜드·모델명으로 거른다
  const q = document.getElementById('q');
  const grid = document.getElementById('grid');
  function filter() {
    if (!grid) return;
    const text = (q ? q.value : '').trim().toLowerCase().replace(/\s+/g, '');
    const pressed = document.querySelector('[data-maker][aria-pressed="true"]');
    const maker = pressed ? pressed.dataset.maker : '';
    let shown = 0;
    grid.querySelectorAll('.mini').forEach(a => {
      const hay = (a.dataset.name || '').toLowerCase().replace(/\s+/g, '');
      const ok = (!text || hay.includes(text)) && (!maker || a.dataset.maker === maker);
      a.hidden = !ok; if (ok) shown++;
    });
    const count = document.getElementById('count'); if (count) count.textContent = shown + '개';
    const none = document.getElementById('noresult'); if (none) none.hidden = shown > 0;
    const nq = document.getElementById('noresult-q'); if (nq) nq.textContent = q && q.value.trim() ? '‘' + q.value.trim() + '’' : '이';
    const nl = document.getElementById('noresult-link'); if (nl) nl.href = '/check' + (q && q.value.trim() ? '?q=' + encodeURIComponent(q.value.trim()) : '');
  }
  if (q) { q.addEventListener('input', filter); }

  // 바로 확인: 캡처 끌어다 놓기·붙여넣기
  const drop = document.getElementById('drop'), fin = document.getElementById('files'), dl = document.getElementById('droplist');
  if (drop && fin) {
    const dt = new DataTransfer();
    const form = document.getElementById('checkform');
    const maxFiles = Number(form.dataset.maxFiles), maxMB = Number(form.dataset.maxMb);
    const fileError = document.getElementById('fileerror');
    const okType = (f) => /^image\//.test(f.type) || f.type === 'application/pdf' || /\.(png|jpe?g|webp|gif|bmp|pdf)$/i.test(f.name);
    function render() {
      fin.files = dt.files;
      dl.innerHTML = '';
      Array.from(dt.files).forEach((f, i) => {
        const li = document.createElement('li');
        li.innerHTML = '<span></span><button type="button" aria-label="빼기">빼기</button>';
        li.querySelector('span').textContent = f.name + ' · ' + Math.max(1, Math.round(f.size / 1024)) + 'KB';
        li.querySelector('button').addEventListener('click', () => { const n = new DataTransfer(); Array.from(dt.files).forEach((g, j) => { if (j !== i) n.items.add(g); }); dt.items.clear(); Array.from(n.files).forEach(g => dt.items.add(g)); render(); });
        dl.appendChild(li);
      });
    }
    function add(list) {
      const incoming = Array.from(list), errors = [];
      incoming.forEach(f => {
        if (!okType(f)) errors.push(f.name + ': 이미지나 PDF만 올릴 수 있어요.');
        if (f.size > maxMB * 1024 * 1024) errors.push(f.name + ': 파일당 ' + maxMB + 'MB까지 올릴 수 있어요.');
      });
      if (dt.files.length + incoming.length > maxFiles) errors.push('파일은 한 번에 ' + maxFiles + '개까지 올릴 수 있어요. 선택한 파일은 추가하지 않았어요.');
      fileError.textContent = errors.join(' '); fileError.hidden = !errors.length;
      if (errors.length) return 0;
      incoming.forEach(f => {
        if (!f.name || f.name === 'image.png') f = new File([f], '캡처-' + (dt.files.length + 1) + '.png', { type: f.type || 'image/png' });
        dt.items.add(f);
      });
      render(); return incoming.length;
    }
    document.getElementById('pick').addEventListener('click', () => { const t = document.createElement('input'); t.type = 'file'; t.multiple = true; t.accept = 'image/*,application/pdf'; t.addEventListener('change', () => add(t.files)); t.click(); });
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => { if (e.dataTransfer && e.dataTransfer.files.length) add(e.dataTransfer.files); });
    document.addEventListener('paste', (e) => {
      const items = e.clipboardData && e.clipboardData.files; if (!items || !items.length) return;
      if (e.target && e.target.tagName === 'TEXTAREA' && !Array.from(items).some(okType)) return;
      e.preventDefault(); add(items); drop.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    let waitTimer;
    form.addEventListener('submit', e => {
      if (form.getAttribute('aria-busy') === 'true') { e.preventDefault(); return; }
      const busy = document.getElementById('busy');
      document.getElementById('wait-message').textContent = dt.files.length
        ? '올린 파일에서 글자를 읽고, AI 문구와 관련 설명을 찾고 있어요.'
        : '입력한 자료에서 AI 문구와 관련 설명을 찾고 있어요. 이미지가 많으면 시간이 더 걸릴 수 있어요.';
      const started = Date.now();
      document.getElementById('wait-elapsed').textContent = '0초';
      document.getElementById('wait-slow').hidden = true;
      busy.hidden = false;
      document.getElementById('go').disabled = true;
      document.getElementById('go').textContent = '확인 중…';
      form.setAttribute('aria-busy', 'true');
      busy.focus({preventScroll: true});
      busy.scrollIntoView({block: 'center', behavior: 'auto'});
      clearInterval(waitTimer);
      waitTimer = setInterval(() => {
        const seconds = Math.floor((Date.now() - started) / 1000);
        document.getElementById('wait-elapsed').textContent = seconds < 60 ? seconds + '초' : Math.floor(seconds / 60) + '분 ' + seconds % 60 + '초';
        document.getElementById('wait-slow').hidden = seconds < 45;
      }, 1000);
    });
    window.addEventListener('pageshow', () => {
      clearInterval(waitTimer);
      document.getElementById('busy').hidden = true;
      document.getElementById('go').disabled = false;
      document.getElementById('go').textContent = '바로 확인';
      form.removeAttribute('aria-busy');
    });
    const checkError = document.getElementById('checkerror'); if (checkError) checkError.focus();
  }

  // 첫 화면의 두 주소 입력창에도 같은 대기 피드백을 제공한다.
  document.querySelectorAll('form.urlform[action="/check"]').forEach(form => {
    const box = document.createElement('section');
    box.className = 'check-wait'; box.hidden = true; box.tabIndex = -1;
    box.setAttribute('aria-label', '자료 확인 중');
    box.innerHTML = '<span class="wait-spinner" aria-hidden="true"></span><div><h2>자료를 확인하고 있어요</h2><p role="status">AI 문구와 관련 설명을 찾고 있어요. 이미지가 많으면 시간이 더 걸릴 수 있어요.</p><p class="meta">경과 시간 <strong data-elapsed>0초</strong> · 이 화면에서 기다려 주세요.</p><p class="meta" data-slow hidden>평소보다 시간이 걸리고 있어요. 외부 서비스 응답에 따라 확인이 늦어질 수 있어요.</p></div>';
    form.after(box);
    let timer;
    const buttons = Array.from(form.querySelectorAll('button'));
    form.addEventListener('submit', e => {
      if (form.getAttribute('aria-busy') === 'true') { e.preventDefault(); return; }
      form.setAttribute('aria-busy', 'true'); buttons.forEach(b => b.disabled = true);
      box.hidden = false; box.focus({preventScroll:true}); box.scrollIntoView({block:'center',behavior:'auto'});
      const start = Date.now();
      timer = setInterval(() => {
        const sec = Math.floor((Date.now()-start)/1000);
        box.querySelector('[data-elapsed]').textContent = sec < 60 ? sec+'초' : Math.floor(sec/60)+'분 '+sec%60+'초';
        box.querySelector('[data-slow]').hidden = sec < 45;
      },1000);
    });
    window.addEventListener('pageshow', () => { clearInterval(timer); box.hidden = true; form.removeAttribute('aria-busy'); buttons.forEach(b => b.disabled = false); });
  });

  // 상단 바: 스크롤하면 그림자
  const topbar = document.querySelector('.topbar');
  if (topbar) { const onS = () => topbar.classList.toggle('scrolled', window.scrollY > 8); onS(); window.addEventListener('scroll', onS, { passive: true }); }

  // 큰 숫자는 차오르듯 센다 (움직임 줄이기면 그대로)
  const motionOK = !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (false && motionOK) { // 숫자 차오르기 효과는 뺐다(캡처·인쇄 순간 0으로 찍힘, 2026-10-13)
    document.querySelectorAll('.lb-big em, .shop .pop .n em').forEach((el, idx) => {
      const target = parseInt(el.textContent, 10); if (isNaN(target) || target === 0) return;
      const start = performance.now() + (el.closest('.pop') ? 2300 : 250) + idx * 80, dur = 700;
      el.textContent = '0';
      const tick = (t) => { const p = Math.min(1, Math.max(0, (t - start) / dur)); const e = 1 - Math.pow(1 - p, 3); el.textContent = String(Math.round(target * e)); if (p < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    });
  }

  // 전체 메뉴
  const mb = document.querySelector('.menu-btn'), menu = document.getElementById('menu');
  function setMenu(open) {
    if (!mb || !menu) return;
    menu.hidden = !open; mb.setAttribute('aria-expanded', String(open)); mb.setAttribute('aria-label', open ? '메뉴 닫기' : '메뉴 열기');
    document.body.classList.toggle('menu-open', open);
    if (open) { const f = menu.querySelector('a'); if (f) f.focus(); }
  }
  if (mb) mb.addEventListener('click', () => setMenu(menu.hidden));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && menu && !menu.hidden) { setMenu(false); mb.focus(); } });

  // 스크롤 등장: 한 번만, 짧게. 움직임 줄이기 설정이면 CSS에서 바로 보인다.
  const rv = document.querySelectorAll('.rv');
  if (rv.length && motionOK && 'IntersectionObserver' in window) {
    root.classList.add('rv-on'); // 이 뒤에만 CSS가 숨긴다. 스크립트가 늦거나 없으면 처음부터 보인다.
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { rootMargin: '0px 0px -6% 0px' });
    rv.forEach((el, i) => { if (el.classList.contains('mini')) el.style.transitionDelay = (i % 3) * 70 + 'ms'; io.observe(el); });
  } else { root.classList.add('rv-all'); rv.forEach(el => el.classList.add('in')); }

  // 공개 집계는 첫 화면부터 실제 값을 유지한다.

  // 기호를 눌러 문구로 갈 때, 해당 줄을 잠깐 밝힌다
  function flash() {
    const id = location.hash.slice(1); if (!id) return;
    const el = document.getElementById(id); if (!el) return;
    el.classList.add('hit'); setTimeout(() => el.classList.remove('hit'), 1600);
  }
  window.addEventListener('hashchange', flash); flash();

  sync();
})();
