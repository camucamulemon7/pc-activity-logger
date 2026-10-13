from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from .gui_settings import (
    default_app_directory,
    default_config_path,
    read_values,
    save_values,
    stored_api_key,
)
from .main import CaptureState, CycleResult, run_once
from .history import record_timestamp
from .markdown import export_markdown
from .openwebui import OpenWebUIClient
from .prompts import DEFAULT_ANALYSIS_SYSTEM_PROMPT
from .windows import OpenWindow, enable_dpi_awareness, get_open_windows
from . import scheduler


LOGGER = logging.getLogger("pc_activity_logger")


class ActivityWorker(QThread):
    status_changed = Signal(str)
    cycle_completed = Signal(object)
    next_capture_at = Signal(float)

    def __init__(self, config: Any, initial_delay_sec: int = 0) -> None:
        super().__init__()
        self.config = config
        self.initial_delay_sec = initial_delay_sec
        self._stop_event = threading.Event()
        self._config_lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()

    def update_config(self, config: Any) -> None:
        with self._config_lock:
            self.config = config

    def current_config(self) -> Any:
        with self._config_lock:
            return self.config

    def run(self) -> None:
        state = CaptureState()
        if self.initial_delay_sec > 0:
            LOGGER.info(
                "Waiting %d seconds before the first capture after automatic startup",
                self.initial_delay_sec,
            )
            self.status_changed.emit(f"初回実行待機中（{self.initial_delay_sec}秒）")
            self.next_capture_at.emit(time.monotonic() + self.initial_delay_sec)
            if self._stop_event.wait(self.initial_delay_sec):
                LOGGER.info("GUI worker stopped during initial delay")
                self.status_changed.emit("停止中")
                return
        self.status_changed.emit("実行中")
        LOGGER.info(
            "GUI worker started; interval=%d seconds",
            self.current_config().capture.interval_sec,
        )
        active_config: Any | None = None
        client: OpenWebUIClient | None = None
        try:
            while not self._stop_event.is_set():
                cycle_config = self.current_config()
                if cycle_config != active_config:
                    if client is not None:
                        client.close()
                        client = None
                    client = OpenWebUIClient(cycle_config.openwebui)
                    active_config = cycle_config
                    # Changed prompts/models/storage require a fresh analysis.
                    # Saving equal values must preserve the existing hash.
                    state = CaptureState()
                assert client is not None
                started = time.monotonic()
                self.next_capture_at.emit(0.0)
                self.status_changed.emit("撮影・解析中")
                try:
                    result = run_once(cycle_config, client, state)
                    if isinstance(result, CycleResult):
                        self.cycle_completed.emit(result)
                except Exception:
                    LOGGER.exception("Capture cycle failed")
                    self.cycle_completed.emit(CycleResult("error"))
                remaining = max(
                    0.0,
                    cycle_config.capture.interval_sec - (time.monotonic() - started),
                )
                self.status_changed.emit("次回撮影待機中")
                self.next_capture_at.emit(time.monotonic() + remaining)
                if self._stop_event.wait(remaining):
                    break
        finally:
            if client is not None:
                client.close()
        LOGGER.info("GUI worker stopped")
        self.status_changed.emit("停止中")


class ConnectionWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, config: Any) -> None:
        super().__init__()
        self.config = config

    def run(self) -> None:
        client: OpenWebUIClient | None = None
        try:
            client = OpenWebUIClient(self.config.openwebui)
            self.succeeded.emit(client.list_models())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if client is not None:
                client.close()


class ExportWorker(QThread):
    succeeded = Signal(str, int, int)
    failed = Signal(str)

    def __init__(self, source: Path, destination: Path) -> None:
        super().__init__()
        self.source = source
        self.destination = destination

    def run(self) -> None:
        try:
            result = export_markdown(self.source, self.destination)
            self.succeeded.emit(str(self.destination), result.exported, result.skipped)
        except Exception as exc:
            self.failed.emit(str(exc))


class SignalLogHandler(logging.Handler):
    def __init__(self, callback: Any) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            self.handleError(record)


class WindowPickerDialog(QDialog):
    def __init__(self, windows: list[OpenWindow], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selection_kind: str | None = None
        self.setWindowTitle("開いているWindowから除外対象を選択")
        self.resize(760, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "対象を選び、アプリ全体または現在のタイトルを除外へ追加します。"
            )
        )
        self.table = QTableWidget(len(windows), 2)
        self.table.setHorizontalHeaderLabels(["アプリ名", "ウィンドウタイトル"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, window in enumerate(windows):
            app_item = QTableWidgetItem(window.app_name)
            app_item.setData(256, window)
            self.table.setItem(row, 0, app_item)
            self.table.setItem(row, 1, QTableWidgetItem(window.title))
        if windows:
            self.table.selectRow(0)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        app_button = QPushButton("アプリ全体を追加")
        title_button = QPushButton("このタイトルを追加")
        close_button = QPushButton("キャンセル")
        app_button.setEnabled(bool(windows))
        title_button.setEnabled(bool(windows))
        app_button.clicked.connect(lambda: self._choose("app"))
        title_button.clicked.connect(lambda: self._choose("title"))
        close_button.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(app_button)
        buttons.addWidget(title_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _choose(self, kind: str) -> None:
        if self.selected_window() is None:
            QMessageBox.warning(self, "未選択", "Windowを1件選択してください。")
            return
        self.selection_kind = kind
        self.accept()

    def selected_window(self) -> OpenWindow | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(256) if item is not None else None


class MainWindow(QMainWindow):
    log_received = Signal(str)

    def __init__(self, config_path: Path, start_background: bool = False) -> None:
        super().__init__()
        self.config_path = config_path
        self.worker: ActivityWorker | None = None
        self.connection_worker: ConnectionWorker | None = None
        self.export_worker: ExportWorker | None = None
        self._next_capture_deadline: float | None = None
        self.really_quit = False
        self.setWindowTitle("PC Activity Logger")
        self.resize(760, 680)
        self._build_ui()
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._update_countdown)
        self._build_tray()
        self._configure_logging()
        self._load_settings()
        try:
            self.autostart.setChecked(scheduler.is_registered())
        except Exception:
            LOGGER.exception("Could not read Task Scheduler state")
        if start_background and self.config_path.exists() and stored_api_key():
            self.start_logging(delay_first_capture=True)
            self.hide()

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)

        openwebui_box = QGroupBox("OpenWebUI")
        openwebui_form = QFormLayout(openwebui_box)
        self.base_url = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.timeout_sec = self._spin(1, 600)
        self.max_tokens = self._spin(64, 32768)
        self.max_retries = self._spin(0, 5)
        self.retry_backoff_sec = self._spin(0, 30)
        self.cleanup_temporary_files = QCheckBox("本ツールの古い一時画像を整理")
        self.temporary_file_max_age_hours = self._spin(1, 8760)
        openwebui_form.addRow("Base URL", self.base_url)
        openwebui_form.addRow("API Key", self.api_key)
        openwebui_form.addRow("モデル", self.model)
        openwebui_form.addRow("タイムアウト（秒）", self.timeout_sec)
        openwebui_form.addRow("最大トークン", self.max_tokens)
        openwebui_form.addRow("通信の再試行回数", self.max_retries)
        openwebui_form.addRow("再試行の初期待機（秒）", self.retry_backoff_sec)
        openwebui_form.addRow("残留画像の整理", self.cleanup_temporary_files)
        openwebui_form.addRow("一時画像の保持（時間）", self.temporary_file_max_age_hours)

        capture_box = QGroupBox("撮影・スキップ")
        capture_form = QFormLayout(capture_box)
        self.interval_sec = self._spin(1, 86400)
        self.startup_delay_min = self._spin(0, 1440)
        self.jpeg_quality = self._spin(1, 95)
        self.idle_threshold_sec = self._spin(0, 86400)
        self.skip_same_screen = QCheckBox("有効")
        self.same_screen_max_distance = self._spin(0, 64)
        self.same_screen_force_after_sec = self._spin(1, 86400)
        self.skip_unavailable_session = QCheckBox("ロック・切断中はスキップ")
        self.excluded_app_names = QPlainTextEdit()
        self.excluded_app_names.setPlaceholderText("例: 1Password.exe（1行1件）")
        self.excluded_app_names.setMaximumHeight(70)
        self.excluded_window_titles = QPlainTextEdit()
        self.excluded_window_titles.setPlaceholderText("例: シークレット情報（1行1件）")
        self.excluded_window_titles.setMaximumHeight(70)
        self.pick_window_button = QPushButton("開いているWindowから選択")
        self.pick_window_button.clicked.connect(self.pick_open_window)
        capture_form.addRow("撮影間隔（秒）", self.interval_sec)
        capture_form.addRow("自動起動後の待機（分、0で即時）", self.startup_delay_min)
        capture_form.addRow("JPEG品質", self.jpeg_quality)
        capture_form.addRow("Idle判定（秒、0で無効）", self.idle_threshold_sec)
        capture_form.addRow("同一画面スキップ", self.skip_same_screen)
        capture_form.addRow("同一判定距離", self.same_screen_max_distance)
        capture_form.addRow("強制再解析（秒）", self.same_screen_force_after_sec)
        capture_form.addRow("セッション判定", self.skip_unavailable_session)
        capture_form.addRow("除外アプリ名（部分一致）", self.excluded_app_names)
        capture_form.addRow("除外タイトル（部分一致）", self.excluded_window_titles)
        capture_form.addRow("", self.pick_window_button)

        storage_box = QGroupBox("保存・連携")
        storage_grid = QGridLayout(storage_box)
        self.data_dir = QLineEdit()
        browse = QPushButton("参照")
        browse.clicked.connect(self.browse_data_dir)
        self.notes_enabled = QCheckBox("OpenWebUI Noteへ追記")
        self.markdown_enabled = QCheckBox("記録ごとのObsidian Markdownも保存")
        self.retention_days = self._spin(0, 3650)
        self.obsidian_vault = QLineEdit()
        browse_vault = QPushButton("参照")
        browse_vault.clicked.connect(self.browse_obsidian_vault)
        self.notes_title_prefix = QLineEdit()
        self.autostart = QCheckBox("Windowsログオン時に自動起動")
        storage_grid.addWidget(QLabel("保存先"), 0, 0)
        storage_grid.addWidget(self.data_dir, 0, 1)
        storage_grid.addWidget(browse, 0, 2)
        storage_grid.addWidget(self.markdown_enabled, 1, 1, 1, 2)
        storage_grid.addWidget(QLabel("Obsidian Vault保存先"), 2, 0)
        storage_grid.addWidget(self.obsidian_vault, 2, 1)
        storage_grid.addWidget(browse_vault, 2, 2)
        storage_grid.addWidget(self.notes_enabled, 3, 1, 1, 2)
        storage_grid.addWidget(QLabel("Noteタイトル接頭辞"), 4, 0)
        storage_grid.addWidget(self.notes_title_prefix, 4, 1, 1, 2)
        storage_grid.addWidget(QLabel("画像の保持日数（0で無期限）"), 5, 0)
        storage_grid.addWidget(self.retention_days, 5, 1, 1, 2)
        storage_grid.addWidget(self.autostart, 6, 1, 1, 2)

        buttons = QHBoxLayout()
        self.test_button = QPushButton("接続テスト")
        self.save_button = QPushButton("設定を保存")
        self.start_button = QPushButton("開始")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.test_button.clicked.connect(self.test_connection)
        self.save_button.clicked.connect(self.save_settings)
        self.start_button.clicked.connect(self.start_logging)
        self.stop_button.clicked.connect(self.stop_logging)
        for button in (
            self.test_button,
            self.save_button,
            self.start_button,
            self.stop_button,
        ):
            buttons.addWidget(button)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("状態:"))
        self.status = QLabel("停止中")
        status_row.addWidget(self.status)
        status_row.addStretch()

        settings_layout.addWidget(openwebui_box)
        settings_layout.addWidget(capture_box)
        settings_layout.addWidget(storage_box)
        status_box = QGroupBox("記録状況")
        status_layout = QVBoxLayout(status_box)
        status_layout.addLayout(status_row)
        self.last_decision = QLabel("直近の判定: まだ実行していません")
        self.last_record = QLabel("直近の記録: なし")
        self.last_record.setTextFormat(Qt.TextFormat.PlainText)
        self.last_record.setWordWrap(True)
        self.next_capture = QLabel("次回撮影: —")
        status_layout.addWidget(self.last_decision)
        status_layout.addWidget(self.last_record)
        status_layout.addWidget(self.next_capture)
        settings_layout.insertWidget(0, status_box)
        settings_layout.addLayout(buttons)
        settings_layout.addStretch()

        prompt_page = QWidget()
        prompt_layout = QVBoxLayout(prompt_page)
        prompt_layout.addWidget(
            QLabel(
                "画像解析時にsystemロールで送信する指示です。"
                "時刻・アプリ名・タイトルは別途自動で送信されます。"
            )
        )
        self.system_prompt = QPlainTextEdit()
        self.system_prompt.setPlaceholderText("解析用システムプロンプト")
        prompt_layout.addWidget(self.system_prompt)
        restore_prompt_button = QPushButton("既定のプロンプトに戻す")
        restore_prompt_button.clicked.connect(self.restore_default_system_prompt)
        prompt_layout.addWidget(restore_prompt_button)

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_description = QLabel(
            "1件の記録につき1つのMarkdownを作り、重要語等のノートで関連付けます。\n"
            "APIキーやOpenWebUIへの接続は不要です。\n"
            "Obsidian Vaultのルートフォルダーを保存先に選択してください。"
        )
        history_description.setWordWrap(True)
        history_layout.addWidget(history_description)
        self.export_button = QPushButton("JSONLを選んでObsidianへ書き出す")
        self.export_button.clicked.connect(self.export_history)
        history_layout.addWidget(self.export_button)
        self.export_all_button = QPushButton("保存先の全履歴をObsidianへ書き出す")
        self.export_all_button.clicked.connect(lambda: self.export_history(all_history=True))
        history_layout.addWidget(self.export_all_button)
        self.export_status = QLabel("")
        self.export_status.setWordWrap(True)
        history_layout.addWidget(self.export_status)
        history_layout.addStretch()

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        clear_button = QPushButton("表示をクリア")
        clear_button.clicked.connect(self.log_view.clear)
        log_layout.addWidget(clear_button)
        open_logs_button = QPushButton("ログフォルダーを開く")
        open_logs_button.clicked.connect(self.open_log_folder)
        log_layout.addWidget(open_logs_button)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_page)
        tabs.addTab(settings_scroll, "設定")
        tabs.addTab(prompt_page, "システムプロンプト")
        tabs.addTab(history_page, "履歴")
        tabs.addTab(log_page, "ログ")
        self.setCentralWidget(tabs)

    @staticmethod
    def _spin(minimum: int, maximum: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    def _build_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        show_action = QAction("設定を開く", self)
        start_action = QAction("開始", self)
        stop_action = QAction("停止", self)
        quit_action = QAction("終了", self)
        show_action.triggered.connect(self.show_window)
        start_action.triggered.connect(self.start_logging)
        stop_action.triggered.connect(self.stop_logging)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(show_action)
        menu.addAction(start_action)
        menu.addAction(stop_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def _configure_logging(self) -> None:
        app_dir = default_app_directory()
        log_dir = app_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = RotatingFileHandler(
            log_dir / "pc-activity-logger.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        gui_handler = SignalLogHandler(self.log_received.emit)
        gui_handler.setFormatter(formatter)
        LOGGER.setLevel(logging.INFO)
        if not any(isinstance(handler, RotatingFileHandler) for handler in LOGGER.handlers):
            LOGGER.addHandler(file_handler)
            LOGGER.addHandler(gui_handler)
        self.log_received.connect(self.log_view.appendPlainText)

    def _load_settings(self) -> None:
        try:
            values = read_values(self.config_path)
            self.base_url.setText(str(values["base_url"]))
            self.api_key.setText(stored_api_key())
            self.model.setCurrentText(str(values["model"]))
            self.timeout_sec.setValue(int(values["timeout_sec"]))
            self.max_tokens.setValue(int(values["max_tokens"]))
            self.max_retries.setValue(int(values["max_retries"]))
            self.retry_backoff_sec.setValue(int(values["retry_backoff_sec"]))
            self.cleanup_temporary_files.setChecked(bool(values["cleanup_temporary_files"]))
            self.temporary_file_max_age_hours.setValue(int(values["temporary_file_max_age_hours"]))
            self.system_prompt.setPlainText(str(values["system_prompt"]))
            self.interval_sec.setValue(int(values["interval_sec"]))
            self.startup_delay_min.setValue(int(values["startup_delay_min"]))
            self.jpeg_quality.setValue(int(values["jpeg_quality"]))
            self.idle_threshold_sec.setValue(int(values["idle_threshold_sec"]))
            self.skip_same_screen.setChecked(bool(values["skip_same_screen"]))
            self.same_screen_max_distance.setValue(
                int(values["same_screen_max_distance"])
            )
            self.same_screen_force_after_sec.setValue(
                int(values["same_screen_force_after_sec"])
            )
            self.skip_unavailable_session.setChecked(
                bool(values["skip_unavailable_session"])
            )
            self.excluded_app_names.setPlainText(
                "\n".join(str(item) for item in values["excluded_app_names"])
            )
            self.excluded_window_titles.setPlainText(
                "\n".join(str(item) for item in values["excluded_window_titles"])
            )
            self.data_dir.setText(str(values["data_dir"]))
            self.markdown_enabled.setChecked(bool(values["markdown_enabled"]))
            self.retention_days.setValue(max(0, int(values["retention_days"])))
            self.obsidian_vault.setText(str(values["obsidian_vault"]))
            self.notes_enabled.setChecked(bool(values["notes_enabled"]))
            self.notes_title_prefix.setText(str(values["notes_title_prefix"]))
        except Exception as exc:
            QMessageBox.critical(self, "設定エラー", str(exc))

    def _values(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url.text(),
            "model": self.model.currentText(),
            "timeout_sec": self.timeout_sec.value(),
            "max_tokens": self.max_tokens.value(),
            "max_retries": self.max_retries.value(),
            "retry_backoff_sec": self.retry_backoff_sec.value(),
            "cleanup_temporary_files": self.cleanup_temporary_files.isChecked(),
            "temporary_file_max_age_hours": self.temporary_file_max_age_hours.value(),
            "system_prompt": self.system_prompt.toPlainText(),
            "interval_sec": self.interval_sec.value(),
            "startup_delay_min": self.startup_delay_min.value(),
            "jpeg_quality": self.jpeg_quality.value(),
            "idle_threshold_sec": self.idle_threshold_sec.value(),
            "skip_same_screen": self.skip_same_screen.isChecked(),
            "same_screen_max_distance": self.same_screen_max_distance.value(),
            "same_screen_force_after_sec": self.same_screen_force_after_sec.value(),
            "skip_unavailable_session": self.skip_unavailable_session.isChecked(),
            "excluded_app_names": self._nonempty_lines(self.excluded_app_names),
            "excluded_window_titles": self._nonempty_lines(
                self.excluded_window_titles
            ),
            "data_dir": self.data_dir.text(),
            "markdown_enabled": self.markdown_enabled.isChecked(),
            "retention_days": self.retention_days.value(),
            "obsidian_vault": self.obsidian_vault.text(),
            "notes_enabled": self.notes_enabled.isChecked(),
            "notes_title_prefix": self.notes_title_prefix.text(),
        }

    @staticmethod
    def _nonempty_lines(widget: QPlainTextEdit) -> list[str]:
        return [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]

    def save_settings(self, show_success: bool = True) -> Any | None:
        try:
            config = save_values(
                self.config_path, self._values(), self.api_key.text()
            )
            if self.worker and self.worker.isRunning():
                self.worker.update_config(config)
                LOGGER.info("Updated running worker configuration")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))
            return None
        try:
            registered = scheduler.is_registered()
            if self.autostart.isChecked() and not registered:
                scheduler.register(self.config_path)
            elif not self.autostart.isChecked() and registered:
                scheduler.unregister()
            if show_success:
                QMessageBox.information(self, "保存完了", "設定を保存しました。")
            return config
        except Exception as exc:
            QMessageBox.critical(
                self, "自動起動設定エラー",
                f"記録設定は保存されましたが、自動起動設定の更新に失敗しました。\n{exc}",
            )
            return config

    def test_connection(self) -> None:
        config = self.save_settings(show_success=False)
        if config is None:
            return
        self.test_button.setEnabled(False)
        self.connection_worker = ConnectionWorker(config)
        self.connection_worker.succeeded.connect(self._connection_succeeded)
        self.connection_worker.failed.connect(self._connection_failed)
        self.connection_worker.finished.connect(
            lambda: self.test_button.setEnabled(True)
        )
        self.connection_worker.start()

    def _connection_succeeded(self, models: list[str]) -> None:
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(current)
        QMessageBox.information(
            self,
            "接続成功",
            f"OpenWebUIへ接続できました。利用可能モデル: {len(models)}件",
        )

    def _connection_failed(self, message: str) -> None:
        QMessageBox.critical(self, "接続失敗", message)

    def start_logging(
        self, _checked: bool = False, delay_first_capture: bool = False
    ) -> None:
        if self.worker and self.worker.isRunning():
            return
        config = self.save_settings(show_success=False)
        if config is None:
            self.show_window()
            return
        initial_delay = config.capture.startup_delay_min * 60 if delay_first_capture else 0
        self.worker = ActivityWorker(config, initial_delay_sec=initial_delay)
        self.worker.status_changed.connect(self.status.setText)
        self.worker.cycle_completed.connect(self._cycle_completed)
        self.worker.next_capture_at.connect(self._set_next_capture)
        self.worker.finished.connect(self._worker_finished)
        self.countdown_timer.start(1000)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.tray.showMessage("PC Activity Logger", "記録を開始しました")

    def stop_logging(self) -> None:
        if self.worker and self.worker.isRunning():
            self.status.setText("停止処理中")
            self.worker.stop()

    def _worker_finished(self) -> None:
        self.countdown_timer.stop()
        self._set_next_capture(0.0)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText("停止中")
        self.tray.showMessage("PC Activity Logger", "記録を停止しました")

    def _cycle_completed(self, result: CycleResult) -> None:
        captions = {
            "recorded": "記録を保存しました", "idle": "Idle中のためスキップ",
            "unavailable": "ロック・切断中のためスキップ", "excluded": "除外条件に一致したためスキップ",
            "unsafe": "前面領域が小さい、または不明のためスキップ", "unchanged": "同一画面のためスキップ",
            "error": "処理に失敗しました。ログを確認してください",
        }
        self.last_decision.setText("直近の判定: " + captions.get(result.reason, result.reason))
        if result.record:
            record = result.record
            at = record_timestamp(record).strftime("%H:%M:%S")
            self.last_record.setText(f"直近の記録: {at} — {record.get('app_name', '')} — {record['activity']}")

    def _set_next_capture(self, deadline: float) -> None:
        self._next_capture_deadline = deadline if deadline > 0 else None
        self._update_countdown()

    def _update_countdown(self) -> None:
        if self._next_capture_deadline is None:
            self.next_capture.setText("次回撮影: —")
        else:
            seconds = max(0, math.ceil(self._next_capture_deadline - time.monotonic()))
            self.next_capture.setText(f"次回撮影まで: {seconds}秒")

    def open_log_folder(self) -> None:
        try:
            directory = default_app_directory() / "logs"
            directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve()))):
                raise OSError("ログフォルダーを開けませんでした。")
        except OSError as exc:
            QMessageBox.critical(self, "ログフォルダーエラー", str(exc))

    def browse_data_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "保存先を選択", self.data_dir.text()
        )
        if selected:
            self.data_dir.setText(selected)

    def browse_obsidian_vault(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Obsidian Vaultのルートフォルダーを選択", self.obsidian_vault.text()
        )
        if selected:
            self.obsidian_vault.setText(selected)

    def export_history(self, _checked: bool = False, all_history: bool = False) -> None:
        if self.export_worker and self.export_worker.isRunning():
            return
        if all_history:
            source = self.data_dir.text()
        else:
            source, _ = QFileDialog.getOpenFileName(
                self, "書き出す履歴を選択", self.data_dir.text(), "履歴 JSONL (*.jsonl)"
            )
        if not source:
            return
        destination = QFileDialog.getExistingDirectory(
            self, "Obsidian Vaultのルートフォルダーを選択", self.obsidian_vault.text()
        )
        if not destination:
            return
        output_path = Path(destination)
        self.export_worker = ExportWorker(Path(source), output_path)
        self.export_worker.succeeded.connect(self._export_succeeded)
        self.export_worker.failed.connect(self._export_failed)
        self.export_worker.finished.connect(self._export_finished)
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        self.export_status.setText("Markdownへ書き出しています…")
        self.export_worker.start()

    def _export_finished(self) -> None:
        self.export_button.setEnabled(True)
        self.export_all_button.setEnabled(True)

    def _export_succeeded(self, destination: str, count: int, skipped: int) -> None:
        self.export_status.setText(f"{count}件を書き出しました。破損レコードのスキップ: {skipped}件\n{destination}")

    def _export_failed(self, message: str) -> None:
        self.export_status.setText("書き出しに失敗しました。")
        QMessageBox.critical(self, "履歴の書き出しエラー", message)

    def restore_default_system_prompt(self) -> None:
        self.system_prompt.setPlainText(DEFAULT_ANALYSIS_SYSTEM_PROMPT)
        self.status.setText("既定のプロンプトへ戻しました。設定を保存してください")

    def pick_open_window(self) -> None:
        try:
            windows = get_open_windows()
        except Exception as exc:
            QMessageBox.critical(self, "Window一覧の取得失敗", str(exc))
            return
        if not windows:
            QMessageBox.information(self, "Windowなし", "選択可能なWindowがありません。")
            return
        dialog = WindowPickerDialog(windows, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        window = dialog.selected_window()
        if window is None:
            return
        if dialog.selection_kind == "app":
            self._append_unique_line(self.excluded_app_names, window.app_name)
        else:
            self._append_unique_line(self.excluded_window_titles, window.title)
        self.status.setText("除外対象を追加しました。設定を保存してください")

    @staticmethod
    def _append_unique_line(widget: QPlainTextEdit, value: str) -> None:
        values = MainWindow._nonempty_lines(widget)
        if value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
            widget.setPlainText("\n".join(values))

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.really_quit:
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "PC Activity Logger", "バックグラウンドで動作しています"
            )

    def quit_application(self) -> None:
        self.really_quit = True
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        for worker in (self.connection_worker, self.export_worker):
            if worker and worker.isRunning():
                worker.wait()
        QApplication.instance().quit()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PC Activity Logger GUI")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    enable_dpi_awareness()
    app = QApplication(sys.argv[:1])
    if args.smoke_test:
        stored_api_key()
        return 0
    app.setApplicationName("PC Activity Logger")
    app.setOrganizationName("PCActivityLogger")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(args.config, start_background=args.background)
    if not args.background or not args.config.exists() or not stored_api_key():
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
