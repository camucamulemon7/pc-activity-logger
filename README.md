# PC Activity Logger for OpenWebUI

Windowsの前面ウィンドウを定期的に撮影し、OpenWebUI経由でVisionモデルに解析させ、作業内容をJSONLとOpenWebUI Noteへ記録するツールです。

画面全体ではなく前面ウィンドウ部分をAIへ送り、Idle中、Windowsロック中、切断セッション中、前回と同一の画面では解析をスキップします。

> [!CAUTION]
> スクリーンショットには個人情報、認証情報、顧客情報などが含まれる可能性があります。社給PCでは、導入前に所属組織の情報セキュリティ規程を確認してください。

## 主な機能

- アクティブウィンドウ、実行ファイル名、ウィンドウタイトルの取得
- アクティブウィンドウが属するモニターの撮影
- AIへ送る画像を前面ウィンドウ領域へ切り出し
- OpenWebUI Files APIへの一時アップロード（`process=false`）
- OpenWebUI Chat Completions APIによるVision解析
- 固定JSONスキーマによる応答検証と、不正応答時の1回リトライ
- 日付別`activity.jsonl`への追記
- OpenWebUIの日別NoteへのMarkdown追記
- JSONL保存成功後のOpenWebUI一時ファイル削除
- キーボード・マウスのIdle時間によるスキップ
- ロック画面、セキュアデスクトップ、切断セッションの検出
- 知覚ハッシュによる同一画面スキップ

## 処理フロー

```text
Windows
  ├─ セッション状態・Idle状態を確認
  ├─ アクティブウィンドウと対象モニターを取得
  ├─ モニター撮影・前面ウィンドウへ切り出し
  ├─ 前回と同じ画面ならスキップ
  ├─ OpenWebUI Files APIへ一時アップロード
  ├─ file_idを使ってVisionモデルへ解析依頼
  ├─ activity.jsonlへ保存
  ├─ OpenWebUI側の一時画像を削除
  └─ OpenWebUI Noteへ追記
```

OpenWebUI側の一時画像はJSONL保存後に削除します。Windows側の画像は証跡として`data/`に保存されます。

## 必要環境

- Windows 10またはWindows 11
- Python 3.10以上
- 画像入力に対応したモデルを登録済みのOpenWebUI
- OpenWebUI APIキー
- OpenWebUIへ接続できるネットワーク環境

動作確認に使用したモデルは`gemma-4-31B-it`です。他のVisionモデルも、OpenAI互換のChat Completions APIで画像入力とJSON Schema出力を処理できれば使用できます。

## インストール

リポジトリを取得して、PowerShellでプロジェクトディレクトリへ移動します。

```powershell
git clone https://github.com/camucamulemon7/pc-activity-logger.git
cd pc-activity-logger
```

セットアップスクリプトを実行します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

次の処理が行われます。

1. `.venv`にPython仮想環境を作成
2. `requirements.txt`の依存パッケージをインストール
3. 存在しない場合だけ`config.example.yaml`を`config.yaml`へコピー

組織のポリシーでPowerShellまたはPythonの実行が制限されている場合、制限を回避せず管理者へ確認してください。

## 設定

生成された`config.yaml`を編集します。このファイルは`.gitignore`に含まれており、GitHubへは公開されません。

```yaml
openwebui:
  base_url: "http://openwebui:8080/api"
  api_key: "YOUR_API_KEY"
  model: "gemma-4-31B-it"
  timeout_sec: 120
  max_tokens: 1024

capture:
  interval_sec: 180
  jpeg_quality: 80
  idle_threshold_sec: 300
  skip_same_screen: true
  same_screen_max_distance: 3
  same_screen_force_after_sec: 900
  skip_unavailable_session: true

storage:
  data_dir: "data"

notes:
  enabled: true
  title_prefix: "PC作業記録"
```

### OpenWebUI設定

| 項目 | 内容 |
|---|---|
| `base_url` | 末尾が`/api`のOpenWebUI URL |
| `api_key` | OpenWebUIで発行したAPIキー |
| `model` | OpenWebUIに表示されるモデルIDと完全に同じ文字列 |
| `timeout_sec` | API応答を待つ最大秒数 |
| `max_tokens` | Visionモデルが返す最大トークン数 |

### 撮影設定

| 項目 | 内容 |
|---|---|
| `interval_sec` | 撮影周期。`180`は3分間隔 |
| `jpeg_quality` | JPEG品質（1～95） |
| `idle_threshold_sec` | 最後の入力からスキップを開始する秒数。`0`で無効 |
| `skip_same_screen` | 前回とほぼ同じ画面の解析を省略するか |
| `same_screen_max_distance` | 同一画面と判定する知覚ハッシュ距離（0～64） |
| `same_screen_force_after_sec` | 同じ画面でも強制的に再解析するまでの秒数 |
| `skip_unavailable_session` | ロック中・切断中の撮影を省略するか |

`same_screen_max_distance`は、まず既定値の`3`を推奨します。大きくすると、より変化のある画面も同一扱いになります。同一画面の比較状態はメモリ上だけに保持されるため、プログラム再起動後の最初の画面は必ず解析します。

### Note設定

`notes.enabled: true`の場合、OpenWebUIに`PC作業記録 YYYY-MM-DD`という日別Noteを作成し、解析結果を追記します。Note更新に失敗しても、ローカルのJSONLは保持されます。

## 実行方法

### 単発実行

最初に、画面取得、API接続、モデル応答、ファイル削除を確認します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Once
```

成功例：

```text
INFO Capturing Code.exe (pc-activity-logger - Visual Studio Code)
INFO Uploaded temporary OpenWebUI File: ...
INFO Deleted temporary OpenWebUI File: ...
INFO Saved activity to ...\activity.jsonl: ...
```

Idle中、Windowsロック中、または前回と同じ画面の場合は、正常動作として`Skipping ...`が表示され、解析は行われません。

### 常駐実行

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

停止するときは`Ctrl+C`を押します。スリープ中は処理が停止し、復帰後に次の周期から状態判定を再開します。

### 別の設定ファイルを使用

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 `
  -Config "C:\path\to\config.yaml"
```

## Windowsログオン時に自動起動

画面取得には対話デスクトップが必要なため、「PC起動時」ではなく「ユーザーログオン時」にタスクスケジューラから起動してください。

タスクスケジューラの操作には次を指定します。

```text
プログラム: powershell.exe
引数: -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\path\to\pc-activity-logger\run.ps1"
開始: C:\path\to\pc-activity-logger
```

ユーザーがログオンしている場合のみ実行する設定にします。ログオフまたはWindowsシャットダウン時にはプロセスも終了します。

PowerShellの実行が許可されていない環境では、タスクから`.venv\Scripts\pythonw.exe`を直接起動できます。

```text
プログラム: C:\path\to\pc-activity-logger\.venv\Scripts\pythonw.exe
引数: -m pc_activity_logger.main --config "C:\path\to\pc-activity-logger\config.yaml"
開始: C:\path\to\pc-activity-logger
```

## 保存形式

```text
data/
└─ 2026-08-21/
   ├─ screenshots/
   │  ├─ 101500_123456_active.jpg
   │  └─ 101500_123456_monitor.jpg
   └─ activity.jsonl
```

- `*_active.jpg`: AI解析に使用した前面ウィンドウ画像
- `*_monitor.jpg`: 前面ウィンドウが存在するモニター全体の画像
- `activity.jsonl`: 時刻、アプリ、ウィンドウ情報、画像パス、解析結果

JSONLの例：

```json
{"timestamp":"2026-08-21T10:15:00+09:00","app_name":"Code.exe","window_title":"pc-activity-logger - Visual Studio Code","activity":"PC作業記録ツールのIdle判定処理を実装","project":"pc-activity-logger","category":"development","detail":"main.pyとwindows.pyを開き、Windowsの最終入力時刻を使ったスキップ処理を確認している","confidence":0.95}
```

## テスト

セットアップ後、次のコマンドでテストを実行できます。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## トラブルシューティング

### `400 Bad Request`

- `openwebui.model`がOpenWebUIのモデルIDと完全に一致しているか確認してください。
- 対象モデルが画像入力に対応しているか確認してください。
- `base_url`が`http://HOST:PORT/api`形式になっているか確認してください。

### `OpenWebUI message content was unusable`

モデルが空の応答を返した場合に発生します。本ツールは自動的に1回だけ再試行します。繰り返す場合はOpenWebUIとモデルサーバーのログ、`max_tokens`、モデルのJSON Schema対応を確認してください。

### `No foreground window is available`

ロック画面、非対話セッション、タスクの実行ユーザーが異なる場合に発生することがあります。タスクスケジューラでは「ユーザーがログオンしている場合のみ実行」を選択してください。

### `Skipping unchanged screen`

エラーではありません。前回解析した画面とほぼ同じため、API呼び出しを省略しています。`same_screen_force_after_sec`を経過すると同じ画面でも再解析します。

## プライバシーとセキュリティ

- `config.yaml`、`data/`、`.venv/`、ログはGit対象外です。
- APIキーをREADME、Issue、ログへ貼り付けないでください。
- OpenWebUIまでの通信経路とデータ保存先を保護してください。
- Windows側には前面画像とモニター全体画像が保存されます。
- API失敗時はトラブルシューティングのためローカル画像が残ります。
- JSONL保存成功後はOpenWebUI側の一時画像を削除します。
- 解析失敗や強制終了のタイミングによっては、OpenWebUI側に一時ファイルが残る可能性があります。

## 現在未実装の機能

- Windows側スクリーンショットの保存期限による自動削除
- アプリ名・ウィンドウタイトルによる撮影除外リスト
- OpenWebUIに残った古い一時ファイルの起動時クリーンアップ
- 日報・週報の自動生成
- プロジェクトへの確定的な紐付け

## ライセンス

[MIT License](LICENSE)
