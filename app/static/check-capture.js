(function () {
  const B = __BASE__, R = __RECEIVE__, selector = __SELECTOR__;
  const roots = Array.from(document.querySelectorAll(selector));
  const selected = roots.filter(n => !roots.some(p => p !== n && p.contains(n)));
  if (!selected.length) {
    alert('상품 설명 영역을 확인하지 못했습니다. 상세 설명을 펼친 뒤 다시 눌러 주세요. 계속 안 되면 설명 글이나 캡처를 AI 정보카드에 넣어 주세요.');
    return;
  }
  const out = document.implementation.createHTMLDocument(document.title);
  const noise = /review|rating|comment|recommend|related|cart|cookie|login|account|checkout|payment|order|customer/i;
  const forbidden = 'script,style,noscript,iframe,object,embed,form,input,textarea,select,button,nav,header,footer,aside,svg,[contenteditable]';
  selected.forEach(root => {
    const copy = root.cloneNode(true);
    copy.querySelectorAll(forbidden).forEach(n => n.remove());
    copy.querySelectorAll('[class],[id]').forEach(n => {
      if (noise.test((n.id || '') + ' ' + (n.getAttribute('class') || ''))) n.remove();
    });
    [copy, ...copy.querySelectorAll('*')].forEach(n => {
      let src = '';
      if (n.tagName === 'IMG') {
        src = n.getAttribute('ec-data-src') || n.getAttribute('data-src') || n.getAttribute('data-original') || n.getAttribute('data-lazy-src') || n.getAttribute('data-echo') || n.getAttribute('src') || '';
        try { const u = new URL(src, location.href); src = /^https?:$/.test(u.protocol) && !u.username && !u.password ? u.href : ''; } catch (_) { src = ''; }
      }
      Array.from(n.attributes).forEach(a => { if (!['class', 'id', 'itemprop'].includes(a.name)) n.removeAttribute(a.name); });
      if (n.tagName === 'IMG') { if (src) n.setAttribute('src', src); else n.remove(); }
    });
    out.body.append(copy);
  });
  const html = out.documentElement.outerHTML;
  if (html.length > 500000) { alert('상품 설명이 너무 큽니다. 필요한 설명 구간을 캡처해 AI 정보카드에 넣어 주세요.'); return; }
  const D = {type: 'aicard-capture', url: location.href, title: document.title, product_html: html};
  const w = window.open(B + R, '_blank');
  if (!w) { alert('결과 창이 열리지 않았습니다. 팝업 허용 후 다시 눌러 주세요.'); return; }
  const h = e => {
    if (e.source === w && e.origin === B && e.data === 'aicard-ready') {
      w.postMessage(D, B); window.removeEventListener('message', h); clearTimeout(timer);
    }
  };
  window.addEventListener('message', h);
  const timer = setTimeout(() => window.removeEventListener('message', h), 30000);
})();
