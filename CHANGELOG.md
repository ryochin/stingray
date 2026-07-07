# Changelog

## 2026/07/07

対象範囲: (`98cfd0b`, 2026-06-21) 〜 (`e7e7719`, 2026-07-07) / 全4コミット

### ✨ フィード健全性ステート（Stale / Error の区別）

フィードの状態を「stale-cache 等による degraded」と「実際の失敗」に明確に分離し、一覧上の表示を分けた。

- **`health` ステートを永続化しAPIで公開** — フィードに `health`（`ok` | `degraded` | `failing`）カラムを追加。degraded（stale-cache / web-norules）を黄色の「Stale」警告、実際の失敗を赤の「Error」として描画するようにした。従来のフロントエンド側ヒューリスティック（`consecutive_failures === 0 && last_error`）を置き換え、手動フェッチ失敗を「Stale」と誤表示していた問題を解消 ([`2e05dce`](https://github.com/ryochin/stingray/commit/2e05dce))
- `record_feed_attempt` / `update_feed_fetch_status` で各結果を health に分類し、`last_fetched_at` のセマンティクスをパス間で統一。スケジュール実行と手動実行で stale-cache 診断を共有。`!!!` バッジとエラーフィルタは severity 軸として `consecutive_failures` に残置 ([`2e05dce`](https://github.com/ryochin/stingray/commit/2e05dce))
- stale-cache フィードを失敗と区別するフロントエンド表示の先行対応 ([`64e28c0`](https://github.com/ryochin/stingray/commit/64e28c0))

### ✨ フィード・ページ取得時のブラウザ User-Agent 送信

- **ブラウザの User-Agent を送信** — 非ブラウザ UA を WAF が 403 で弾くサイト（例: brevis.exblog.jp）に対応。フィード・ページ取得時にブラウザ相当の User-Agent を送るようにし、`config.yml` で設定可能にした ([`2297572`](https://github.com/ryochin/stingray/commit/2297572))

### 🐛 Fixes

- LLM のバイトフォールバック由来のアーティファクトを UTF-8 にサニタイズ ([`e7e7719`](https://github.com/ryochin/stingray/commit/e7e7719))
