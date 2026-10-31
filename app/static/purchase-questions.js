/* 문의 문안은 이용자가 직접 복사합니다. 서버 전송이나 저장은 하지 않습니다. */
(() => {
  const panel = document.getElementById('purchase-questions');
  if (!panel) return;
  const text = panel.querySelector('textarea');
  const button = panel.querySelector('[data-copy-questions]');
  const status = panel.querySelector('[role="status"]');
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text.value);
      status.textContent = '질문을 복사했습니다. 확인이 필요한 항목만 골라 사용하세요.';
    } catch (_) {
      text.focus();
      text.select();
      status.textContent = '질문을 선택했습니다. 기기의 복사 기능으로 복사해 주세요.';
    }
  });
})();
