import copy
import unittest
from bs4 import BeautifulSoup
from app.display_mask import mask_public_html, product_alias, PUBLIC_TEMPLATES


class DisplayMaskTests(unittest.TestCase):
    products = [dict(id=67, name="Bespoke AI 스팀", maker="삼성전자", model="VR90F01SAG", category="로봇청소기"),
                dict(id=68, name="Bespoke AI 스팀", maker="삼성전자", model="VR90F02SAG", category="로봇청소기")]

    def test_visible_identity_hidden_but_search_links_and_source_data_preserved(self):
        source = '''<html><head><title>Bespoke AI 스팀</title><meta name="description" content="삼성전자 VR90F01SAG"></head><body><main>
        <a class="mini" href="/card/67" data-name="Bespoke AI 스팀 삼성전자 VR90F01SAG" data-maker="삼성전자"><h3>Bespoke AI 스팀</h3></a>
        <blockquote>삼성전자 Bespoke AI 스팀 VR90F01SAG: 6kg 이하, 95.8%(46/48), 82.8%(48/58), 89.1%(41/46)</blockquote>
        <a href="https://www.samsung.com/VR90F01SAG" title="삼성전자 원문">원문</a>
        <input type="hidden" name="name" value="Bespoke AI 스팀"><script>const search="삼성전자";</script>
        </main></body></html>'''
        original = copy.deepcopy(self.products)
        soup = BeautifulSoup(mask_public_html(source, self.products), 'html.parser')
        self.assertNotIn('삼성', soup.main.get_text())
        self.assertNotIn('Bespoke', soup.main.get_text())
        self.assertNotIn('VR90F01SAG', soup.main.get_text())
        self.assertIn('VR90F01SAG', soup.select_one('.mini')['data-name'])
        self.assertEqual(soup.select_one('.mini')['data-maker'], '삼성전자')
        self.assertEqual(soup.select_one('a[title]')['href'], 'https://www.samsung.com/VR90F01SAG')
        self.assertEqual(soup.input['value'], 'Bespoke AI 스팀')
        self.assertEqual(soup.script.string, 'const search="삼성전자";')
        for value in ['6kg 이하', '95.8%(46/48)', '82.8%(48/58)', '89.1%(41/46)']:
            self.assertIn(value, soup.blockquote.text)
        self.assertEqual(self.products, original)

    def test_duplicate_product_names_have_distinct_stable_aliases(self):
        html = '<main>' + ''.join(f'<article class="label" id="label-{pid}"><h1 class="lb-name"><a href="/card/{pid}">Bespoke AI 스팀</a></h1><button data-share="Bespoke AI 스팀">공유</button></article>' for pid in [67, 68]) + '</main>'
        soup = BeautifulSoup(mask_public_html(html, self.products), 'html.parser')
        self.assertEqual([el.get_text() for el in soup.select('.lb-name')], ['제품 067 · 로봇청소기','제품 068 · 로봇청소기'])
        self.assertIn('제품 067', soup.button['data-share'])
        again = BeautifulSoup(mask_public_html(html, list(reversed(self.products))), 'html.parser')
        self.assertEqual(soup.get_text(), again.get_text())
        self.assertEqual(soup.select_one('.lb-name a')['href'], '/card/67')

    def test_fragment_and_accessible_text_masked_without_changing_form_submission(self):
        html = '<article class="label quick" aria-label="LG 분석"><h2 class="lb-name">새 LG 제품</h2><p class="quick-meta"><a href="https://lge.co.kr/product">lge.co.kr/product</a></p><form><input name="name" value="새 LG 제품" type="text"></form><blockquote>LG ThinQ · 6kg 이하</blockquote></article>'
        soup = BeautifulSoup(mask_public_html(html, self.products, context={'pname':'새 LG 제품'}, fragment=True), 'html.parser')
        self.assertNotIn('LG', soup.get_text())
        self.assertNotIn('LG', soup.article['aria-label'])
        self.assertEqual(soup.input['value'], '새 LG 제품')
        self.assertEqual(soup.input['type'], 'hidden')
        self.assertEqual(soup.a['href'], 'https://lge.co.kr/product')
        self.assertIn('6kg 이하', soup.blockquote.text)
        self.assertIsNotNone(soup.select_one('.identity-notice'))

    def test_compare_selection_and_release_date_are_preserved(self):
        html = '<main><select><option value="67" data-search="삼성전자 VR90F01SAG" selected>Bespoke AI 스팀 · 2025-03 출시 · VR90F01SAG</option></select></main>'
        soup = BeautifulSoup(mask_public_html(html, self.products), 'html.parser')
        self.assertEqual(soup.option.text, '제품 067 · 로봇청소기 · 2025-03 출시')
        self.assertTrue(soup.option.has_attr('selected'))
        self.assertIn('VR90F01SAG', soup.option['data-search'])
        self.assertIn('제품 067', soup.option['data-search'])

    def test_admin_templates_are_excluded_and_html_stays_escaped(self):
        self.assertNotIn('review_card.html', PUBLIC_TEMPLATES)
        html = '<main><q>&lt;img src=x onerror=alert(1)&gt; 삼성전자</q><p>ISO 24495-1 · OCR · AI · 197초 → 85초</p></main>'
        soup = BeautifulSoup(mask_public_html(html, self.products), 'html.parser')
        self.assertIsNone(soup.img)
        self.assertEqual(soup.p.text, 'ISO 24495-1 · OCR · AI · 197초 → 85초')

    def test_generic_category_is_not_redacted_as_a_product_name(self):
        products = self.products + [dict(id=100, name='인덕션', model='MOD12345', maker='삼성전자', category='인덕션')]
        soup = BeautifulSoup(mask_public_html('<main><p>인덕션 (6)</p><optgroup label="에어드레서"></optgroup></main>', products), 'html.parser')
        self.assertEqual(soup.p.text, '인덕션 (6)')
        self.assertEqual(soup.optgroup['label'], '의류관리기')

    def test_conditions_keep_different_models_distinguishable(self):
        html = '<main><p>VR90F01SAG는 지원하며 VR90F02SAG는 지원하지 않습니다. 6kg 이하입니다.</p></main>'
        soup = BeautifulSoup(mask_public_html(html, self.products), 'html.parser')
        self.assertEqual(soup.p.text, '모델 067는 지원하며 모델 068는 지원하지 않습니다. 6kg 이하입니다.')


if __name__ == '__main__':
    unittest.main()
