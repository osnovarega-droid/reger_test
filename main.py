import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from glob import glob
from pathlib import Path

try:
    import websocket
except ImportError:
    websocket = None

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


FIRST_NAMES = [
    "Anton", "Ivan", "Dmitry", "Maxim", "Alex", "Nikita", "Roman", "Victor",
    "Kirill", "Denis", "Mark", "Andrew", "Michael", "Daniel", "Sergey",
]
LAST_NAMES = [
    "Smirnov", "Ivanov", "Petrov", "Sokolov", "Volkov", "Kuznetsov", "Popov",
    "Fedorov", "Morozov", "Orlov", "Lebedev", "Novikov", "Pavlov", "Egorov",
]
CDP_PORT = 9222


def get_chromium_popen_kwargs():
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }

    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )

    return kwargs


def generate_outlook_email():
    digits = random.randint(100, 99999)
    return f"{random.choice(FIRST_NAMES)}_{random.choice(LAST_NAMES)}{digits}@outlook.com"


def wait_for_debugger(port, timeout=20):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/list"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                tabs = json.loads(response.read().decode("utf-8"))
            for tab in tabs:
                if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                    return tab["webSocketDebuggerUrl"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.4)

    raise RuntimeError("Не удалось подключиться к Chromium DevTools.")


class CdpClient:
    def __init__(self, ws_url):
        if websocket is None:
            raise RuntimeError("Установите зависимость websocket-client: py -m pip install -r requirements.txt")

        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.next_id = 0

    def call(self, method, params=None):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))

        while True:
            response = json.loads(self.ws.recv())
            if response.get("id") == message_id:
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", str(response["error"])))
                return response.get("result", {})

    def close(self):
        self.ws.close()


def automate_signup_page(port=CDP_PORT):
    email = generate_outlook_email()
    ws_url = wait_for_debugger(port)
    cdp = CdpClient(ws_url)

    js = """
        const email = __EMAIL__;
        const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
        const visible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        };
        const waitUntil = async (predicate, timeout = 30000) => {
            const started = Date.now();
            while (Date.now() - started < timeout) {
                const value = predicate();
                if (value) return value;
                await sleep(250);
            }
            throw new Error('Страница не загрузила нужный элемент за отведённое время.');
        };
        const clickCenter = async (el) => {
            el.scrollIntoView({block: 'center', inline: 'center'});
            await sleep(250);
            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            el.click();
            await sleep(500);
        };

        (async () => {
            await waitUntil(() => document.readyState === 'complete' || document.readyState === 'interactive');
            await sleep(1500);
            const microsoft = await waitUntil(() => [...document.querySelectorAll('div, span, img')].find(el => visible(el) && (el.innerText || el.alt || '').includes('Microsoft')));
            await clickCenter(microsoft);
            const emailInput = await waitUntil(() => [...document.querySelectorAll('input')].find(el => visible(el) && (el.type === 'email' || /email|membername|login/i.test(el.name + ' ' + el.id + ' ' + el.placeholder + ' ' + el.getAttribute('aria-label')))));
            await clickCenter(emailInput);
            emailInput.focus();
            emailInput.value = '';
            emailInput.dispatchEvent(new Event('input', {bubbles: true}));
            await sleep(300);
            for (const char of email) {
                emailInput.value += char;
                emailInput.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: char}));
                await sleep(25 + Math.random() * 35);
            }
            emailInput.dispatchEvent(new Event('change', {bubbles: true}));
            await sleep(800);
            const nextButton = await waitUntil(() => [...document.querySelectorAll('button, input[type="submit"]')].find(el => visible(el) && /далее|next/i.test(el.innerText || el.value || el.getAttribute('aria-label') || '')));
            await clickCenter(nextButton);
            done({ok: true, email});
        })().catch(error => done({ok: false, error: error.message, email}));
    """.replace("__EMAIL__", json.dumps(email))

    try:
        wrapper = "new Promise(done => { " + js + " })"
        result = cdp.call("Runtime.evaluate", {
            "expression": wrapper,
            "awaitPromise": True,
            "timeout": 45000,
            "userGesture": True,
            "returnByValue": True,
        })
        value = result.get("result", {}).get("value", {})
        if not value.get("ok"):
            raise RuntimeError(value.get("error", "Неизвестная ошибка автоматизации."))
        return value["email"]
    finally:
        cdp.close()


class ChromiumFinder(QObject):
    finished = Signal(str)

    def run(self):
        self.finished.emit(find_chromium_auto())


class RegerRunner(QObject):
    finished = Signal(bool, str)

    def __init__(self, chromium_path):
        super().__init__()
        self.chromium_path = chromium_path

    def run(self):
        user_data_dir = tempfile.mkdtemp(prefix="reger-chromium-")

        try:
            subprocess.Popen([
                self.chromium_path,
                f"--remote-debugging-port={CDP_PORT}",
                f"--user-data-dir={user_data_dir}",
                "--incognito",
                "--new-window",
                "--no-default-browser-check",
                "--no-first-run",
                TARGET_URL,
            ], **get_chromium_popen_kwargs())
            email = automate_signup_page()
            self.finished.emit(True, f"Start reger: введена почта {email} и нажата кнопка Далее.")
        except Exception as exc:
            self.finished.emit(False, f"Ошибка Start reger: {exc}")
        finally:
            shutil.rmtree(user_data_dir, ignore_errors=True)


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
        matches = [Path(match) for match in sorted(glob(expanded))] if "*" in expanded else [Path(expanded)]
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
        self.reger_thread = None
        self.reger_worker = None

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

        self.start_button = QPushButton("Start reger")
        self.start_button.setObjectName("startButton")
        self.start_button.setFixedWidth(130)
        self.start_button.clicked.connect(self.start_reger)

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(24, 24, 24, 24)
        page_layout.setSpacing(0)
        page_layout.addWidget(self.start_button, 0)
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
            #startButton { background-color: #16A34A; font-size: 14px; padding: 10px 14px; }
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
        if self.reger_thread and self.reger_thread.isRunning():
            self.add_log("Start reger уже выполняется.")
            return

        self.config = load_config()
        chromium_path = self.config.get("chromium_path", "").strip()
        valid, error = validate_chromium_path(chromium_path)

        if not valid:
            self.add_log(error)
            return

        self.start_button.setEnabled(False)
        self.add_log("Start reger: открываю Chromium и жду загрузку страницы.")

        self.reger_thread = QThread(self)
        self.reger_worker = RegerRunner(chromium_path)
        self.reger_worker.moveToThread(self.reger_thread)
        self.reger_thread.started.connect(self.reger_worker.run)
        self.reger_worker.finished.connect(self.on_reger_finished)
        self.reger_worker.finished.connect(self.reger_thread.quit)
        self.reger_worker.finished.connect(self.reger_worker.deleteLater)
        self.reger_thread.finished.connect(self.reger_thread.deleteLater)
        self.reger_thread.start()

    def on_reger_finished(self, ok, message):
        self.start_button.setEnabled(True)
        self.reger_thread = None
        self.reger_worker = None
        self.add_log(message)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChromiumLauncher()
    window.show()
    sys.exit(app.exec())
