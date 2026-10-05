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
- 解析の成否にかかわらないOpenWebUI一時ファイル削除
- キーボード・マウスのIdle時間によるスキップ
- ロック画面、セキュアデスクトップ、切断セッションの検出
- 知覚ハッシュによる同一画面スキップ
- アプリ名・ウィンドウタイトルによる撮影前の除外
- Windows GUIからの設定、接続テスト、記録開始・停止
- システムトレイ常駐とログオン時の自動起動
- Windows Credential ManagerへのAPIキー保存
- GUIからの解析用システムプロンプト編集と既定値復元
- PyInstallerによるポータブルEXE生成

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

OpenWebUI側の一時画像は解析の成否にかかわらず処理周期の最後に削除します。Windows側の画像は証跡として`data/`に保存されます。

## 必要環境

- Windows 10またはWindows 11
- 画像入力に対応したモデルを登録済みのOpenWebUI
- OpenWebUI APIキー
- OpenWebUIへ接続できるネットワーク環境

ソースコードから実行またはEXEをビルドする場合はPython 3.10以上が必要です。ビルド済みEXEを実行するだけならPythonは不要です。

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

## GUI版

### ソースコードから起動

GUI用の依存パッケージをインストールします。

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-gui.txt
```

次にGUIを起動します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run-gui.ps1
```

初回起動後は次の順に操作します。

1. OpenWebUI Base URLとAPIキーを入力
2. 「接続テスト」で利用可能なモデルを取得
3. Visionモデルと撮影・保存設定を確認
4. 必要に応じて「システムプロンプト」タブの指示を編集
5. 「設定を保存」を押す
6. 「記録を開始」を押す

ウィンドウの閉じるボタンを押すと終了せず、システムトレイへ格納されます。終了する場合はトレイアイコンのメニューから「終了」を選択してください。

GUI版の設定ファイル、ログ、既定の保存データは次に作成されます。

```text
%LOCALAPPDATA%\PCActivityLogger\
├─ config.yaml
├─ data\
└─ logs\pc-activity-logger.log
```

APIキーは`config.yaml`へ書き込まず、現在のWindowsユーザーのCredential Managerへ保存します。CLI版のプロジェクト直下にある`config.yaml`とは独立しています。

### Windowsログオン時にGUIを自動起動

GUIの「Windowsログオン時に自動起動」を有効にして設定を保存すると、現在のユーザーのタスクスケジューラへ登録されます。管理者権限は要求しませんが、組織のポリシーでタスク登録が禁止されている場合は利用できません。

自動起動時はウィンドウを表示せずトレイへ常駐し、撮影間隔分（既定180秒）待ってから最初の記録を開始します。これにより、ログオン直後のデスクトップ準備中には解析を実行しません。GUIで手動の「開始」を押した場合は即時実行します。ログオフまたはシャットダウン時にはWindowsによって終了されます。

### Windows EXEをビルド

次のスクリプトでPyInstallerのポータブル`onedir`版を生成します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-gui.ps1
```

生成物は次のディレクトリです。

```text
dist\PCActivityLogger\
├─ PCActivityLogger.exe
└─ _internal\
```

配布するときは`PCActivityLogger.exe`だけではなく、`PCActivityLogger`フォルダー全体をZIPなどで配布してください。初回起動は`PCActivityLogger.exe`をダブルクリックします。現時点ではコード署名を行わないため、組織のSmartScreenやアプリ制御ポリシーにより実行が止められる場合があります。

## 設定

以下はCLI版の設定です。生成された`config.yaml`を編集します。このファイルは`.gitignore`に含まれており、GitHubへは公開されません。GUI版は画面上で同じ項目を設定できます。

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
  excluded_app_names:
    - "1Password.exe"
  excluded_window_titles:
    - "機密プロジェクト"

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
| `system_prompt` | 画像解析時に`system`ロールで送信する指示。省略時は内蔵の既定値 |

Files、Chat Completions、Notesを含むすべてのOpenWebUI APIリクエストには、クライアント識別用として`User-Agent: pc-activity-logger`と`X-OpenWebUI-Client-User-Agent: pc-activity-logger`が固定で付与されます。OpenWebUIが標準`User-Agent`からモデル向けヘッダーを生成する場合にも、Langfuse上では`pc-activity-logger`として記録されます。これらの値は設定ファイルから変更できません。

### システムプロンプト

GUI版では「システムプロンプト」タブから解析指示を編集できます。既存の詳細な作業分類・プライバシー保護・JSON出力指示が初期値として表示されます。「既定のプロンプトに戻す」を押すと内蔵値へ復元できます。変更または復元した後は「設定を保存」を押してください。記録中に保存した場合、進行中の解析には影響せず次の周期から新しい設定を使用します。

時刻、画像識別子、アプリ名、ウィンドウタイトルはプロンプト欄とは別に毎回自動送信されます。GUIで保存したプロンプトは`%LOCALAPPDATA%\PCActivityLogger\config.yaml`の`openwebui.system_prompt`へ保存されます。CLI版でも同じキーを任意指定でき、省略した場合は内蔵の既定値を使用します。

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
| `excluded_app_names` | 撮影しないアプリ名の一覧。大文字・小文字を区別しない部分一致 |
| `excluded_window_titles` | 撮影しないウィンドウタイトルの一覧。大文字・小文字を区別しない部分一致 |

`same_screen_max_distance`は、まず既定値の`3`を推奨します。大きくすると、より変化のある画面も同一扱いになります。同一画面の比較状態はメモリ上だけに保持されるため、プログラム再起動後の最初の画面は必ず解析します。

### 撮影除外設定

GUI版では「開いているWindowから選択」を押すと、現在表示中のWindowをアプリ名とタイトルの一覧から選択できます。「アプリ全体を追加」または「このタイトルを追加」を選び、設定を保存してください。手入力する場合は「除外アプリ名」と「除外タイトル」へ1行1件で入力します。CLI版では上記の`excluded_app_names`と`excluded_window_titles`を使用します。いずれかの文字列が現在のアプリ名またはウィンドウタイトルに部分一致すると、スクリーンショット取得前にその周期を終了します。

一度追加して保存した対象は履歴として設定ファイルに残るため、そのWindowを閉じた後やPCを再起動した後も除外されます。タイトルは選択時の全文が追加されるので、日時やファイル名など変化する部分がある場合は、追加後に安定した部分だけへ短く編集してください。

たとえば`excluded_app_names`に`1Password.exe`を指定すると1Password全体を除外し、`excluded_window_titles`に`機密プロジェクト`を指定すると、その文字列をタイトルに含むChrome、Edge、VS Codeなどの画面を除外できます。除外された画面のアプリ名やタイトルはログへ出力しません。

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

GUI版では画面上の「Windowsログオン時に自動起動」を使えます。以下はCLI版を登録する手順です。

### スクリプトで登録（推奨）

セットアップと`config.yaml`の設定を済ませた後、通常権限のPowerShellで実行します。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\register-task.ps1
```

タスク名は`PC Activity Logger`です。現在のユーザーが次回ログオンしたとき、`.venv\Scripts\pythonw.exe`で画面を表示せずに起動します。既に同名タスクがある場合は現在の設定で更新します。

登録直後にも起動する場合：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\register-task.ps1 -StartNow
```

状態確認：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\status-task.ps1
```

停止して登録解除：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\unregister-task.ps1
```

実際には登録せず、登録内容だけを確認する場合は`-WhatIf`を指定できます。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\register-task.ps1 -WhatIf
```

社給PCなどでタスク登録がグループポリシーにより禁止されている場合、制限を回避せずIT管理者へ依頼してください。

### タスクスケジューラで手動登録

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

GUIを含む全依存関係が必要なため、先に`requirements-gui.txt`をインストールしてください。

GitHub ActionsのCIはpushとpull requestで起動し、Linux（Ubuntu）とWindowsの両方でPython 3.10・3.12を使って同じ全テストを実行します。依存関係の整合性とPythonソースのコンパイルも確認します。GUI関連テストは`QT_QPA_PLATFORM=offscreen`で実行し、APIキーや実OpenWebUIは不要です。

テストは合成画像と一時ディレクトリを使い、画面取得、OpenWebUI通信、Credential Manager、タスクスケジューラをモックします。OSによるテストのskipはありません。Windowsジョブの成功は、実デスクトップの撮影、ロック・切断状態の判定、システムトレイ、ログオン時の起動、Credential Managerへの実保存、EXEのビルド・実行を保証しません。これらはWindows実機で別途確認してください。Outlookとの実連携は本プロジェクトのCI対象に含まれません。

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

### `Skipping capture; foreground window matches an exclusion rule`

エラーではありません。現在のアプリ名またはウィンドウタイトルが撮影除外設定に一致したため、スクリーンショット取得とAPI呼び出しを省略しています。

## プライバシーとセキュリティ

- `config.yaml`、`data/`、`.venv/`、ログはGit対象外です。
- GUI版のAPIキーはWindows Credential Managerへ保存され、GUI版の`config.yaml`には含まれません。
- APIキーをREADME、Issue、ログへ貼り付けないでください。
- OpenWebUIまでの通信経路とデータ保存先を保護してください。
- Windows側には前面画像とモニター全体画像が保存されます。
- API失敗時はトラブルシューティングのためローカル画像が残ります。
- 解析失敗時もOpenWebUI側の一時画像を削除します。
- プロセス強制終了や通信障害のタイミングによっては、OpenWebUI側に一時ファイルが残る可能性があります。

## 現在未実装の機能

- Windows側スクリーンショットの保存期限による自動削除
- OpenWebUIに残った古い一時ファイルの起動時クリーンアップ
- 日報・週報の自動生成
- プロジェクトへの確定的な紐付け

## ライセンス

[MIT License](LICENSE)
