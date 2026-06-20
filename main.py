import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFrame,
    QStackedWidget,
)

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
TARGET_URL = "https://signup.live.com/signup"


def find_chromium_auto():
    possible_paths = [
        r"C:\Program Files\Chromium\Application\chrome.exe",
        r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
        r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
        r"%LOCALAPPDATA%\Chromium\chrome.exe",
    ]

    for raw_path in possible_paths:
        path = Path(os.path.expandvars(raw_path))
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

        except Exception:
            pass

    return ""


DEFAULT_CHROMIUM_PATH = find_chromium_auto()


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                config = json.load(file)

            saved_path = config.get("chromium_path", "")
            if saved_path and Path(saved_path).exists():
                return config

        except Exception:
            pass

    return {"chromium_path": DEFAULT_CHROMIUM_PATH}


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4, ensure_ascii=False)


def validate_chromium_path(path_text):
    path_text = path_text.strip()

    if not path_text:
        return False, "Chromium не найден автоматически. Укажите путь вручную."

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

        self.setWindowTitle("Reger Chromium Launcher")
        self.setFixedSize(800, 600)

        self.config = load_config()

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(210)

        self.logo = QLabel("REGER")
        self.logo.setObjectName("logo")

        self.settings_button = QPushButton("⚙  Settings")
        self.settings_button.setObjectName("navButton")
        self.settings_button.clicked.connect(lambda: self.pages.setCurrentIndex(0))

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 24)
        sidebar_layout.setSpacing(14)
        sidebar_layout.addWidget(self.logo)
        sidebar_layout.addSpacing(24)
        sidebar_layout.addWidget(self.settings_button)
        sidebar_layout.addStretch()

        self.pages = QStackedWidget()
        self.pages.addWidget(self.create_settings_page())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.sidebar)
        root.addWidget(self.pages)

        self.apply_style()

    def create_settings_page(self):
        page = QWidget()

        title = QLabel("Settings")
        title.setObjectName("pageTitle")

        subtitle = QLabel("Путь до Chromium определяется автоматически. Если не найден — укажите вручную.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)

        card = QFrame()
        card.setObjectName("card")

        label = QLabel("Путь до Chromium")
        label.setObjectName("inputLabel")

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Chromium не найден автоматически")
        self.path_input.setText(self.config.get("chromium_path", ""))

        self.choose_button = QPushButton("Выбрать файл")
        self.choose_button.clicked.connect(self.choose_file)

        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("saveButton")
        self.save_button.clicked.connect(self.save_path)

        self.open_button = QPushButton("Открыть Chromium")
        self.open_button.setObjectName("openButton")
        self.open_button.clicked.connect(self.open_chromium)

        self.auto_find_button = QPushButton("Найти автоматически")
        self.auto_find_button.clicked.connect(self.auto_find_again)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

        if self.path_input.text().strip():
            self.status_label.setText("Chromium найден автоматически или загружен из настроек.")
        else:
            self.status_label.setText("Chromium не найден. Укажите путь вручную или нажмите поиск.")

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input)
        path_row.addWidget(self.choose_button)

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.save_button)
        buttons_row.addWidget(self.open_button)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(16)
        card_layout.addWidget(label)
        card_layout.addLayout(path_row)
        card_layout.addWidget(self.auto_find_button)
        card_layout.addLayout(buttons_row)
        card_layout.addWidget(self.status_label)
        card_layout.addStretch()

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(36, 34, 36, 34)
        page_layout.setSpacing(18)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addWidget(card)
        page_layout.addStretch()

        return page

    def apply_style(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #0F172A;
                color: #F8FAFC;
                font-family: Segoe UI;
                font-size: 14px;
            }

            #sidebar {
                background-color: #020617;
                border-right: 1px solid #1E293B;
            }

            #logo {
                font-size: 26px;
                font-weight: bold;
                color: #38BDF8;
            }

            #navButton {
                text-align: left;
                background-color: #1E293B;
                border: none;
                border-radius: 12px;
                padding: 14px;
                color: white;
                font-weight: bold;
            }

            #navButton:hover {
                background-color: #334155;
            }

            #pageTitle {
                font-size: 32px;
                font-weight: bold;
                color: white;
            }

            #pageSubtitle {
                color: #94A3B8;
                font-size: 15px;
            }

            #card {
                background-color: #1E293B;
                border-radius: 20px;
            }

            #inputLabel {
                color: #CBD5E1;
                font-weight: bold;
            }

            QLineEdit {
                background-color: #020617;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 13px;
                color: white;
            }

            QLineEdit:focus {
                border: 1px solid #38BDF8;
            }

            QPushButton {
                background-color: #334155;
                border: none;
                border-radius: 12px;
                padding: 13px 18px;
                color: white;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #475569;
            }

            #saveButton {
                background-color: #2563EB;
            }

            #saveButton:hover {
                background-color: #1D4ED8;
            }

            #openButton {
                background-color: #16A34A;
            }

            #openButton:hover {
                background-color: #15803D;
            }

            #statusLabel {
                color: #CBD5E1;
                min-height: 24px;
            }
        """)

    def choose_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Chromium .exe",
            "",
            "Executable (*.exe)"
        )

        if file_path:
            self.path_input.setText(file_path)

    def auto_find_again(self):
        found_path = find_chromium_auto()

        if found_path:
            self.path_input.setText(found_path)
            self.status_label.setText(f"Chromium найден: {found_path}")
            QMessageBox.information(self, "Найдено", f"Chromium найден:\n{found_path}")
        else:
            self.status_label.setText("Chromium не найден автоматически.")
            QMessageBox.warning(self, "Не найдено", "Chromium не найден автоматически.")

    def save_path(self):
        chromium_path = self.path_input.text().strip()
        valid, error = validate_chromium_path(chromium_path)

        if not valid:
            QMessageBox.critical(self, "Ошибка", error)
            self.status_label.setText(error)
            return

        save_config({"chromium_path": chromium_path})
        self.status_label.setText("Настройки сохранены.")
        QMessageBox.information(self, "Готово", "Путь до Chromium сохранён.")

    def open_chromium(self):
        chromium_path = self.path_input.text().strip()
        valid, error = validate_chromium_path(chromium_path)

        if not valid:
            QMessageBox.critical(self, "Ошибка", error)
            self.status_label.setText(error)
            return

        try:
            subprocess.Popen([
                chromium_path,
                "--incognito",
                "--new-window",
                "--no-first-run",
                TARGET_URL,
            ])
            self.status_label.setText("Chromium открыт в режиме инкогнито.")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка запуска", str(exc))
            self.status_label.setText("Не удалось запустить Chromium.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChromiumLauncher()
    window.show()
    sys.exit(app.exec())