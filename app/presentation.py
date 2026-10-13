"""Public copy corrections; original quotes and reviewer history stay unchanged."""
from copy import deepcopy
from .schema import public_status


def display_card(raw):
    if not raw:
        return raw
    card = deepcopy(raw)
    for claim in card.get('claims', []):
        claim['display_status'] = public_status(claim)
    from .pipeline.easy import make_easy
    if card.get('product', {}).get('model') == 'VR90F01SAG' and card.get('checked_at') == '2026-10-08':
        condition_copy = {
            'c5': '실험실 기능으로 제공되는 오염 구역 집중 청소는 인식 범위나 정확도 등 세부 기능이 달라질 수 있어요.',
            'c12': '사람이 없는지 감지하려면 제품과 공유기가 벽으로 나뉘지 않은 같은 방에 있어야 해요. 두 기기 사이의 거리는 5m 이내여야 해요.',
            'c13': '이 기능을 쓰려면 SmartThings 앱을 설치해 제품과 연결하고, 무선 네트워크에도 연결해야 해요. 청소 예약에서 미리 설정해 두어야 해요.',
        }
        for claim in card.get('claims', []):
            # Preserve original condition text for exports and reviewer history.
            if claim.get('id') in condition_copy and claim.get('conditions') and not claim.get('reviewer_status'):
                claim['display_conditions'] = condition_copy[claim['id']]
        items = card.get('items', {})
        updates = {
            'conditions': ('음성 인식', '물체 인식은 물체의 모양이나 주변 환경에 영향을 받을 수 있어요. 음성 인식은 주변 소음이 크거나 목소리가 작을 때, 제품이 Wi-Fi에 연결되지 않았을 때 작동하지 않을 수 있어요. 제품 신호 범위 안에 장애물이 있으면 일부 기능의 반응에도 영향을 줄 수 있으므로, 아래 원문의 적용 범위를 함께 확인해 주세요.'),
            'data': ('스마트폰에 다운로드', '제조사는 카메라를 사물 인식과 영상 모니터링에 사용한다고 설명해요. 실시간으로 보는 영상은 클라우드에 저장하지 않아요. 녹화한 영상은 클라우드에 24시간 보관하며, 스마트폰에 내려받을 수 있어요. 클라우드는 인터넷으로 연결된 서버의 저장 공간이에요. 이 설명만으로는 처리 국가나 AI 학습 이용 여부까지 알 수 없어요.'),
            'safety': ('Knox', '제조사는 Knox 보안 기술로 기기와 중요 데이터를 보호한다고 설명해요. 앱을 업데이트하면 지원하는 운영체제나 화면 구성이 달라질 수 있어요. 아래 인용만으로는 보안 업데이트 지원 기간이나 오작동 시 보호장치를 확인할 수 없어요.'),
        }
        for key, (anchor, text) in updates.items():
            item = items.get(key, {})
            if not item.get('reviewed') and anchor in ' '.join(e.get('quote', '') for e in item.get('evidence', [])):
                item['summary'] = text
                item['copy_revision'] = '2026-10-13'
        remedy = items.get('remedy', {})
        if remedy.get('status') == '자료 부족' and not remedy.get('evidence') and not remedy.get('reviewed'):
            remedy.update(
                status='조건 확인',
                summary='2026년 10월 13일 확인한 제조사 판매페이지에는 제품 보증 기간이 1년이라고 적혀 있어요. 디지털 인버터 모터의 10년 보증은 해당 모터의 무상 수리에 한정되며, 제품 교환이나 구입가 환급에는 적용되지 않아요. 자세한 조건은 제품 보증서를 확인해 주세요. 이 보증 안내만으로 일반적인 교환·환불 조건이나 문의처까지 확인된 것은 아니에요.',
                evidence=[{
                    'doc_id': 'copy-review-20261013-warranty', 'kind': '제조사 판매페이지 · 2026-10-13 추가 확인',
                    'quote': '10년 보증은 10년 이내 무상 수리를 의미하며, 제품 교환 및 구입가 환급에는 적용되지 않습니다.\n10년 보증은 디지털 인버터 모터 한정입니다.\n제품 품질 보증 기간은 1년이며,',
                    'url': 'https://www.samsung.com/sec/vacuum-cleaners/jetbot-vr90f01sag-d2c/VR90F01SAG/'
                }], copy_revision='2026-10-13')
        kc = card.get('public_db', {}).get('kc', {})
        if 'VR90F01SAG·VR90F01' in kc.get('detail', '') and '2026-10-12 조회' in kc.get('detail', ''):
            kc['detail'] = '모델명 VR90F01SAG·VR90F01로 검색했으나 결과를 찾지 못했다는 기록이 있어요. 인증번호를 확보하지 못해 인증번호로는 대조하지 못했어요. 이 결과만으로 미인증 제품이라고 판단할 수 없어요. 2026-10-12 조회 기록'
    card['easy'] = make_easy(card)
    return card
