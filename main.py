import ctypes
import ctypes.wintypes
import importlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import csv

from datetime import datetime
from glob import glob
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
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
LOGPASS_FILE = APP_DIR / "logpass.txt"
TARGET_URL = "https://signup.live.com/signup"


FIRST_NAMES = [
    "Anton", "Ivan", "Dmitry", "Maxim", "Alex", "Nikita", "Roman", "Victor",
    "Kirill", "Denis", "Mark", "Andrew", "Michael", "Daniel", "Sergey",
]
LAST_NAMES = [
    "Smirnov", "Ivanov", "Petrov", "Sokolov", "Volkov", "Kuznetsov", "Popov",
    "Fedorov", "Morozov", "Orlov", "Lebedev", "Novikov", "Pavlov", "Egorov",
]
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 12
PASSWORD_UPPERCASE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PASSWORD_LOWERCASE = "abcdefghijklmnopqrstuvwxyz"
PASSWORD_DIGITS = "0123456789"
VISUAL_AUTOMATION_TIMEOUT = 60
SIGNUP_TITLE_WORDS = ["создание", "создан", "учет", "учёт", "записи", "майкрософт", "microsoft", "account"]
DAY_FIELD_WORDS = ["день", "day"]
MONTH_FIELD_WORDS = ["месяц", "month"]
YEAR_FIELD_WORDS = ["год", "year"]
BIRTH_DAY_MIN = 1
BIRTH_DAY_MAX = 18
BIRTH_YEAR_MIN = 1980
BIRTH_YEAR_MAX = 2008

EDGE_INITIAL_CHECK_DELAY = 5
EDGE_MONITOR_INTERVAL = 1
EDGE_WINDOW_TITLE = "Microsoft Edge"
EDGE_PROCESS_NAME = "msedge.exe"

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
def get_edge_process_ids():
    if os.name != "nt":
        return set()

    try:
        completed = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {EDGE_PROCESS_NAME}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if completed.returncode != 0:
        return set()

    process_ids = set()
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].lower() != EDGE_PROCESS_NAME:
            continue
        try:
            process_ids.add(int(row[1]))
        except ValueError:
            continue

    return process_ids


def get_window_process_ids(window_title=None):
    if os.name != "nt":
        return set()

    expected_title = window_title.lower() if window_title else None
    process_ids = set()
    user32 = ctypes.windll.user32

    def enum_handler(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if expected_title:
            if length <= 0:
                return True

            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if expected_title not in title.lower():
                return True

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            process_ids.add(pid.value)
        return True

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(enum_windows_proc(enum_handler), 0)
    return process_ids

def read_process_output(output_file):
    try:
        text = Path(output_file).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""

    return text[-1200:]


def build_edge_args(edge_path, user_data_dir):
    return [
        edge_path,
        "--inprivate",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        TARGET_URL,
    ]


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


def wait_for_edge_pid(output_file, known_pids=None, launched_pid=None, timeout=EDGE_INITIAL_CHECK_DELAY):
    known_pids = known_pids or set()
    deadline = time.time() + timeout
    last_seen_pids = set()

    while time.time() < deadline:
        current_pids = get_edge_process_ids()
        last_seen_pids = current_pids

        window_pids = get_window_process_ids()

        if launched_pid and launched_pid in current_pids and launched_pid in window_pids:
            return launched_pid

        new_pids = current_pids - known_pids
        if new_pids:

            window_new_pids = sorted(new_pids & window_pids)
            if window_new_pids:
                return window_new_pids[0]



        reusable_pids = sorted(current_pids & window_pids)
        if reusable_pids:
            return reusable_pids[0]

        time.sleep(0.25)


    details = read_process_output(output_file)
    message = (
        f'Процесс "{EDGE_PROCESS_NAME}" не найден в диспетчере задач за {timeout} сек. '
        "Start reger теперь привязывается к PID Microsoft Edge и работает с этим окном."
    )
    if last_seen_pids:
        message += f" Последние найденные PID: {', '.join(map(str, sorted(last_seen_pids)))}."
    if details:
        message += f" Вывод Microsoft Edge: {details}"
    raise RuntimeError(message)


def wait_until_edge_closed(edge_pid=None):
    while (edge_pid and edge_pid in get_edge_process_ids()) or (not edge_pid and is_edge_window_open()):
        time.sleep(EDGE_MONITOR_INTERVAL)


def generate_outlook_email():
    digits = random.randint(100, 99999)
    first_name = random.choice(FIRST_NAMES).lower()
    last_name = random.choice(LAST_NAMES).lower()
    return f"{first_name}_{last_name}{digits}@outlook.com"

def save_account_credentials(email, password):
    with open(LOGPASS_FILE, "a", encoding="utf-8") as file:
        file.write(f"{email}:{password}\n")

def generate_password():
    password_length = random.randint(PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH)
    required_characters = [
        random.choice(PASSWORD_UPPERCASE),
        random.choice(PASSWORD_LOWERCASE),
        random.choice(PASSWORD_DIGITS),
    ]
    allowed_characters = PASSWORD_UPPERCASE + PASSWORD_LOWERCASE + PASSWORD_DIGITS
    required_characters.extend(
        random.choice(allowed_characters) for _ in range(password_length - len(required_characters))
    )
    random.shuffle(required_characters)
    return "".join(required_characters)

def get_window_handles_for_pid(target_pid):
    if os.name != "nt" or not target_pid:
        return []

    handles = []
    user32 = ctypes.windll.user32

    def enum_handler(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == target_pid:
            handles.append(hwnd)
        return True

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(enum_windows_proc(enum_handler), 0)
    return handles


def activate_window_handle(hwnd):
    if os.name != "nt" or not hwnd:
        return False

    user32 = ctypes.windll.user32
    hwnd_topmost = ctypes.c_void_p(-1)
    hwnd_notopmost = ctypes.c_void_p(-2)
    sw_restore = 9
    swp_nomove = 0x0002
    swp_nosize = 0x0001
    swp_showwindow = 0x0040

    user32.ShowWindow(hwnd, sw_restore)
    user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, swp_nomove | swp_nosize | swp_showwindow)
    user32.SetWindowPos(hwnd, hwnd_notopmost, 0, 0, 0, 0, swp_nomove | swp_nosize | swp_showwindow)
    user32.SetForegroundWindow(hwnd)
    return True


def activate_edge_window(edge_pid=None, window_title=EDGE_WINDOW_TITLE):
    if os.name != "nt":
        return False
    if edge_pid:
        handles = get_window_handles_for_pid(edge_pid)
        if handles:
            return activate_window_handle(handles[0])
        return False
    expected_title = window_title.lower()
    user32 = ctypes.windll.user32
    found_hwnd = ctypes.c_void_p()

    def enum_handler(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if expected_title in buffer.value.lower():
            found_hwnd.value = hwnd
            return False
        return True

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(enum_windows_proc(enum_handler), 0)
    if not found_hwnd.value:
        return False

    return activate_window_handle(found_hwnd.value)


def get_foreground_window_rect():
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}


def load_optional_module(module_name):
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None

def normalize_ocr_text(text):
    return "".join(character for character in text.lower().replace("ё", "е") if character.isalnum())

def find_text_point_on_screen(pyautogui_module, pytesseract_module, words, confidence=45):
    if pyautogui_module is None or pytesseract_module is None:
        return None

    screenshot = pyautogui_module.screenshot()
    try:
        data = pytesseract_module.image_to_data(
            screenshot,
            lang="rus+eng",
            output_type=pytesseract_module.Output.DICT,
            config="--psm 6",
        )
    except (pytesseract_module.TesseractNotFoundError, RuntimeError, OSError):
        return None

    normalized_words = [normalize_ocr_text(word) for word in words]
    count = len(data.get("text", []))
    for index in range(count):
        raw_text = (data["text"][index] or "").strip()
        text = normalize_ocr_text(raw_text)
        if not text:
            continue
        try:
            item_confidence = float(data.get("conf", [0])[index])
        except (TypeError, ValueError):
            item_confidence = 0
        if item_confidence < confidence:
            continue
        if any(word in text for word in normalized_words):
            return {
                "x": data["left"][index] + data["width"][index] / 2,
                "y": data["top"][index] + data["height"][index] / 2,
                "text": raw_text,
            }
    return None


def get_text_points_on_screen(pyautogui_module, pytesseract_module, confidence=35):
    if pyautogui_module is None or pytesseract_module is None:
        return []

    screenshot = pyautogui_module.screenshot()
    try:
        data = pytesseract_module.image_to_data(
            screenshot,
            lang="rus+eng",
            output_type=pytesseract_module.Output.DICT,
            config="--psm 6",
        )
    except (pytesseract_module.TesseractNotFoundError, RuntimeError, OSError):
        return []

    points = []
    count = len(data.get("text", []))
    for index in range(count):
        raw_text = (data["text"][index] or "").strip()
        if not raw_text:
            continue
        try:
            item_confidence = float(data.get("conf", [0])[index])
        except (TypeError, ValueError):
            item_confidence = 0
        if item_confidence < confidence:
            continue
        points.append({
            "x": data["left"][index] + data["width"][index] / 2,
            "y": data["top"][index] + data["height"][index] / 2,
            "text": raw_text,
            "normalized": normalize_ocr_text(raw_text),
            "confidence": item_confidence,
        })
    return points


def click_text_or_fallback(pyautogui_module, pytesseract_module, words, fallback_relative_x, fallback_relative_y, log, description):
    point = find_text_point_on_screen(pyautogui_module, pytesseract_module, words) if pytesseract_module is not None else None
    if point:
        log(f"Start reger: на скриншоте найдены координаты поля {description} ({int(point['x'])}, {int(point['y'])}); нажимаю.")
    else:
        point = fallback_point(pyautogui_module, fallback_relative_x, fallback_relative_y)
        log(f"Start reger: координаты поля {description} через OCR не найдены, использую резервную точку ({int(point['x'])}, {int(point['y'])}).")
    pyautogui_module.moveTo(point["x"], point["y"], duration=0.2)
    pyautogui_module.click(clicks=1)
    return point


def click_random_birth_day(pyautogui_module, pytesseract_module, day_field_point, log):
    selected_day = random.randint(BIRTH_DAY_MIN, BIRTH_DAY_MAX)
    candidates = []
    if pytesseract_module is not None:
        for point in get_text_points_on_screen(pyautogui_module, pytesseract_module):
            if point["normalized"] != str(selected_day):
                continue
            if day_field_point and point["y"] <= day_field_point["y"] + 8:
                continue
            candidates.append(point)

    if candidates:
        candidates.sort(key=lambda item: item["y"])
        point = candidates[0]
        log(f"Start reger: на скриншоте найдены координаты дня {selected_day} ({int(point['x'])}, {int(point['y'])}); нажимаю.")
    else:
        if day_field_point:
            point = {"x": day_field_point["x"], "y": day_field_point["y"] + 45 + (selected_day - 1) * 28}
        else:
            point = fallback_point(pyautogui_module, 0.36, 0.58)
        log(f"Start reger: день {selected_day} через OCR не найден, нажимаю резервные координаты ({int(point['x'])}, {int(point['y'])}).")

    pyautogui_module.moveTo(point["x"], point["y"], duration=0.2)
    pyautogui_module.click(clicks=1)
    return selected_day


def fill_birth_date_after_password(pyautogui_module, pytesseract_module, log):
    time.sleep(2)
    day_point = click_text_or_fallback(pyautogui_module, pytesseract_module, DAY_FIELD_WORDS, 0.37, 0.72, log, "День")
    time.sleep(1)
    selected_day = click_random_birth_day(pyautogui_module, pytesseract_module, day_point, log)

    time.sleep(0.5)
    click_text_or_fallback(pyautogui_module, pytesseract_module, MONTH_FIELD_WORDS, 0.49, 0.72, log, "Месяц")
    pyautogui_module.press("enter")
    log("Start reger: поле Месяц нажато, затем нажата клавиша Enter для выбора месяца.")

    time.sleep(0.5)
    click_text_or_fallback(pyautogui_module, pytesseract_module, YEAR_FIELD_WORDS, 0.62, 0.72, log, "Год")
    selected_year = random.randint(BIRTH_YEAR_MIN, BIRTH_YEAR_MAX)
    pyautogui_module.write(str(selected_year), interval=0.03)
    log(f"Start reger: в поле Год введён случайный год {selected_year}.")

    time.sleep(0.5)
    final_button_point = find_blue_button_point(pyautogui_module)
    if not final_button_point:
        final_button_point = fallback_point(pyautogui_module, 0.50, 0.965)
        log("Start reger: финальная синяя кнопка на скриншоте не найдена, использую резервные координаты.")
    else:
        log(
            "Start reger: на финальном скриншоте найдена синяя кнопка "
            f"({int(final_button_point['x'])}, {int(final_button_point['y'])}); нажимаю."
        )
    pyautogui_module.moveTo(final_button_point["x"], final_button_point["y"], duration=0.2)
    pyautogui_module.click(clicks=1)
    log(f"Start reger: дата рождения выбрана: день {selected_day}, год {selected_year}; финальная синяя кнопка нажата.")


def is_microsoft_button_blue(red, green, blue):
    return 0 <= red <= 45 and 90 <= green <= 170 and 160 <= blue <= 235 and blue > red + 110 and green > red + 60


def find_blue_button_point(pyautogui_module, min_width=120, min_height=28):
    screenshot = pyautogui_module.screenshot()
    width, height = screenshot.size
    rect = get_foreground_window_rect()

    if rect:
        left = max(0, min(width - 1, rect["left"]))
        top = max(0, min(height - 1, rect["top"]))
        right = max(left + 1, min(width, rect["right"]))
        bottom = max(top + 1, min(height, rect["bottom"]))
    else:
        left, top, right, bottom = 0, 0, width, height

    image = screenshot.convert("RGB")
    pixel_access = image.load()
    scan_width = right - left
    scan_height = bottom - top
    blue_mask = bytearray(scan_width * scan_height)

    for y in range(top, bottom):
        row_offset = (y - top) * scan_width
        for x in range(left, right):
            red, green, blue = pixel_access[x, y]
            if is_microsoft_button_blue(red, green, blue):
                blue_mask[row_offset + (x - left)] = 1

    visited = bytearray(len(blue_mask))
    candidates = []
    for local_y in range(scan_height):
        for local_x in range(scan_width):
            start_index = local_y * scan_width + local_x
            if not blue_mask[start_index] or visited[start_index]:
                continue

            stack = [(local_x, local_y)]
            visited[start_index] = 1
            min_x = max_x = local_x
            min_y = max_y = local_y
            area = 0

            while stack:
                current_x, current_y = stack.pop()
                area += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)

                for next_x, next_y in (
                    (current_x + 1, current_y),
                    (current_x - 1, current_y),
                    (current_x, current_y + 1),
                    (current_x, current_y - 1),
                ):
                    if next_x < 0 or next_x >= scan_width or next_y < 0 or next_y >= scan_height:
                        continue
                    next_index = next_y * scan_width + next_x
                    if blue_mask[next_index] and not visited[next_index]:
                        visited[next_index] = 1
                        stack.append((next_x, next_y))

            component_width = max_x - min_x + 1
            component_height = max_y - min_y + 1
            if component_width >= min_width and component_height >= min_height:
                candidates.append({
                    "x": left + min_x + component_width / 2,
                    "y": top + min_y + component_height / 2,
                    "width": component_width,
                    "height": component_height,
                    "area": area,
                })

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["area"], item["width"] * item["height"]), reverse=True)
    return candidates[0]

def fallback_point(pyautogui_module, relative_x, relative_y):
    rect = get_foreground_window_rect()
    if not rect:
        width, height = pyautogui_module.size()
        return {"x": width * relative_x, "y": height * relative_y}
    return {
        "x": rect["left"] + (rect["right"] - rect["left"]) * relative_x,
        "y": rect["top"] + (rect["bottom"] - rect["top"]) * relative_y,
    }
def copy_text_to_clipboard(text):
    if os.name == "nt":
        subprocess.run(
            ["clip"],
            input=text,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return

    clipboard = QApplication.clipboard()
    if clipboard is None:
        raise RuntimeError("Не удалось получить системный буфер обмена для вставки email через Ctrl+V.")
    clipboard.setText(text)

def automate_signup_page(status_callback=None, edge_pid=None):


    def log(message):
        if status_callback:
            status_callback(message)

    pyautogui = load_optional_module("pyautogui")
    pytesseract = load_optional_module("pytesseract")
    if pyautogui is None:
        raise RuntimeError(
            "Для визуальной автоматизации нужен пакет pyautogui и доступ к экрану. "
            "Установите зависимости из requirements.txt и запускайте программу в обычной Windows-сессии."
        )
    if pytesseract is None:
        log("Start reger: pytesseract недоступен, включён резервный режим координат без OCR.")

    log("Start reger: работаю без DevTools — по PID получаю HWND окна Edge, поднимаю его поверх остальных один раз и продолжаю по координатам.")
    pyautogui.PAUSE = 0.25

    deadline = time.time() + VISUAL_AUTOMATION_TIMEOUT
    while time.time() < deadline:
        if activate_edge_window(edge_pid):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f'Не найден HWND открытого окна Microsoft Edge для PID {edge_pid} через диспетчер задач.')

    time.sleep(3)
    pyautogui.hotkey("ctrl", "l")
    pyautogui.write(TARGET_URL, interval=0.01)
    pyautogui.press("enter")
    log(f"Start reger: URL открыт напрямую: {TARGET_URL}.")
    time.sleep(5)

    title_point = None
    title_deadline = min(deadline, time.time() + 8)
    while pytesseract is not None and time.time() < title_deadline:
        title_point = find_text_point_on_screen(pyautogui, pytesseract, SIGNUP_TITLE_WORDS)
        if title_point:
            break
        time.sleep(0.5)

    if title_point:

        pyautogui.moveTo(title_point["x"], title_point["y"], duration=0.2)
        pyautogui.click(clicks=1)
    else:
        title_point = fallback_point(pyautogui, 0.50, 0.26)

        pyautogui.moveTo(title_point["x"], title_point["y"], duration=0.2)
        pyautogui.click(clicks=1)
    time.sleep(2)
    pyautogui.press("tab")
    log("Start reger: через 2 секунды после клика по заголовку нажата клавиша Tab.")

    email = generate_outlook_email()
    copy_text_to_clipboard(email)
    pyautogui.hotkey("ctrl", "v")
    log(f"Start reger: сгенерированный email {email} вставлен через Ctrl+V.")


    time.sleep(0.8)
    next_button_point = find_blue_button_point(pyautogui)
    if not next_button_point:
        next_button_point = fallback_point(pyautogui, 0.50, 0.665)
        log("Start reger: синяя кнопка на скриншоте не найдена, использую резервные координаты кнопки Далее.")
    else:
        log(
            "Start reger: на скриншоте найдена синяя кнопка "
            f"({int(next_button_point['x'])}, {int(next_button_point['y'])}); навожу мышь и нажимаю."
        )

    pyautogui.moveTo(next_button_point["x"], next_button_point["y"], duration=0.2)
    pyautogui.click(clicks=1)
    log("Start reger: кнопка Далее нажата по координатам синей кнопки.")
    time.sleep(2)
    password = generate_password()
    copy_text_to_clipboard(password)
    pyautogui.hotkey("ctrl", "v")
    log(
        "Start reger: через 2 секунды сгенерирован пароль "
        f"длиной {len(password)} символов (A-Z, a-z, 0-9) и вставлен через Ctrl+V."
    )

    time.sleep(2)
    password_next_button_point = find_blue_button_point(pyautogui)
    if not password_next_button_point:
        password_next_button_point = fallback_point(pyautogui, 0.50, 0.665)
        log("Start reger: после ввода пароля синяя кнопка на скриншоте не найдена, использую резервные координаты.")
    else:
        log(
            "Start reger: через 2 секунды после ввода пароля на скриншоте найдена синяя кнопка "
            f"({int(password_next_button_point['x'])}, {int(password_next_button_point['y'])}); навожу мышь и нажимаю."
        )

    pyautogui.moveTo(password_next_button_point["x"], password_next_button_point["y"], duration=0.2)
    pyautogui.click(clicks=1)
    log("Start reger: кнопка после ввода пароля нажата по координатам синей кнопки.")

    fill_birth_date_after_password(pyautogui, pytesseract, log)

    save_account_credentials(email, password)
    log(f"Start reger: аккаунт сохранён в {LOGPASS_FILE.name} в формате mail:password.")
    return email, password


class EdgeFinder(QObject):
    finished = Signal(str)

    def run(self):
        self.finished.emit(find_edge_auto())


class RegerRunner(QObject):
    status = Signal(str)
    account_created = Signal(str, str, str, bool, bool, bool)
    finished = Signal(bool, str)

    def __init__(self, edge_path):
        super().__init__()
        self.edge_path = edge_path

    def run(self):
        output_dir = tempfile.mkdtemp(prefix="reger-edge-")
        output_file = Path(output_dir) / "edge-startup.log"
        user_data_dir = Path(output_dir) / "profile"
        process = None

        try:
            edge_pids_before_start = get_edge_process_ids()
            with open(output_file, "w", encoding="utf-8", errors="replace") as stderr_target:
                process = subprocess.Popen(
                    build_edge_args(self.edge_path, user_data_dir),
                    **get_edge_popen_kwargs(stderr_target),
                )
                self.status.emit(f'Start reger: Microsoft Edge запущен в режиме InPrivate. Ищу PID "{EDGE_PROCESS_NAME}" через диспетчер задач.')
                edge_pid = wait_for_edge_pid(output_file, edge_pids_before_start, process.pid)

            self.status.emit(f"Start reger: найден Microsoft Edge PID {edge_pid}. Продолжаю регистрацию в этом окне.")
            email, password = automate_signup_page(self.status.emit, edge_pid)
            self.account_created.emit(email, password, "—", True, False, False)
            self.status.emit(f"Start reger: введена электронная почта {email}, затем сгенерирован пароль и нажата следующая синяя кнопка.")
            wait_until_edge_closed(edge_pid)
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
        title = QLabel("Accounts")
        title.setObjectName("accountsTitle")

        subtitle = QLabel("Список созданных аккаунтов. Email сохраняется в logpass.txt в формате mail:password.")
        subtitle.setObjectName("accountsSubtitle")
        subtitle.setWordWrap(True)

        self.accounts_table = QTableWidget(0, 3)
        self.accounts_table.setObjectName("accountsTable")
        self.accounts_table.setHorizontalHeaderLabels(["1. Емейл", "2. Steam", "3. 2FA"])
        self.accounts_table.verticalHeader().setVisible(False)
        self.accounts_table.setShowGrid(False)
        self.accounts_table.setAlternatingRowColors(True)
        self.accounts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.accounts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.accounts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.accounts_table.horizontalHeader().setHighlightSections(False)

        page_layout.setSpacing(16)
        page_layout.addWidget(self.start_button, 0)
        page_layout.addSpacing(8)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addWidget(self.accounts_table, 1)
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
            #accountsTitle { font-size: 26px; font-weight: 900; color: white; margin-top: 6px; }
            #accountsSubtitle { color: #94A3B8; font-size: 14px; }
            #accountsTable { background-color: #0F172A; alternate-background-color: #111C31; border: 1px solid #263449; border-radius: 18px; color: #E2E8F0; padding: 8px; }
            #accountsTable::item { padding: 12px; border-bottom: 1px solid #1E293B; }
            #accountsTable::item:selected { background-color: #1E40AF; color: white; }
            QHeaderView::section { background-color: #172033; color: #BAE6FD; border: none; border-bottom: 1px solid #334155; padding: 12px; font-weight: 900; }
        """)

    def add_log(self, message):
        self.logs_box.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def add_account_row(self, email, password, steam, email_ready=True, steam_ready=False, twofa_ready=False):
        row = self.accounts_table.rowCount()
        self.accounts_table.insertRow(row)
        values = [
            f"{email} {'✅' if email_ready else '❌'}",
            f"{steam or '—'} {'✅' if steam_ready else '❌'}",
            f"2FA {'✅' if twofa_ready else '❌'}",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(f"Пароль: {password}" if column == 0 else value)
            self.accounts_table.setItem(row, column, item)
        self.accounts_table.scrollToBottom()
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
        self.reger_worker.account_created.connect(self.add_account_row)
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
