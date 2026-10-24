"""LLM 공급자 추상화. Anthropic / OpenAI / rules(규칙 기반 데모) 를 같은 인터페이스로 쓴다.

모든 호출은 JSON 응답을 요구하고, 파싱 실패 시 한 번 재시도한다.
"""
import contextvars
from contextlib import contextmanager
import base64
import json
import os
import re
import time
from typing import Any

from .. import config

SYSTEM_PROMPT = """당신은 소비자 정보카드 작성을 돕는 분석 보조자입니다.
원칙:
1. 주어진 문서에 있는 내용만 근거로 삼고, 문서에 없는 내용은 추측하지 않습니다.
2. 제품의 우열, 진짜/가짜 AI, 법 위반 여부를 판단하지 않습니다. 표시 문구와 공개 근거의 관계만 기술합니다.
3. 판단이 어려우면 '자료 부족' 또는 '추가 확인 필요'로 남깁니다.
4. 인용(quote)은 반드시 원문 문장을 그대로 복사합니다. 요약·의역하지 않습니다.
5. 출력은 요구된 JSON 형식만 반환합니다. 설명 문장을 덧붙이지 않습니다."""


class LLMError(RuntimeError):
    pass


# A separate budget for quick URL-image OCR. Existing formal-card calls keep
# their prior retry/timeout behavior outside this context.
_ocr_budget = contextvars.ContextVar('aicard_ocr_budget', default=None)


def _request_timeout(default):
    budget = _ocr_budget.get()
    if budget is None:
        return default
    remaining = budget['deadline'] - time.monotonic()
    if budget['requests'] <= 0 or remaining <= 0:
        raise LLMError('OCR request budget exhausted')
    budget['requests'] -= 1
    return min(default, remaining)


_force_rules: contextvars.ContextVar[bool] = contextvars.ContextVar("aicard_force_rules", default=False)


@contextmanager
def rules_only():
    """이 블록 안에서는 LLM을 부르지 않고 규칙 판정만 쓴다(즉석 확인용, 크레딧 0)."""
    token = _force_rules.set(True)
    try:
        yield
    finally:
        _force_rules.reset(token)


def provider() -> str:
    if _force_rules.get():
        return "rules"
    return config.LLM_PROVIDER


def available() -> bool:
    return provider() in ("gemini", "anthropic", "openai")


def _extract_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def _anthropic_call(prompt: str, images: list[bytes] | None, max_tokens: int) -> str:
    import anthropic
    options = {'timeout': _request_timeout(180), 'max_retries': 0} if _ocr_budget.get() is not None else {}
    client = anthropic.Anthropic(**options)
    content: list[dict[str, Any]] = []
    for img in images or []:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": _mime(img),
            "data": base64.b64encode(img).decode()}})
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _openai_call(prompt: str, images: list[bytes] | None, max_tokens: int) -> str:
    from openai import OpenAI
    options = {'timeout': _request_timeout(180), 'max_retries': 0} if _ocr_budget.get() is not None else {}
    client = OpenAI(**options)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images or []:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{_mime(img)};base64,{base64.b64encode(img).decode()}"}})
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL, max_tokens=max_tokens,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": content}])
    return resp.choices[0].message.content or ""


_gemini_ok: tuple[str, str] | None = None   # (주소, 모델) — 한 번 성공한 조합을 기억
_gemini_dead: set[tuple[str, str]] = set()   # 영구 실패(404 단종, 403 API 미사용) 조합은 다시 시도하지 않는다


def _gemini_call(prompt: str, images: list[bytes] | None, max_tokens: int) -> str:
    """Gemini REST 호출. SDK 없이 httpx로 직접 부른다.
    주소 × 모델 조합을 차례로 시도하고, 혼잡(429·503)이면 잠깐 기다렸다 한 번 더 시도한다."""
    import time
    import httpx
    global _gemini_ok
    parts: list[dict[str, Any]] = []
    for img in images or []:
        parts.append({"inline_data": {"mime_type": _mime(img), "data": base64.b64encode(img).decode()}})
    parts.append({"text": prompt})
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": min(max(max_tokens * 2, 8192), 32768), "temperature": 0.0,  # 생각 토큰도 이 한도에 포함되므로 넉넉히
                             "responseMimeType": "application/json"},
    }
    combos = [(ep, m) for ep in config.GEMINI_ENDPOINTS for m in config.GEMINI_MODELS
              if (ep, m) not in _gemini_dead]
    if _gemini_ok:
        combos = [_gemini_ok] + [c for c in combos if c != _gemini_ok]
    errors: list[str] = []
    for ep, model in combos:
        url = ep.format(model=model)
        for attempt in range(3):
            try:
                r = httpx.post(url, json=body, timeout=_request_timeout(180),
                               headers={"x-goog-api-key": config.GEMINI_API_KEY,
                                        "Content-Type": "application/json"})
            except httpx.HTTPError as e:
                if _ocr_budget.get() is not None:
                    raise
                errors.append(f"{model}: 연결 실패 {e}")
                break
            if r.status_code == 200:
                _gemini_ok = (ep, model)
                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    raise LLMError(f"Gemini 응답이 비었습니다: {str(data)[:300]}")
                return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = r.text
            errors.append(f"{model} @ {url.split('/')[2]}: HTTP {r.status_code} {msg[:160]}")
            if _ocr_budget.get() is not None:
                raise LLMError(f'OCR provider HTTP {r.status_code}')
            if r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text.lower()):
                _gemini_dead.add((ep, model))   # 하루 한도 소진: 오늘은 이 모델을 건너뛴다
                break
            if r.status_code in (429, 500, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))   # 5초, 10초 기다렸다 다시
                continue
            if r.status_code in (403, 404):
                _gemini_dead.add((ep, model))
            break
    raise LLMError("Gemini 호출 실패 — " + " | ".join(errors[-6:]))


def _mime(img: bytes) -> str:
    if img[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if img[:3] == b"GIF":
        return "image/gif"
    if img[:4] == b"RIFF" and img[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def ask_json(prompt: str, images: list[bytes] | None = None, max_tokens: int = 4000) -> Any:
    """JSON 응답을 요구하는 단일 호출."""
    p = provider()
    if p == "gemini":
        call = _gemini_call
    elif p == "anthropic":
        call = _anthropic_call
    elif p == "openai":
        call = _openai_call
    else:
        raise LLMError("LLM 키가 없습니다. GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY 중 하나를 설정하거나 "
                       "LLM_PROVIDER=rules 데모 모드를 사용하세요.")
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = call(prompt, images, max_tokens)
            return _extract_json(raw)
        except json.JSONDecodeError as e:
            last_err = e
            prompt = prompt + "\n\n(이전 응답이 올바른 JSON이 아니었습니다. JSON만 다시 출력하세요.)"
        except Exception as e:  # SDK 오류는 그대로 올린다
            if _ocr_budget.get() is not None:
                raise
            raise LLMError(str(e)) from e
    raise LLMError(f"JSON 파싱 실패: {last_err}")


def ocr_images(imgs: list[bytes]) -> list[str]:
    """여러 이미지를 한 번의 호출로 읽는다(크레딧 절약). 이미지 순서대로 글자 목록을 돌려준다."""
    if not available() or not imgs:
        return []
    data = ask_json(
        f"이미지 {len(imgs)}장이 순서대로 주어집니다. 제품 상세페이지를 잘라 낸 조각들입니다. "
        "각 이미지에 적힌 글자를 위에서 아래로 빠짐없이 그대로 옮겨 적으세요. 각주(*, ※)와 작은 글씨도 포함합니다. "
        "표는 줄 단위로 풀어 쓰고, 읽을 수 없는 부분은 생략합니다. 해석·요약하지 않습니다. "
        '형식: {"pages": ["1번 이미지 글자", "2번 이미지 글자", ...]}',
        images=imgs, max_tokens=8000)
    pages = data.get("pages", []) if isinstance(data, dict) else []
    return [str(p) for p in pages]


def ocr_image(img: bytes) -> str:
    """상세페이지 이미지 → 텍스트. 비전 LLM을 쓴다."""
    if not available():
        return ""
    data = ask_json(
        "이 이미지에 적힌 모든 글자를 읽어 순서대로 옮겨 적으세요. 표는 줄 단위로 풀어 쓰세요. "
        "읽을 수 없는 부분은 생략합니다. 형식: {\"text\": \"...\"}",
        images=[img], max_tokens=3000)
    return str(data.get("text", "")) if isinstance(data, dict) else ""


def ocr_images_indexed(imgs: list[bytes], timeout_seconds: float = 20) -> list[str | None]:
    """Quick URL OCR only: validate image identity, never shift omitted pages."""
    if not imgs:
        return []
    token = _ocr_budget.set({'deadline': time.monotonic() + timeout_seconds, 'requests': 1})
    try:
        data = ask_json(
            f'이미지 {len(imgs)}장이 순서대로 주어집니다. 각 이미지의 글자를 그대로 옮기세요. '
            '해석하거나 요약하지 마세요. 각주와 작은 글씨도 포함하세요. '
            'image는 입력 순서의 1부터 시작하는 번호입니다. 모든 번호를 한 번씩 반환하세요. '
            '글자를 확인하지 못한 이미지도 생략하지 말고 text를 빈 문자열로 반환하세요. '
            '형식: {"pages":[{"image":1,"text":"첫 이미지 글자"},{"image":2,"text":""}]}',
            images=imgs, max_tokens=8000)
    finally:
        _ocr_budget.reset(token)
    texts = [None] * len(imgs)
    seen = set()
    pages = data.get('pages', []) if isinstance(data, dict) else []
    if not isinstance(pages, list):
        return texts
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = page.get('image')
        if type(number) is not int or not 1 <= number <= len(imgs):
            continue
        index = number - 1
        if index in seen:
            texts[index] = None
            continue
        seen.add(index)
        text = page.get('text')
        if isinstance(text, str):
            texts[index] = text.strip()
    return texts
