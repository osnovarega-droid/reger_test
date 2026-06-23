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
PRIVACY_NOTICE_URL = "https://privacynotice.account.microsoft.com/"


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
POST_NAME_CHALLENGE_TIMEOUT = 240
POST_NAME_GRAY_BUTTON_DELAY = 2
POST_NAME_SCREENSHOT_INTERVAL = 3
SIGNUP_TITLE_WORDS = ["создание", "создан", "учет", "учёт", "записи", "майкрософт", "microsoft", "account"]
TRY_AGAIN_WORDS = ["попробуйте", "еще", "раз", "try", "again"]
PRESS_AGAIN_WORDS = ["нажмите", "снова", "press", "again"]
DAY_FIELD_WORDS = ["день", "day"]
MONTH_FIELD_WORDS = ["месяц", "month"]
YEAR_FIELD_WORDS = ["год", "year"]
BIRTH_DAY_MIN = 1
BIRTH_DAY_MAX = 18
BIRTH_YEAR_MIN = 1980
BIRTH_YEAR_MAX = 2008
GRAY_BUTTON_RGB = (112, 112, 112)
GRAY_BUTTON_COLOR_TOLERANCE = 0

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

def load_account_credentials():
    accounts = []
    if not LOGPASS_FILE.exists():
        return accounts

    try:
        lines = LOGPASS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return accounts

    for line in lines:
        line = line.strip()
        if not line or ":" not in line:
            continue

        email, password = line.split(":", 1)
        email = email.strip()
        password = password.strip()
        if email and password:
            accounts.append((email, password))

    return accounts


def save_account_credentials(email, password):
    LOGPASS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOGPASS_FILE, "a", encoding="utf-8") as file:
        file.write(f"{email}:{password}\n")
        file.flush()
        os.fsync(file.fileno())

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

def find_phrase_point_on_screen(pyautogui_module, pytesseract_module, words, confidence=35, max_gap=4):
    points = get_text_points_on_screen(pyautogui_module, pytesseract_module, confidence)
    if not points:
        return None

    normalized_words = [normalize_ocr_text(word) for word in words if normalize_ocr_text(word)]
    if not normalized_words:
        return None

    full_text = " ".join(point["normalized"] for point in points)
    if all(word in full_text for word in normalized_words):
        matching_points = [
            point for point in points
            if any(word in point["normalized"] or point["normalized"] in word for word in normalized_words)
        ]
        if matching_points:
            return {
                "x": sum(point["x"] for point in matching_points) / len(matching_points),
                "y": sum(point["y"] for point in matching_points) / len(matching_points),
                "text": " ".join(point["text"] for point in matching_points),
            }

    for start_index in range(len(points)):
        phrase_parts = []
        phrase_points = []
        for point in points[start_index:start_index + len(normalized_words) + max_gap]:
            phrase_parts.append(point["normalized"])
            phrase_points.append(point)
            phrase_text = "".join(phrase_parts)
            if all(word in phrase_text for word in normalized_words):
                return {
                    "x": sum(item["x"] for item in phrase_points) / len(phrase_points),
                    "y": sum(item["y"] for item in phrase_points) / len(phrase_points),
                    "text": " ".join(item["text"] for item in phrase_points),
                }
    return None

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


def select_dropdown_with_random_downs(pyautogui_module, field_name, log):
    down_presses = random.randint(1, 10)
    for _ in range(down_presses):
        pyautogui_module.press("down")
        time.sleep(0.05)
    pyautogui_module.press("enter")
    log(f"Start reger: поле {field_name} выбрано через {down_presses} случайных нажатий стрелки вниз и Enter.")
    return down_presses
def get_display_names_from_email(email):
    local_part = email.split("@", 1)[0]
    name_part = "".join(character for character in local_part if not character.isdigit())
    first_name, separator, last_name = name_part.partition("_")
    if not separator or not first_name or not last_name:
        return None, None
    return first_name.capitalize(), last_name.capitalize()
    return first_name.capitalize(), last_name.capitalize()

def fill_birth_date_after_password(pyautogui_module, pytesseract_module, log):
    time.sleep(2)
    click_text_or_fallback(pyautogui_module, pytesseract_module, DAY_FIELD_WORDS, 0.37, 0.72, log, "День")
    time.sleep(1)
    selected_day_downs = select_dropdown_with_random_downs(pyautogui_module, "День", log)

    time.sleep(0.5)
    click_text_or_fallback(pyautogui_module, pytesseract_module, MONTH_FIELD_WORDS, 0.49, 0.72, log, "Месяц")
    time.sleep(0.5)
    selected_month_downs = select_dropdown_with_random_downs(pyautogui_module, "Месяц", log)

    time.sleep(0.5)
    click_text_or_fallback(pyautogui_module, pytesseract_module, YEAR_FIELD_WORDS, 0.62, 0.72, log, "Год")
    selected_year = random.randint(BIRTH_YEAR_MIN, BIRTH_YEAR_MAX)
    pyautogui_module.write(str(selected_year), interval=0.03)
    log(f"Start reger: в поле Год введён случайный год {selected_year}.")

    log("Start reger: после ввода года сильно прокручиваю страницу вниз 3 секунды перед поиском финальной синей кнопки.")
    scroll_deadline = time.time() + 3
    while time.time() < scroll_deadline:
        pyautogui_module.scroll(-10)
        time.sleep(0.1)

    time.sleep(0.5)
    log("Start reger: после прокрутки делаю скриншот браузера и ищу финальную синюю кнопку.")
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
    log(
        "Start reger: дата рождения выбрана: "
        f"день после {selected_day_downs} нажатий вниз, месяц после {selected_month_downs} нажатий вниз, "
        f"год {selected_year}; финальная синяя кнопка нажата."
    )

    time.sleep(1)
    log("Start reger: после синей кнопки ищу на скриншоте серую кнопку по цвету RGB (112,112,112) / #707070.")
    gray_button_point = find_gray_button_point(pyautogui_module)
    if gray_button_point:
        log(
            "Start reger: найден цвет серой кнопки #707070 "
            f"({int(gray_button_point['x'])}, {int(gray_button_point['y'])}); нажимаю."
        )
        pyautogui_module.moveTo(gray_button_point["x"], gray_button_point["y"], duration=0.2)
        pyautogui_module.click(clicks=1)
    else:
        log("Start reger: серый цвет #707070 на скриншоте не найден; продолжаю дальше по логике без резервного клика.")

def fill_name_after_birth_date(pyautogui_module, email, log):
    first_name, last_name = get_display_names_from_email(email)
    if not first_name or not last_name:
        log(f"Start reger: не удалось разобрать имя и фамилию из логина {email}; пропускаю ввод имени.")
        return

    time.sleep(2)
    copy_text_to_clipboard(first_name)
    pyautogui_module.hotkey("ctrl", "v")
    log(f"Start reger: из созданного логина {email} определено имя {first_name}; имя вставлено через Ctrl+V.")

    pyautogui_module.press("tab")
    copy_text_to_clipboard(last_name)
    pyautogui_module.hotkey("ctrl", "v")
    log(f"Start reger: после Tab введена фамилия {last_name} из созданного логина.")

    time.sleep(0.8)
    log("Start reger: после ввода имени и фамилии делаю скриншот браузера и ищу синюю кнопку.")
    name_button_point = find_blue_button_point(pyautogui_module)
    if not name_button_point:
        name_button_point = fallback_point(pyautogui_module, 0.50, 0.665)
        log("Start reger: синяя кнопка после ввода имени на скриншоте не найдена, использую резервные координаты.")
    else:
        log(
            "Start reger: на скриншоте после ввода имени найдена синяя кнопка "
            f"({int(name_button_point['x'])}, {int(name_button_point['y'])}); нажимаю."
        )

    pyautogui_module.moveTo(name_button_point["x"], name_button_point["y"], duration=0.2)
    pyautogui_module.click(clicks=1)
    log("Start reger: синяя кнопка после ввода имени и фамилии нажата.")
    
def is_microsoft_button_blue(red, green, blue):
    return 0 <= red <= 45 and 90 <= green <= 170 and 160 <= blue <= 235 and blue > red + 110 and green > red + 60

def is_target_gray_button_color(red, green, blue):
    target_red, target_green, target_blue = GRAY_BUTTON_RGB
    return (
        abs(red - target_red) <= GRAY_BUTTON_COLOR_TOLERANCE
        and abs(green - target_green) <= GRAY_BUTTON_COLOR_TOLERANCE
        and abs(blue - target_blue) <= GRAY_BUTTON_COLOR_TOLERANCE
    )


def find_colored_button_point(pyautogui_module, color_matcher, min_width=40, min_height=18):
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
    color_mask = bytearray(scan_width * scan_height)

    for y in range(top, bottom):
        row_offset = (y - top) * scan_width
        for x in range(left, right):
            red, green, blue = pixel_access[x, y]
            if color_matcher(red, green, blue):
                color_mask[row_offset + (x - left)] = 1

    visited = bytearray(len(color_mask))
    candidates = []
    for local_y in range(scan_height):
        for local_x in range(scan_width):
            start_index = local_y * scan_width + local_x
            if not color_mask[start_index] or visited[start_index]:
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
                    if color_mask[next_index] and not visited[next_index]:
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


def find_gray_button_point(pyautogui_module):
    return find_colored_button_point(pyautogui_module, is_target_gray_button_color)
    

def is_microsoft_button_gray(red, green, blue):
    return (
        85 <= red <= 170
        and 85 <= green <= 170
        and 85 <= blue <= 170
        and abs(red - green) <= 18
        and abs(green - blue) <= 18
        and abs(red - blue) <= 18
    )


def is_error_text_red(red, green, blue):
    return red >= 150 and green <= 95 and blue <= 95 and red > green + 60 and red > blue + 60


def find_colored_component_point(pyautogui_module, color_predicate, min_width=120, min_height=28):
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
    color_mask = bytearray(scan_width * scan_height)

    for y in range(top, bottom):
        row_offset = (y - top) * scan_width
        for x in range(left, right):
            red, green, blue = pixel_access[x, y]
            if color_predicate(red, green, blue):
                color_mask[row_offset + (x - left)] = 1

    visited = bytearray(len(color_mask))
    candidates = []
    for local_y in range(scan_height):
        for local_x in range(scan_width):
            start_index = local_y * scan_width + local_x
            if not color_mask[start_index] or visited[start_index]:
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
                    if color_mask[next_index] and not visited[next_index]:
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

def find_blue_button_point(pyautogui_module, min_width=120, min_height=28):
    return find_colored_component_point(pyautogui_module, is_microsoft_button_blue, min_width, min_height)


def find_gray_button_point(pyautogui_module, min_width=80, min_height=26):
    return find_colored_component_point(pyautogui_module, is_microsoft_button_gray, min_width, min_height)


def find_red_text_point(pyautogui_module, min_width=20, min_height=6):
    return find_colored_component_point(pyautogui_module, is_error_text_red, min_width, min_height)
    
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

def get_clipboard_text():
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return ""
    return clipboard.text() or ""


def get_browser_url(pyautogui_module):
    previous_clipboard_text = get_clipboard_text()
    pyautogui_module.hotkey("ctrl", "l")
    time.sleep(0.1)
    pyautogui_module.hotkey("ctrl", "c")
    time.sleep(0.1)
    current_url = get_clipboard_text().strip()
    if previous_clipboard_text != current_url:
        copy_text_to_clipboard(previous_clipboard_text)
    pyautogui_module.press("esc")
    return current_url


def is_privacy_notice_open(pyautogui_module):
    current_url = get_browser_url(pyautogui_module)
    return current_url.lower().startswith(PRIVACY_NOTICE_URL.lower()), current_url


def click_gray_button_after_name(pyautogui_module, log):
    time.sleep(POST_NAME_GRAY_BUTTON_DELAY)
    log("Start reger: через 2 секунды после синей кнопки делаю скриншот браузера и ищу серую кнопку.")
    gray_button_point = find_gray_button_point(pyautogui_module)
    if not gray_button_point:
        gray_button_point = fallback_point(pyautogui_module, 0.50, 0.73)
        log("Start reger: серая кнопка на скриншоте не найдена, использую резервные координаты.")
    else:
        log(
            "Start reger: на скриншоте найдена серая кнопка "
            f"({int(gray_button_point['x'])}, {int(gray_button_point['y'])}); нажимаю."
        )
    pyautogui_module.moveTo(gray_button_point["x"], gray_button_point["y"], duration=0.2)
    pyautogui_module.click(clicks=1)


def handle_post_name_challenge(pyautogui_module, pytesseract_module, log):
    click_gray_button_after_name(pyautogui_module, log)
    deadline = time.time() + POST_NAME_CHALLENGE_TIMEOUT

    while time.time() < deadline:
        time.sleep(POST_NAME_SCREENSHOT_INTERVAL)
        log('Start reger: прошло 3 секунды, делаю скриншот браузера и проверяю кнопку "Нажмите снова".')

        privacy_open, current_url = is_privacy_notice_open(pyautogui_module)
        if privacy_open:
            log(f"Start reger: открыта целевая ссылка {current_url}.")
            return True

        press_again_point = None
        if pytesseract_module is not None:
            press_again_point = find_phrase_point_on_screen(pyautogui_module, pytesseract_module, PRESS_AGAIN_WORDS)
        if not press_again_point:
            continue

        blue_button_point = find_blue_button_point(pyautogui_module)
        click_point = blue_button_point or press_again_point
        log(
            'Start reger: найдена синяя кнопка с текстом "Нажмите снова"; '
            f"нажимаю ({int(click_point['x'])}, {int(click_point['y'])})."
        )
        pyautogui_module.moveTo(click_point["x"], click_point["y"], duration=0.2)
        pyautogui_module.click(clicks=1)

        time.sleep(1)
        log('Start reger: после нажатия "Нажмите снова" делаю скриншот и проверяю красный текст "Попробуйте еще раз".')
        try_again_point = None
        if pytesseract_module is not None:
            try_again_point = find_phrase_point_on_screen(pyautogui_module, pytesseract_module, TRY_AGAIN_WORDS)
        if not try_again_point:
            try_again_point = find_red_text_point(pyautogui_module)

        if try_again_point:
            screenshot_path = APP_DIR / f"try_again_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            pyautogui_module.screenshot().save(screenshot_path)
            log(
                'Start reger: найден красный текст "Попробуйте еще раз"; '
                f"скриншот сохранён в {screenshot_path.name}, заново нажимаю серую кнопку."
            )
            click_gray_button_after_name(pyautogui_module, log)

    raise RuntimeError(
        f'Не удалось дождаться открытия ссылки "{PRIVACY_NOTICE_URL}" '
        f"за {POST_NAME_CHALLENGE_TIMEOUT} секунд после ввода имени и фамилии."
    )
    
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
    if not activate_edge_window(edge_pid):
        raise RuntimeError(f'Не удалось повторно активировать HWND окна Microsoft Edge для PID {edge_pid} перед вводом URL.')
    log("Start reger: перед вводом URL окно Edge повторно поднято и активировано через HWND.")
    time.sleep(0.5)
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
    save_account_credentials(email, password)
    log(f"Start reger: email и пароль сохранены в {LOGPASS_FILE.name} в формате mail:password сразу после ввода пароля.")

    fill_birth_date_after_password(pyautogui, pytesseract, log)
    fill_name_after_birth_date(pyautogui, email, log)
    handle_post_name_challenge(pyautogui, pytesseract, log)
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
        self.load_saved_accounts()
        
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

    def load_saved_accounts(self):
        accounts = load_account_credentials()
        for email, password in accounts:
            self.add_account_row(email, password, "—", True, False, False)

        if accounts:
            self.add_log(f"Загружено аккаунтов из {LOGPASS_FILE.name}: {len(accounts)}.")
        else:
            self.add_log(f"Сохранённые аккаунты в {LOGPASS_FILE.name} не найдены.")
            
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
