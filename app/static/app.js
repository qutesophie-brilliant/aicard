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
    if (!('speechSynthesis' in window)) { btn.textContent = '이 브라우저는 읽어주기를 지원하지 않습니다'; return; }
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
    try { await navigator.clipboard.writeText(url); done('주소를 복사했습니다'); }
    catch (e) { window.prompt('이 주소를 복사하세요', url); }
  }

  // 제품 찾기: 이름·브랜드·모델명으로 거른다
  const q = document.getElementById('q');
  const grid = document.getElementById('grid');
  const catalog = document.querySelector('#find[data-identities]');
  const reveal = document.getElementById('catalog-reveal');
  const brands = document.getElementById('catalog-brands');
  let namesRequested = false;
  function syncIdentities() {
    if (!catalog) return;
    const searching = Boolean(q && q.value.trim());
    const visible = namesRequested || searching;
    catalog.dataset.identities = visible ? 'visible' : 'masked';
    catalog.querySelectorAll('[data-identity-text]').forEach(el => {
      el.textContent = visible ? el.dataset.identityText : el.dataset.maskedText;
    });
    if (brands) brands.hidden = !visible;
    if (reveal) {
      reveal.setAttribute('aria-pressed', String(visible));
      reveal.textContent = visible ? '제품명 다시 가리기' : '실제 제품명 보기';
    }
  }
  if (reveal) reveal.addEventListener('click', () => {
    namesRequested = catalog.dataset.identities !== 'visible';
    if (!namesRequested) {
      if (q) q.value = '';
      if (brands) brands.querySelectorAll('[data-maker]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.maker === '')));
    }
    filter();
  });
  function filter() {
    if (!grid) return;
    syncIdentities();
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
  if (catalog) { filter(); window.addEventListener('pageshow', filter); }

  // 바로 확인: 캡처 끌어다 놓기·붙여넣기
  const drop = document.getElementById('drop'), fin = document.getElementById('files'), dl = document.getElementById('droplist');
  if (drop && fin) {
    const dt = new DataTransfer();
    const form = document.getElementById('checkform');
    const maxFiles = Number(form.dataset.maxFiles), maxMB = Number(form.dataset.maxMb);
    const fileError = document.getElementById('fileerror');
    const fileStatus = document.getElementById('file-status');
    const go = document.getElementById('go');
    const urlField = form.querySelector('[name="url"]'), textField = form.querySelector('[name="text"]');
    // Validate at submit time so an unused, incomplete URL cannot block files/text.
    form.noValidate = true;
    let omittedInvalidUrl = false;
    const okType = (f) => /^image\//.test(f.type) || f.type === 'application/pdf' || /\.(png|jpe?g|webp|gif|bmp|pdf)$/i.test(f.name);
    function syncSubmit() {
      if (form.getAttribute('aria-busy') === 'true') return;
      go.textContent = dt.files.length ? '파일 ' + dt.files.length + '개 분석 시작'
        : textField && textField.value.trim() ? '설명 글 분석 시작'
        : urlField && urlField.value.trim() ? '주소 분석 시작' : '분석 시작';
    }
    function render() {
      fin.files = dt.files;
      dl.innerHTML = '';
      Array.from(dt.files).forEach((f, i) => {
        const li = document.createElement('li');
        li.innerHTML = '<span></span><button type="button" aria-label="빼기">빼기</button>';
        li.querySelector('span').textContent = f.name + ' · ' + Math.max(1, Math.round(f.size / 1024)) + 'KB';
        li.querySelector('span').title = f.name;
        li.querySelector('button').setAttribute('aria-label', f.name + ' 선택 취소');
        li.querySelector('button').addEventListener('click', () => { const n = new DataTransfer(); Array.from(dt.files).forEach((g, j) => { if (j !== i) n.items.add(g); }); dt.items.clear(); Array.from(n.files).forEach(g => dt.items.add(g)); render(); });
        dl.appendChild(li);
      });
      fileStatus.textContent = dt.files.length ? '파일 ' + dt.files.length + '개 선택됨 · 아래 버튼을 누르면 분석을 시작합니다.' : '선택한 파일이 없습니다.';
      drop.classList.toggle('has-files', dt.files.length > 0);
      syncSubmit();
    }
    function add(list) {
      const incoming = Array.from(list), errors = [];
      incoming.forEach(f => {
        if (!okType(f)) errors.push(f.name + ': 이미지나 PDF만 올릴 수 있습니다.');
        if (f.size > maxMB * 1024 * 1024) errors.push(f.name + ': 파일당 ' + maxMB + 'MB까지 올릴 수 있습니다.');
      });
      if (dt.files.length + incoming.length > maxFiles) errors.push('파일은 한 번에 ' + maxFiles + '개까지 올릴 수 있습니다. 선택한 파일은 추가하지 않았습니다.');
      fileError.textContent = errors.join(' '); fileError.hidden = !errors.length;
      if (errors.length) return 0;
      incoming.forEach(f => {
        if (!f.name || f.name === 'image.png') f = new File([f], '캡처-' + (dt.files.length + 1) + '.png', { type: f.type || 'image/png' });
        dt.items.add(f);
      });
      render(); return incoming.length;
    }
    document.getElementById('pick').addEventListener('click', () => fin.click());
    [urlField, textField].filter(Boolean).forEach(field => field.addEventListener('input', syncSubmit));
    fin.addEventListener('change', () => {
      const selected = Array.from(fin.files);
      render(); // Restore accepted files before validating the new selection.
      add(selected);
    });
    drop.addEventListener('keydown', e => {
      if (e.target === drop && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); fin.click(); }
    });
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
      const urlInput = form.querySelector('[name="url"]'), textInput = form.querySelector('[name="text"]');
      const hasAlternativeInput = dt.files.length || (textInput && textInput.value.trim());
      if (urlInput && hasAlternativeInput && !urlInput.validity.valid && !urlInput.disabled) {
        urlInput.disabled = true; // Omit only this invalid source URL from FormData.
        omittedInvalidUrl = true;
      }
      if (!form.reportValidity()) {
        e.preventDefault();
        if (omittedInvalidUrl) { urlInput.disabled = false; omittedInvalidUrl = false; }
        return;
      }
      if (!dt.files.length && !(urlInput && urlInput.value.trim()) && !(textInput && textInput.value.trim())) {
        e.preventDefault();
        fileError.textContent = '판매페이지 주소를 입력하거나, 캡처 이미지·PDF를 골라 주세요.';
        fileError.hidden = false;
        fileError.tabIndex = -1;
        fileError.focus();
        return;
      }
      const busy = document.getElementById('busy');
      document.getElementById('wait-message').textContent = dt.files.length
        ? '파일 ' + dt.files.length + '개에서 글자를 읽고, AI 문구와 관련 설명을 찾고 있습니다. 긴 이미지는 자동으로 나누어 읽습니다.'
        : '입력한 자료에서 AI 문구와 관련 설명을 찾고 있습니다. 이미지가 많으면 시간이 더 걸릴 수 있습니다.';
      const started = Date.now();
      document.getElementById('wait-elapsed').textContent = '0초';
      document.getElementById('wait-slow').hidden = true;
      busy.hidden = false;
      form.querySelectorAll('[data-check-submit]').forEach(b => b.disabled = true);
      go.textContent = '분석 중…';
      form.setAttribute('aria-busy', 'true');
      busy.focus({preventScroll: true});
      busy.scrollIntoView({block: 'center', behavior: 'auto'});
      clearInterval(waitTimer);
      waitTimer = setInterval(() => {
        const seconds = Math.floor((Date.now() - started) / 1000);
        document.getElementById('wait-elapsed').textContent = seconds < 60 ? seconds + '초' : Math.floor(seconds / 60) + '분 ' + seconds % 60 + '초';
        document.getElementById('wait-slow').hidden = seconds < 45;
      }, 1000);
      submitCheckInPlace(e, form, message => {
        clearInterval(waitTimer);
        busy.hidden = true;
        form.removeAttribute('aria-busy');
        if (omittedInvalidUrl) { urlField.disabled = false; omittedInvalidUrl = false; }
        form.querySelectorAll('[data-check-submit]').forEach(b => b.disabled = false);
        render();
        fileError.textContent = message;
        fileError.hidden = false; fileError.tabIndex = -1; fileError.focus();
      });
    });
    window.addEventListener('pageshow', () => {
      if (form.dataset.asyncPending) return;
      clearInterval(waitTimer);
      document.getElementById('busy').hidden = true;
      form.querySelectorAll('[data-check-submit]').forEach(b => b.disabled = false);
      form.removeAttribute('aria-busy');
      if (omittedInvalidUrl) { urlField.disabled = false; omittedInvalidUrl = false; }
      render();
    });
    render();
    const checkError = document.getElementById('checkerror'); if (checkError) checkError.focus();
  }

  // Keep this document active while the server works. A native POST navigation
  // can suspend mobile timers before the response arrives.
  function submitCheckInPlace(event, form, onFailure) {
    if (typeof window.fetch !== 'function') return;
    event.preventDefault();
    form.dataset.asyncPending = 'true';
    const payload = new FormData(form);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 600000);
    window.fetch(form.action || '/check', {
      method: 'POST', body: payload, credentials: 'same-origin', signal: controller.signal
    }).then(async response => {
      if (!response.ok) {
        throw new Error(response.status === 429
          ? '현재 요청이 많습니다. 잠시 뒤 다시 시도해 주세요.'
          : '분석 서버에서 응답을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.');
      }
      if (!(response.headers.get('content-type') || '').includes('text/html')) {
        throw new Error('분석 결과를 표시하지 못했습니다. 잠시 뒤 다시 시도해 주세요.');
      }
      const html = await response.text();
      const target = new URL(response.url || form.action || '/check', location.href);
      if (target.origin !== new URL(location.href).origin) throw new Error('분석 결과 주소를 확인하지 못했습니다.');
      clearTimeout(timeout);
      window.history.pushState(null, '', target.href);
      document.open(); document.write(html); document.close();
    }).catch(error => {
      clearTimeout(timeout);
      delete form.dataset.asyncPending;
      onFailure(error.name === 'AbortError'
        ? '분석 응답 시간이 길어 연결을 종료했습니다. 잠시 뒤 다시 확인해 주세요.'
        : error instanceof TypeError
          ? '서버 연결이 끊겼습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.'
          : error.message);
    });
  }

  // 첫 화면의 두 주소 입력창에도 같은 대기 피드백을 제공한다.
  document.querySelectorAll('form.urlform[action="/check"]').forEach(form => {
    const box = document.createElement('section');
    box.className = 'check-wait'; box.hidden = true; box.tabIndex = -1;
    box.setAttribute('aria-label', '자료 확인 중');
    box.innerHTML = '<span class="wait-spinner" aria-hidden="true"></span><div><h2>자료를 확인하고 있습니다</h2><p role="status">AI 문구와 관련 설명을 찾고 있습니다.</p><p class="meta">분석에는 약 1분이 걸릴 수 있습니다. 페이지에 따라 더 오래 걸릴 수 있습니다.</p><p class="meta">경과 시간 <strong data-elapsed>0초</strong> · 이 화면에서 기다려 주세요.</p><p class="meta" data-slow hidden>평소보다 시간이 걸리고 있습니다. 외부 서비스 응답에 따라 확인이 늦어질 수 있습니다.</p><figure class="wait-example"><figcaption><strong>정식 카드 예시</strong><br>사람이 원문을 확인한 공개 카드의 기존 화면입니다.<br>현재 입력한 링크의 분석 결과가 아닙니다.</figcaption><img src="/static/check-card-example.png" alt="공개 카드 예시: 제품명, AI 문구별 확인 상태와 주의사항" width="1265" height="712" loading="lazy"><p class="meta">바로 확인에서는 관련 문장과 조건을 보여 주며, 위와 같은 정식 판정은 하지 않습니다.</p><a href="/card/67" target="_blank" rel="noopener">예시 카드 새 창에서 보기 ↗</a></figure></div>';
    form.after(box);
    let timer;
    const buttons = Array.from(form.querySelectorAll('button'));
    form.addEventListener('submit', e => {
      if (form.getAttribute('aria-busy') === 'true') { e.preventDefault(); return; }
      form.setAttribute('aria-busy', 'true'); buttons.forEach(b => b.disabled = true);
      box.hidden = false; box.focus({preventScroll:true}); box.scrollIntoView({block:'center',behavior:'auto'});
      box.querySelector('[role="status"]').textContent = 'AI 문구와 관련 설명을 찾고 있습니다.';
      box.querySelector('[data-elapsed]').textContent = '0초';
      box.querySelector('[data-slow]').hidden = true;
      const start = Date.now();
      timer = setInterval(() => {
        const sec = Math.floor((Date.now()-start)/1000);
        box.querySelector('[data-elapsed]').textContent = sec < 60 ? sec+'초' : Math.floor(sec/60)+'분 '+sec%60+'초';
        box.querySelector('[data-slow]').hidden = sec < 45;
      },1000);
      submitCheckInPlace(e, form, message => {
        clearInterval(timer);
        form.removeAttribute('aria-busy'); buttons.forEach(b => b.disabled = false);
        box.querySelector('[role="status"]').textContent = message;
        box.querySelector('[data-slow]').hidden = true;
        box.focus();
      });
    });
    window.addEventListener('pageshow', () => { if (form.dataset.asyncPending) return; clearInterval(timer); box.hidden = true; form.removeAttribute('aria-busy'); buttons.forEach(b => b.disabled = false); });
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
