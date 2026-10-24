"""Conservative display checks without rewriting approved or research records."""
from copy import deepcopy
from .pipeline.coverage import _guard_remedy_scope


def safe_public_card(card):
    item = card.get('items', {}).get('remedy')
    if not item:
        return card
    guarded = _guard_remedy_scope(item, item.get('evidence', []))
    if guarded == item:
        return card
    result = deepcopy(card)
    result['items']['remedy'] = guarded
    result.setdefault('easy', {}).setdefault('items', {})['remedy'] = (
        '환불 안내가 어떤 계약에 적용되는지 다시 확인해야 합니다. 원문을 확인해 주십시오.')
    result['display_scope_notice'] = (
        '환불·해지 요약의 적용 대상을 추가 점검 중입니다. '
        '해당 항목은 ‘추가 확인 필요’로 표시하며, 원문 인용은 그대로 제공합니다.')
    return result
