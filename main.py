import ctypes
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from glob import glob
from pathlib import Path

import websocket

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
PAGE_AUTOMATION_TIMEOUT = 45
EDGE_INITIAL_CHECK_DELAY = 5
EDGE_MONITOR_INTERVAL = 1
EDGE_WINDOW_TITLE = "Microsoft Edge"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
def get_edge_popen_kwargs(stderr_target=None):
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_target or subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 1
        kwargs["startupinfo"] = startupinfo

    return kwargs


def read_process_output(output_file):
    try:
        text = Path(output_file).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""

    return text[-1200:]


def build_edge_args(edge_path, port, user_data_dir):
    return [
        edge_path,
        "--inprivate",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        TARGET_URL,
    ]


def is_debugger_available(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False

def get_window_titles():
    if os.name != "nt":
        return []

    titles = []
    user32 = ctypes.windll.user32

    def enum_handler(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            titles.append(title)
        return True

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(enum_windows_proc(enum_handler), 0)
    return titles


def is_edge_window_open(window_title=EDGE_WINDOW_TITLE):
    expected_title = window_title.lower()
    return any(expected_title in title.lower() for title in get_window_titles())


def wait_for_edge_window(output_file, timeout=EDGE_INITIAL_CHECK_DELAY):
    deadline = time.time() + timeout

    while time.time() < deadline:
        if is_edge_window_open():
            return True
        time.sleep(0.25)



 
    details = read_process_output(output_file)
    message = (
        f'Окно с названием "{EDGE_WINDOW_TITLE}" не найдено за {timeout} сек. '
        "Проверка теперь выполняется по названию окна, а не по PID процесса."
    )
    if details:
        message += f" Вывод Microsoft Edge: {details}"
    raise RuntimeError(message)


def wait_until_edge_closed(port):
    while is_edge_window_open() or is_debugger_available(port):
        time.sleep(EDGE_MONITOR_INTERVAL)


def generate_outlook_email():
    digits = random.randint(100, 99999)
    return f"{random.choice(FIRST_NAMES)}_{random.choice(LAST_NAMES)}{digits}@outlook.com"


def wait_for_debugger(port, timeout=20):
    deadline = time.time() + timeout
    list_url = f"http://127.0.0.1:{port}/json/list"
    new_tab_url = f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(TARGET_URL, safe=':/?=&')}"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(list_url, timeout=1) as response:
                tabs = json.loads(response.read().decode("utf-8"))
            for tab in tabs:
                if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                    return tab["webSocketDebuggerUrl"]

            try:
                request = urllib.request.Request(new_tab_url, method="PUT")
                response = urllib.request.urlopen(request, timeout=1)
            except urllib.error.HTTPError:
                response = urllib.request.urlopen(new_tab_url, timeout=1)

            with response:
                tab = json.loads(response.read().decode("utf-8"))
            if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                return tab["webSocketDebuggerUrl"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.4)

    raise RuntimeError("Не удалось подключиться к Microsoft Edge DevTools.")


class CdpClient:
    def __init__(self, ws_url):
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
    ws_url = wait_for_debugger(port, timeout=PAGE_AUTOMATION_TIMEOUT)
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
            await waitUntil(() => document.readyState === 'complete');
            await sleep(1500);
            const microsoft = await waitUntil(() => [...document.querySelectorAll('div, span, img')].find(el => visible(el) && (el.innerText || el.alt || '').includes('Microsoft')));
            await clickCenter(microsoft);
            const emailInput = await waitUntil(() => [...document.querySelectorAll('input')].find(el => visible(el) && (el.type === 'email' || /email|membername|login|электрон/i.test(el.name + ' ' + el.id + ' ' + el.placeholder + ' ' + el.getAttribute('aria-label')))));
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


class EdgeFinder(QObject):
    finished = Signal(str)

    def run(self):
        self.finished.emit(find_edge_auto())


class RegerRunner(QObject):
    status = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, edge_path):
        super().__init__()
        self.edge_path = edge_path

    def run(self):
        output_dir = tempfile.mkdtemp(prefix="reger-edge-")
        output_file = Path(output_dir) / "edge-startup.log"
        user_data_dir = Path(output_dir) / "profile"
        port = get_free_port()
        process = None

        try:
            with open(output_file, "w", encoding="utf-8", errors="replace") as stderr_target:
                process = subprocess.Popen(
                    build_edge_args(self.edge_path, port, user_data_dir),
                    **get_edge_popen_kwargs(stderr_target),
                )
                self.status.emit(f'Start reger: Microsoft Edge запущен в режиме InPrivate. Проверю окно "{EDGE_WINDOW_TITLE}" через {EDGE_INITIAL_CHECK_DELAY} сек.')
                wait_for_edge_window(output_file)

            self.status.emit(f'Start reger: найдено окно "{EDGE_WINDOW_TITLE}". Продолжаю регистрацию.')
            email = automate_signup_page(port)
            self.status.emit(f"Start reger: введена электронная почта {email} и нажата кнопка Далее.")
            wait_until_edge_closed(port)
            self.finished.emit(True, f'Start reger: окно "{EDGE_WINDOW_TITLE}" закрыто.')
        except Exception as exc:
            if process and process.poll() is None:
                self.status.emit(f'Start reger: окно "{EDGE_WINDOW_TITLE}" оставлено открытым.')
            self.finished.emit(False, f"Ошибка Start reger: {exc}")
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


def get_default_edge_path():
    return r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def find_edge_auto():
    possible_paths = [

        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",

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

                if file.name.lower() == "msedge.exe" and "microsoft" in file_text and "edge" in file_text:
                    return str(file)

        except (OSError, PermissionError):
            continue

    return ""


def load_config():
    default_config = {"edge_path": get_default_edge_path()}

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)

        except (OSError, json.JSONDecodeError):
            return default_config
        return {**default_config, **loaded}
    return default_config


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4, ensure_ascii=False)


def validate_edge_path(path_text):
    path_text = path_text.strip()

    if not path_text:
        return False, 'Не указано "edge_path"'

    edge_path = Path(path_text)

    if not edge_path.exists():
        return False, "Файл Microsoft Edge не найден."

    if not edge_path.is_file():
        return False, "Указанный путь не является файлом."

    if edge_path.suffix.lower() != ".exe":
        return False, "Нужно выбрать .exe файл."

    return True, ""


class EdgeLauncher(QWidget):
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



        self.main_button = self.create_nav_button("▦  Main", 0)
        self.settings_button = self.create_nav_button("⚙  Settings", 1)

        self.logs_title = QLabel("*logs")
        self.logs_title.setObjectName("logsTitle")
        self.logs_box = QTextEdit()
        self.logs_box.setObjectName("logsBox")
        self.logs_box.setReadOnly(True)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 10, 18, 18)
        sidebar_layout.setSpacing(8)
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

        label = QLabel("Путь до Microsoft Edge")
        label.setObjectName("inputLabel")

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Например: C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")
        self.path_input.setText(self.config.get("edge_path", ""))
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
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите Microsoft Edge .exe", "", "Executable (*.exe)")
        if file_path:
            self.path_input.setText(file_path)
            self.add_log("Путь выбран вручную.")

    def auto_find_again(self):
        if self.find_thread and self.find_thread.isRunning():
            self.add_log("Автопоиск уже выполняется.")
            return

        self.auto_find_button.setEnabled(False)
        self.status_label.setText("Идёт поиск Microsoft Edge...")
        self.add_log("Запущен автоматический поиск Microsoft Edge.")

        self.find_thread = QThread(self)
        self.find_worker = EdgeFinder()
        self.find_worker.moveToThread(self.find_thread)
        self.find_thread.started.connect(self.find_worker.run)
        self.find_worker.finished.connect(self.on_auto_find_finished)
        self.find_worker.finished.connect(self.find_thread.quit)
        self.find_worker.finished.connect(self.find_worker.deleteLater)
        self.find_thread.finished.connect(self.on_find_thread_finished)
        self.find_thread.finished.connect(self.find_thread.deleteLater)
        self.find_thread.start()

    def on_auto_find_finished(self, found_path):
        self.auto_find_button.setEnabled(True)


        if found_path:
            self.path_input.setText(found_path)
            self.status_label.setText(f"Microsoft Edge найден: {found_path}")
            self.add_log(f"Microsoft Edge найден: {found_path}")
        else:
            self.status_label.setText("Microsoft Edge не найден автоматически.")
            self.add_log("Microsoft Edge не найден автоматически.")
    def on_find_thread_finished(self):
        self.find_thread = None
        self.find_worker = None
    def save_path(self):
        edge_path = self.path_input.text().strip()
        valid, error = validate_edge_path(edge_path)

        if not valid:
            self.status_label.setText(error)
            self.add_log(error)
            return

        self.config["edge_path"] = edge_path
        save_config(self.config)
        self.status_label.setText("Настройки сохранены.")
        self.add_log("Настройки сохранены.")

    def start_reger(self):
        if self.reger_thread and self.reger_thread.isRunning():
            self.add_log("Start reger уже выполняется.")
            return

        input_path = self.path_input.text().strip()
        config_path = load_config().get("edge_path", "").strip()
        candidates = [input_path, config_path, find_edge_auto()]
        edge_path = next((path for path in candidates if validate_edge_path(path)[0]), "")

        if not edge_path:
            self.add_log(
                "Microsoft Edge не найден. Укажите путь к msedge.exe в Settings "
                "или нажмите «Найти автоматически»."
            )
            return

        if edge_path != self.config.get("edge_path", ""):
            self.config["edge_path"] = edge_path
            self.path_input.setText(edge_path)
            save_config(self.config)
            self.add_log(f"Путь к Microsoft Edge сохранён: {edge_path}")

        self.start_button.setEnabled(False)
        self.add_log("Start reger: открываю Microsoft Edge в режиме InPrivate.")

        self.reger_thread = QThread(self)
        self.reger_worker = RegerRunner(edge_path)
        self.reger_worker.moveToThread(self.reger_thread)
        self.reger_thread.started.connect(self.reger_worker.run)
        self.reger_worker.status.connect(self.add_log)
        self.reger_worker.finished.connect(self.on_reger_finished)
        self.reger_worker.finished.connect(self.reger_thread.quit)
        self.reger_worker.finished.connect(self.reger_worker.deleteLater)
        self.reger_thread.finished.connect(self.on_reger_thread_finished)
        self.reger_thread.finished.connect(self.reger_thread.deleteLater)
        self.reger_thread.start()

    def on_reger_finished(self, ok, message):
        self.start_button.setEnabled(True)
        self.add_log(message)

    def on_reger_thread_finished(self):
        self.reger_thread = None
        self.reger_worker = None



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EdgeLauncher()
    window.show()
    sys.exit(app.exec())
