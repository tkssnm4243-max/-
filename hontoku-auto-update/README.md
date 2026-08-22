# ホントク 自動更新セットアップ

このフォルダには、「ホントク」アプリを **毎日自動でチラシ価格を取得して更新する**ための
一式が入っています。月額0円（GitHub Actions無料枠＋Netlify無料枠）で動く構成です。

## 中身

```
hontoku-auto-update/
├── index.html                        ← アプリ本体（このままNetlifyのルートに置く）
├── today_prices.json                 ← 「今日の価格」データ（毎日自動更新される）
├── scraper/
│   ├── scrape_flyers.py              ← 5店舗のチラシを取得してOCRするスクリプト
│   ├── stores.json                   ← 店舗一覧・商品名（別名）の設定
│   └── requirements.txt              ← Python依存パッケージ
└── .github/workflows/update-flyers.yml  ← 毎日自動実行するGitHub Actions設定
```

## セットアップ手順（最初の1回だけ）

### 1. GitHubリポジトリを作る

1. https://github.com にログイン（アカウントがなければ新規作成、無料）
2. 右上の「+」→「New repository」
3. リポジトリ名は何でもOK（例: `hontoku-app`）。Public/Privateはどちらでも可
   （Privateでも今回の使用量ならGitHub Actionsの無料枠で十分足ります）
4. 作成したら、このフォルダの中身一式をそのリポジトリにアップロードする
   （GitHubの画面から「Add file」→「Upload files」でこのフォルダの中身をドラッグ&ドロップでもOK。
   Gitに慣れていればもちろん `git init && git add . && git commit && git push` でも可）

### 2. GitHub Actionsに「書き込み権限」を与える

自動実行が `today_prices.json` を書き換えてリポジトリにコミットし直すので、権限が必要です。

1. リポジトリの「Settings」タブ
2. 左メニュー「Actions」→「General」
3. 一番下の「Workflow permissions」で
   **「Read and write permissions」**を選んで保存

これを忘れると、毎日の自動実行は成功してもコミットの部分だけ失敗します。

### 3. Netlifyのサイトを、このGitHubリポジトリに接続する

今は「ファイルをドラッグ&ドロップ」で更新する運用ですが、今後は
「GitHubにプッシュされたら自動でNetlifyに反映される」運用に切り替えます。

1. https://app.netlify.com にログイン
2. 今のサイト（`sweet-flan-694cf3`）を開く
3. 「Site configuration」→「Build & deploy」→「Link repository」（もしくは「Link site to Git」）
4. GitHubと連携し、さっき作ったリポジトリを選択
5. ビルド設定は以下でOK（静的HTMLだけなのでビルドコマンドは不要）
   - Build command: 空欄のまま
   - Publish directory: `/` （リポジトリのルート。`index.html`がある場所）
6. 「Deploy site」

これで、リポジトリの中身が変わるたびに（＝毎日の自動更新のたびに）Netlifyが自動で再デプロイしてくれます。

### 4. 動作確認（手動で1回試す）

セットアップ後、1日待たなくても手動でテストできます。

1. GitHubのリポジトリ画面で「Actions」タブ
2. 左側の「Update today's flyer prices」を選択
3. 右側の「Run workflow」ボタン→「Run workflow」
4. 数分待つと実行ログが見られる（何件マッチしたか、エラーがあったかが表示される）
5. 成功していれば `today_prices.json` が更新され、Netlifyにも自動反映される

## 正直に書いておきたいこと（精度について）

このスクリプトは無料のOCR（Tesseract）でチラシの画像から価格を読み取っています。
チラシは商品写真・装飾・いろいろなフォントが混ざったデザインなので、**読み取りmiss・誤読は普通に起こります**。
実装上、次のような工夫はしていますが、完璧ではありません。

- 商品名の座標に一番近い価格を採用する（遠く離れた無関係な価格を拾いにくくする）
- カタログの基準価格から大きく外れた値（0.4倍未満・2.5倍超）は誤読とみなして除外する
- 今回読み取れなかった項目は、前回のデータをそのまま残す（1回の失敗でデータが消えない）

とはいえ、たまに変な価格が入ることは想定しておいてください。気になったら
`today_prices.json` を直接編集してGitHubにコミットすれば、次の自動実行までその値が使われます
（ただし次の自動実行で上書きされる可能性はあります）。

## 費用について

- GitHub Actions: 個人アカウントの無料枠は月2000分。このジョブは1回数分なので、
  毎日実行しても月100分程度しか使わず、余裕で無料枠内です
- Netlify: 無料プランの範囲内で問題なし
- OCR: Tesseractは完全無料（AI課金なし）

つまり、想定通り**月額0円**で運用できます。

## 対象サイトについて

このスクリプトはトクバイ（tokubai.co.jp）の各店舗ページから、1日1回・各店舗最大2枚の
チラシ画像だけを取得します。サーバー負荷を避けるため、リクエストの間には必ず数秒の
待機を入れています。個人の私的利用の範囲を想定した設計ですが、利用規約の解釈・
実行の最終判断はご自身でお願いします。
