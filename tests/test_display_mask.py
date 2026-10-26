import copy
import unittest
from bs4 import BeautifulSoup
from app.display_mask import mask_public_html, PUBLIC_TEMPLATES


class DisplayMaskTests(unittest.TestCase):
    products = [dict(id=67, name="Bespoke AI 스팀", maker="삼성전자", model="VR90F01SAG", category="로봇청소기")]

    def test_only_home_example_is_masked(self):
        html = '''<html><head><title>삼성전자 검색</title></head><body><main>
        <div class="shop"><div class="bar"><span class="url">samsung.com/product</span></div><h3>Bespoke AI 스팀</h3><p>삼성전자 VR90F01SAG</p><q>Bespoke AI 스팀: 6kg 이하</q><a href="/card/67">카드 전체 보기</a></div><p class="shop-cap">판매페이지 예시</p>
        <button data-maker="삼성전자">삼성전자</button><a class="mini" href="/card/67" data-name="삼성전자 Bespoke AI 스팀 VR90F01SAG"><h3>Bespoke AI 스팀</h3><span>삼성전자 VR90F01SAG</span></a>
        </main></body></html>'''
        before = copy.deepcopy(self.products)
        soup = BeautifulSoup(mask_public_html(html, self.products), 'html.parser')
        self.assertEqual(soup.select_one('.shop h3').text, '제품 A')
        for identity in ['삼성전자', 'Bespoke', 'VR90F01SAG', 'samsung.com']:
            self.assertNotIn(identity, soup.select_one('.shop').get_text())
        self.assertEqual(soup.select_one('.mini h3').text, 'Bespoke AI 스팀')
        self.assertIn('VR90F01SAG', soup.select_one('.mini').get_text())
        self.assertEqual(soup.button.text, '삼성전자')
        self.assertEqual(soup.title.text, '삼성전자 검색')
        self.assertEqual(soup.select_one('.shop a')['href'], '/card/67')
        self.assertIn('6kg 이하', soup.q.text)
        self.assertEqual(before, self.products)
        self.assertIsNone(soup.select_one('.identity-notice'))

    def test_other_pages_are_not_in_masking_scope(self):
        self.assertEqual(PUBLIC_TEMPLATES, {'index.html'})
        html = '<main><h1>삼성전자 Bespoke AI 스팀 VR90F01SAG</h1></main>'
        self.assertEqual(mask_public_html(html, self.products), html)

    def test_original_search_index_and_scripts_are_unchanged(self):
        html = '''<main><div class="shop"><h3>Bespoke AI 스팀</h3></div><script>const original="삼성전자";</script><input value="VR90F01SAG"><a class="mini" data-name="Bespoke AI 스팀 삼성전자 VR90F01SAG" href="/card/67">Bespoke AI 스팀</a></main>'''
        soup=BeautifulSoup(mask_public_html(html,self.products),'html.parser')
        self.assertEqual(soup.script.string,'const original="삼성전자";')
        self.assertEqual(soup.input['value'],'VR90F01SAG')
        self.assertEqual(soup.select_one('.mini')['data-name'],'Bespoke AI 스팀 삼성전자 VR90F01SAG')

    def test_html_stays_escaped_and_metrics_unchanged(self):
        html='<main><div class="shop"><q>&lt;img src=x onerror=alert(1)&gt; 삼성전자</q></div><p>95.8%(46/48) · 82.8%(48/58) · 89.1%(41/46)</p></main>'
        soup=BeautifulSoup(mask_public_html(html,self.products),'html.parser')
        self.assertIsNone(soup.img)
        self.assertEqual(soup.p.text,'95.8%(46/48) · 82.8%(48/58) · 89.1%(41/46)')

if __name__ == '__main__':
    unittest.main()
