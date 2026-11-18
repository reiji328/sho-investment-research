# Sho's投資情報局｜公開動画台帳（取得プログラム）

対象: [Sho's投資情報局](https://www.youtube.com/channel/UCK9Pl6bbWqMC_x7IDca2tIg) / `@SHO1112`。非公式の個人用研究補助プロジェクトです。チャンネル運営者との提携・承認を示すものではありません。

## 目的と現在の範囲

YouTube Data API v3 で **一般に閲覧可能な動画のメタデータ** を収集し、`videos.csv` / `videos.json` / `manifest.json` の3ファイルに出力します。動画・音声・字幕は取得せず、動画の中身を視聴したり、投資手法の正確性を判断したりしません。

- `videos.csv`: タイトル、動画ID、リンク、公開日時（UTC/JST）、長さなど。iPhoneでも表計算アプリで閲覧可。
- `videos.json`: 上記に加え、APIが返す説明文（概要欄）を含む処理用データ。
- `manifest.json`: 実行日時、取得件数、欠損・重複の数、取得上の限界。
- 未分析の動画には `research_status=未分析` の仮表示を付けます。**内容確認が済んだという意味ではありません。**

**APIの公開動画リストに現れない非公開・削除動画や、APIが返さない項目は取得できません。** YouTube上の「動画数」と実取得件数が一致するとは限りません。重複は動画IDで排除し、取得失敗時には空の台帳を正常成果として出力しません。

## 実行と保存について

- 取得: GitHub Actions（通常の `ubuntu-latest`）。1日1回 **日本時間05:23ごろ** に起動を予定。GitHub側のスケジュール遅延はあり得ます。
- 手動実行: Actions → `Collect Sho YouTube video catalog` → `Run workflow`。
- 保存: 成功したActions実行の `Artifacts` に最新台帳をZIPで保管し、**21日後に自動削除**します。Git履歴には第三者のAPIデータをコミットしません。
- 次の段階で、内容に基づく独自研究ノートや手法の整理を **非公開の My-Knowledge** に保存します。この公開リポジトリには個人用ノートや秘密情報を置きません。
- 各時点で最新の台帳を使ってください。21日以上すべての実行が失敗すると、以前の台帳は自動的に消えます。

### 初期設定（iPhoneでも可）

1. **完了:** PUBLIC リポジトリ `reiji328/sho-investment-research` の作成。既存の `My-Knowledge` は非公開のままです。
2. **完了:** このプロジェクトのプログラム・テスト・ワークフローを配置。
3. **初回設定済み:** [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成し、**YouTube Data API v3** を有効にしてAPIキーを発行する。キーは可能なら **YouTube Data API v3 にAPI制限**を設定する。GitHub-hosted runnerの接続元IPは固定ではないため、IP制限はそのままでは使えません。請求・使用量の制限も確認してください。
4. **初回設定済み:** GitHubのリポジトリ → Settings → Secrets and variables → Actions → New repository secret。名前を **`YOUTUBE_API_KEY`**、値を発行したAPIキーにする。APIキーをREADME、ログ、Issues、チャット、Git履歴に絶対に貼り付けない。
5. **初回実行成功（2026-11-18）:** Actions → `Collect Sho YouTube video catalog` → `Run workflow` を1回実行。テスト成功とメタデータ取得成功、Artifactsに3ファイルがあることを確認する。
6. 取得後は `manifest.json` を見て、実取得件数・見えない動画数・取得日時を確認する。

### ローカルテスト

Python 3.11+、標準ライブラリのみ:

```sh
python -m unittest discover -s tests -v
YOUTUBE_API_KEY='YOUR_PRIVATE_KEY' python scripts/collect.py
```

実際のキーは環境変数でのみ渡してください。キーが設定されていなければ実取得は行いません。

## 注意事項

- **公開リポジトリ**なのでソースコード、Actionsの実行結果・ログ、保持されているArtifactsは外部から見えます。個人の売買戦略・秘密情報・有料配信資料をアップロードしないでください。
- YouTube APIから取得した**第三者の非承認データは原則30日以内に更新または削除**する必要があります。そのためデータは長く残るGit履歴ではなく短期保持のArtifactsに置きます。具体的な利用方法によっては追加の規約確認が必要です。
- 公開リポジトリの標準GitHub-hosted runnerは無料ですが、大型ランナー等は別扱いです。YouTube APIのクォータは別制限です。
- YouTubeの内容についてこのリポジトリは**推薦・予測・売買指示を提供しません**。将来の独自研究では発言・仮説・検証結果を区別します。

参照: [YouTube Data API v3](https://developers.google.com/youtube/v3) · [YouTube API Developer Policies](https://developers.google.com/youtube/terms/developer-policies) · [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage)

## タイトルに基づく仮分類（2026-11-19追加）

- `scripts/classify.py` は最新の `output/videos.json` から動画ID・タイトル・公開日・長さだけを読み、**内容を視聴したと誤認させない**タイトルベースの仮分類を実施します。
- 生成するファイル：`classified_videos.csv`（全件のジャンル・複数テーマタグ・重複グループ）、`priority_research_queue.csv`（優先研究候補・同一タイトルを1件にまとめた一覧）、`classification_manifest.json`（件数・分類規則版・限界）。
- 優先度A=明示的な「日本株爆益戦略」シリーズ、テクニカル指標の基礎説明、売買・執行方法の解説候補。B=その他の手法説明候補。C=タイトル上、独立した手法の説明と判断できない動画。**Cは不要・視聴済み・質が低いという意味ではありません。**
- **2026-11-18の初回データ（3,439件）を2026-11-19にローカル処理した参考結果**：A20、B41、C3,378件、異なるタイトル3,131、同一タイトルが2本以上存在するグループ305。A/Bを同一タイトル単位で重複整理した研究候補は58タイトル。
- この分類処理をGitHub Actionsの取得後ステップに追加済み。**分類追加後のGitHub Actions実行成功はまだ未確認**。次回以降のArtifactに出力する予定です。
- 220件の差分は研究の判断により「非公開扱い・調査対象外」とするが、実際の非公開設定は確認していない。プログラムは根拠のない220件のレコードを作成しません。
- **重要**：全動画のURL、タイトル、テーマから推測した「研究候補」であり、動画内の発言、指標の計算条件、売買エントリー・損切り、期待値や再現性の根拠を確認したものではありません。今後の研究には動画本編からの証拠確認が必要です。
- 生成した分類CSV/JSONは従来の元APIデータと同じく**21日保持のArtifactsのみ**へ出力し、Git履歴へコミットしません。第三者APIメタデータの更新・削除要件に従ってください。

## ライブ配信の研究優先度を下げる（2026-11-19・分類v0.2）

ユーザー方針：通常動画からShoさんの投資手法を研究し、ライブ配信・ライブアーカイブは後で事例確認に使う。**ライブを台帳から消さない。**

- APIの `live_broadcast_content` と `has_live_streaming_details` から、「通常動画」「ライブアーカイブ」「ライブ配信中」「配信予定」「判定保留」を区別する。タイトルに「ライブ」と書かれているかや、長さだけで決めない。
- 出力列 `video_format`・`format_evidence`・`research_order_group` を追加。`priority`（従来のタイトルから推定したA/B/C）は変更せず、**研究の着手順序だけ**変更する。
- 着手順：**①通常A（爆益戦略①〜⑥を先頭）→②通常B→③ライブA→④ライブB→⑤判定保留A/B→⑥通常C→⑦ライブC→⑧判定保留C**。Cも後日の研究対象。
- 2026-11-18実データ3,439件の再分類：通常動画 **1,409件**、ライブアーカイブ **2,029件**、配信予定 **1件**。現在の優先研究キュー58タイトルの内訳は通常A18、通常B1、ライブA2、ライブB37。すべて旧分類からの再計算であり、新しいAPI取得ではない。
- 具体的な説明・指標の数値はタイトル分類では分からないため、本編・字幕で裏付けた内容だけを研究ノートへ記録する。動画を見たふりはしない。
- このルールのローカル合成データ試験と初回3,439件データでの再分類・並び順チェックは実施済み。**改訂したGitHub Actionsの実行結果はまだ未確認**。

