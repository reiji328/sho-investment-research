# Sho 長区間解析・暗号化リレー v0.2（事前準備版）

**状態（2026-11-23）**：5分・10分に対応する公開コードとオフラインテストの準備済み。**Gemini APIは追加実行していない**。解析結果・ユーザー独自の研究仮説は公開GitHubへ保存しない。

## 既存の動作済み3分解析と新しい準備版

- 既存の `scripts/sho_encrypted_relay_*.py`、過去の `.github/workflows/sho-encrypted-relay-*.yml` を**変更しない**。
- 新しいコード：`scripts/sho_longclip_relay.py`。動画① `oYvsDRcrXdw` の区間 `5m` = 20:00〜25:00、`10m` = 15:00〜25:00 に**限定**する。別動画やそれ以外の区間を勝手に対象にしない。
- 新しいテスト：`tests/test_sho_longclip_relay.py`。オフライン専用GitHub Actions：`.github/workflows/sho-longclip-offline-tests.yml`。これらにはGemini APIキーを渡さず、合成した回答と使い捨てテスト用RSA-3072秘密鍵を使う。
- 事前テスト：[GitHub Actions run 45088314459](https://github.com/reiji328/sho-investment-research/actions/runs/45088314459)（**11/11 PASS**、2026-11-23実施、Gemini API送信0回）。
- **35,000トークン制限はGeminiの公式上限ではない**。旧コードではその超過を保存エラーとしていた。v0.2ではプラスの整数入力トークン数を記録し、異常な多さは警告としてmanifestに残して、正常な回答を暗号化する。ソフト警告の目安：5分50,000／10分100,000トークン。厳密なAPI料金や無料枠の保証ではない。
- `MAX_TOKENS` 等で出力が不完全でも、テキストが存在すれば`response_complete=false`、`model_finish_reason=MAX_TOKENS`と明記して**暗号化保存**する。未完成の内容を完全な研究要約・成功例として扱わない。
- 安全な通信・保存：動画台帳の厳密な一致、明示的な`--execute`、有効な公開証明書、HTTPS送信1回のみ、APIエラー時に機密のエラー本文を印字しない、公開側への本文平文保存なし、AES-256-GCMのCMS暗号化、SHA-256、公開manifestに本文を含めない。秘密鍵は公開RepoやActionsへ配置しない。

## 実行境界

**このソースの追加だけでGemini APIは呼び出されない**。既定はオフラインの事前点検で、`--execute`を明示しなければ送信しない。既存のオフラインテストWorkflowはGemini APIを一切呼ばない。

将来の単発実行構成を検討するときのコマンド形式（**現時点では実行しない**）：

```sh
python3 scripts/sho_longclip_relay.py \
  --clip 5m \
  --catalog /path/to/approved/videos.json \
  --certificate /path/to/one-time-public.crt \
  --output-dir /path/to/empty-sealed-dir \
  --execute
```

実行の前に、①該当するGoogleプロジェクトの無料枠/課金状態、②公開Actions専用の1回限りのトリガー、③一時RSA鍵を公開Repoへ出さない受け渡し、④Artifactの保持期限3日、⑤暗号化内容を非公開カードへ移したかを確認すること。

**公開Artifactの成功 ≠ 非公開GitHubへの保存完了**。現行は暗号化Artifactの取得、秘密鍵の復号、非公開研究カードへの追記と読み戻しが別ステップ。ソース・ログ・Artifact・非公開カードを混同しない。

正式なデイトレ8戦略の変更、費用発生の承認、APIキーの権限追加、自動注文、動画②への適用はこの実装で承認されたわけではない。
