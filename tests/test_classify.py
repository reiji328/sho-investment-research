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


    def test_live_metadata_detection_and_deprioritization(self):
        from scripts.classify import detect_video_format, research_order_group

        self.assertEqual(detect_video_format({
            'live_broadcast_content': 'none',
            'has_live_streaming_details': False,
        })[0], '通常動画')
        self.assertEqual(detect_video_format({
            'live_broadcast_content': 'none',
            'has_live_streaming_details': True,
        })[0], 'ライブアーカイブ')
        self.assertEqual(detect_video_format({
            'live_broadcast_content': 'upcoming',
            'has_live_streaming_details': True,
        })[0], '配信予定')
        self.assertEqual(detect_video_format({})[0], '判定保留')
        self.assertEqual(research_order_group('A', '通常動画'), 1)
        self.assertEqual(research_order_group('B', '通常動画'), 2)
        self.assertEqual(research_order_group('A', 'ライブアーカイブ'), 3)
        self.assertEqual(research_order_group('B', 'ライブアーカイブ'), 4)
        self.assertEqual(research_order_group('A', '判定保留'), 5)

    def test_normal_methods_before_live_methods_without_removing_live(self):
        rows, manifest = classify_records([
            {'video_id': 'la', 'title': '【日本株爆益戦略②】方法',
             'published_at_jst': '2026-03-01',
             'live_broadcast_content': 'none', 'has_live_streaming_details': True},
            {'video_id': 'nb', 'title': '株投資で勝てない人の共通点',
             'published_at_jst': '2026-03-02',
             'live_broadcast_content': 'none', 'has_live_streaming_details': False},
            {'video_id': 'na', 'title': '【日本株爆益戦略①】方法',
             'published_at_jst': '2026-03-03',
             'live_broadcast_content': 'none', 'has_live_streaming_details': False},
            {'video_id': 'lb', 'title': '株投資で勝てない人の共通点・実例',
             'published_at_jst': '2026-03-04',
             'live_broadcast_content': 'none', 'has_live_streaming_details': True},
        ])
        self.assertEqual(manifest['video_format_counts'],
                         {'ライブアーカイブ': 2, '通常動画': 2})
        self.assertEqual([row['video_id'] for row in make_queue(rows)],
                         ['na', 'nb', 'la', 'lb'])
        self.assertEqual([row['priority'] for row in rows], ['A', 'B', 'A', 'B'])

    def test_duplicate_title_prefers_normal_video(self):
        rows, manifest = classify_records([
            {'video_id': 'live', 'title': '【日本株爆益戦略①】方法',
             'published_at_jst': '2026-03-01',
             'live_broadcast_content': 'none', 'has_live_streaming_details': True},
            {'video_id': 'regular', 'title': '【日本株爆益戦略①】方法',
             'published_at_jst': '2026-03-02',
             'live_broadcast_content': 'none', 'has_live_streaming_details': False},
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual([v['video_id'] for v in make_queue(rows)], ['regular'])


if __name__ == '__main__':
    unittest.main()
