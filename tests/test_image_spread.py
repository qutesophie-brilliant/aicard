import unittest
from app.pipeline.product_images import spread_candidates


class ImageSpreadTests(unittest.TestCase):
    def test_early_downloads_cover_middle_and_end_of_long_description(self):
        rows=[(0,str(i)) for i in range(71)]
        result=spread_candidates(rows,24)
        self.assertEqual(set(rows),set(result))
        self.assertEqual(len(rows),len(result))
        self.assertEqual([(0,'0'),(0,'70'),(0,'35')],result[:3])
        self.assertTrue(any(int(url)>60 for _,url in result[:8]))
        self.assertTrue(any(15<int(url)<25 for _,url in result[:8]))

    def test_short_description_order_is_unchanged(self):
        rows=[(0,str(i)) for i in range(8)]
        self.assertEqual(rows,spread_candidates(rows,24))

    def test_explicit_ai_labels_keep_priority(self):
        rows=[(2,'ai-detail')]+[(0,str(i)) for i in range(40)]
        self.assertEqual((2,'ai-detail'),spread_candidates(rows,24)[0])


if __name__=='__main__': unittest.main()
