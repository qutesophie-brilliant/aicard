import unittest
from app.pipeline.product_page import product_body, product_text


class ProductSpecificationScopeTests(unittest.TestCase):
    HTML = '''<nav>AI 다른 제품 찾기</nav><div class="product-info">
    <div class="overview-content"><p>회전형 보안 카메라로 움직임을 감지하고 알림을 제공합니다. 앱에서 녹화 내용을 확인합니다.</p></div>
    <div id="div_specifications"><table><tbody><tr><th>AI 감지</th>
    <td>동작 감지<br>사람 감지<br>아기 울음소리 감지</td></tr>
    <tr><th>보안</th><td>온디바이스 머신러닝</td></tr></tbody></table>
    <div class="recommendations">다른 제품 AI 기능</div></div></div>
    <footer>공통 서비스 정보</footer>'''

    def test_product_specifications_are_included_with_overview(self):
        text = product_text(product_body(self.HTML, 'https://www.tp-link.com/kr/home-networking/cloud-camera/new-product/'))
        for phrase in ('회전형 보안 카메라', 'AI 감지', '동작 감지', '사람 감지', '아기 울음소리 감지', '온디바이스 머신러닝'):
            self.assertIn(phrase, text)
        for phrase in ('다른 제품', '공통 서비스'):
            self.assertNotIn(phrase, text)

    def test_other_hosts_do_not_gain_vendor_specific_section(self):
        text = product_text(product_body(self.HTML, 'https://example.org/product/1'))
        self.assertNotIn('AI 감지', text)
        self.assertIn('회전형 보안 카메라', text)

    def test_specifications_outside_product_info_are_excluded(self):
        html = self.HTML.replace('<div id="div_specifications">', '</div><div id="div_specifications">')
        text = product_text(product_body(html, 'https://www.tp-link.com/kr/home-networking/cloud-camera/new-product/'))
        self.assertNotIn('AI 감지', text)
