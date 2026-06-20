import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFrame,
    QStackedWidget,
    QTextEdit,
)

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
TARGET_URL = "https://signup.live.com/signup"


class ChromiumFinder(QObject):
    finished = Signal(str)

    def run(self):
        self.finished.emit(find_chromium_auto())


def find_chromium_auto():
    possible_paths = [
        r"C:\Program Files\Chromium\Application\chrome.exe",
        r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
        r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
        r"%LOCALAPPDATA%\Chromium\chrome.exe",
        r"%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win64\chrome.exe",
    ]

    for raw_path in possible_paths:
        expanded = os.path.expandvars(raw_path)
        matches = sorted(Path().glob(expanded)) if "*" in expanded else [Path(expanded)]
        for path in matches:
            if path.exists() and path.is_file():
                return str(path)

    search_roots = [
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
        Path.home() / "AppData" / "Local",
        Path.home() / "AppData" / "Roaming",
        Path.home() / "Desktop",
        Path.home() / "Downloads",
    ]

    for root in search_roots:
        if not root.exists():
            continue

        try:
            for file in root.rglob("*.exe"):
                file_text = str(file).lower()

                if (
                    file.name.lower() in ["chrome.exe", "chromium.exe"]
                    and "chromium" in file_text
                ):
                    return str(file)

        except (OSError, PermissionError):
            continue

    return ""


def load_config():
    default_config = {"chromium_path": ""}

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)
            return {**default_config, **loaded}
        except (OSError, json.JSONDecodeError):
            return default_config

    return default_config


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4, ensure_ascii=False)


def validate_chromium_path(path_text):
    path_text = path_text.strip()

    if not path_text:
        return False, 'Не указано "chromium_path"'

    chromium_path = Path(path_text)

    if not chromium_path.exists():
        return False, "Файл Chromium не найден."

    if not chromium_path.is_file():
        return False, "Указанный путь не является файлом."

    if chromium_path.suffix.lower() != ".exe":
        return False, "Нужно выбрать .exe файл."

    return True, ""


class ChromiumLauncher(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Reger")
        self.setMinimumSize(980, 640)

        self.config = load_config()
        self.find_thread = None
        self.find_worker = None

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)

        self.logo = QLabel("REGER")
        self.logo.setObjectName("logo")

        self.main_button = self.create_nav_button("▦  Main", 0)
        self.settings_button = self.create_nav_button("⚙  Settings", 1)

        self.logs_title = QLabel("*logs")
        self.logs_title.setObjectName("logsTitle")
        self.logs_box = QTextEdit()
        self.logs_box.setObjectName("logsBox")
        self.logs_box.setReadOnly(True)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 18)
        sidebar_layout.setSpacing(12)
        sidebar_layout.addWidget(self.logo)
        sidebar_layout.addSpacing(20)
        sidebar_layout.addWidget(self.main_button)
        sidebar_layout.addWidget(self.settings_button)
        sidebar_layout.addSpacing(20)
        sidebar_layout.addWidget(self.logs_title)
        sidebar_layout.addWidget(self.logs_box, 1)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.create_main_page())
        self.pages.addWidget(self.create_settings_page())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.sidebar)
        root.addWidget(self.pages)

        self.apply_style()
        self.switch_page(0)
        self.add_log("Интерфейс запущен.")

    def create_nav_button(self, text, page_index):
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.clicked.connect(lambda: self.switch_page(page_index))
        return button

    def switch_page(self, page_index):
        self.pages.setCurrentIndex(page_index)
        for index, button in enumerate([self.main_button, self.settings_button]):
            button.setProperty("active", index == page_index)
            button.style().unpolish(button)
            button.style().polish(button)

    def create_main_page(self):
        page = QWidget()

        title = QLabel("Main")
        title.setObjectName("pageTitle")

        subtitle = QLabel("Пока здесь только запуск Reger через Chromium из config.json.")
        subtitle.setObjectName("pageSubtitle")

        card = QFrame()
        card.setObjectName("heroCard")

        start_title = QLabel("Start reger")
        start_title.setObjectName("cardTitle")

        start_text = QLabel("Откроет ссылку регистрации в Chromium, путь к которому указан в настройках.")
        start_text.setObjectName("pageSubtitle")
        start_text.setWordWrap(True)

        self.start_button = QPushButton("▶  Start reger")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_reger)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(16)
        card_layout.addWidget(start_title)
        card_layout.addWidget(start_text)
        card_layout.addWidget(self.start_button)
        card_layout.addStretch()

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(40, 36, 40, 36)
        page_layout.setSpacing(18)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addWidget(card)
        page_layout.addStretch()
        return page

    def create_settings_page(self):
        page = QWidget()

        title = QLabel("Settings")
        title.setObjectName("pageTitle")

        subtitle = QLabel("Здесь пока указываются только пути. Автопоиск работает в фоне и не блокирует интерфейс.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)

        card = QFrame()
        card.setObjectName("card")

        label = QLabel("Путь до Chromium")
        label.setObjectName("inputLabel")

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Например: C:\\...\\chrome.exe")
        self.path_input.setText(self.config.get("chromium_path", ""))

        self.choose_button = QPushButton("Выбрать файл")
        self.choose_button.clicked.connect(self.choose_file)

        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("saveButton")
        self.save_button.clicked.connect(self.save_path)

        self.auto_find_button = QPushButton("Найти автоматически")
        self.auto_find_button.clicked.connect(self.auto_find_again)

        self.status_label = QLabel("Путь загружен из config.json." if self.path_input.text().strip() else "Укажите путь или запустите автопоиск.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input)
        path_row.addWidget(self.choose_button)

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.save_button)
        buttons_row.addWidget(self.auto_find_button)
        buttons_row.addStretch()

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(16)
        card_layout.addWidget(label)
        card_layout.addLayout(path_row)
        card_layout.addLayout(buttons_row)
        card_layout.addWidget(self.status_label)
        card_layout.addStretch()

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(40, 36, 40, 36)
        page_layout.setSpacing(18)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addWidget(card)
        page_layout.addStretch()

        return page

    def apply_style(self):
        self.setStyleSheet("""
            QWidget { background-color: #0B1120; color: #F8FAFC; font-family: Segoe UI, Arial; font-size: 14px; }
            #sidebar { background-color: #020617; border-right: 1px solid #1E293B; }
            #logo { font-size: 28px; font-weight: 900; color: #38BDF8; letter-spacing: 3px; }
            #navButton { text-align: left; background-color: transparent; border: 1px solid transparent; border-radius: 14px; padding: 14px; color: #CBD5E1; font-weight: 700; }
            #navButton:hover { background-color: #162033; color: white; }
            #navButton[active="true"] { background-color: #1E40AF; color: white; border: 1px solid #60A5FA; }
            #logsTitle { color: #94A3B8; font-weight: 800; padding-top: 4px; }
            #logsBox { background-color: #030712; border: 1px solid #1E293B; border-radius: 14px; color: #BAE6FD; padding: 10px; font-family: Consolas, monospace; font-size: 12px; }
            #pageTitle { font-size: 34px; font-weight: 900; color: white; }
            #pageSubtitle { color: #94A3B8; font-size: 15px; }
            #card, #heroCard { background-color: #111827; border: 1px solid #263449; border-radius: 24px; }
            #heroCard { min-height: 220px; }
            #cardTitle { font-size: 24px; font-weight: 900; color: white; }
            #inputLabel { color: #CBD5E1; font-weight: 800; }
            QLineEdit { background-color: #020617; border: 1px solid #334155; border-radius: 12px; padding: 13px; color: white; }
            QLineEdit:focus { border: 1px solid #38BDF8; }
            QPushButton { background-color: #334155; border: none; border-radius: 12px; padding: 13px 18px; color: white; font-weight: 800; }
            QPushButton:hover { background-color: #475569; }
            QPushButton:disabled { background-color: #1E293B; color: #64748B; }
            #saveButton { background-color: #2563EB; }
            #saveButton:hover { background-color: #1D4ED8; }
            #startButton { background-color: #16A34A; font-size: 16px; padding: 16px 22px; }
            #startButton:hover { background-color: #15803D; }
            #statusLabel { color: #CBD5E1; min-height: 24px; }
        """)

    def add_log(self, message):
        self.logs_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def choose_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите Chromium .exe", "", "Executable (*.exe)")
        if file_path:
            self.path_input.setText(file_path)
            self.add_log("Путь выбран вручную.")

    def auto_find_again(self):
        if self.find_thread and self.find_thread.isRunning():
            self.add_log("Автопоиск уже выполняется.")
            return

        self.auto_find_button.setEnabled(False)
        self.status_label.setText("Идёт поиск Chromium...")
        self.add_log("Запущен автоматический поиск Chromium.")

        self.find_thread = QThread(self)
        self.find_worker = ChromiumFinder()
        self.find_worker.moveToThread(self.find_thread)
        self.find_thread.started.connect(self.find_worker.run)
        self.find_worker.finished.connect(self.on_auto_find_finished)
        self.find_worker.finished.connect(self.find_thread.quit)
        self.find_worker.finished.connect(self.find_worker.deleteLater)
        self.find_thread.finished.connect(self.find_thread.deleteLater)
        self.find_thread.start()

    def on_auto_find_finished(self, found_path):
        self.auto_find_button.setEnabled(True)
        self.find_thread = None
        self.find_worker = None

        if found_path:
            self.path_input.setText(found_path)
            self.status_label.setText(f"Chromium найден: {found_path}")
            self.add_log(f"Chromium найден: {found_path}")
        else:
            self.status_label.setText("Chromium не найден автоматически.")
            self.add_log("Chromium не найден автоматически.")

    def save_path(self):
        chromium_path = self.path_input.text().strip()
        valid, error = validate_chromium_path(chromium_path)

        if not valid:
            self.status_label.setText(error)
            self.add_log(error)
            return

        self.config["chromium_path"] = chromium_path
        save_config(self.config)
        self.status_label.setText("Настройки сохранены.")
        self.add_log("Настройки сохранены.")

    def start_reger(self):
        self.config = load_config()
        chromium_path = self.config.get("chromium_path", "").strip()
        valid, error = validate_chromium_path(chromium_path)

        if not valid:
            self.add_log(error)
            return

        try:
            subprocess.Popen([chromium_path, "--incognito", "--new-window", "--no-first-run", TARGET_URL])
            self.add_log("Start reger: Chromium открыт.")
        except Exception as exc:
            self.add_log(f"Не удалось запустить Chromium: {exc}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChromiumLauncher()
    window.show()
    sys.exit(app.exec())
