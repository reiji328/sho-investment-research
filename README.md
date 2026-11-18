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
3. **未設定:** [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成し、**YouTube Data API v3** を有効にしてAPIキーを発行する。キーは可能なら **YouTube Data API v3 にAPI制限**を設定する。GitHub-hosted runnerの接続元IPは固定ではないため、IP制限はそのままでは使えません。請求・使用量の制限も確認してください。
4. **未設定:** GitHubのリポジトリ → Settings → Secrets and variables → Actions → New repository secret。名前を **`YOUTUBE_API_KEY`**、値を発行したAPIキーにする。APIキーをREADME、ログ、Issues、チャット、Git履歴に絶対に貼り付けない。
5. **未実行:** Actions → `Collect Sho YouTube video catalog` → `Run workflow` を1回実行。テスト成功とメタデータ取得成功、Artifactsに3ファイルがあることを確認する。
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
