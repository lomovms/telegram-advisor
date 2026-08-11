from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import html
import json
import re
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QPoint, QRect, QSize, Qt, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QPlainTextEdit,
    QProgressBar,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .analysis_client import AnalysisClient, AnalysisClientError
from .app_log import log_event
from .config import AppConfig
from .db import Database
from .models import AdvisorResult, ClientProfile
from .telegram_api import (
    TelegramApiError,
    download_telegram_avatar,
    import_messages_from_telegram,
    import_new_messages_from_telegram,
    listen_new_messages_from_telegram,
    send_telegram_message,
)
from .telegram_importer import TelegramExportError, load_export, save_clean_text_export


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, *args, pass_progress: bool = False) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.pass_progress = pass_progress

    def run(self) -> None:
        try:
            if self.pass_progress:
                self.finished.emit(self.fn(self.progress.emit, *self.args))
            else:
                self.finished.emit(self.fn(*self.args))
        except AnalysisClientError as exc:
            self.failed.emit(str(exc))
        except TelegramApiError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())


class TelegramLiveWorker(QObject):
    message = Signal(dict)
    read = Signal(int)
    failed = Signal(str)
    finished = Signal()
    progress = Signal(str)

    def __init__(self, config: AppConfig, peer: str, after_message_id: int = 0, session_suffix: str = "live") -> None:
        super().__init__()
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_suffix).strip("._") or "live"
        live_session = config.telegram_session_path.with_name(
            f"{config.telegram_session_path.stem}.{safe_suffix}{config.telegram_session_path.suffix}"
        )
        if config.telegram_session_path.exists() and not live_session.exists():
            live_session.write_bytes(config.telegram_session_path.read_bytes())
        self.config = replace(config, telegram_session_path=live_session)
        self.peer = peer
        self.after_message_id = max(0, after_message_id)
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            self.progress.emit(f"Live Telegram: добираю пропущенные сообщения для {self.peer}...")
            if self.after_message_id > 0:
                _chat_name, messages = import_new_messages_from_telegram(
                    self.config,
                    self.peer,
                    self.after_message_id,
                    progress=None,
                )
                for message in messages:
                    self._emit_message(message)
                if messages:
                    self.progress.emit(f"Live Telegram: добрал пропущенных сообщений: {len(messages)}")

            if self._stop_event.is_set():
                return
            self.progress.emit(f"Live Telegram: подписываюсь на новые сообщения {self.peer}...")
            listen_new_messages_from_telegram(
                self.config,
                self.peer,
                self._emit_message,
                on_read=self._emit_read,
                progress=self.progress.emit,
                stop_event=self._stop_event,
            )
        except TelegramApiError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()

    def _emit_message(self, message: dict) -> None:
        message_id = message.get("telegram_message_id")
        if message_id is not None:
            self.after_message_id = max(self.after_message_id, int(message_id))
        self.message.emit(message)

    def _emit_read(self, max_message_id: int) -> None:
        self.read.emit(max_message_id)


class AvatarLabel(QLabel):
    def __init__(self, size: int = 56) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self.setFixedSize(size, size)
        self.setObjectName("profileAvatar")

    def set_avatar(self, path: str) -> None:
        pixmap = QPixmap(path)
        self._pixmap = pixmap if not pixmap.isNull() else QPixmap()
        self.setVisible(not self._pixmap.isNull())
        self.update()

    def clear_avatar(self) -> None:
        self._pixmap = QPixmap()
        self.hide()
        self.update()

    def paintEvent(self, event) -> None:
        if self._pixmap.isNull():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(self.rect())
        painter.setClipPath(path)
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)


class TitleBar(QWidget):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("titleBar")
        self._drag_offset: QPoint | None = None

        self.app_button = QPushButton("App")
        self.app_button.setObjectName("titleMenuButton")
        self.app_menu = QMenu(self)
        self.app_button.setMenu(self.app_menu)

        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("windowTitle")

        self.minimize_button = QPushButton("—")
        self.minimize_button.setObjectName("windowControl")
        self.minimize_button.clicked.connect(window.showMinimized)

        self.maximize_button = QPushButton("□")
        self.maximize_button.setObjectName("windowControl")
        self.maximize_button.clicked.connect(self._toggle_maximized)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("windowClose")
        self.close_button.clicked.connect(window.close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(6)
        layout.addWidget(self.app_button)
        layout.addWidget(self.title, stretch=1)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def add_app_action(self, action: QAction) -> None:
        self.app_menu.addAction(action)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self.window.isMaximized():
                self.window.showNormal()
            self.window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_maximized(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
            self.maximize_button.setText("□")
        else:
            self.window.showMaximized()
            self.maximize_button.setText("❐")


class OverlayWindow(QWidget):
    rewrite_requested = Signal(str)
    send_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Advisor")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.CustomizeWindowHint
        )
        self.setFixedWidth(430)

        self.summary = _readonly_plain_text()
        self.strategy = _readonly_plain_text()
        self.risk = _readonly_plain_text()
        self.tone_advice = QLabel("")
        self.tone_advice.setObjectName("toneAdvice")
        self.tone_advice.setWordWrap(True)
        self.reply = QPlainTextEdit()
        self.reply.setObjectName("replyDraft")
        self.reply.setMinimumHeight(140)
        self.rewrite_comment = QPlainTextEdit()
        self.rewrite_comment.setPlaceholderText(
            "Что нужно учесть в ответе: цель, границы, желаемый исход..."
        )
        self.rewrite_comment.setMaximumHeight(86)

        self.tone = QComboBox()
        self.tone.addItems(["мой стиль", "стратегичный", "спокойный", "деловой", "жёсткий", "короткий"])
        self.tone.currentTextChanged.connect(self._tone_changed)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("primaryAction")
        self.copy_button.clicked.connect(self.copy_reply)
        self.rewrite_button = QPushButton("Переписать")
        self.rewrite_button.clicked.connect(self.request_rewrite)
        self.send_button = QPushButton("Отправить")
        self.send_button.clicked.connect(self.request_send)
        self.minimize_button = QPushButton("Свернуть")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.hide)

        top = QHBoxLayout()
        top.addWidget(QLabel("Тон"))
        top.addWidget(self.tone, stretch=1)
        top.addWidget(self.minimize_button)
        top.addWidget(self.close_button)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.rewrite_button)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.copy_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(QLabel("Ситуация"))
        layout.addWidget(self.summary)
        layout.addWidget(QLabel("Риск"))
        layout.addWidget(self.risk)
        layout.addWidget(QLabel("Лучший стиль"))
        layout.addWidget(self.tone_advice)
        layout.addWidget(QLabel("Стратегия"))
        layout.addWidget(self.strategy)
        layout.addWidget(QLabel("Черновик"))
        layout.addWidget(self.reply)
        layout.addWidget(QLabel("Моя цель / комментарий"))
        layout.addWidget(self.rewrite_comment)
        layout.addLayout(buttons)

        self._result: AdvisorResult | None = None

    def show_result(self, result: AdvisorResult) -> None:
        self._result = result
        self.summary.setPlainText(
            f"{result.situation_summary}\n\nНамерение: {result.client_intent}\nУверенность: {result.confidence:.2f}"
        )
        self.risk.setPlainText(result.risk)
        recommended_tone = result.effective_recommended_tone()
        self.tone_advice.setText(
            f"{recommended_tone}"
            + (f": {result.tone_reason}" if result.tone_reason else "")
        )
        self.strategy.setPlainText(
            result.recommended_strategy
            + ("\n\nНе делать:\n- " + "\n- ".join(result.do_not_do) if result.do_not_do else "")
        )
        tone_index = self.tone.findText(recommended_tone)
        if tone_index >= 0:
            self.tone.setCurrentIndex(tone_index)
        self._tone_changed(self.tone.currentText())
        self._place_right()
        self.show()
        self.raise_()
        self.activateWindow()

    def copy_reply(self) -> None:
        QGuiApplication.clipboard().setText(self.reply.toPlainText())

    def request_rewrite(self) -> None:
        self.rewrite_requested.emit(self.rewrite_comment.toPlainText().strip())

    def request_send(self) -> None:
        self.send_requested.emit(self.reply.toPlainText().strip())

    def _tone_changed(self, tone: str) -> None:
        if not self._result:
            return
        text = self._result.reply_for_tone(tone)
        if tone == "короткий" and len(text) > 260:
            text = text[:257].rstrip() + "..."
        self.reply.setPlainText(text)
        self._apply_reply_tone_style(tone)

    def _apply_reply_tone_style(self, tone: str) -> None:
        tone_kind = {
            "мой стиль": "mine",
            "стратегичный": "strategic",
            "спокойный": "soft",
            "деловой": "neutral",
            "жёсткий": "hard",
            "короткий": "short",
        }.get(tone, "neutral")
        self.reply.setProperty("toneKind", tone_kind)
        self.reply.style().unpolish(self.reply)
        self.reply.style().polish(self.reply)

    def _place_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        height = min(860, screen.height())
        self.setGeometry(screen.right() - self.width(), screen.top(), self.width(), height)


class LiveProfileDialog(QDialog):
    def __init__(self, parent: QWidget, profiles: list[ClientProfile], checked_ids: set[int], db: Database) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live-чаты")
        self.resize(480, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите чаты для live-слежения"))
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("liveProfiles")
        self.profile_list.setIconSize(QSize(36, 36))
        self.profile_list.setMinimumHeight(340)
        for profile in profiles:
            if profile.id is None:
                continue
            account = db.get_setting(f"telegram_account:{profile.id}", "work")
            account_label = "личный" if account == "personal" else "рабочий"
            item = QListWidgetItem(f"{profile.chat_name} ({account_label})")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, account)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if profile.id in checked_ids else Qt.CheckState.Unchecked)
            avatar_path = db.get_setting(f"telegram_avatar:{profile.id}")
            if avatar_path and Path(avatar_path).exists():
                item.setIcon(QIcon(avatar_path))
            item.setForeground(Qt.GlobalColor.green if account == "personal" else Qt.GlobalColor.cyan)
            self.profile_list.addItem(item)
        layout.addWidget(self.profile_list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def checked_ids(self) -> set[int]:
        ids: set[int] = set()
        for index in range(self.profile_list.count()):
            item = self.profile_list.item(index)
            profile_id = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and profile_id is not None:
                ids.add(int(profile_id))
        return ids


class MainWindow(QMainWindow):
    hotkey_triggered = Signal()
    live_message_received = Signal(int, dict)
    live_messages_read = Signal(int, int)
    live_failed_received = Signal(int, str)
    live_finished_received = Signal(int)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.db = Database(config.db_path)
        self.ai = AnalysisClient(config)
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._live_sessions: dict[int, tuple[threading.Thread, TelegramLiveWorker]] = {}
        self._live_new_count = 0
        self._telegram_read_max_by_profile: dict[int, int] = {}
        self._live_message_lines: list[str] = []
        self._analysis_runs = 0
        self._analysis_memory: list[str] = []
        self._result: AdvisorResult | None = None
        self._ai_read_profile_id: int | None = None
        self._ai_read_message_id: int = 0
        self._clear_message_input_after_send = False
        self._closing = False
        self._resize_margin = 8
        self._resize_edges: set[str] = set()
        self._resize_start_pos = QPoint()
        self._resize_start_geometry = QRect()
        self._splitter_sizes_save_timer = QTimer(self)
        self._splitter_sizes_save_timer.setSingleShot(True)
        self._splitter_sizes_save_timer.timeout.connect(self._save_main_splitter_sizes)

        self.setWindowTitle("Telegram Negotiation Advisor")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.title_bar = TitleBar(self)
        self.setMenuWidget(self.title_bar)
        self.resize(1240, 780)
        self.setMinimumSize(900, 560)

        self.account_combo = QComboBox()
        self.account_combo.addItem("рабочий", "work")
        self.account_combo.addItem("личный", "personal")

        self.contact_search = QLineEdit()
        self.contact_search.setPlaceholderText("Поиск")
        self.contact_search.setObjectName("contactSearch")
        self.contact_list = QListWidget()
        self.contact_list.setObjectName("contactList")
        self.contact_list.setIconSize(QSize(44, 44))
        self.contact_list.setMinimumWidth(290)
        self.contact_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.contact_list.customContextMenuRequested.connect(self._show_contact_context_menu)

        self.message_limit_label = QLabel()
        self.message_limit_slider = QSlider(Qt.Orientation.Horizontal)
        self.message_limit_slider.setRange(20, 500)
        self.message_limit_slider.setSingleStep(10)
        self.message_limit_slider.setPageStep(50)
        self.message_limit_slider.setFixedWidth(150)
        self.message_limit_slider.setToolTip("Сколько последних сообщений давать AI в контекст анализа.")
        self.message_limit_slider.setValue(self._stored_message_limit())
        self.message_limit_slider.valueChanged.connect(self._message_limit_changed)
        self._message_limit_changed(self.message_limit_slider.value())

        self.import_button = QPushButton("Export")
        self.import_button.clicked.connect(self.import_export)
        self.telegram_import_button = QPushButton("Telegram")
        self.telegram_import_button.clicked.connect(self.import_telegram_chat)
        self.update_messages_button = QPushButton("Обновить")
        self.update_messages_button.clicked.connect(self.update_telegram_messages)
        self.analyze_button = QPushButton("Сгенерировать")
        self.analyze_button.setObjectName("primaryAction")
        self.analyze_button.clicked.connect(self.analyze_current_screen)

        self.status = QLabel("Готово")
        self.status.setObjectName("statusLabel")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.chat_avatar = AvatarLabel(44)
        self.chat_avatar.hide()
        self.chat_title = QLabel("Выберите контакт")
        self.chat_title.setObjectName("chatTitle")
        self.chat_subtitle = QLabel("Импортируйте Telegram-чат или выберите уже импортированный контакт")
        self.chat_subtitle.setObjectName("chatSubtitle")
        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setObjectName("chatView")
        self.message_input = QPlainTextEdit()
        self.message_input.setObjectName("messageInput")
        self.message_input.setPlaceholderText("Сообщение...")
        self.message_input.setMaximumHeight(72)
        self.message_input.installEventFilter(self)
        self.send_message_button = QPushButton("Отправить")
        self.send_message_button.clicked.connect(self._send_manual_message)

        self.profile_preview = QTextEdit()
        self.profile_preview.setReadOnly(True)
        self.profile_avatar = AvatarLabel()
        self.profile_avatar.hide()
        self.refresh_avatar_button = QPushButton("Обновить аватар")
        self.refresh_avatar_button.clicked.connect(self.refresh_selected_avatar)

        self.user_style = QPlainTextEdit()
        self.user_style.setPlaceholderText(
            "Голос/лексика: сарказм, шутки, эмодзи, типичные фразы. Мягкость и стратегия задаются тоном..."
        )
        self.save_style_button = QPushButton("Сохранить стиль")
        self.save_style_button.clicked.connect(self.save_user_style)
        self.user_comment = QPlainTextEdit()
        self.user_comment.setPlaceholderText(
            "Например: хочу добиться предоплаты, мягко отказать от новых правок, зафиксировать срок..."
        )
        self.user_comment.setMaximumHeight(76)

        self.ai_tone = QComboBox()
        self.ai_tone.addItems(["мой стиль", "стратегичный", "спокойный", "деловой", "жёсткий", "короткий"])
        self.ai_tone.currentTextChanged.connect(self._ai_tone_changed)
        self.tone_advice = QLabel("AI ещё не считал этот диалог")
        self.tone_advice.setObjectName("toneAdvice")
        self.tone_advice.setWordWrap(True)
        self.reply_best = self._reply_editor("mine")
        self.reply_soft = self._reply_editor("soft")
        self.reply_hard = self._reply_editor("hard")
        self.summary = _readonly_plain_text()
        self.risk = _readonly_plain_text()
        self.strategy = _readonly_plain_text()
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_current_reply)
        self.insert_reply_button = QPushButton("В чат")
        self.insert_reply_button.clicked.connect(self.insert_current_reply)
        self.send_ai_button = QPushButton("Отправить")
        self.send_ai_button.clicked.connect(self.send_current_reply)
        self.rewrite_button = QPushButton("Переписать")
        self.rewrite_button.clicked.connect(lambda: self.rewrite_with_comment(self.user_comment.toPlainText().strip()))
        self.ai_unread_label = QLabel("ИИ не прочел диалог")
        self.ai_unread_label.setObjectName("aiUnreadStatus")
        self.ai_unread_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ai_unread_label.hide()

        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_panel.setMinimumWidth(290)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)
        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("Аккаунт"))
        account_row.addWidget(self.account_combo, stretch=1)
        account_row.addWidget(self.update_messages_button)
        left_layout.addLayout(account_row)
        left_layout.addWidget(self.contact_search)
        import_row = QHBoxLayout()
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.telegram_import_button)
        left_layout.addLayout(import_row)
        left_layout.addWidget(self.contact_list, stretch=1)

        chat_panel = QWidget()
        chat_panel.setObjectName("chatPanel")
        chat_panel.setMinimumWidth(300)
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        chat_header = QHBoxLayout()
        chat_header.setContentsMargins(10, 8, 10, 8)
        chat_header.setSpacing(8)
        chat_header.addWidget(self.chat_avatar)
        chat_title_box = QVBoxLayout()
        chat_title_box.setSpacing(2)
        chat_title_box.addWidget(self.chat_title)
        chat_title_box.addWidget(self.chat_subtitle)
        chat_header.addLayout(chat_title_box, stretch=1)
        chat_layout.addLayout(chat_header)
        chat_layout.addWidget(self.chat_view, stretch=1)
        composer = QHBoxLayout()
        composer.setContentsMargins(10, 8, 10, 10)
        composer.setSpacing(6)
        composer.addWidget(self.message_input, stretch=1)
        composer.addWidget(self.send_message_button)
        chat_layout.addLayout(composer)

        self.ai_tabs = QTabWidget()
        self.ai_tabs.setObjectName("aiTabs")
        self.ai_tabs.addTab(self.reply_best, "Лучший")
        self.ai_tabs.addTab(self.reply_soft, "Мягко")
        self.ai_tabs.addTab(self.reply_hard, "Жёстко")

        strategy_tab = QWidget()
        strategy_layout = QVBoxLayout(strategy_tab)
        strategy_layout.addWidget(QLabel("Ситуация"))
        strategy_layout.addWidget(self.summary)
        strategy_layout.addWidget(QLabel("Риск"))
        strategy_layout.addWidget(self.risk)
        strategy_layout.addWidget(QLabel("Стратегия"))
        strategy_layout.addWidget(self.strategy)
        self.ai_tabs.addTab(strategy_tab, "Разбор")

        profile_tab = QWidget()
        profile_layout = QVBoxLayout(profile_tab)
        profile_header = QHBoxLayout()
        profile_header.addWidget(self.profile_avatar)
        profile_header.addWidget(self.refresh_avatar_button)
        profile_header.addStretch(1)
        profile_layout.addLayout(profile_header)
        profile_layout.addWidget(self.profile_preview, stretch=1)
        self.ai_tabs.addTab(profile_tab, "Профиль")

        style_tab = QWidget()
        style_layout = QVBoxLayout(style_tab)
        style_layout.addWidget(QLabel("Мой голос / лексика"))
        style_layout.addWidget(self.user_style, stretch=1)
        style_layout.addWidget(self.save_style_button)
        limit_row = QHBoxLayout()
        limit_row.addWidget(self.message_limit_label)
        limit_row.addWidget(self.message_limit_slider, stretch=1)
        style_layout.addLayout(limit_row)
        self.ai_tabs.addTab(style_tab, "Стиль")

        right_panel = QWidget()
        right_panel.setObjectName("aiPanel")
        right_panel.setMinimumWidth(280)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)
        ai_header = QHBoxLayout()
        ai_title = QLabel("AI")
        ai_title.setObjectName("aiTitle")
        ai_header.addWidget(ai_title)
        ai_header.addWidget(self.analyze_button)
        right_layout.addLayout(ai_header)
        tone_row = QHBoxLayout()
        tone_row.addWidget(QLabel("Тон"))
        tone_row.addWidget(self.ai_tone, stretch=1)
        right_layout.addLayout(tone_row)
        right_layout.addWidget(self.tone_advice)
        right_layout.addWidget(self.ai_tabs, stretch=1)
        right_layout.addWidget(QLabel("Цель / комментарий"))
        right_layout.addWidget(self.user_comment)
        ai_actions = QHBoxLayout()
        ai_actions.addWidget(self.rewrite_button)
        ai_actions.addStretch(1)
        ai_actions.addWidget(self.insert_reply_button)
        ai_actions.addWidget(self.send_ai_button)
        ai_actions.addWidget(self.copy_button)
        right_layout.addLayout(ai_actions)
        right_layout.addWidget(self.ai_unread_label)
        right_layout.addWidget(self.progress)
        right_layout.addWidget(self.status)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setHandleWidth(12)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setOpaqueResize(True)
        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(chat_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([320, 610, 360])
        self._restore_main_splitter_sizes()
        for index in range(1, self.main_splitter.count()):
            self.main_splitter.handle(index).setToolTip("Потяните, чтобы изменить ширину панелей")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_splitter)

        root = QWidget()
        root.setObjectName("centralWidget")
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.contact_search.textChanged.connect(self._filter_contacts)
        self.contact_list.currentItemChanged.connect(self._profile_changed)
        self.account_combo.currentIndexChanged.connect(self._account_changed)
        self.hotkey_triggered.connect(self.analyze_current_screen)
        self.live_message_received.connect(self._live_message_received, Qt.ConnectionType.QueuedConnection)
        self.live_messages_read.connect(self._live_messages_read, Qt.ConnectionType.QueuedConnection)
        self.live_failed_received.connect(self._live_failed, Qt.ConnectionType.QueuedConnection)
        self.live_finished_received.connect(self._live_finished, Qt.ConnectionType.QueuedConnection)
        self.main_splitter.splitterMoved.connect(self._schedule_main_splitter_sizes_save)
        self._setup_tray()
        self._load_profiles()
        self._load_user_style()
        self._register_hotkey()
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0, self._start_live_telegram)

    def import_export(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Telegram export",
            str(Path.home()),
            "Telegram export (*.json *.html *.htm)",
        )
        if not filename:
            return
        path = Path(filename)

        def job(progress) -> tuple[ClientProfile, int, Path, str]:
            progress("Export: читаю файл...")
            chat_name, messages = load_export(path)
            if not messages:
                raise TelegramExportError("В export не найдено текстовых сообщений")
            return self._ingest_messages(chat_name, messages, path, progress=progress)

        self._run_worker(job, self._import_done, "Импорт и анализ истории...", pass_progress=True)

    def import_telegram_chat(self) -> None:
        account = self._selected_account()
        peer, ok = QInputDialog.getText(
            self,
            "Импорт Telegram",
            "Чат: @username, id или точное название",
        )
        peer_text = peer.strip()
        if not ok or not peer_text:
            return

        def job(progress) -> tuple[ClientProfile, int, Path, str]:
            telegram_config = self._telegram_config_for_account(account)
            chat_name, messages = import_messages_from_telegram(telegram_config, peer_text, progress=progress)
            result = self._ingest_messages(
                chat_name,
                messages,
                Path(f"{chat_name}.telegram"),
                progress=progress,
                telegram_peer=peer_text,
                telegram_account=account,
            )
            profile = result[0]
            if profile.id is not None and not self.db.get_setting(f"telegram_avatar:{profile.id}"):
                avatar_path = download_telegram_avatar(
                    telegram_config,
                    peer_text,
                    self.config.db_path.parent / "avatars",
                    progress=progress,
                )
                if avatar_path:
                    self.db.set_setting(f"telegram_avatar:{profile.id}", str(avatar_path))
            return result

        self._run_worker(job, self._import_done, "Импорт из Telegram...", pass_progress=True)

    def update_telegram_messages(self) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            QMessageBox.information(self, "Нужен профиль", "Выберите профиль Telegram для обновления сообщений.")
            return

        peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip()
        if not peer:
            QMessageBox.information(
                self,
                "Нужен импорт Telegram",
                "У этого профиля нет сохраненного Telegram-чата. Один раз сделайте полный Импорт Telegram.",
            )
            return

        after_id = self.db.max_telegram_message_id(profile.id)
        if after_id <= 0:
            QMessageBox.information(
                self,
                "Нужен полный импорт",
                "В старом профиле нет Telegram-курсора. Один раз сделайте Импорт Telegram, потом обновление будет читать только новые сообщения.",
            )
            return

        account = self._profile_account(profile)

        def job(progress) -> tuple[ClientProfile, int, Path, str]:
            chat_name, messages = import_new_messages_from_telegram(
                self._telegram_config_for_account(account),
                peer,
                after_id,
                progress=progress,
            )
            if messages:
                self.db.save_messages(profile.id, messages)
            updated_profile = profile
            all_messages = self.db.recent_messages(profile.id, limit=self.config.telegram_history_limit)
            if _profile_needs_ai_refresh(profile):
                progress("Telegram: пересчитываю старый эвристический профиль через AI...")
                updated_profile = self.ai.build_profile(chat_name or profile.chat_name, all_messages, profile)
                updated_profile.id = profile.id
                self.db.save_profile(updated_profile)
            clean_export_path = save_clean_text_export(
                all_messages,
                Path(f"{chat_name}.telegram"),
                export_name=chat_name,
            )
            return updated_profile, len(messages), clean_export_path, ""

        self._run_worker(job, self._import_done, "Telegram: обновляю новые сообщения...", pass_progress=True)

    def toggle_live_telegram(self, checked: bool) -> None:
        if checked:
            self._start_live_telegram()
        else:
            self._stop_live_telegram()

    def _start_live_telegram(self) -> None:
        profiles = self._live_capable_profiles()
        if not profiles:
            self.status.setText("Фоновая синхронизация: нет Telegram-импортов")
            return
        skipped_config = 0
        started = 0
        for profile in profiles:
            if profile.id is None or profile.id in self._live_sessions:
                continue
            peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip()
            if not peer:
                continue
            account = self._profile_account(profile)
            telegram_config = self._telegram_config_for_account(account)
            if not telegram_config.telegram_api_id or not telegram_config.telegram_api_hash:
                skipped_config += 1
                continue
            worker = TelegramLiveWorker(
                telegram_config,
                peer,
                self.db.max_telegram_message_id(profile.id),
                session_suffix=f"live-{profile.id}",
            )
            worker.progress.connect(self.status.setText, Qt.ConnectionType.QueuedConnection)
            worker.message.connect(
                lambda message, pid=profile.id: self.live_message_received.emit(pid, message),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.read.connect(
                lambda max_message_id, pid=profile.id: self.live_messages_read.emit(pid, max_message_id),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.failed.connect(
                lambda details, pid=profile.id: self.live_failed_received.emit(pid, details),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.finished.connect(
                lambda pid=profile.id: self.live_finished_received.emit(pid),
                Qt.ConnectionType.QueuedConnection,
            )
            thread = threading.Thread(
                target=worker.run,
                name=f"telegram-live-{profile.id}",
                daemon=True,
            )
            self._live_sessions[profile.id] = (thread, worker)
            thread.start()
            started += 1

        if started:
            self.status.setText(f"Фоновая синхронизация: слушаю чатов {len(self._live_sessions)}")
            self._render_profile()
            return
        if skipped_config and not self._live_sessions:
            self.status.setText("Фоновая синхронизация недоступна: заполните TELEGRAM_API_ID/API_HASH")
        elif self._live_sessions:
            self.status.setText(f"Фоновая синхронизация: слушаю чатов {len(self._live_sessions)}")

    def _live_capable_profiles(self) -> list[ClientProfile]:
        profiles: list[ClientProfile] = []
        for profile in self.db.list_profiles():
            if profile.id is None:
                continue
            if self.db.get_setting(f"telegram_peer:{profile.id}").strip():
                profiles.append(profile)
        return profiles

    def _stop_live_telegram(self) -> None:
        if not self._live_sessions:
            return
        for _thread, worker in list(self._live_sessions.values()):
            try:
                worker.stop()
            except RuntimeError:
                continue
        self.status.setText("Фоновая синхронизация: останавливаю...")

    def _stop_live_profile(self, profile_id: int) -> None:
        session = self._live_sessions.pop(profile_id, None)
        if not session:
            return
        _thread, worker = session
        try:
            worker.stop()
        except RuntimeError:
            pass

    def _shutdown_live_telegram(self, max_wait_ms: int = 700) -> None:
        sessions = list(self._live_sessions.values())
        if not sessions:
            return
        for _thread, worker in sessions:
            try:
                worker.stop()
            except RuntimeError:
                pass
        deadline = time.monotonic() + max(0, max_wait_ms) / 1000
        for thread, _worker in sessions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if thread.is_alive():
                thread.join(min(0.05, remaining))
        self._live_sessions = {
            profile_id: session
            for profile_id, session in self._live_sessions.items()
            if session[0].is_alive()
        }

    def _restore_main_splitter_sizes(self) -> None:
        raw_sizes = self.db.get_setting("ui_main_splitter_sizes")
        if not raw_sizes:
            return
        try:
            sizes = json.loads(raw_sizes)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if (
            isinstance(sizes, list)
            and len(sizes) == self.main_splitter.count()
            and all(isinstance(size, int) and size > 0 for size in sizes)
        ):
            self.main_splitter.setSizes(sizes)

    def _schedule_main_splitter_sizes_save(self, _position: int, _index: int) -> None:
        self._splitter_sizes_save_timer.start(250)

    def _save_main_splitter_sizes(self) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) == self.main_splitter.count() and all(size > 0 for size in sizes):
            self.db.set_setting("ui_main_splitter_sizes", json.dumps(sizes))

    def _live_message_received(self, profile_id: int, message: dict) -> None:
        if self._closing:
            return
        profile = self.db.get_profile(profile_id)
        if not profile:
            return
        self.db.save_messages(profile_id, [message])
        self._live_new_count += 1
        sender = str(message.get("sender", ""))
        self._update_contact_preview(profile_id, message)
        if self._selected_profile_id() == profile_id:
            self._render_recent_messages()
            self._render_profile()
            self._update_ai_unread_status(clear_stale=True)
        self.status.setText(f"Фоновая синхронизация: новых сообщений {self._live_new_count}; последнее от {sender}")

    def _live_messages_read(self, profile_id: int, max_message_id: int) -> None:
        if self._closing:
            return
        self._telegram_read_max_by_profile[profile_id] = max(
            max_message_id,
            self._telegram_read_max_by_profile.get(profile_id, 0),
        )
        self.db.mark_outgoing_messages_read(profile_id, max_message_id)
        if self._selected_profile_id() == profile_id:
            self._render_recent_messages()

    def _live_failed(self, profile_id: int, details: str) -> None:
        if self._closing:
            return
        profile = self.db.get_profile(profile_id)
        name = profile.chat_name if profile else str(profile_id)
        log_event(f"live telegram: failed for {name}: {details[:500]}")
        self.status.setText(f"Фоновая синхронизация: ошибка в {name}")

    def _live_finished(self, profile_id: int) -> None:
        if self._closing:
            self._live_sessions.pop(profile_id, None)
            return
        self._live_sessions.pop(profile_id, None)
        self._render_profile()
        if self._live_sessions:
            self.status.setText(f"Фоновая синхронизация: слушаю чатов {len(self._live_sessions)}")
        elif not self.status.text().startswith("Фоновая синхронизация: новых сообщений"):
            self.status.setText("Фоновая синхронизация остановлена")

    def _ingest_messages(
        self,
        chat_name: str,
        messages: list[dict],
        source_path: Path,
        progress=None,
        telegram_peer: str = "",
        telegram_account: str = "work",
    ) -> tuple[ClientProfile, int, Path, str]:
        if not messages:
            raise TelegramExportError("В чате не найдено текстовых сообщений")
        if progress:
            progress(f"Импорт: сохраняю очищенный текст ({len(messages)} сообщений)...")
        clean_export_path = save_clean_text_export(messages, source_path, export_name=chat_name)
        existing_profile = (
            self._get_profile_by_telegram_peer(telegram_peer, telegram_account)
            if telegram_peer
            else None
        ) or self._get_profile_by_chat_name_for_account(chat_name, telegram_account)
        fingerprint = _messages_fingerprint(messages)
        fingerprint_key = f"import_fingerprint:{chat_name}"
        previous_fingerprint = self.db.get_setting(fingerprint_key)
        user_style = ""

        if (
            existing_profile
            and previous_fingerprint == fingerprint
            and not _profile_needs_ai_refresh(existing_profile)
        ):
            if progress:
                progress("Импорт: история не изменилась, переиспользую профиль и стиль...")
            profile = existing_profile
            profile_id = existing_profile.id
            if profile_id is not None:
                self.db.replace_messages(profile_id, messages)
                if telegram_peer:
                    self.db.set_setting(f"telegram_peer:{profile_id}", telegram_peer)
                self.db.set_setting(f"telegram_account:{profile_id}", telegram_account)
            return profile, len(messages), clean_export_path, user_style

        if progress:
            if existing_profile and previous_fingerprint == fingerprint:
                progress("Импорт: старый эвристический профиль, пересчитываю через AI...")
            else:
                progress("Импорт: обновляю профиль через AI...")
        profile = self.ai.build_profile(chat_name, messages, existing_profile)
        if profile is None:
            profile = ClientProfile(
                id=existing_profile.id if existing_profile else None,
                chat_name=chat_name,
                agreements=existing_profile.agreements if existing_profile else "Профиль создан локально: AI-анализ истории не вернул данные.",
                payment_promises=existing_profile.payment_promises if existing_profile else "",
                disputed_points=existing_profile.disputed_points if existing_profile else "",
                behavior_patterns=f"Импортировано сообщений: {len(messages)}.",
                communication_style=existing_profile.communication_style if existing_profile else "",
                tone_recommendations=(
                    existing_profile.tone_recommendations
                    if existing_profile
                    else "Писать в сохраненном стиле пользователя и учитывать текущую цель ответа."
                ),
            )
        elif existing_profile and profile.id is None:
            profile.id = existing_profile.id

        previous_style = self.db.get_setting("user_style_profile")
        if progress:
            progress("Импорт: обновляю память голоса/лексики...")
        user_style = self.ai.build_user_style(chat_name, messages, previous_style)
        if progress:
            progress("Импорт: сохраняю сообщения в локальную базу...")
        profile_id = self.db.save_profile(profile)
        self.db.replace_messages(profile_id, messages)
        if telegram_peer:
            self.db.set_setting(f"telegram_peer:{profile_id}", telegram_peer)
        self.db.set_setting(f"telegram_account:{profile_id}", telegram_account)
        if user_style:
            self.db.set_setting("user_style_profile", user_style)
        self.db.set_setting(fingerprint_key, fingerprint)
        profile.id = profile_id
        return profile, len(messages), clean_export_path, user_style

    def analyze_current_screen(self) -> None:
        self._analyze_current_screen(self.user_comment.toPlainText().strip())

    def save_user_style(self) -> None:
        style = self.user_style.toPlainText().strip()
        self.db.set_setting("user_style_profile", style)
        self.status.setText("Стиль сохранен" if style else "Стиль очищен")

    def _reply_editor(self, tone_kind: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName("replyDraft")
        editor.setProperty("toneKind", tone_kind)
        editor.setPlaceholderText("Здесь появится вариант ответа после анализа диалога")
        editor.setMinimumHeight(220)
        return editor

    def _current_reply_editor(self) -> QPlainTextEdit:
        current = self.ai_tabs.currentWidget()
        if current in (self.reply_best, self.reply_soft, self.reply_hard):
            return current
        return self.reply_best

    def _current_reply_text(self) -> str:
        return self._current_reply_editor().toPlainText().strip()

    def copy_current_reply(self) -> None:
        text = self._current_reply_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.status.setText("Ответ скопирован")

    def insert_current_reply(self) -> None:
        text = self._current_reply_text()
        if text:
            self.message_input.setPlainText(text)
            self.status.setText("Ответ вставлен в поле сообщения")

    def send_current_reply(self) -> None:
        self.send_reply_to_telegram(self._current_reply_text())

    def _send_manual_message(self) -> None:
        text = self.message_input.toPlainText().strip()
        if not text:
            return
        self._clear_message_input_after_send = True
        self.send_reply_to_telegram(text)

    def _ai_tone_changed(self, tone: str) -> None:
        index_by_tone = {
            "мой стиль": 0,
            "стратегичный": 0,
            "деловой": 0,
            "короткий": 0,
            "спокойный": 1,
            "жёсткий": 2,
        }
        self.ai_tabs.setCurrentIndex(index_by_tone.get(tone, 0))

    def rewrite_with_comment(self, comment: str) -> None:
        if not comment:
            QMessageBox.information(
                self,
                "Нужен комментарий",
                "Напишите, какую цель или ограничение нужно учесть в новом ответе.",
            )
            return
        self.user_comment.setPlainText(comment)
        self._analyze_current_screen(comment)

    def send_reply_to_telegram(self, text: str) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            QMessageBox.information(self, "Нужен профиль", "Выберите профиль Telegram, которому отправлять ответ.")
            return
        if not text:
            QMessageBox.information(self, "Пустой текст", "В черновике нет текста для отправки.")
            return

        peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip()
        if not peer:
            peer_input, ok = QInputDialog.getText(
                self,
                "Отправка Telegram",
                "Чат: @username, id или точное название",
                text=profile.chat_name,
            )
            peer = peer_input.strip()
            if not ok or not peer:
                return
            self.db.set_setting(f"telegram_peer:{profile.id}", peer)

        def job(progress) -> tuple[int, dict]:
            sent_message = send_telegram_message(
                self._telegram_config_for_account(self._profile_account(profile)),
                peer,
                text,
                progress=progress,
            )
            return profile.id, sent_message

        self._run_worker(job, self._telegram_send_done, "Telegram: отправляю сообщение...", pass_progress=True)

    def _telegram_send_done(self, payload: tuple[int, dict]) -> None:
        profile_id, sent_message = payload
        self.db.save_messages(profile_id, [sent_message])
        self.db.mark_outgoing_messages_read(
            profile_id,
            self._telegram_read_max_by_profile.get(profile_id, 0),
        )
        self._update_contact_preview(profile_id, sent_message)
        if self._selected_profile_id() == profile_id:
            self._render_recent_messages()
            self._update_ai_unread_status()
        if self._clear_message_input_after_send:
            self.message_input.clear()
            self._clear_message_input_after_send = False
        self.status.setText("Telegram: сообщение отправлено")

    def refresh_selected_avatar(self) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            QMessageBox.information(self, "Нужен профиль", "Выберите Telegram-профиль для обновления аватарки.")
            return

        peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip()
        if not peer:
            peer_input, ok = QInputDialog.getText(
                self,
                "Обновить аватар",
                "Чат: @username, id или точное название",
                text=profile.chat_name,
            )
            peer = peer_input.strip()
            if not ok or not peer:
                return
            self.db.set_setting(f"telegram_peer:{profile.id}", peer)

        def job(progress) -> tuple[int, Path | None]:
            avatar_path = download_telegram_avatar(
                self._telegram_config_for_account(self._profile_account(profile)),
                peer,
                self.config.db_path.parent / "avatars",
                progress=progress,
            )
            return profile.id, avatar_path

        self._run_worker(job, self._avatar_refresh_done, "Telegram: обновляю аватарку...", pass_progress=True)

    def _avatar_refresh_done(self, payload: tuple[int, Path | None]) -> None:
        profile_id, avatar_path = payload
        if avatar_path:
            self.db.set_setting(f"telegram_avatar:{profile_id}", str(avatar_path))
            self.status.setText("Telegram: аватарка обновлена")
        else:
            self.status.setText("Telegram: у профиля нет доступной аватарки")
        self._render_profile()

    def _analyze_current_screen(self, user_comment: str) -> None:
        self._analysis_runs += 1
        run_id = self._analysis_runs
        log_event(f"analysis #{run_id}: button/hotkey received")
        profile = self._selected_profile()
        tone = self.ai_tone.currentText()
        user_style_profile = _voice_style_only(
            self.user_style.toPlainText().strip() or self.db.get_setting("user_style_profile")
        )

        def job(progress) -> AdvisorResult:
            synced_count = self._sync_selected_profile_messages(profile, progress)
            if synced_count:
                progress(f"Telegram: добавлено новых сообщений: {synced_count}")
            message_limit = self._analysis_message_limit()
            progress(f"Запрос #{run_id}: загружаю последние {message_limit} сообщений...")
            recent = self.db.recent_messages(profile.id, limit=message_limit) if profile and profile.id else []
            return self.ai.analyze_screenshot(
                b"",
                profile,
                recent,
                tone,
                previous_analysis_context=self._analysis_context(),
                user_comment=user_comment,
                user_style_profile=user_style_profile,
                progress=progress,
            )

        self._run_worker(job, self._show_analysis_result, f"Запрос #{run_id}: анализ истории Telegram...", pass_progress=True)

    def _show_analysis_result(self, result: AdvisorResult) -> None:
        log_event("analysis: result received")
        self._result = result
        profile = self._selected_profile()
        if profile and profile.id is not None:
            self._ai_read_profile_id = profile.id
            self._ai_read_message_id = self.db.max_telegram_message_id(profile.id)
        self._remember_analysis(result)
        self._render_recent_messages()
        self.summary.setPlainText(
            f"{result.situation_summary}\n\nНамерение: {result.client_intent}\nУверенность: {result.confidence:.2f}"
        )
        self.risk.setPlainText(result.risk)
        self.strategy.setPlainText(
            result.recommended_strategy
            + ("\n\nНе делать:\n- " + "\n- ".join(result.do_not_do) if result.do_not_do else "")
        )
        self.reply_best.setPlainText(result.best_reply)
        self.reply_soft.setPlainText(result.soft_reply or result.best_reply)
        self.reply_hard.setPlainText(result.hard_reply or result.best_reply)
        recommended_tone = result.effective_recommended_tone()
        self.tone_advice.setText(
            f"{recommended_tone}"
            + (f": {result.tone_reason}" if result.tone_reason else "")
        )
        tone_index = self.ai_tone.findText(recommended_tone)
        if tone_index >= 0:
            self.ai_tone.blockSignals(True)
            self.ai_tone.setCurrentIndex(tone_index)
            self.ai_tone.blockSignals(False)
        self._ai_tone_changed(self.ai_tone.currentText())
        self._update_ai_unread_status()
        self.status.setText("AI: варианты ответа обновлены")

    def _import_done(self, payload: tuple[ClientProfile, int, Path, str]) -> None:
        profile, count, clean_export_path, user_style = payload
        self._load_profiles(select_id=profile.id)
        self._load_user_style()
        self._render_recent_messages()
        self._update_ai_unread_status(clear_stale=True)
        self._start_live_telegram()
        style_note = "; стиль обновлен" if user_style else ""
        self.status.setText(f"Импортировано сообщений: {count}; очищенный файл: {clean_export_path}{style_note}")

    def _run_worker(self, fn, done_callback, status: str, pass_progress: bool = False) -> None:
        if self._thread is not None and self._thread.isRunning():
            log_event("worker: refused start because previous task is still running")
            QMessageBox.information(self, "Задача выполняется", "Дождитесь завершения текущей операции.")
            return
        log_event(f"worker: starting task: {status}")
        self.status.setText(status)
        self.progress.show()
        self.import_button.setEnabled(False)
        self.telegram_import_button.setEnabled(False)
        self.update_messages_button.setEnabled(False)
        self.refresh_avatar_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.send_message_button.setEnabled(False)
        self.send_ai_button.setEnabled(False)
        self.rewrite_button.setEnabled(False)

        self._thread = QThread()
        self._worker = Worker(fn, pass_progress=pass_progress)
        thread = self._thread
        worker = self._worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.status.setText)
        worker.finished.connect(done_callback)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._worker_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _worker_finished(self) -> None:
        log_event("worker: finished")
        self.progress.hide()
        self.import_button.setEnabled(True)
        self.telegram_import_button.setEnabled(True)
        self.update_messages_button.setEnabled(True)
        self.refresh_avatar_button.setEnabled(True)
        self.analyze_button.setEnabled(True)
        self.send_message_button.setEnabled(True)
        self.send_ai_button.setEnabled(True)
        self.rewrite_button.setEnabled(True)
        if not (
            self.status.text().startswith("Импортировано сообщений:")
            or self.status.text().startswith("Telegram: сообщение отправлено")
            or self.status.text().startswith("Telegram: аватарка")
            or self.status.text().startswith("Telegram: у профиля")
            or self.status.text().startswith("AI:")
            or self.status.text().startswith("Фоновая синхронизация:")
        ):
            self.status.setText("Готово")
        self._worker = None
        self._thread = None

    def _worker_failed(self, details: str) -> None:
        log_event(f"worker: failed: {details[:500]}")
        self._clear_message_input_after_send = False
        self.status.setText("Ошибка")
        QMessageBox.critical(self, "Ошибка", details[-3000:])

    def _load_profiles(self, select_id: int | None = None) -> None:
        selected_id = select_id if select_id is not None else self._selected_profile_id()
        selected_account = self._selected_account()
        self.contact_list.blockSignals(True)
        self.contact_list.clear()
        for profile in self.db.list_profiles():
            if profile.id is not None and self._profile_account(profile) == selected_account:
                self._add_contact_item(profile)
        self.contact_list.blockSignals(False)
        selected = self._select_contact_id(selected_id) if selected_id is not None else False
        if not selected and self.contact_list.count():
            self.contact_list.setCurrentRow(0)
        self._filter_contacts(self.contact_search.text())
        self._render_profile()
        self._render_recent_messages()
        self._update_ai_unread_status(clear_stale=True)
        self._apply_account_colors()

    def _add_contact_item(self, profile: ClientProfile) -> None:
        account = self._profile_account(profile)
        account_label = "личный" if account == "personal" else "рабочий"
        recent = self.db.recent_messages(profile.id, limit=30) if profile.id is not None else []
        preview = _contact_preview(profile, recent[-1], recent) if recent else "Нет сообщений"
        item = QListWidgetItem(f"{profile.chat_name}\n{preview}")
        item.setData(Qt.ItemDataRole.UserRole, profile.id)
        item.setData(Qt.ItemDataRole.UserRole + 1, account)
        item.setData(Qt.ItemDataRole.UserRole + 2, f"{profile.chat_name} {preview}".casefold())
        item.setToolTip(f"{profile.chat_name} ({account_label})")
        avatar_path = self.db.get_setting(f"telegram_avatar:{profile.id}") if profile.id is not None else ""
        if avatar_path and Path(avatar_path).exists():
            item.setIcon(QIcon(avatar_path))
        item.setForeground(Qt.GlobalColor.green if account == "personal" else Qt.GlobalColor.cyan)
        self.contact_list.addItem(item)

    def _select_contact_id(self, profile_id: int) -> bool:
        for index in range(self.contact_list.count()):
            item = self.contact_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == profile_id:
                self.contact_list.setCurrentRow(index)
                return True
        return False

    def _selected_profile_id(self) -> int | None:
        item = self.contact_list.currentItem()
        if not item:
            return None
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        return int(profile_id) if profile_id is not None else None

    def _show_contact_context_menu(self, position: QPoint) -> None:
        item = self.contact_list.itemAt(position)
        if not item:
            return
        self.contact_list.setCurrentItem(item)
        profile = self._selected_profile()
        if not profile or profile.id is None:
            return

        menu = QMenu(self)
        refresh_action = menu.addAction("Обновить")
        delete_action = menu.addAction("Удалить")
        selected_action = menu.exec(self.contact_list.mapToGlobal(position))
        if selected_action == refresh_action:
            self.update_telegram_messages()
        elif selected_action == delete_action:
            self._delete_selected_profile()

    def _delete_selected_profile(self) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            return
        answer = QMessageBox.question(
            self,
            "Удалить профиль",
            (
                f"Удалить «{profile.chat_name}» из приложения?\n\n"
                "Будут удалены локальный профиль, импортированные сообщения и настройки этого Telegram-чата."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        profile_id = profile.id
        self._stop_live_profile(profile_id)
        self.db.delete_profile(profile_id)
        if self._ai_read_profile_id == profile_id:
            self._ai_read_profile_id = None
            self._ai_read_message_id = 0
            self._result = None
        self._load_profiles()
        self._start_live_telegram()
        self.status.setText(f"Профиль удален: {profile.chat_name}")

    def _filter_contacts(self, text: str) -> None:
        needle = text.strip().casefold()
        first_visible = None
        for index in range(self.contact_list.count()):
            item = self.contact_list.item(index)
            haystack = str(item.data(Qt.ItemDataRole.UserRole + 2) or "")
            hidden = bool(needle and needle not in haystack)
            item.setHidden(hidden)
            if not hidden and first_visible is None:
                first_visible = index
        current = self.contact_list.currentItem()
        if current and not current.isHidden():
            return
        if first_visible is not None:
            self.contact_list.setCurrentRow(first_visible)

    def _update_contact_preview(self, profile_id: int, message: dict) -> None:
        profile = self.db.get_profile(profile_id)
        if not profile:
            return
        preview = _contact_preview(profile, message, [message])
        for index in range(self.contact_list.count()):
            item = self.contact_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == profile_id:
                item.setText(f"{profile.chat_name}\n{preview}")
                item.setData(Qt.ItemDataRole.UserRole + 2, f"{profile.chat_name} {preview}".casefold())
                break

    def _selected_profile(self) -> ClientProfile | None:
        profile_id = self._selected_profile_id()
        if profile_id is None:
            return None
        return self.db.get_profile(int(profile_id))

    def _selected_account(self) -> str:
        account = self.account_combo.currentData()
        return str(account or "work")

    def _profile_account(self, profile: ClientProfile) -> str:
        if profile.id is None:
            return self._selected_account()
        account = self.db.get_setting(f"telegram_account:{profile.id}", "work").strip()
        return account if account in {"work", "personal"} else "work"

    def _get_profile_by_chat_name_for_account(self, chat_name: str, account: str) -> ClientProfile | None:
        for profile in self.db.list_profiles():
            if profile.chat_name == chat_name and self._profile_account(profile) == account:
                return profile
        return None

    def _get_profile_by_telegram_peer(self, peer: str, account: str) -> ClientProfile | None:
        normalized_peer = _normalize_telegram_peer(peer)
        if not normalized_peer:
            return None
        for profile in self.db.list_profiles():
            if self._profile_account(profile) != account or profile.id is None:
                continue
            saved_peer = self.db.get_setting(f"telegram_peer:{profile.id}")
            if _normalize_telegram_peer(saved_peer) == normalized_peer:
                return profile
        return None

    def _telegram_config_for_account(self, account: str) -> AppConfig:
        if account != "personal":
            return self.config
        return replace(
            self.config,
            telegram_api_id=self.config.telegram_personal_api_id,
            telegram_api_hash=self.config.telegram_personal_api_hash,
            telegram_phone=self.config.telegram_personal_phone,
            telegram_session_path=self.config.telegram_personal_session_path,
        )

    def _account_changed(self) -> None:
        self._load_profiles()

    def _apply_account_colors(self) -> None:
        account = self._selected_account()
        self.account_combo.setProperty("accountKind", account)
        self.account_combo.style().unpolish(self.account_combo)
        self.account_combo.style().polish(self.account_combo)

    def _render_profile(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.profile_avatar.clear_avatar()
            self.chat_avatar.clear_avatar()
            self.refresh_avatar_button.setEnabled(False)
            self.chat_title.setText("Выберите контакт")
            self.chat_subtitle.setText("Импортируйте Telegram-чат или выберите уже импортированный контакт")
            self.profile_preview.setPlainText(
                "Выберите профиль или импортируйте Telegram-чат. Анализ выполняется по истории сообщений."
            )
            return
        account = self._profile_account(profile)
        index = self.account_combo.findData(account)
        if index >= 0 and self.account_combo.currentIndex() != index:
            self.account_combo.blockSignals(True)
            self.account_combo.setCurrentIndex(index)
            self.account_combo.blockSignals(False)
        self.refresh_avatar_button.setEnabled(True)
        peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip() if profile.id is not None else ""
        live_status = "фон включен" if profile.id in self._live_sessions else "только локальная история"
        self.chat_title.setText(profile.chat_name)
        self.chat_subtitle.setText(f"{peer or 'export'} · {live_status}")
        avatar_path = self.db.get_setting(f"telegram_avatar:{profile.id}") if profile.id is not None else ""
        if avatar_path and Path(avatar_path).exists():
            self.profile_avatar.set_avatar(avatar_path)
            self.chat_avatar.set_avatar(avatar_path)
        else:
            self.profile_avatar.clear_avatar()
            self.chat_avatar.clear_avatar()
        self.profile_preview.setPlainText(
            f"Чат: {profile.chat_name}\n\n"
            f"Договоренности:\n{profile.agreements}\n\n"
            f"Оплата:\n{profile.payment_promises}\n\n"
            f"Спорные моменты:\n{profile.disputed_points}\n\n"
            f"Паттерны:\n{profile.behavior_patterns}\n\n"
            f"Стиль:\n{profile.communication_style}\n\n"
            f"Рекомендации:\n{profile.tone_recommendations}"
        )

    def _profile_changed(self, *_args) -> None:
        self._analysis_memory.clear()
        self._render_profile()
        self._render_recent_messages()
        self._update_ai_unread_status(clear_stale=True)

    def _load_user_style(self) -> None:
        self.user_style.setPlainText(self.db.get_setting("user_style_profile"))

    def _stored_message_limit(self) -> int:
        try:
            value = int(self.db.get_setting("analysis_message_limit", "100"))
        except ValueError:
            value = 100
        return max(20, min(500, value))

    def _analysis_message_limit(self) -> int:
        value = self.message_limit_slider.value()
        return max(20, min(500, int(round(value / 10) * 10)))

    def _message_limit_changed(self, value: int) -> None:
        rounded = max(20, min(500, int(round(value / 10) * 10)))
        if rounded != value:
            self.message_limit_slider.blockSignals(True)
            self.message_limit_slider.setValue(rounded)
            self.message_limit_slider.blockSignals(False)
        self.message_limit_label.setText(f"Контекст: {rounded}")
        self.db.set_setting("analysis_message_limit", str(rounded))

    def _sync_selected_profile_messages(self, profile: ClientProfile | None, progress) -> int:
        if not profile or profile.id is None:
            return 0
        if profile.id in self._live_sessions:
            progress("Telegram: фоновая синхронизация уже слушает этот чат, анализирую сохраненную историю...")
            return 0
        peer = self.db.get_setting(f"telegram_peer:{profile.id}").strip()
        if not peer:
            return 0
        after_id = self.db.max_telegram_message_id(profile.id)
        if after_id <= 0:
            return 0
        account = self._profile_account(profile)
        progress("Telegram: проверяю новые сообщения перед анализом...")
        try:
            _chat_name, messages = import_new_messages_from_telegram(
                self._telegram_config_for_account(account),
                peer,
                after_id,
                progress=progress,
            )
        except Exception as exc:
            if "database is locked" not in str(exc).lower():
                raise
            log_event(f"telegram sync skipped because session database is locked: {exc}")
            progress("Telegram: session занята, анализирую уже сохраненную историю...")
            return 0
        if messages:
            self.db.save_messages(profile.id, messages)
        return len(messages)

    def _render_recent_messages(self) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            self.chat_view.setHtml(_empty_chat_html())
            self._schedule_chat_scroll_bottom()
            return
        messages = self.db.recent_messages(profile.id, limit=self._analysis_message_limit())
        self.chat_view.setHtml(_chat_html(profile, messages))
        self._schedule_chat_scroll_bottom()

    def _render_recent_messages_for_profiles(self, profiles: list[ClientProfile]) -> None:
        if not profiles:
            self.chat_view.setHtml(_empty_chat_html())
            self._schedule_chat_scroll_bottom()
            return
        selected = profiles[0]
        if selected.id is None:
            self.chat_view.setHtml(_empty_chat_html())
            self._schedule_chat_scroll_bottom()
            return
        messages = self.db.recent_messages(selected.id, limit=self._analysis_message_limit())
        self.chat_view.setHtml(_chat_html(selected, messages))
        self._schedule_chat_scroll_bottom()

    def _schedule_chat_scroll_bottom(self) -> None:
        QTimer.singleShot(0, self._scroll_chat_to_bottom)
        QTimer.singleShot(60, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self) -> None:
        if self._closing:
            return
        scrollbar = self.chat_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_ai_unread_status(self, clear_stale: bool = False) -> None:
        profile = self._selected_profile()
        if not profile or profile.id is None:
            self.ai_unread_label.hide()
            return
        max_message_id = self.db.max_telegram_message_id(profile.id)
        unread = self._ai_read_profile_id != profile.id or (
            max_message_id > 0 and self._ai_read_message_id < max_message_id
        )
        self.ai_unread_label.setVisible(unread)
        if unread and clear_stale:
            self._clear_ai_result()

    def _clear_ai_result(self) -> None:
        self._result = None
        self.tone_advice.setText("AI ещё не считал этот диалог")
        self.summary.clear()
        self.risk.clear()
        self.strategy.clear()
        self.reply_best.clear()
        self.reply_soft.clear()
        self.reply_hard.clear()

    def _analysis_context(self) -> str:
        return "\n\n".join(self._analysis_memory[-3:])

    def _remember_analysis(self, result: AdvisorResult) -> None:
        item = (
            f"Ситуация: {result.situation_summary}\n"
            f"Намерение: {result.client_intent}\n"
            f"Риск: {result.risk}\n"
            f"Лучший стиль: {result.effective_recommended_tone()}\n"
            f"Стратегия: {result.recommended_strategy}\n"
            f"Лучший ответ: {result.best_reply}"
        )
        self._analysis_memory.append(item[:2000])
        self._analysis_memory = self._analysis_memory[-3:]

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        show_action = QAction("Показать", self)
        show_action.triggered.connect(self.show)
        analyze_action = QAction("Предложить стратегию", self)
        analyze_action.triggered.connect(self.analyze_current_screen)
        self.title_bar.add_app_action(show_action)
        self.title_bar.add_app_action(analyze_action)

    def _register_hotkey(self) -> None:
        try:
            import keyboard

            keyboard.add_hotkey(
                "ctrl+shift+s",
                lambda: self.hotkey_triggered.emit(),
                suppress=False,
                trigger_on_release=True,
            )
            self.status.setText("Готово. Горячая клавиша: Ctrl+Shift+S")
        except Exception as exc:
            self.status.setText(f"Горячая клавиша недоступна: {exc}. Используйте кнопку.")

    def closeEvent(self, event) -> None:
        self._closing = True
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
        self._shutdown_live_telegram()
        try:
            import keyboard

            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        super().closeEvent(event)

    def nativeEvent(self, event_type, message):
        if sys.platform != "win32" or self.isMaximized() or self.isFullScreen():
            return super().nativeEvent(event_type, message)
        if event_type not in {
            "windows_generic_MSG",
            "windows_dispatcher_MSG",
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        }:
            return super().nativeEvent(event_type, message)
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
        except Exception:
            return super().nativeEvent(event_type, message)
        if msg.message != 0x0084:  # WM_NCHITTEST
            return super().nativeEvent(event_type, message)

        border = 8
        pos = self.mapFromGlobal(QPoint(_signed_word(msg.lParam), _signed_word(msg.lParam >> 16)))
        x = pos.x()
        y = pos.y()
        left = x < border
        right = x >= self.width() - border
        top = y < border
        bottom = y >= self.height() - border

        if top and left:
            return True, 13  # HTTOPLEFT
        if top and right:
            return True, 14  # HTTOPRIGHT
        if bottom and left:
            return True, 16  # HTBOTTOMLEFT
        if bottom and right:
            return True, 17  # HTBOTTOMRIGHT
        if left:
            return True, 10  # HTLEFT
        if right:
            return True, 11  # HTRIGHT
        if top:
            return True, 12  # HTTOP
        if bottom:
            return True, 15  # HTBOTTOM
        return super().nativeEvent(event_type, message)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.message_input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._send_manual_message()
                return True
        if isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)):
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edges = self._resize_edges_at(event.globalPosition().toPoint())
                if edges:
                    self._resize_edges = edges
                    self._resize_start_pos = event.globalPosition().toPoint()
                    self._resize_start_geometry = QRect(self.geometry())
                    event.accept()
                    return True
            if event_type == QEvent.Type.MouseMove:
                global_pos = event.globalPosition().toPoint()
                if self._resize_edges:
                    self._resize_to(global_pos)
                    event.accept()
                    return True
                self._update_resize_cursor(watched, global_pos)
            if event_type == QEvent.Type.MouseButtonRelease and self._resize_edges:
                self._resize_edges = set()
                self.unsetCursor()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _resize_edges_at(self, global_pos: QPoint) -> set[str]:
        if self.isMaximized() or self.isFullScreen():
            return set()
        pos = self.mapFromGlobal(global_pos)
        if pos.x() < 0 or pos.y() < 0 or pos.x() > self.width() or pos.y() > self.height():
            return set()
        margin = self._resize_margin
        edges: set[str] = set()
        if pos.x() <= margin:
            edges.add("left")
        elif pos.x() >= self.width() - margin:
            edges.add("right")
        if pos.y() <= margin:
            edges.add("top")
        elif pos.y() >= self.height() - margin:
            edges.add("bottom")
        return edges

    def _update_resize_cursor(self, watched: QWidget, global_pos: QPoint) -> None:
        edges = self._resize_edges_at(global_pos)
        if not edges:
            watched.unsetCursor()
            return
        if {"left", "top"}.issubset(edges) or {"right", "bottom"}.issubset(edges):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif {"right", "top"}.issubset(edges) or {"left", "bottom"}.issubset(edges):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif "left" in edges or "right" in edges:
            cursor = Qt.CursorShape.SizeHorCursor
        else:
            cursor = Qt.CursorShape.SizeVerCursor
        watched.setCursor(cursor)

    def _resize_to(self, global_pos: QPoint) -> None:
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()
        geometry = QRect(self._resize_start_geometry)
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()

        if "left" in self._resize_edges:
            left = geometry.left() + dx
            if geometry.right() - left + 1 < min_width:
                left = geometry.right() - min_width + 1
            geometry.setLeft(left)
        if "right" in self._resize_edges:
            right = geometry.right() + dx
            if right - geometry.left() + 1 < min_width:
                right = geometry.left() + min_width - 1
            geometry.setRight(right)
        if "top" in self._resize_edges:
            top = geometry.top() + dy
            if geometry.bottom() - top + 1 < min_height:
                top = geometry.bottom() - min_height + 1
            geometry.setTop(top)
        if "bottom" in self._resize_edges:
            bottom = geometry.bottom() + dy
            if bottom - geometry.top() + 1 < min_height:
                bottom = geometry.top() + min_height - 1
            geometry.setBottom(bottom)
        self.setGeometry(geometry)


def run_app(config: AppConfig) -> int:
    app = QApplication(sys.argv)
    lock_path = str((config.db_path.parent / "pain_assistant.lock").resolve())
    app_lock = QLockFile(lock_path)
    app_lock.setStaleLockTime(0)
    if not app_lock.tryLock(100):
        QMessageBox.information(
            None,
            "Уже запущено",
            "Telegram Negotiation Advisor уже запущен. Закройте существующее окно перед новым запуском.",
        )
        return 0
    app._pain_assistant_lock = app_lock
    app.setStyleSheet(_telegram_dark_stylesheet())
    window = MainWindow(config)
    window.show()
    return app.exec()


def _readonly_plain_text() -> QPlainTextEdit:
    widget = QPlainTextEdit()
    widget.setReadOnly(True)
    return widget


def _contact_preview(profile: ClientProfile, message: dict, messages: list[dict]) -> str:
    sender = "Вы" if _message_is_outgoing(profile, message, messages) else str(message.get("sender", "") or "").strip()
    text = " ".join(str(message.get("text", "") or "").split())
    if len(text) > 54:
        text = text[:51].rstrip() + "..."
    if sender:
        return f"{sender}: {text}"
    return text or "Нет сообщений"


def _empty_chat_html() -> str:
    return (
        '<html><body style="background:#0b1622; color:#7f95aa; font-family:Segoe UI;">'
        '<div style="padding:28px; text-align:center;">Выберите контакт слева</div>'
        "</body></html>"
    )


def _chat_html(profile: ClientProfile, messages: list[dict]) -> str:
    if not messages:
        return (
            '<html><body style="background:#0b1622; color:#7f95aa; font-family:Segoe UI;">'
            f'<div style="padding:28px; text-align:center;">В {html.escape(profile.chat_name)} пока нет сообщений</div>'
            "</body></html>"
        )
    rows = []
    for message in messages:
        text = _html_message_text(str(message.get("text", "") or ""))
        if not text:
            continue
        outgoing = _message_is_outgoing(profile, message, messages)
        sender = "Вы" if outgoing else html.escape(str(message.get("sender", "") or profile.chat_name))
        time_text = html.escape(_message_time(message))
        bubble = "#2f6ea5" if outgoing else "#3a2818"
        border = "#4e9ad8" if outgoing else "#9a642f"
        sender_color = "#a7d3ff" if outgoing else "#f0a24a"
        time_color = "#b7d7f0" if outgoing else "#ddb083"
        delivery = _delivery_indicator(message) if outgoing else ""
        align = "right" if outgoing else "left"
        left_spacer = '<td width="28%"></td>' if outgoing else ""
        right_spacer = '<td width="28%"></td>' if not outgoing else ""
        rows.append(
            '<table width="100%" cellspacing="0" cellpadding="3">'
            "<tr>"
            f"{left_spacer}"
            f'<td width="72%" align="{align}">'
            f'<table cellspacing="0" cellpadding="8" style="background:{bubble}; border:1px solid {border}; border-radius:10px;">'
            "<tr><td>"
            f'<div style="color:{sender_color}; font-size:9pt; font-weight:600; margin-bottom:4px;">{sender}</div>'
            f'<div style="color:#f4f7fb; font-size:10pt;">{text}</div>'
            f'<div style="color:{time_color}; font-size:8pt; margin-top:5px;" align="right">{time_text}{delivery}</div>'
            "</td></tr></table>"
            "</td>"
            f"{right_spacer}"
            "</tr></table>"
        )
    return (
        '<html><body style="background:#0b1622; color:#f4f7fb; font-family:Segoe UI;">'
        '<div style="padding:10px 12px;">'
        + "".join(rows)
        + "</div></body></html>"
    )


def _html_message_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "<br>".join(html.escape(line) for line in cleaned.split("\n"))


def _delivery_indicator(message: dict) -> str:
    status = str(message.get("delivery_status", "") or "").strip().lower()
    checks = "&#10003;&#10003;" if status == "read" else "&#10003;"
    color = "#d7ecff" if status == "read" else "#b7d7f0"
    return f'<span style="color:{color}; margin-left:5px;">{checks}</span>'


def _message_is_outgoing(profile: ClientProfile, message: dict, messages: list[dict]) -> bool:
    if message.get("out"):
        return True
    sender = str(message.get("sender", "") or "").strip()
    if not sender:
        return False
    customer_sender = _inferred_customer_sender(profile, messages)
    return bool(customer_sender and sender != customer_sender)


def _inferred_customer_sender(profile: ClientProfile, messages: list[dict]) -> str:
    senders = sorted({str(message.get("sender", "") or "").strip() for message in messages if message.get("sender")})
    if len(senders) != 2:
        return ""
    profile_tokens = _identity_tokens(profile.chat_name)
    if not profile_tokens:
        return ""
    scored = []
    for sender in senders:
        sender_tokens = _identity_tokens(sender)
        score = len(profile_tokens.intersection(sender_tokens))
        normalized_sender = _identity_normalized(sender)
        normalized_profile = _identity_normalized(profile.chat_name)
        if normalized_sender and normalized_sender in normalized_profile:
            score += 3
        scored.append((score, sender))
    scored.sort(reverse=True)
    if scored[0][0] <= 0 or scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w@]+", value.casefold().replace("ё", "е"))
        if len(token) >= 3
    }


def _identity_normalized(value: str) -> str:
    return "".join(re.findall(r"[\w@]+", value.casefold().replace("ё", "е")))


def _message_time(message: dict) -> str:
    raw = str(message.get("message_date") or message.get("date") or "")
    match = re.search(r"(\d{2}):(\d{2})", raw)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return raw[:16]


def _signed_word(value: int) -> int:
    value = int(value) & 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _messages_fingerprint(messages: list[dict]) -> str:
    normalized = [
        {
            "date": str(message.get("date", "")),
            "sender": str(message.get("sender", "")),
            "text": str(message.get("text", "")),
        }
        for message in messages
        if message.get("text")
    ]
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _profile_needs_ai_refresh(profile: ClientProfile) -> bool:
    agreements = profile.agreements.lower()
    return (
        "ai backend недоступен" in agreements
        or "профиль составлен эвристически" in agreements
        or "openai api не настроен" in agreements
    )


def _voice_style_only(style: str) -> str:
    if not style:
        return ""
    blocked_fragments = [
        "мягче",
        "мягко",
        "профессиональнее",
        "профессионально",
        "не спорить",
        "предлагать варианты",
        "быстрые тесты",
        "визуальные правки",
        "итерационно",
        "избегать длинных абзацев",
        "категоричного",
    ]
    sentences = re.split(r"(?<=[.!?])\s+", style)
    kept = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
        and not any(fragment in sentence.lower() for fragment in blocked_fragments)
    ]
    return " ".join(kept).strip()


def _normalize_telegram_peer(peer: str) -> str:
    text = str(peer or "").strip().casefold()
    if text.startswith("@"):
        text = text[1:]
    return text


def _telegram_dark_stylesheet() -> str:
    return """
    QWidget {
        background: #0f1b26;
        color: #f4f7fb;
        font-family: "Segoe UI";
        font-size: 10pt;
    }

    QMainWindow, QWidget#centralWidget {
        background: #0f1b26;
    }

    QSplitter#mainSplitter::handle:horizontal {
        background: #1c3043;
        margin: 0;
        width: 12px;
        border-left: 1px solid #314b63;
        border-right: 1px solid #314b63;
    }

    QSplitter#mainSplitter::handle:horizontal:hover {
        background: #4e9ad8;
        border-left-color: #79b9eb;
        border-right-color: #79b9eb;
    }

    QWidget#leftPanel {
        background: #111d29;
        border-right: 1px solid #223548;
    }

    QWidget#chatPanel {
        background: #0b1622;
        border-right: 1px solid #223548;
    }

    QWidget#aiPanel {
        background: #111d29;
    }

    QWidget#titleBar {
        background: #172532;
        border-bottom: 1px solid #223548;
        min-height: 38px;
        max-height: 38px;
    }

    QLabel#windowTitle {
        color: #f4f7fb;
        font-weight: 600;
        padding-left: 4px;
    }

    QPushButton#titleMenuButton {
        background: #223548;
        border: 0;
        border-radius: 7px;
        color: #dbe7f3;
        min-width: 42px;
        min-height: 26px;
        padding: 2px 8px;
    }

    QPushButton#titleMenuButton:hover {
        background: #2b4660;
    }

    QPushButton#windowControl,
    QPushButton#windowClose {
        background: transparent;
        border: 0;
        border-radius: 0;
        color: #dbe7f3;
        font-size: 12pt;
        min-width: 42px;
        min-height: 32px;
        padding: 0;
    }

    QPushButton#windowControl:hover {
        background: #223548;
    }

    QPushButton#windowClose:hover {
        background: #d94f45;
        color: #ffffff;
    }

    QMenuBar {
        background: #172532;
        color: #dbe7f3;
        border-bottom: 1px solid #223548;
        padding: 2px;
    }

    QMenuBar::item {
        background: transparent;
        padding: 6px 10px;
        border-radius: 6px;
    }

    QMenuBar::item:selected {
        background: #223548;
    }

    QMenu {
        background: #172532;
        color: #f4f7fb;
        border: 1px solid #26394d;
        padding: 5px;
    }

    QMenu::item {
        padding: 7px 28px 7px 12px;
        border-radius: 5px;
    }

    QMenu::item:selected {
        background: #2f5f8f;
    }

    QLabel {
        color: #9fb3c8;
        background: transparent;
    }

    QCheckBox {
        color: #dbe7f3;
        background: transparent;
        spacing: 7px;
    }

    QCheckBox::indicator {
        width: 17px;
        height: 17px;
        border-radius: 5px;
        border: 1px solid #3c5268;
        background: #172532;
    }

    QCheckBox::indicator:hover {
        border-color: #4e9ad8;
    }

    QCheckBox::indicator:checked {
        background: #2f6ea5;
        border-color: #4e9ad8;
    }

    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QListWidget {
        background: #172532;
        color: #f4f7fb;
        border: 1px solid #26394d;
        border-radius: 8px;
        padding: 8px;
        selection-background-color: #2f6ea5;
        selection-color: #ffffff;
    }

    QPlainTextEdit:focus,
    QTextEdit:focus,
    QLineEdit:focus,
    QComboBox:focus {
        border: 1px solid #4e9ad8;
    }

    QPlainTextEdit[readOnly="true"],
    QTextEdit[readOnly="true"] {
        background: #132130;
    }

    QPlainTextEdit#replyDraft[toneKind="soft"] {
        background: #10271f;
        border: 1px solid #2f8f65;
    }

    QPlainTextEdit#replyDraft[toneKind="mine"] {
        background: #201f12;
        border: 1px solid #c49b3f;
    }

    QPlainTextEdit#replyDraft[toneKind="strategic"] {
        background: #231a2f;
        border: 1px solid #9a6ad6;
    }

    QPlainTextEdit#replyDraft[toneKind="neutral"] {
        background: #122235;
        border: 1px solid #3f86c6;
    }

    QPlainTextEdit#replyDraft[toneKind="hard"] {
        background: #2b1718;
        border: 1px solid #c45a55;
    }

    QPlainTextEdit#replyDraft[toneKind="short"] {
        background: #1b232b;
        border: 1px solid #718294;
    }

    QLineEdit#contactSearch {
        background: #223142;
        border: 0;
        border-radius: 18px;
        min-height: 28px;
        padding: 5px 12px;
        color: #dbe7f3;
    }

    QListWidget#contactList {
        background: #111d29;
        border: 0;
        border-radius: 0;
        padding: 0;
        outline: 0;
    }

    QListWidget#contactList::item {
        border: 0;
        border-radius: 0;
        padding: 9px 8px;
        min-height: 54px;
        color: #dbe7f3;
    }

    QListWidget#contactList::item:selected {
        background: #2f5f8f;
        color: #ffffff;
    }

    QListWidget#contactList::item:hover {
        background: #1a2a3a;
    }

    QLabel#chatTitle {
        color: #ffffff;
        font-size: 12pt;
        font-weight: 700;
    }

    QLabel#chatSubtitle {
        color: #7f95aa;
        font-size: 9pt;
    }

    QLabel#aiTitle {
        color: #ffffff;
        font-size: 12pt;
        font-weight: 700;
    }

    QTextEdit#chatView {
        background: #0b1622;
        border: 0;
        border-radius: 0;
        padding: 0px;
    }

    QPlainTextEdit#messageInput {
        background: #172532;
        border: 1px solid #26394d;
        border-radius: 18px;
        padding: 8px 12px;
    }

    QTabWidget#aiTabs::pane {
        border: 1px solid #26394d;
        border-radius: 8px;
        background: #132130;
        top: -1px;
    }

    QTabBar::tab {
        background: transparent;
        color: #7f95aa;
        padding: 8px 10px;
        border: 0;
        border-bottom: 2px solid transparent;
    }

    QTabBar::tab:selected {
        color: #62a9ee;
        border-bottom: 2px solid #62a9ee;
    }

    QTabBar::tab:hover {
        color: #dbe7f3;
    }

    QComboBox {
        min-height: 28px;
        padding: 4px 10px;
    }

    QComboBox::drop-down {
        border: 0;
        width: 26px;
    }

    QComboBox QAbstractItemView {
        background: #172532;
        color: #f4f7fb;
        border: 1px solid #26394d;
        selection-background-color: #2f5f8f;
        outline: 0;
    }

    QListWidget#liveProfiles::item {
        padding: 5px;
        min-height: 34px;
    }

    QListWidget#liveProfiles::item:selected {
        background: #223548;
    }

    QPushButton {
        background: #223548;
        color: #f4f7fb;
        border: 1px solid #2d455c;
        border-radius: 8px;
        padding: 7px 12px;
        min-height: 24px;
    }

    QPushButton:hover {
        background: #2b4660;
        border-color: #3c6f9f;
    }

    QPushButton:pressed {
        background: #2f6ea5;
    }

    QPushButton:disabled {
        background: #152331;
        color: #5f7488;
        border-color: #213246;
    }

    QPushButton#primaryAction {
        background: #2f6ea5;
        border-color: #3c82bd;
    }

    QPushButton#primaryAction:hover {
        background: #3b7fb9;
    }

    QPushButton#primaryAction:pressed {
        background: #28618f;
    }

    QPushButton#primaryAction:disabled {
        background: #1d3448;
        color: #6f879c;
        border-color: #284057;
    }

    QLabel#statusLabel {
        color: #7fb5e6;
        padding: 3px 1px;
    }

    QLabel#aiUnreadStatus {
        color: #ffd7d7;
        background: #351516;
        border: 1px solid #c45a55;
        border-radius: 8px;
        padding: 7px 10px;
        font-weight: 700;
    }

    QLabel#toneAdvice {
        background: #172532;
        color: #f4f7fb;
        border: 1px solid #3f86c6;
        border-radius: 8px;
        padding: 8px;
        font-weight: 600;
    }

    QLabel#liveSelection {
        color: #dbe7f3;
        background: #172532;
        border: 1px solid #26394d;
        border-radius: 8px;
        padding: 8px;
    }

    QComboBox[accountKind="work"] {
        border: 1px solid #4e9ad8;
        color: #8fc8ff;
    }

    QComboBox[accountKind="personal"] {
        border: 1px solid #4ccf7a;
        color: #8ef0ad;
    }

    QLabel#profileAvatar {
        background: #172532;
        border: 1px solid #26394d;
        border-radius: 8px;
        padding: 0;
    }

    QProgressBar {
        background: #172532;
        border: 1px solid #26394d;
        border-radius: 6px;
        min-height: 8px;
        max-height: 8px;
        text-align: center;
        color: transparent;
    }

    QProgressBar::chunk {
        background: #4e9ad8;
        border-radius: 6px;
    }

    QScrollBar:vertical {
        background: #101c27;
        width: 10px;
        margin: 0;
    }

    QScrollBar::handle:vertical {
        background: #34485d;
        border-radius: 5px;
        min-height: 28px;
    }

    QScrollBar::handle:vertical:hover {
        background: #4b637a;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0;
        background: transparent;
    }

    QScrollBar:horizontal {
        background: #101c27;
        height: 10px;
        margin: 0;
    }

    QScrollBar::handle:horizontal {
        background: #34485d;
        border-radius: 5px;
        min-width: 28px;
    }

    QMessageBox {
        background: #172532;
    }
    """
