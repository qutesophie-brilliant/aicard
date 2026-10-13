"""Meaning-preserving easy view: do not publish unchecked LLM compression."""
from typing import Any
from ..schema import COVERAGE_ITEMS, public_status


def make_easy(card: dict[str, Any]) -> dict[str, Any]:
    """Share factual summaries until separately simplified copy is reviewed.

    The former generator omitted storage locations, download exceptions and
    the specific feature to which a condition applied. Shortness is not QA.
    """
    claims = card.get('claims', [])
    n_ok = sum(public_status(c) in ('근거 확인', '조건 확인') for c in claims)
    headline = f'AI 관련 문구 {len(claims)}개 중 {n_ok}개에 작동 설명이 연결돼 있어요.'
    items = {
        'functions': '제조사가 설명한 기능과 사용 조건을 문구별로 모았어요. 아래 인용은 제조사 자료에 적힌 내용이에요.',
        'evidence': headline,
    }
    for key in COVERAGE_ITEMS:
        item = card.get('items', {}).get(key, {})
        text = item.get('summary') or ''
        if item.get('status') == '자료 부족' and (not text or text in (
            '공개자료에서 확인되지 않았습니다.', '회사가 올린 자료에는 이 내용이 없어요.'
        )):
            text = '이 카드에 사용한 자료에서는 관련 안내를 찾지 못했어요.'
        items[key] = text
    return {'headline': headline, 'items': items, 'method': 'shared-factual-summary-v2'}


_rules_easy = make_easy
