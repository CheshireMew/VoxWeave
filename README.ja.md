<!-- readme-header:start -->

<p align="center">
  <img src="./assets/readme/logo.svg" width="112" alt="VoxWeave">
</p>

<h1 align="center">VoxWeave</h1>

<p align="center">
  <strong>Windows 上で、オフライン素材とマイク音声をローカルに RVC 変換。</strong>
</p>

<p align="center">
  <a href="./README.md">中文</a> · <a href="./README.en.md">English</a> · <strong>日本語</strong> | <a href="./docs/ARCHITECTURE.md">文档</a> | <a href="./CONTRIBUTING.md">贡献</a> | <a href="https://github.com/CheshireMew/VoxWeave/issues">反馈</a>
</p>

<p align="center">
  <a href="https://x.com/0xCheshire" title="X"><img src="https://img.shields.io/badge/X-%400xCheshire-000000?logo=x&amp;logoColor=white" alt="X：@0xCheshire"></a>
  <a href="https://t.me/CheshireBTC" title="Telegram"><img src="https://img.shields.io/badge/Telegram-CheshireBTC-26A5E4?logo=telegram&amp;logoColor=white" alt="Telegram：CheshireBTC"></a>
  <a href="https://blog.blacknico.com/" title="Blog"><img src="https://img.shields.io/badge/Blog-blog.blacknico.com-2E7D32?logo=rss&amp;logoColor=white" alt="博客：blog.blacknico.com"></a>
  <a href="https://blacknico.com/" title="Homepage"><img src="https://img.shields.io/badge/Home-blacknico.com-1F6FEB?logo=googlechrome&amp;logoColor=white" alt="个人主页：blacknico.com"></a>
</p>

<p align="center">
  <a href="https://github.com/CheshireMew/VoxWeave/stargazers"><img src="https://img.shields.io/github/stars/CheshireMew/VoxWeave?style=flat" alt="GitHub Stars"></a>
  <a href="https://github.com/CheshireMew/VoxWeave/forks"><img src="https://img.shields.io/github/forks/CheshireMew/VoxWeave?style=flat" alt="GitHub Forks"></a>
  <a href="https://github.com/CheshireMew/VoxWeave/blob/main/LICENSE"><img src="https://img.shields.io/github/license/CheshireMew/VoxWeave?style=flat" alt="Repository License"></a>
</p>

<!-- readme-header:end -->

VoxWeave は、Windows 上でローカルに動く RVC ボイスチェンジ・ワークステーションです。ローカルの音声、楽曲、動画、フォルダー、マイク入力を渡すと、デスクトップアプリから試聴、オフライン変換、リアルタイム変換、バッチ処理を実行できます。結果、失敗理由、生成物の場所は一つのタスクセンターで確認できます。

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="メディア、フォルダー、マイク入力を一つのローカルサービスで処理し、検証済みファイルまたはリアルタイム再生として出力する流れ">
</p>

## 用途に合うかを確認する

| やりたいこと | VoxWeave の出力 | 開始場所 |
| --- | --- | --- |
| 音声、歌、動画を変換する | 元ファイルを上書きしない完成品。動画では元の映像・音声を保ち、変換音声トラックを追加 | 変換ワークスペース |
| マイク音声をリアルタイムに変換する | 選択した再生デバイスへの変換音声と、検出・推論時間・音声中断の状態 | リアルタイム変換 |
| フォルダー全体や新着ファイルを処理する | キャンセル、再試行、内容による重複排除が可能な共有タスク | バッチと監視 |
| スクリプトや AI ツールから操作する | 検出可能なローカル HTTP/WebSocket 契約と安定した JSON 結果 | CLI と API |

実機検証済みの環境は Windows 11 と NVIDIA CUDA です。Linux と macOS の境界はソースにありますが、実機検証は未完了です。ポータブル Windows ZIP にはアプリ用の Python とデスクトップ依存関係が含まれますが、RVC、FFmpeg、音声モデル、仮想オーディオデバイス、モデル学習、GPT-SoVITS は含まれません。インストーラーは提供しません。

## クイックスタート

### 1. Windows ZIP を使う

同じリリースから Windows x64 ZIP と `.sha256` を取得してチェックサムを確認し、`VoxWeave` ディレクトリ全体を展開して `VoxWeave.exe` を実行します。既存のデータディレクトリと RVC 環境があれば再利用します。見つからない場合は非システムドライブを選び、固定版の RVC、Python、FFmpeg、初期モデルをダウンロードする前に確認します。大容量のランタイム、モデル、キャッシュ、ログ、タスク生成物はアプリの外に保存されます。

### 2. ソース環境を準備する

Python 3.12、Git、FFmpeg、NVIDIA CUDA GPU が必要です。データディレクトリはソースの外に指定してください。Python 環境、pip キャッシュ、一時ファイル、データベース、ログ、ダウンロード、タスク生成物はすべてここに保存されます。

```powershell
git clone https://github.com/CheshireMew/VoxWeave.git
cd VoxWeave
.\scripts\bootstrap.ps1 -DataRoot D:\Tools\VoxWeave
```

互換性のある固定版 RVC 環境がすでにある場合は、初回設定時に指定できます。

```powershell
.\scripts\bootstrap.ps1 `
  -DataRoot D:\Tools\VoxWeave `
  -RvcRoot E:\path\to\Retrieval-based-Voice-Conversion-WebUI `
  -RvcPython E:\path\to\Retrieval-based-Voice-Conversion-WebUI\.venv\Scripts\python.exe `
  -Ffmpeg D:\path\to\ffmpeg.exe `
  -Ffprobe D:\path\to\ffprobe.exe
```

`requirements.lock` は、Windows/Python 3.12 で検証した依存関係の集合です。bootstrap と CI は同じ制約を使います。ソース側には、Git から無視される `.voxweave.local.json` のデータディレクトリ参照だけが残ります。

### 3. ソース版デスクトップアプリを起動する

コンソールを表示せずに起動するには、リポジトリ直下の `VoxWeave.vbs` をダブルクリックします。起動エラーを確認したい場合は PowerShell で実行します。

```powershell
.\scripts\run.ps1
```

`VoxWeave.bat` は古いショートカットを `VoxWeave.vbs` に引き渡して終了します。ソース更新後は、`.\scripts\voxweave.ps1 service stop` で既存サービスを正常終了してから再起動してください。

### 4. RVC 環境がない場合はランタイムを導入する

まずデスクトップアプリを起動し、ローカルサービスを利用可能にします。次に別の PowerShell からインストールタスクを送信します。

```powershell
.\scripts\voxweave.ps1 --json execute runtime.install --arguments '{}'
```

固定された RVC ソース、独立 Python 環境、必須の推論資産がデータディレクトリに導入されます。再配布ライセンスを確認できない任意のボーカル分離ウェイトは既定で取得しません。WeSpeaker ONNX ウェイトは CC-BY-4.0 に従って導入します。詳しくは [第三者コンポーネント](THIRD_PARTY_NOTICES.md) を参照してください。

### 5. 最初の変換を完了する

1. 「モデルライブラリ」でローカルフォルダーを検索するか、使用権のある `.pth` と任意の `.index` を追加します。
2. 「変換ワークスペース」で入力、出力先、対象モデルを選びます。
3. 試聴を生成し、ピッチ、F0、インデックス率などを確認してから本変換を開始します。
4. 「タスクセンター」で進行状況を確認し、完了した生成物を再生または開きます。

VoxWeave は既存の出力を既定で上書きしません。タスクは入力、モデル、インデックスの識別情報を固定し、実行前後に再検証します。結果マニフェストには最終ファイルと SHA-256 が記録されるため、再試行時に変更済みの素材やモデルへ黙って差し替わることはありません。

## 三つの主要ワークフロー

### オフラインメディア

変換ワークスペースは WAV、FLAC、MP3、MP4、MKV を受け付けます。音声モードでは話者を解析し、選択した話者だけを変換できます。ユーザーが任意の分離モデルを用意すれば、歌唱モードでボーカルを処理して伴奏へ戻せます。最大四組の設定で同期 A/B 試聴を生成できます。

長い音声は低エネルギー位置で分割しますが、RVC モデルは一度だけ読み込みます。動画では元の映像・音声ストリームをコピーし、名前付きの変換音声トラックを追加します。公開前に完成ファイルのデコード、マニフェスト、ハッシュを検証します。

### リアルタイムマイク

「設定と診断」で Windows オーディオホスト、マイク、再生デバイスを選び、「リアルタイム変換」でモデルと設定を選びます。入出力は同じホスト API に属している必要があります。連続モードでは、再生音の回り込みを防ぐためヘッドホンを推奨します。

0.25、0.5、1.0 秒の三段階の遅延予算を選べます。Silero VAD と設定したマイク開始レベルが推論開始を判断します。テストモードは一文を取り込み、変換し、発話が止まってから再生するため、連続使用前の確認に向いています。

リアルタイム処理とオフライン処理は同じ GPU 境界を共有します。オフラインタスク実行中はリアルタイムセッションを開始できません。リアルタイム中に送信したタスクは待機し、セッション停止後に再開します。モデル準備はオフラインキューを一時停止するため、バックグラウンドでは自動実行しません。「モデルを準備」を明示的に押すか、セッション開始時に同じ準備処理の完了を待ちます。

### バッチとフォルダー監視

バッチルールは入力、出力、モデル、監視状態をデータベースに保存します。新しいファイルは書き込みが安定してからキューに入ります。出力先は入力検索から除外され、内容 SHA-256 で重複を避けます。一つのファイルが失敗しても、バッチ全体の結果は失われません。

タスクはキャンセル、再試行、タスクセンターでの確認が可能です。VoxWeave は中間生成物を自動削除しません。容量を空ける場合は、設定画面で確認付きのアーカイブを実行するか、`storage.archive` 長時間タスクを明示的に送信します。

## CLI とローカル API

デスクトップアプリ、CLI、自動化クライアントは同じローカルサービスを使います。古い契約をスクリプトへ固定せず、実行中サービスの操作一覧と schema を先に取得してください。

```powershell
.\scripts\voxweave.ps1 --json describe
.\scripts\voxweave.ps1 --json models
.\scripts\voxweave.ps1 --json execute runtime.inspect --arguments '{}'
```

リクエストは `voxweave-control v1` を使います。状態を変更する操作と長時間操作には、サービス全体で一意の `request_id` が必要です。同じ ID、操作、引数、呼び出し元で再送すると、二重実行せず最初の永続化済み結果を返します。長時間操作はすぐに `task_id` を返します。`task get` で確認するか、ディスカバリーファイルが示す認証済み WebSocket を利用できます。

```powershell
.\scripts\voxweave.ps1 --json execute conversion.run --arguments '{
  "input":"D:\\media\\source.wav",
  "output":"D:\\media\\source-converted.wav",
  "model":"MODEL_ID_FROM_MODELS",
  "pitch":9,
  "f0":"rmvpe",
  "content_mode":"clean",
  "overwrite":false
}'

.\scripts\voxweave.ps1 --json task get TASK_ID
```

サービスはランダムな `127.0.0.1` ポートだけで待ち受けます。ディスカバリーファイルには PID、プロトコル版、一時トークンが含まれます。クライアントは古いファイルを信用せず、プロセスとハンドシェイクを確認します。詳細は [プロトコル仕様](docs/PROTOCOL.md) を参照してください。

## データ、モデル、境界

- SQLite は、モデル、プリセット、タスク、バッチルール、リアルタイムセッション、イベント、生成物、アーカイブの運用状態真源です。ユーザー設定は別の版管理された設定ファイルを真源とし、サービスだけが更新します。
- 構造化 JSON ログはデータディレクトリに保存され、10 MB ごとにローテーションし、最大 5 ファイルを保持します。
- 診断にはランタイム、モデル、タスク、リアルタイム、ストレージ、ログの要約が含まれますが、モデルやメディア本体は埋め込みません。
- 外部モデルは元の場所で登録し、ウェイトとインデックスをハッシュ化します。コピー、改名、アップロードはしません。
- URL または公式カタログからの導入には、追跡可能な出典、正確なサイズ、SHA-256、ライセンスが必要です。

音声モデルは実在人物やキャラクターを模倣する場合があります。利用者は、音声主体、モデル作者、素材の権利者から必要な許可を得て、適用法とプラットフォーム規則に従う責任があります。[モデル出典とライセンス方針](MODEL_POLICY.md) も確認してください。

## アーキテクチャと検証

QML デスクトップ、CLI、外部クライアントは、認証済みループバック API だけを通ってバックエンドへ入ります。モデルの検索、タスク DB の変更、RVC 呼び出しを直接行いません。オフラインタスクは単一ワーカー、リアルタイム音声は常駐別プロセスで処理し、一つの GPU スケジューラで調整します。

関連資料：

- [アーキテクチャとデータ境界](docs/ARCHITECTURE.md)
- [プロトコル仕様](docs/PROTOCOL.md)
- [Windows 0.1 実機検証記録](docs/VALIDATION.md)
- [公開 schema](schemas/)
- [変更履歴](CHANGELOG.md)

## 開発

クイックスタートでソース環境を作成し、Windows の検証入口を実行します。

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m ruff check .
D:\Tools\VoxWeave\.venv\Scripts\python.exe -m pytest
```

実 CUDA チェーンのスクリプトは、実行中サービスを通してモデル解決、タスク送信、RVC 推論、最終メディアのデコードを検証します。

```powershell
D:\Tools\VoxWeave\.venv\Scripts\python.exe scripts\verify_real_user_chain.py `
  --input D:\media\voice.wav `
  --model MODEL_ID_FROM_MODELS `
  --output-root D:\Tools\VoxWeave\validation\run
```

Pull Request の前に [CONTRIBUTING.md](CONTRIBUTING.md) を確認してください。現在は Windows のみを検証対象とします。公式リリースはポータブル Windows x64 ZIP で提供し、通常のコントリビューション作業ではリリースパッケージを生成しません。

メンテナーはクリーンなコミットだけを対象にし、リポジトリとシステムドライブの外へ出力します。

```powershell
.\scripts\build-exe.ps1 `
  -Python D:\Tools\VoxWeave\.venv\Scripts\python.exe `
  -OutputRoot D:\Tools\VoxWeave\release-builds
```

同じバージョンとコミットにつき構築ディレクトリは一つだけです。ZIP、SHA-256、ファイルマニフェスト、リリース概要を生成した後、別ディレクトリへ展開して全ファイルを検証し、アプリと QML をオフスクリーンで起動します。バージョン、コミット、コンポーネントのライセンス、ランタイム DLL、QML 起動、ハッシュの不一致があれば失敗します。完全な条件は [Windows リリース手順](docs/RELEASING.md) を参照してください。

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history.svg">
  <img alt="VoxWeave GitHub Star History" src="https://raw.githubusercontent.com/CheshireMew/VoxWeave/star-history/star-history.svg">
</picture>

グラフは GitHub Actions で定期的に生成し、専用の `star-history` ブランチへ公開します。

## ライセンスと第三者コンポーネント

VoxWeave のソースは [AGPL-3.0-or-later](LICENSE-NOTICE.md) です。完全なライセンス説明は [LICENSING.md](LICENSING.md) を参照してください。Windows ZIP にはデスクトップアプリに必要な CPython、PySide6/Qt、Python 依存関係が含まれますが、RVC、管理対象の RVC Python 環境、FFmpeg、推論ウェイト、音声モデルは含まれません。各 ZIP には完全なライセンスディレクトリ、Qt/PySide のソース取得・差し替え手順、全ファイルのハッシュマニフェストが入ります。出典、固定版、再配布境界は [第三者コンポーネント](THIRD_PARTY_NOTICES.md) を参照してください。
