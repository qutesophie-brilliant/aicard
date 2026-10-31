/* Keep a chosen product while narrowing the next product by name. */
(function () {
  const category = document.getElementById('compare-category');
  const search = document.getElementById('compare-search');
  const selects = ['compare-a', 'compare-b'].map(id => document.getElementById(id));
  if (!category || !search || selects.some(el => !el)) return;
  const form = selects[0].form;
  const submit = form.querySelector('[type="submit"]');
  const countLabel = document.getElementById('compare-count');
  const status = document.getElementById('compare-selection-status');
  const originals = selects.map(el => Array.from(el.children).map(n => n.cloneNode(true)));
  const normalize = text => text.toLocaleLowerCase().replace(/\s+/g, '');

  function syncSelection() {
    const options = selects.map(el => el.selectedOptions[0]);
    options.forEach((option, i) => {
      document.getElementById(i ? 'selection-b' : 'selection-a').textContent =
        option && option.value ? option.textContent : '제품을 선택해 주세요.';
    });
    let message = '';
    if (!selects[0].value) message = '첫 번째 제품을 선택해 주세요.';
    else if (!selects[1].value) message = '같은 품목에서 두 번째 제품을 선택해 주세요.';
    else if (selects[0].value === selects[1].value) message = '서로 다른 제품 두 개를 선택해 주세요.';
    else if (options[0].dataset.category !== options[1].dataset.category) message = '같은 품목의 제품을 선택해 주세요.';
    submit.disabled = Boolean(message);
    status.textContent = message || '두 제품이 준비됐습니다. AI 기능·조건부터 원문까지 나란히 확인하세요.';
    selects[1].setCustomValidity(selects[1].value ? message : '');
  }

  function filter() {
    const query = normalize(search.value.trim());
    const selectedValues = selects.map(el => el.value);
    let count = 0;
    selects.forEach((el, i) => {
      const selected = selectedValues[i];
      const nodes = [];
      originals[i].forEach(original => {
        const group = original.cloneNode(true);
        if (group.tagName === 'OPTGROUP') {
          Array.from(group.children).forEach(option => {
            const sameCategory = !category.value || option.dataset.category === category.value;
            const matches = sameCategory && (!query || normalize(option.dataset.search).includes(query));
            if (matches && i === 0) count++;
            // A search narrows candidates; changing category clears incompatible choices.
            if (!sameCategory || (!matches && option.value !== selected)) option.remove();
            else option.disabled = option.value === selectedValues[1 - i] && option.value !== selected;
          });
          if (!group.children.length) return;
        }
        nodes.push(group);
      });
      el.replaceChildren(...nodes);
      el.value = Array.from(el.options).some(o => o.value === selected) ? selected : '';
    });
    countLabel.textContent = (query ? '검색 결과 ' : '이 목록의 제품 ') + count + '개 · 검색 중에도 선택한 제품은 유지됩니다.';
    syncSelection();
  }

  category.addEventListener('change', () => { search.value = ''; filter(); });
  search.addEventListener('input', filter);
  selects[0].addEventListener('change', () => {
    const option = selects[0].selectedOptions[0];
    if (option && option.value) { category.value = option.dataset.category; search.value = ''; }
    filter();
  });
  selects[1].addEventListener('change', filter);
  form.addEventListener('submit', event => { syncSelection(); if (submit.disabled) event.preventDefault(); });
  const left = selects[0].selectedOptions[0], right = selects[1].selectedOptions[0];
  if (left && left.value && (!right || !right.value || left.dataset.category === right.dataset.category)) category.value = left.dataset.category;
  filter();
})();
