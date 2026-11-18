import json
import tempfile
import unittest
from pathlib import Path

from scripts.classify import classify_records, classify_title, make_queue, run


class ClassificationTests(unittest.TestCase):
    def test_series_and_indicator_methods(self):
        self.assertEqual(classify_title('【日本株爆益戦略⑤】MACDとRSIの使い方')[:3],
                         ('売買戦略特集', 'A', '5'))
        self.assertEqual(classify_title('【テクニカル分析】MACDの基礎と応用。')[1], 'A')
        self.assertEqual(classify_title('【株投資】板気配の見極め方と売買タイミング')[1], 'A')

    def test_daily_headline_is_not_automatically_a_strategy(self):
        self.assertEqual(classify_title('5/16 日経平均上昇。RSI過熱で半導体が急騰。')[1], 'C')
        self.assertEqual(classify_title('日本株空売り急増で日経平均下落')[1], 'C')

    def test_title_duplicates_and_queue(self):
        items = [
            {'video_id': 'v1', 'url': 'https://www.youtube.com/watch?v=v1',
             'title': '【日本株爆益戦略①】勝ち方', 'published_at_jst': '2023-01-02'},
            {'video_id': 'v2', 'url': 'https://www.youtube.com/watch?v=v2',
             'title': '【日本株爆益戦略①】勝ち方', 'published_at_jst': '2023-01-03'},
            {'video_id': 'v3', 'url': 'https://www.youtube.com/watch?v=v3',
             'title': '日経平均下落', 'published_at_jst': '2023-01-04'},
        ]
        rows, manifest = classify_records(items)
        self.assertEqual(manifest['total_videos'], 3)
        self.assertEqual(manifest['unique_titles'], 2)
        self.assertEqual(manifest['videos_in_repeated_title_groups'], 2)
        self.assertEqual(rows[0]['same_title_count'], 2)
        self.assertEqual(len(make_queue(rows)), 1)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'videos.json'
            source.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
            out = Path(tmp) / 'out'
            run(source, out)
            self.assertTrue((out / 'priority_research_queue.csv').exists())
            self.assertTrue((out / 'classification_manifest.json').exists())

    def test_invalid_duplicate_ids(self):
        with self.assertRaises(ValueError):
            classify_records([{'video_id': 'x', 'title': 'A'},
                              {'video_id': 'x', 'title': 'B'}])


if __name__ == '__main__':
    unittest.main()
