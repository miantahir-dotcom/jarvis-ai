import sys
import os
import re
import queue
import threading
import traceback
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import platform
import urllib.request
import webbrowser
import time

import psutil
import speech_recognition as sr
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTextEdit, QFrame, QProgressBar, QSizePolicy, QStackedWidget,
    QComboBox, QSpinBox, QCheckBox, QLineEdit, QMessageBox, QScrollArea, QFileDialog,
    QDateTimeEdit, QListWidget, QListWidgetItem, QInputDialog
)

APP_NAME = "JARVIS"

def open_chatgpt_with_prompt(prompt, log_callback=None):
    """Open ChatGPT, focus its composer, and paste the user's prompt for review.

    It deliberately does not press Send. Browser layouts/load times vary, so if
    focus automation fails the prompt remains on the clipboard for manual paste.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        if log_callback:
            log_callback("CHATGPT: Say 'ChatGPT prompt' followed by what you want ChatGPT to do.")
        return False
    try:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Qt application clipboard is unavailable")
        app.clipboard().setText(prompt)
        webbrowser.open("https://chatgpt.com/", new=2)
        if log_callback:
            log_callback("CHATGPT: Opened ChatGPT. Preparing your prompt; please wait…")
        time.sleep(9)
        try:
            import pyautogui
            # Best-effort focus of the message composer. Never click Send.
            width, height = pyautogui.size()
            pyautogui.click(int(width * 0.52), int(height * 0.86))
            time.sleep(0.4)
            pyautogui.hotkey("ctrl", "v")
            if log_callback:
                log_callback("CHATGPT: Prompt pasted. Check it, then press Send when ready.")
            return True
        except Exception as exc:
            if log_callback:
                log_callback(f"CHATGPT: Prompt copied to clipboard, but auto-paste could not finish ({exc}). Click ChatGPT's message box and press Ctrl+V.")
            return True
    except Exception as exc:
        if log_callback:
            log_callback(f"CHATGPT ERROR: {exc}")
        return False
# Supports both layouts: desktop_app.py beside main.py, or inside desktop_app/
_THIS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = _THIS_DIR if (_THIS_DIR / "main.py").exists() else _THIS_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def find_dashboard_wallpaper():
    """Find the optional dashboard wallpaper across common project layouts."""
    candidates = (
        PROJECT_DIR / "assets" / "dashboard_scenery.png",
        _THIS_DIR / "assets" / "dashboard_scenery.png",
        PROJECT_DIR / "dashboard_scenery.png",
        _THIS_DIR / "dashboard_scenery.png",
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            # Qt stylesheets expect a filesystem path here, not a file:// URL.
            # Forward slashes are accepted by Qt on Windows and avoid pixmap errors.
            return candidate.resolve().as_posix()
    return ""


DASHBOARD_WALLPAPER_URL = find_dashboard_wallpaper()

try:
    import main as jarvis_core
    CORE_IMPORT_ERROR = None
except Exception as exc:
    jarvis_core = None
    CORE_IMPORT_ERROR = str(exc)

STYLE = """
QWidget { background:#070b18; color:#eff7ff; font-family:'Segoe UI'; font-size:12px; }
QFrame#Card { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #14284a,stop:0.45 #101a35,stop:1 #24143c); border:1px solid #2869a8; border-radius:20px; }
QFrame#Card:hover { border:1px solid #22d3ee; }
QFrame#Sidebar { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #241044,stop:0.35 #111b38,stop:0.72 #092b43,stop:1 #10142b); border-right:2px solid #00d9ff; }
QLabel { background:transparent; border:0; }
QLabel#Brand { color:#67f5ff; font-size:25px; font-weight:900; letter-spacing:3px; }
QLabel#Subtitle { color:#9bc9ef; font-size:11px; }
QLabel#SectionTitle { color:#63eaff; font-size:17px; font-weight:900; }
QLabel#SectionHint { color:#b4c9eb; font-size:12px; }
QLabel#MetricValue { color:#ffffff; font-size:29px; font-weight:900; }
QLabel#Status { color:#39f5b0; font-size:13px; font-weight:900; }
QLabel#Hero { color:#8beaff; font-size:32px; font-weight:950; padding:2px; }
QLabel#FocusTime { color:#8beaff; font-size:36px; font-weight:900; letter-spacing:2px; padding:5px; }
QLabel#FocusHint { color:#b4c9eb; font-size:12px; }
QLabel#ReminderHighlight { color:#fff0b4; font-size:13px; font-weight:800; padding:9px 11px; background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3d2555,stop:1 #173a55); border:1px solid #d56bff; border-radius:11px; }
QListWidget { background:#09152a; border:1px solid #5865e8; border-radius:12px; padding:7px; color:#edf5ff; }
QListWidget::item { padding:9px; border-bottom:1px solid #253c63; border-radius:5px; }
QListWidget::item:selected { background:#5237a8; color:white; }
QToolTip { background:#21133e; color:#ffffff; border:1px solid #4deaff; padding:7px; border-radius:6px; }
QPushButton { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #163d62,stop:0.55 #292b69,stop:1 #45215e); color:#f8fbff; border:1px solid #3989c7; border-radius:12px; padding:10px 12px; font-weight:750; min-height:28px; }
QPushButton:hover { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #007fbd,stop:0.5 #6551d9,stop:1 #c13fa8); border:1px solid #a6faff; }
QPushButton:pressed { background:#0ea5e9; border:1px solid #ffffff; padding-top:11px; }
QPushButton:disabled { color:#7587a8; border-color:#293c59; background:#111a2c; }
QPushButton#PrimaryButton { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #00d5f2,stop:0.42 #247cff,stop:0.75 #7955ff,stop:1 #d946ef); border:1px solid #b5fbff; color:white; font-size:13px; font-weight:900; }
QPushButton#PrimaryButton:hover { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00f5d4,stop:0.5 #3b82f6,stop:1 #ec4899); }
QPushButton#DangerButton { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #fb477e,stop:1 #9d174d); border:1px solid #ff9fbd; color:white; font-size:13px; font-weight:900; }
QPushButton#NavButton { text-align:left; padding:13px 12px; border:1px solid transparent; background:transparent; font-size:13px; border-radius:12px; }
QPushButton#NavButton:hover, QPushButton#NavButton[active="true"] { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #453080,stop:0.6 #164b72,stop:1 #0e6976); border:1px solid #4deaff; }
QTextEdit { background:#071326; border:1px solid #3566b4; border-radius:13px; color:#83ffc2; padding:11px; font-family:Consolas; font-size:11px; selection-background-color:#6044bc; }
QProgressBar { background:#172746; border:1px solid #244f76; border-radius:7px; text-align:center; min-height:15px; max-height:15px; color:#ffffff; }
QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00d4ff,stop:0.35 #3b82f6,stop:0.68 #a855f7,stop:1 #ff59c7); border-radius:6px; }
QComboBox, QSpinBox, QLineEdit, QDateTimeEdit { background:#111d39; border:1px solid #5867d9; border-radius:9px; padding:8px; color:#f5f7ff; min-height:21px; selection-background-color:#6941c6; }
QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QDateTimeEdit:focus { border:1px solid #6df4ff; background:#132747; }
QComboBox QAbstractItemView { background:#111d39; color:#f5f7ff; selection-background-color:#6344c8; border:1px solid #5b6ee1; }
QCheckBox { spacing:8px; color:#dcecff; }
QCheckBox::indicator { width:16px; height:16px; }
QCheckBox::indicator:unchecked { background:#101c35; border:1px solid #6682d8; border-radius:4px; }
QCheckBox::indicator:checked { background:#00c9ef; border:1px solid #8af8ff; border-radius:4px; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:#091326; width:12px; margin:3px; border-radius:6px; }
QScrollBar::handle:vertical { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #00d4ff,stop:1 #9155f7); min-height:35px; border-radius:5px; }
QScrollBar::handle:vertical:hover { background:#e879f9; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:#091326; height:10px; border-radius:5px; }
QScrollBar::handle:horizontal { background:#5965d8; min-width:25px; border-radius:5px; }
"""

class JarvisWorker(QThread):
    log_signal = Signal(str)
    answer_signal = Signal(str)
    status_signal = Signal(str)
    listening_signal = Signal(bool)
    reminder_request = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.listening = False
        self.commands = queue.Queue()
        self.language = "en-PK"
        self.phrase_limit = 8
        self._listen_lock = threading.Lock()

    def send_command(self, command):
        self.commands.put(command)

    def configure(self, language=None, phrase_limit=None):
        if language:
            self.language = language
        if phrase_limit:
            self.phrase_limit = int(phrase_limit)
        self.log_signal.emit(f"Voice settings applied: language={self.language}, phrase limit={self.phrase_limit}s")

    def set_listening(self, enabled):
        self.listening = bool(enabled)
        self.status_signal.emit("LISTENING" if enabled else "READY")
        self.listening_signal.emit(bool(enabled))
        self.log_signal.emit("Voice listening enabled." if enabled else "Voice listening paused.")

    def execute_command(self, command):
        command = (command or "").strip()
        if not command:
            return
        self.log_signal.emit(f"YOU: {command}")
        # Natural-language shortcut: "ChatGPT prompt <text>" or
        # "open ChatGPT and write <text>". Never auto-submit external content.
        lower_command = command.lower().strip()
        prompt = None
        prompt_prefixes = (
            "chatgpt prompt", "open chatgpt and write", "open chatgpt and type",
            "chatgpt ko prompt do", "chatgpt mein likho", "chatgpt se kaho",
            "chatgpt se bolo", "chatgpt se poochho", "ask chatgpt",
            "tell chatgpt", "chatgpt,", "chat gpt prompt",
        )
        for prefix in prompt_prefixes:
            if lower_command.startswith(prefix):
                prompt = command[len(prefix):].strip(" :,-")
                break
        if prompt is not None:
            threading.Thread(
                target=open_chatgpt_with_prompt,
                args=(prompt, self.log_signal.emit),
                daemon=True,
            ).start()
            self.answer_signal.emit("Opening ChatGPT and preparing your prompt. Please review it and press Send yourself.")
            return
        if lower_command in ("open chatgpt", "chatgpt kholo", "chat gpt kholo"):
            webbrowser.open("https://chatgpt.com/", new=2)
            self.log_signal.emit("CHATGPT: Opened ChatGPT in your default browser.")
            self.answer_signal.emit("ChatGPT is opening in your browser.")
            return
        reminder = self.parse_reminder_command(command)
        if reminder:
            reminder_text, due = reminder
            self.reminder_request.emit(reminder_text, due.isoformat())
            confirmation = f"Okay, I will remind you about {reminder_text} at {due:%I:%M %p} on {due:%d %B}."
            self.answer_signal.emit(confirmation)
            if jarvis_core is not None and hasattr(jarvis_core, "speak"):
                threading.Thread(target=jarvis_core.speak, args=(confirmation,), daemon=True).start()
            return
        if jarvis_core is None:
            self.log_signal.emit("MAIN.PY IMPORT ERROR: " + str(CORE_IMPORT_ERROR))
            self.answer_signal.emit("I could not load main.py. Please check the project folder.")
            return
        try:
            if hasattr(jarvis_core, "process_command"):
                jarvis_core.process_command(command)
            else:
                self.log_signal.emit("ERROR: main.py does not contain process_command().")
        except SystemExit:
            self.log_signal.emit("Exit command received; dashboard remains open.")
        except Exception as exc:
            self.log_signal.emit(f"COMMAND ERROR: {exc}")
            self.log_signal.emit(traceback.format_exc())

    @staticmethod
    def parse_reminder_command(command):
        """Parse common English/Roman Urdu reminder requests."""
        text = re.sub(r"\s+", " ", (command or "").strip().lower())
        intent = any(k in text for k in ("remind me", "set reminder", "set a reminder", "reminder", "yaad dila", "yaad dilana", "yaad karwana"))
        now = datetime.now().replace(second=0, microsecond=0)
        match = re.search(r"(?:remind me|set (?:a )?reminder) in (\d+) (seconds?|minutes?|hours?) to (.+)$", text) if intent else None
        if match:
            amount, unit, task = match.groups()
            factor = 3600 if unit.startswith("hour") else 60 if unit.startswith("minute") else 1
            if task.strip():
                return task.strip().capitalize(), now + timedelta(seconds=int(amount) * factor)
        match = re.search(r"(?:remind me|set (?:a )?reminder) (tomorrow )?at (\d{1,2})(?::(\d{2}))?\s*(am|pm)(?: to | for | about )(.+)$", text) if intent else None
        if match:
            tomorrow, hour, minute, meridiem, task = match.groups()
            hour, minute = int(hour), int(minute or 0)
            if 1 <= hour <= 12 and 0 <= minute <= 59 and task.strip():
                hour = hour % 12 + (12 if meridiem == "pm" else 0)
                due = now.replace(hour=hour, minute=minute)
                if tomorrow:
                    due += timedelta(days=1)
                elif due <= now:
                    due += timedelta(days=1)
                return task.strip().capitalize(), due
        # Examples: "reminder study networking 9 pm" or "Study networking 9 baje".
        match = re.search(r"(?:(?:reminder|yaad dila(?:na)?|yaad karwa(?:na)?)\s+)?(.+?)\s+(?:at )?(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)(?:\s|$)", text) if (intent or "baje" in text) else None
        if match:
            task, hour, minute, meridiem = match.groups()
            hour, minute = int(hour), int(minute or 0)
            if meridiem == "baje":
                # In this app's common Roman Urdu reminder phrasing, 1-11 baje means evening.
                hour = hour + 12 if 1 <= hour <= 11 else hour
            else:
                hour = hour % 12 + (12 if meridiem == "pm" else 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59 and task.strip():
                due = now.replace(hour=hour, minute=minute)
                if due <= now:
                    due += timedelta(days=1)
                return task.strip().capitalize(), due
        return None

    def run(self):
        pycom = None
        microphone = None
        original_speak = None
        source = None
        try:
            try:
                import pythoncom
                pycom = pythoncom
                pycom.CoInitialize()
            except Exception:
                pass

            if jarvis_core is not None and hasattr(jarvis_core, "speak"):
                original_speak = jarvis_core.speak
                def gui_speak(message):
                    self.answer_signal.emit(str(message))
                    try:
                        original_speak(message)
                    except Exception as exc:
                        self.log_signal.emit(f"TTS ERROR: {exc}")
                jarvis_core.speak = gui_speak

            if jarvis_core is not None and hasattr(jarvis_core, "initialize_ai"):
                def init_ai():
                    try:
                        jarvis_core.initialize_ai()
                        self.log_signal.emit("AI initialization completed.")
                    except Exception as exc:
                        self.log_signal.emit(f"AI initialization warning: {exc}")
                threading.Thread(target=init_ai, daemon=True).start()

            recognizer = getattr(jarvis_core, "recognizer", None) if jarvis_core is not None else None
            if recognizer is None:
                recognizer = sr.Recognizer()
                if jarvis_core is not None:
                    jarvis_core.recognizer = recognizer
            recognizer.dynamic_energy_threshold = True
            recognizer.pause_threshold = 0.8
            recognizer.phrase_threshold = 0.3
            recognizer.non_speaking_duration = 0.5

            self.log_signal.emit("Checking microphone...")
            try:
                microphone = sr.Microphone()
                source = microphone.__enter__()
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.7)
                except Exception as exc:
                    self.log_signal.emit(f"Microphone calibration note: {exc}")
                self.log_signal.emit("Microphone ready. Press START LISTENING to talk.")
            except Exception as exc:
                microphone = None
                source = None
                self.log_signal.emit(f"MICROPHONE UNAVAILABLE: {exc}")
                self.log_signal.emit("Typed commands and dashboard actions will still work; check Windows microphone permissions/device.")
            self.status_signal.emit("READY")

            while self.running:
                try:
                    command = self.commands.get_nowait()
                    self.execute_command(command)
                    continue
                except queue.Empty:
                    pass

                if not self.listening:
                    self.msleep(80)
                    continue

                if source is None:
                    self.status_signal.emit("ERROR")
                    self.log_signal.emit("Voice listening cannot start because no microphone is available. Typed commands remain enabled.")
                    self.listening = False
                    self.listening_signal.emit(False)
                    continue

                try:
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=self.phrase_limit)
                    heard = ""
                    last_recognition_error = None
                    # Regional English is usually better for Pakistani accents; fall back
                    # to Indian English and Urdu if the first recognizer cannot parse it.
                    language_candidates = list(dict.fromkeys((self.language, "en-PK", "en-IN", "ur-PK")))
                    for language_code in language_candidates:
                        try:
                            heard = recognizer.recognize_google(audio, language=language_code).lower().strip()
                            if heard:
                                break
                        except sr.RequestError as recognition_error:
                            # Stop on service/network errors instead of wasting time retrying identical audio.
                            last_recognition_error = recognition_error
                            break
                        except sr.UnknownValueError as recognition_error:
                            # Try a fallback locale only when recognition service is available.
                            last_recognition_error = recognition_error
                            continue
                    if not heard and isinstance(last_recognition_error, sr.RequestError):
                        raise last_recognition_error
                    if heard and self.running:
                        self.log_signal.emit(f"HEARD ({language_code}): {heard}")
                        self.execute_command(heard)
                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    self.log_signal.emit(f"SPEECH SERVICE ERROR: {exc}")
                    self.msleep(1000)
                except Exception as exc:
                    self.log_signal.emit(f"MICROPHONE ERROR: {exc}")
                    self.msleep(500)
        except Exception as exc:
            self.log_signal.emit(f"VOICE WORKER ERROR: {exc}")
            self.log_signal.emit(traceback.format_exc())
            self.status_signal.emit("ERROR")
        finally:
            if jarvis_core is not None and original_speak is not None:
                try:
                    jarvis_core.speak = original_speak
                except Exception:
                    pass
            if microphone is not None:
                try:
                    microphone.__exit__(None, None, None)
                except Exception:
                    pass
            if pycom is not None:
                try:
                    pycom.CoUninitialize()
                except Exception:
                    pass
            if not self.running:
                self.status_signal.emit("STOPPED")
            else:
                self.status_signal.emit("ERROR")
            self.log_signal.emit("JARVIS voice worker stopped.")

    def stop_worker(self):
        self.running = False
        self.listening = False
        if self.isRunning():
            self.wait(4000)


class JarvisWindow(QWidget):
    weather_result = Signal(dict)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.weather_result.connect(self.apply_weather_result)
        self.nav_buttons = {}
        self.auto_scroll = True
        self.focus_seconds = 25 * 60
        self.focus_running = False
        self.reminders = []
        self.reminder_file = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "reminders.json"
        self.load_reminders()
        self.notes = []
        self.notes_file = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "notes.json"
        self._net_previous = psutil.net_io_counters()
        self._net_previous_at = datetime.now()
        self.load_notes()
        self.setWindowTitle("JARVIS AI • Futuristic Desktop Assistant")
        if DASHBOARD_WALLPAPER_URL:
            self.setWindowIcon(QIcon(DASHBOARD_WALLPAPER_URL))
        self.setMinimumSize(980, 620)
        self.resize(1440, 810)
        self.setStyleSheet(STYLE)
        self.build_ui()
        self.load_saved_settings()
        self.note_save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.note_save_shortcut.activated.connect(self.save_note_shortcut)
        self.command_focus_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.command_focus_shortcut.activated.connect(self.focus_command_input)
        self.voice_shortcut = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        self.voice_shortcut.activated.connect(self.toggle_listening)
        self.dashboard_shortcut = QShortcut(QKeySequence("Ctrl+1"), self)
        self.dashboard_shortcut.activated.connect(lambda: self.show_page("Dashboard"))
        self.notes_shortcut = QShortcut(QKeySequence("Ctrl+2"), self)
        self.notes_shortcut.activated.connect(lambda: self.show_page("Quick Notes"))
        self.monitor_shortcut = QShortcut(QKeySequence("Ctrl+3"), self)
        self.monitor_shortcut.activated.connect(lambda: self.show_page("System Monitor"))
        self.voice_page_shortcut = QShortcut(QKeySequence("Ctrl+4"), self)
        self.voice_page_shortcut.activated.connect(lambda: self.show_page("Voice Control"))
        self.start_worker()
        if self.worker:
            self.worker.configure(self.language_combo.currentData(), self.phrase_spin.value())
        # Prime psutil once: its first CPU sample is commonly 0.0 by design.
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        self._battery_alert_latched = False
        self._performance_high_samples = 0
        self._performance_alert_latched = False
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.update_telemetry)
        # First interval-based sample is taken after the timer has had a full second.
        self.telemetry_timer.start(1000)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.fetch_weather_async)
        self.weather_timer.start(15 * 60 * 1000)
        self.fetch_weather_async()
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(1000)
        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self.tick_focus_timer)
        # Prime visible telemetry with a short real CPU sample; never block the UI for long.
        QTimer.singleShot(250, self.update_telemetry)
        self.update_clock()
        self.show_page("Dashboard")
        self.add_log("Dashboard ready: Ctrl+K command input • Ctrl+Shift+M microphone • Ctrl+1 Dashboard • Ctrl+2 Notes • Ctrl+3 Monitor • Ctrl+4 Voice.")
        if getattr(self, "start_minimized_checkbox", None) and self.start_minimized_checkbox.isChecked():
            QTimer.singleShot(0, self.showMinimized)

    def card(self):
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return frame

    def build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 15)
        side.setSpacing(9)
        logo = QLabel("◉")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("color:#19d8ff;font-size:54px;border:2px solid #16d9ff;border-radius:44px;background:#081b3c;padding:8px;")
        brand = QLabel("J.A.R.V.I.S")
        brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("PERSONAL AI ASSISTANT")
        sub.setObjectName("Subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(logo)
        side.addWidget(brand)
        side.addWidget(sub)
        side.addSpacing(20)
        for label, icon in [("Dashboard", "⌂"), ("Voice Control", "🎙"), ("Quick Actions", "⚡"), ("System Monitor", "📊"), ("Reminders", "🔔"), ("Focus Sprint", "⏱"), ("Quick Notes", "📝"), ("Settings", "⚙"), ("About", "ⓘ")]:
            button = QPushButton(f"{icon}   {label}")
            button.setObjectName("NavButton")
            button.setProperty("active", False)
            button.clicked.connect(lambda checked=False, name=label: self.show_page(name))
            self.nav_buttons[label] = button
            side.addWidget(button)
        side.addStretch(1)
        tagline = QLabel("Smarter\nFaster\nAlways With You")
        tagline.setStyleSheet("font-size:15px;color:#d5efff;")
        side.addWidget(tagline)
        side.addWidget(QLabel("〰 〰 〰"))
        outer.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(18, 12, 18, 12)
        content.setSpacing(12)
        outer.addLayout(content, 1)

        header = self.card()
        scenic_asset = DASHBOARD_WALLPAPER_URL
        wallpaper_style = f'background-image:url("{scenic_asset}"); background-position:center; background-repeat:no-repeat;' if scenic_asset else ""
        header.setStyleSheet(f'QFrame#Card {{ {wallpaper_style} background-color:#07152f; border:1px solid #238bff; border-radius:20px; }}')
        header.setFixedHeight(112)
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 10, 18, 10)
        left = QVBoxLayout()
        self.hero_label = QLabel("Good Morning, Muhammad Tahir!")
        self.hero_label.setObjectName("Hero")
        self.hero_label.setStyleSheet("color:#64eaff;font-size:30px;font-weight:950;padding:2px;")
        left.addWidget(self.hero_label)
        left.addWidget(QLabel("Your personal AI assistant is ready."))
        h.addLayout(left)
        h.addStretch()
        self.top_status = QLabel("● ONLINE")
        self.top_status.setObjectName("Status")
        h.addWidget(self.top_status)
        h.addSpacing(15)
        self.clock_label = QLabel("--:--:--")
        self.clock_label.setStyleSheet("font-size:15px;font-weight:bold;")
        h.addWidget(self.clock_label)
        content.addWidget(header)

        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        self.dashboard_page = self.build_dashboard()
        self.voice_page = self.build_voice_page()
        self.notes_page = self.build_notes_page()
        self.settings_page = self.build_settings_page()
        self.about_page = self.build_about_page()
        self.quick_actions_page = self.build_tool_page("QUICK ACTIONS", "Launch everyday tools without hunting through menus.", [
            ("🌐 Chrome", "open chrome"), ("▶ YouTube", "open youtube"), ("💬 WhatsApp Web", "open whatsapp web"),
            ("</> VS Code", "open vs code"), ("📝 Notepad", "open notepad"), ("🧮 Calculator", "open calculator"),
            ("📁 File Explorer", "open file explorer"), ("⬇ Downloads", "open downloads"),
            ("🖼 Pictures", "open pictures"), ("📄 Documents", "open documents"),
            ("❌ Close Explorer Windows", "close file explorer"), ("❌ Close Downloads", "close downloads"),
            ("📸 Screenshot", "take screenshot"),
            ("🔋 Battery Status", "battery status"), ("📊 System Info", "system information"), ("⚙ Windows Settings", "open settings"),
            ("🔊 Volume Up", "volume up"), ("🔉 Volume Down", "volume down"), ("🧰 Task Manager", "open task manager"),
            ("🖥 Display Settings", "open display settings"), ("📶 Wi-Fi Settings", "open wifi settings"),
            ("📡 Wi-Fi Status", "wifi status"), ("🔋 Battery Health %", "battery health percentage"),
            ("📑 Battery Health Report", "battery health report"), ("🩺 System Health Check", "system health"),
            ("🔋 Low Battery Alert", "battery status"), ("🧠 Performance Check", "pc performance batao"),
            ("🗂️ Organize Downloads", "organize downloads"), ("⏰ 10-Min Reminder", "remind me in 10 minutes to check my task"),
            ("🌐 Internet Check", "internet check"), ("⏱ PC Uptime", "pc uptime"), ("🧠 Top RAM Apps", "top memory apps"),
            ("🕒 Current Time", "what time is it"), ("📅 Today's Date", "today's date"),
            ("💽 Storage Overview", "disk space overview"), ("⬇ Recent Downloads", "recent downloads"), ("🔒 Lock PC", "lock computer")])
        self.system_page = self.build_system_page()
        self.reminders_page = self.build_reminders_page()
        self.focus_page = self.build_focus_page()
        for page in (self.dashboard_page, self.voice_page, self.notes_page, self.settings_page, self.about_page, self.quick_actions_page, self.system_page, self.reminders_page, self.focus_page):
            self.pages.addWidget(page)

        footer = QHBoxLayout()
        self.footer_label = QLabel("〰  JARVIS AI  •  DESKTOP CONTROL SYSTEM")
        self.footer_label.setObjectName("Subtitle")
        footer.addWidget(self.footer_label)
        footer.addStretch()
        footer.addWidget(QLabel("POWERED BY AI"))
        footer.addWidget(QLabel("│"))
        footer.addWidget(QLabel("BUILD BY MUHAMMAD TAHIR"))
        self.exit_button = QPushButton("⏻  EXIT")
        self.exit_button.setObjectName("DangerButton")
        self.exit_button.clicked.connect(self.close)
        footer.addWidget(self.exit_button)
        content.addLayout(footer)


    def build_tool_page(self, title, subtitle, actions):
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); layout = QVBoxLayout(body); layout.setSpacing(14)
        hero = self.card(); hv = QVBoxLayout(hero)
        heading = QLabel("◈  " + title); heading.setObjectName("SectionTitle")
        hv.addWidget(heading); desc = QLabel(subtitle); desc.setWordWrap(True); hv.addWidget(desc)
        layout.addWidget(hero)
        search = QLineEdit(); search.setPlaceholderText("⌕  Search actions…"); search.setClearButtonEnabled(True); layout.addWidget(search)
        grid = QGridLayout(); grid.setSpacing(10); layout.addLayout(grid)
        buttons=[]
        for i,(label,command) in enumerate(actions):
            b=QPushButton(label); b.setMinimumHeight(52); b.clicked.connect(lambda checked=False, cmd=command: self.send_command(cmd))
            grid.addWidget(b,i//3,i%3); buttons.append((b,label.lower(),command.lower()))
        search.textChanged.connect(lambda text: [b.setVisible(not text.strip() or text.lower() in label or text.lower() in cmd) for b,label,cmd in buttons])
        layout.addStretch(1); scroll.setWidget(body); outer.addWidget(scroll); return page

    def build_system_page(self):
        page=QWidget(); layout=QVBoxLayout(page)
        hero=self.card(); v=QVBoxLayout(hero); title=QLabel("📡  LIVE SYSTEM MONITOR"); title.setObjectName("SectionTitle"); v.addWidget(title)
        v.addWidget(QLabel("Live CPU, memory, battery and device health. Values refresh automatically."))
        row=QHBoxLayout(); self.system_cpu_value,self.system_cpu_bar=self.add_metric(row,"⚡ CPU LOAD"); self.system_ram_value,self.system_ram_bar=self.add_metric(row,"▦ MEMORY"); self.system_battery_value,self.system_battery_bar=self.add_metric(row,"🔋 BATTERY"); v.addLayout(row)
        row2=QHBoxLayout(); self.system_disk_value,self.system_disk_bar=self.add_metric(row2,"💾 SYSTEM DRIVE"); self.system_net_down,self.system_net_down_bar=self.add_metric(row2,"⬇ DOWNLOAD RATE"); self.system_net_up,self.system_net_up_bar=self.add_metric(row2,"⬆ UPLOAD RATE"); v.addLayout(row2)
        self.system_details=QLabel("Collecting system details…"); self.system_details.setWordWrap(True); v.addWidget(self.system_details)
        refresh=QPushButton("↻  REFRESH NOW"); refresh.setObjectName("PrimaryButton"); refresh.clicked.connect(self.update_telemetry); v.addWidget(refresh)
        layout.addWidget(hero); layout.addStretch(1); return page

    def build_reminders_page(self):
        page=QWidget(); layout=QVBoxLayout(page); card=self.card(); v=QVBoxLayout(card)
        title=QLabel("🔔  REMINDER CENTER"); title.setObjectName("SectionTitle"); v.addWidget(title)
        v.addWidget(QLabel("Create, schedule and remove reminders. Reminders are stored locally on this PC."))
        self.reminders_page_list=QListWidget(); self.reminders_page_list.setMinimumHeight(230); v.addWidget(self.reminders_page_list)
        row=QHBoxLayout(); add=QPushButton("＋ CREATE REMINDER"); add.setObjectName("PrimaryButton"); add.clicked.connect(lambda: self.show_page("Voice Control")); row.addWidget(add)
        delete=QPushButton("✕ REMOVE SELECTED"); delete.clicked.connect(self.remove_reminder_from_center); row.addWidget(delete); v.addLayout(row)
        layout.addWidget(card); layout.addStretch(1); self.refresh_reminder_center(); return page

    def remove_reminder_from_center(self):
        row=self.reminders_page_list.currentRow() if hasattr(self,"reminders_page_list") else -1
        ordered=sorted(self.reminders,key=lambda x:x.get("due",""))
        if row<0 or row>=len(ordered):
            QMessageBox.information(self,"Select a Reminder","Select a reminder first."); return
        target=ordered[row]
        self.reminders=[x for x in self.reminders if x.get("id")!=target.get("id")]
        self.save_reminders(); self.refresh_reminder_ui(); self.refresh_reminder_center(); self.add_log("Reminder removed: "+target.get("text",""))

    def refresh_reminder_center(self):
        if not hasattr(self,"reminders_page_list"): return
        self.reminders_page_list.clear()
        for item in sorted(self.reminders,key=lambda x:x.get("due","")):
            try: due=datetime.fromisoformat(item["due"]).strftime("%a, %d %b • %I:%M %p")
            except Exception: due="Scheduled"
            self.reminders_page_list.addItem(f"🔔  {due}  —  {item.get('text','Reminder')}")

    def build_focus_page(self):
        page=QWidget(); layout=QVBoxLayout(page); card=self.card(); v=QVBoxLayout(card)
        title=QLabel("⏱  FOCUS SPRINT"); title.setObjectName("SectionTitle"); v.addWidget(title)
        v.addWidget(QLabel("A distraction-light 25-minute work session. Start, pause or reset your focus timer."))
        self.focus_page_time=QLabel("25:00"); self.focus_page_time.setAlignment(Qt.AlignmentFlag.AlignCenter); self.focus_page_time.setStyleSheet("font-size:64px;font-weight:900;color:#71eaff;padding:24px;"); v.addWidget(self.focus_page_time)
        row=QHBoxLayout(); start=QPushButton("▶  START / PAUSE"); start.setObjectName("PrimaryButton"); start.clicked.connect(self.toggle_focus_timer); reset=QPushButton("↺  RESET 25 MIN"); reset.clicked.connect(self.reset_focus_timer); row.addWidget(start); row.addWidget(reset); v.addLayout(row)
        v.addWidget(QLabel("Tip: put your phone aside, choose one task, and take a short break when the timer finishes."))
        layout.addWidget(card); layout.addStretch(1); return page

    def toggle_focus_timer(self):
        if self.focus_running:
            self.focus_timer.stop(); self.focus_running=False; self.add_log("Focus timer paused.")
        else:
            if self.focus_seconds<=0: self.focus_seconds=25*60
            self.focus_timer.start(1000); self.focus_running=True; self.add_log("Focus session started.")
        if hasattr(self,"focus_start_button"): self.focus_start_button.setText("Ⅱ  PAUSE FOCUS" if self.focus_running else "▶  RESUME FOCUS")

    def build_dashboard(self):
        """Neon dashboard inspired by the supplied JARVIS concept image."""
        scenic_asset = DASHBOARD_WALLPAPER_URL
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(2, 2, 8, 14)
        root.setSpacing(11)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # Status hero + live system meters
        top = QHBoxLayout()
        top.setSpacing(10)
        core = self.card()
        core.setMinimumWidth(300)
        c = QVBoxLayout(core)
        c.setContentsMargins(16, 13, 16, 13)
        c.setSpacing(8)
        core_head = QHBoxLayout()
        orb = QLabel("◉")
        orb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orb.setFixedSize(72, 72)
        orb.setStyleSheet("color:#29e9ff;font-size:52px;border:3px solid #16d9d5;border-radius:36px;background:#061b38;")
        core_head.addWidget(orb)
        words = QVBoxLayout()
        title = QLabel("JARVIS STATUS")
        title.setObjectName("SectionTitle")
        words.addWidget(title)
        self.core_status = QLabel("● LISTENING READY")
        self.core_status.setStyleSheet("color:#39f5b0;font-size:15px;font-weight:900;padding:5px 0;")
        words.addWidget(self.core_status)
        words.addWidget(QLabel("Your AI assistant is ready for commands."))
        core_head.addLayout(words, 1)
        c.addLayout(core_head)
        self.next_reminder_label = QLabel("◷  No upcoming reminders")
        self.next_reminder_label.setObjectName("ReminderHighlight")
        self.next_reminder_label.setWordWrap(True)
        c.addWidget(self.next_reminder_label)
        buttons = QHBoxLayout()
        self.listen_button = QPushButton("🎙  START LISTENING")
        self.listen_button.setObjectName("PrimaryButton")
        self.listen_button.clicked.connect(self.start_listening)
        self.stop_button = QPushButton("■  STOP LISTENING")
        self.stop_button.clicked.connect(self.stop_listening)
        buttons.addWidget(self.listen_button)
        buttons.addWidget(self.stop_button)
        c.addLayout(buttons)
        top.addWidget(core, 5)

        telemetry = self.card()
        t = QVBoxLayout(telemetry)
        t.setContentsMargins(12, 12, 12, 12)
        t.setSpacing(8)
        th = QLabel("📡  SYSTEM TELEMETRY")
        th.setObjectName("SectionTitle")
        t.addWidget(th)
        metrics = QHBoxLayout()
        metrics.setSpacing(7)
        self.cpu_value, self.cpu_bar = self.add_metric(metrics, "⚡ CPU")
        self.ram_value, self.ram_bar = self.add_metric(metrics, "▦ RAM")
        self.battery_value, self.battery_bar = self.add_metric(metrics, "🔋 BATTERY")
        t.addLayout(metrics)
        top.addWidget(telemetry, 6)
        root.addLayout(top)

        # Scenic weather-style panel inspired by the reference dashboard.
        scenic_row = QHBoxLayout()
        scenic_row.setSpacing(10)
        weather = self.card()
        weather.setMinimumHeight(112)
        wallpaper_style = f'background-image:url("{scenic_asset}"); background-position:center; background-repeat:no-repeat;' if scenic_asset else ""
        weather.setStyleSheet(f'QFrame#Card {{ {wallpaper_style} background-color:#07152f; border:1px solid #357bda; border-radius:18px; }}')
        wl = QHBoxLayout(weather)
        wl.setContentsMargins(18,10,18,10)
        wicon = QLabel("🌤")
        self.weather_icon = wicon
        wicon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wicon.setFixedWidth(72)
        wicon.setStyleSheet("font-size:50px;color:#a8dfff;")
        wl.addWidget(wicon)
        wtext = QVBoxLayout()
        wtitle = QLabel("LAHORE  •  WEATHER PANEL")
        wtitle.setObjectName("SectionTitle")
        # Keep the weather panel clean; weather updates automatically every 15 minutes.
        wtext.addWidget(wtitle)
        wdesc = QLabel("Loading live weather for Lahore…")
        self.weather_desc = wdesc
        wdesc.setWordWrap(True)
        wdesc.setStyleSheet("color:#cce8ff;font-size:12px;")
        wtext.addWidget(wdesc)
        wl.addLayout(wtext,1)
        wtemp = QLabel("☁  --°C")
        self.weather_temp = wtemp
        wtemp.setStyleSheet("font-size:27px;font-weight:900;color:#ffffff;")
        wl.addWidget(wtemp)
        scenic_row.addWidget(weather, 3)

        info = self.card()
        info.setMinimumHeight(112)
        il = QVBoxLayout(info)
        il.setContentsMargins(16,10,16,10)
        it = QLabel("▣  SYSTEM INFO")
        it.setObjectName("SectionTitle")
        il.addWidget(it)
        self.system_info_label = QLabel(f"OS: {platform.system()} {platform.release()}\nCPU / RAM / Battery: updating live below")
        self.system_info_label.setStyleSheet("color:#bdeaff;font-size:12px;")
        self.system_info_label.setWordWrap(True)
        il.addWidget(self.system_info_label)
        scenic_row.addWidget(info, 2)
        root.addLayout(scenic_row)

        # Fast-launch grid
        actions = self.card()
        a = QVBoxLayout(actions)
        a.setContentsMargins(14, 12, 14, 13)
        a.setSpacing(7)
        heading = QLabel("⚡  QUICK ACTIONS")
        heading.setObjectName("SectionTitle")
        a.addWidget(heading)
        a.addWidget(QLabel("One click, many possibilities"))
        self.action_search = QLineEdit()
        self.action_search.setPlaceholderText("⌕  Search quick actions… e.g. screenshot, Chrome, battery")
        self.action_search.setClearButtonEnabled(True)
        self.action_search.setMinimumHeight(38)
        a.addWidget(self.action_search)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.quick_action_grid = grid
        self.quick_action_buttons = []
        action_list = [
            ("🌐  Open Chrome", "open chrome"), ("</>  Open VS Code", "open vs code"),
            ("📝  Open Notepad", "open notepad"), ("▶  Open YouTube", "open youtube"),
            ("💬  WhatsApp Web", "open whatsapp web"), ("🧮  Calculator", "open calculator"),
            ("⚙  Windows Settings", "open settings"), ("📁  File Explorer", "open file explorer"),
            ("🔋  Battery Status", "battery status"), ("🔋  Battery Health %", "battery health percentage"),
            ("📑  Battery Health Report", "battery health report"), ("📊  System Info", "system information"),
            ("🧠  Performance Check", "pc performance batao"), ("🗂️  Organize Downloads", "organize downloads"),
            ("⏰  10-Min Reminder", "remind me in 10 minutes to check my task"),
            ("🌐  Internet Check", "internet check"), ("⏱  PC Uptime", "pc uptime"),
            ("🧠  Top RAM Apps", "top memory apps"), ("📸  Screenshot", "take screenshot"), ("📂  Open Downloads", "open downloads"),
            ("🖼  Open Pictures", "open pictures"), ("📄  Open Documents", "open documents"),
            ("❌  Close Explorer Windows", "close file explorer"), ("❌  Close Downloads", "close downloads"),
            ("🔊  Volume Up", "volume up"), ("🔉  Volume Down", "volume down"),
            ("🧰  Task Manager", "open task manager"), ("🪟  Show Desktop", "show desktop"),
            ("🕒  Tell Time", "what time is it"), ("📅  Today's Date", "today date"),
            ("💽  Storage Overview", "disk space overview"), ("⬇  Recent Downloads", "recent downloads"),
            ("🔎  Magnifier", "open magnifier"), ("🖥  Display Settings", "open display settings"),
            ("📶  Wi-Fi Settings", "open wifi settings"), ("🎨  Personalization", "open personalization"),
            ("🔒  Lock PC", "lock computer"), ("😊  Emoji Panel", "open emoji panel"),
        ]
        for i, (label, command) in enumerate(action_list):
            b = QPushButton(label)
            b.setMinimumHeight(42)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda checked=False, cmd=command: self.send_command(cmd))
            b.setToolTip(f"Run command: {command}")
            grid.addWidget(b, i // 4, i % 4)
            self.quick_action_buttons.append((b, label.lower(), command.lower()))
        self.action_search.textChanged.connect(self.filter_quick_actions)
        a.addLayout(grid)

        # Focus and quick-note cards beside each other
        middle = QHBoxLayout()
        middle.setSpacing(10)
        focus = self.card()
        fr = QHBoxLayout(focus)
        fr.setContentsMargins(14, 11, 14, 11)
        fl = QVBoxLayout()
        ft = QLabel("🎯  FOCUS SPRINT")
        ft.setObjectName("SectionTitle")
        fl.addWidget(ft)
        fl.addWidget(QLabel("Stay focused, get more done"))
        self.focus_time_label = QLabel("25:00")
        self.focus_time_label.setObjectName("FocusTime")
        self.focus_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fl.addWidget(self.focus_time_label)
        fr.addLayout(fl, 1)
        fb = QVBoxLayout()
        self.focus_start_button = QPushButton("▶  START")
        self.focus_start_button.setObjectName("PrimaryButton")
        self.focus_start_button.clicked.connect(self.toggle_focus_timer)
        focus_reset_button = QPushButton("↺  RESET")
        focus_reset_button.clicked.connect(self.reset_focus_timer)
        fb.addWidget(self.focus_start_button)
        fb.addWidget(focus_reset_button)
        fr.addLayout(fb)
        middle.addWidget(focus, 5)

        notes = self.card()
        nl = QVBoxLayout(notes)
        nl.setContentsMargins(14, 11, 14, 11)
        nt = QLabel("📝  QUICK NOTES")
        nt.setObjectName("SectionTitle")
        nl.addWidget(nt)
        self.dashboard_note_preview = QLabel("Save ideas and reminders in your notes.")
        self.dashboard_note_preview.setWordWrap(True)
        self.dashboard_note_preview.setMinimumHeight(38)
        nl.addWidget(self.dashboard_note_preview)
        nb = QHBoxLayout()
        new_note_btn = QPushButton("＋  NEW NOTE")
        new_note_btn.clicked.connect(lambda: (self.show_page("Quick Notes"), self.new_note()))
        view_notes_btn = QPushButton("▤  VIEW NOTES")
        view_notes_btn.clicked.connect(lambda: self.show_page("Quick Notes"))
        nb.addWidget(new_note_btn)
        nb.addWidget(view_notes_btn)
        nl.addLayout(nb)
        middle.addWidget(notes, 4)
        root.addLayout(middle)

        # Upcoming reminders + activity console
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        reminders_card = self.card()
        rl = QVBoxLayout(reminders_card)
        rl.setContentsMargins(14, 11, 14, 11)
        rt = QLabel("🔔  UPCOMING REMINDERS")
        rt.setObjectName("SectionTitle")
        rl.addWidget(rt)
        self.dashboard_reminders = QListWidget()
        self.dashboard_reminders.setMaximumHeight(155)
        rl.addWidget(self.dashboard_reminders)
        reminder_buttons = QHBoxLayout()
        add_reminder_btn = QPushButton("＋  ADD REMINDER")
        add_reminder_btn.clicked.connect(lambda: self.show_page("Voice Control"))
        reminder_buttons.addWidget(add_reminder_btn)
        rl.addLayout(reminder_buttons)
        bottom.addWidget(reminders_card, 4)

        console_card = self.card()
        co = QVBoxLayout(console_card)
        co.setContentsMargins(14, 11, 14, 12)
        title_row = QHBoxLayout()
        title = QLabel("›  ACTIVITY CONSOLE")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        export_btn = QPushButton("⇩ EXPORT LOG")
        export_btn.clicked.connect(self.export_console)
        title_row.addWidget(export_btn)
        clear = QPushButton("CLEAR")
        clear.clicked.connect(self.clear_console)
        title_row.addWidget(clear)
        co.addLayout(title_row)
        tools_row = QHBoxLayout()
        tools_row.addWidget(QLabel("Live activity and command results"))
        tools_row.addStretch()
        self.auto_scroll_box = QCheckBox("Auto Scroll")
        self.auto_scroll_box.setChecked(True)
        self.auto_scroll_box.toggled.connect(self.set_auto_scroll)
        tools_row.addWidget(self.auto_scroll_box)
        co.addLayout(tools_row)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumWidth(0)
        self.console.setMinimumHeight(160)
        self.console.setMaximumHeight(225)
        co.addWidget(self.console)
        co.addWidget(QLabel("⌘  TYPE A COMMAND"))
        command_row = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setMinimumHeight(40)
        self.command_input.setPlaceholderText("Try: open VS Code, battery status, or ChatGPT prompt make a dark wallpaper…")
        self.command_input.returnPressed.connect(self.submit_typed_command)
        command_row.addWidget(self.command_input, 1)
        send_btn = QPushButton("SEND  ➤")
        send_btn.setObjectName("PrimaryButton")
        send_btn.setMinimumHeight(40)
        send_btn.clicked.connect(self.submit_typed_command)
        command_row.addWidget(send_btn)
        co.addLayout(command_row)
        bottom.addWidget(console_card, 6)
        root.addLayout(bottom)
        root.addStretch(1)
        body.setMinimumWidth(0)
        return page

    def filter_quick_actions(self, text):
        """Filter quick-action tiles without changing the underlying command set."""
        query = (text or "").strip().lower()
        visible_index = 0
        for button, label, command in getattr(self, "quick_action_buttons", []):
            visible = not query or query in label or query in command
            button.setVisible(visible)
            if visible:
                # Re-pack visible buttons into a compact four-column grid.
                grid = getattr(self, "quick_action_grid", None)
                if isinstance(grid, QGridLayout):
                    grid.removeWidget(button)
                    grid.addWidget(button, visible_index // 4, visible_index % 4)
                visible_index += 1
        if hasattr(self, "action_search"):
            self.action_search.setToolTip(f"{visible_index} action(s) found")

    def filter_voice_actions(self, text):
        """Filter voice toolkit buttons while keeping all actions available."""
        query = (text or "").strip().lower()
        visible_index = 0
        for button, label, command in getattr(self, "voice_action_buttons", []):
            visible = not query or query in label or query in command
            button.setVisible(visible)
            if visible and isinstance(getattr(self, "voice_action_grid", None), QGridLayout):
                self.voice_action_grid.removeWidget(button)
                self.voice_action_grid.addWidget(button, visible_index // 3, visible_index % 3)
                visible_index += 1
        if hasattr(self, "voice_action_search"):
            self.voice_action_search.setToolTip(f"{visible_index} action(s) found")

    def toggle_focus_timer(self):
        if self.focus_running:
            self.focus_timer.stop()
            self.focus_running = False
            self.focus_start_button.setText("▶  RESUME FOCUS")
            self.add_log("Focus timer paused.")
        else:
            if self.focus_seconds <= 0:
                self.focus_seconds = 25 * 60
            self.focus_timer.start(1000)
            self.focus_running = True
            self.focus_start_button.setText("Ⅱ  PAUSE FOCUS")
            self.add_log("Focus session started.")

    def tick_focus_timer(self):
        if self.focus_seconds > 0:
            self.focus_seconds -= 1
        minutes, seconds = divmod(max(0, self.focus_seconds), 60)
        self.focus_time_label.setText(f"{minutes:02d}:{seconds:02d}")
        if hasattr(self, "focus_page_time"): self.focus_page_time.setText(f"{minutes:02d}:{seconds:02d}")
        if self.focus_seconds <= 0:
            self.focus_timer.stop()
            self.focus_running = False
            self.focus_start_button.setText("▶  START FOCUS")
            self.add_log("Focus session complete! Take a short break.")
            QMessageBox.information(self, "Focus Session Complete", "Great work! Your 25-minute focus session is complete. Take a short break.")

    def reset_focus_timer(self):
        self.focus_timer.stop()
        self.focus_running = False
        self.focus_seconds = 25 * 60
        self.focus_time_label.setText("25:00")
        if hasattr(self, "focus_page_time"): self.focus_page_time.setText("25:00")
        self.focus_start_button.setText("▶  START FOCUS")
        self.add_log("Focus timer reset to 25 minutes.")

    def focus_command_input(self):
        """Ctrl+K jumps straight to the dashboard command box."""
        self.show_page("Dashboard")
        if hasattr(self, "command_input"):
            self.command_input.setFocus()
            self.command_input.selectAll()

    def submit_typed_command(self):
        if not hasattr(self, "command_input"):
            return
        command = self.command_input.text().strip()
        if command:
            self.command_input.clear()
            self.send_command(command)

    @staticmethod
    def add_metric(layout, caption):
        box = QFrame()
        box.setStyleSheet("background:#031326;border:1px solid #063d61;border-radius:11px;")
        v = QVBoxLayout(box)
        v.setContentsMargins(9, 8, 9, 8)
        v.setSpacing(5)
        cap = QLabel(caption)
        cap.setStyleSheet("font-size:10px;font-weight:bold;color:#d5edff;")
        v.addWidget(cap)
        value = QLabel("--")
        value.setObjectName("MetricValue")
        value.setMinimumHeight(31)
        v.addWidget(value)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        v.addWidget(bar)
        layout.addWidget(box, 1)
        return value, bar

    def build_voice_page(self):
        # Put the full Voice Control workspace inside a scroll area. The toolkit
        # and reminder planner are intentionally taller than many laptop screens.
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 8, 12)
        layout.setSpacing(12)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        card = self.card()
        v = QVBoxLayout(card)
        v.setContentsMargins(24, 22, 24, 22)
        title = QLabel("VOICE CONTROL")
        title.setObjectName("SectionTitle")
        v.addWidget(title)
        v.addWidget(QLabel("Control the microphone and test JARVIS voice output."))
        row = QHBoxLayout()
        self.voice_start = QPushButton("🎙  START LISTENING")
        self.voice_start.setObjectName("PrimaryButton")
        self.voice_start.clicked.connect(self.start_listening)
        self.voice_stop = QPushButton("■  STOP LISTENING")
        self.voice_stop.clicked.connect(self.stop_listening)
        self.voice_test = QPushButton("🔊  TEST VOICE")
        self.voice_test.clicked.connect(self.test_voice)
        row.addWidget(self.voice_start)
        row.addWidget(self.voice_stop)
        row.addWidget(self.voice_test)
        v.addLayout(row)
        self.voice_status_label = QLabel("Status: READY")
        self.voice_status_label.setStyleSheet("color:#00e69a;font-size:15px;font-weight:bold;padding:8px;")
        v.addWidget(self.voice_status_label)
        voice_help = QLabel("Talk naturally in Pakistani English or Roman Urdu: “VS Code kholo”, “mera Downloads folder kholo”, “kal mujhe assignment yaad dilana”, “volume barhao”, or “screenshot le lo”. JARVIS tries Pakistani English, Indian English, and Urdu recognition. If it misunderstands you, check the HEARD transcript in Activity Console and try a shorter phrase.")
        voice_help.setWordWrap(True)
        voice_help.setStyleSheet("color:#a9d9ef;padding:6px 0;")
        v.addWidget(voice_help)
        layout.addWidget(card)

        tools_card = self.card()
        tv = QVBoxLayout(tools_card)
        tv.setContentsMargins(24, 20, 24, 20)
        tv.addWidget(QLabel("VOICE COMMAND TOOLKIT", objectName="SectionTitle"))
        tv.addWidget(QLabel("Search a command, click an action, or speak naturally. Commands are routed to main.py."))
        self.voice_action_search = QLineEdit()
        self.voice_action_search.setPlaceholderText("⌕  Search voice actions… e.g. volume, screenshot, Wi-Fi")
        self.voice_action_search.setClearButtonEnabled(True)
        self.voice_action_search.setMinimumHeight(38)
        tv.addWidget(self.voice_action_search)
        grid = QGridLayout()
        self.voice_action_grid = grid
        self.voice_action_buttons = []
        quick_commands = [
            ("📁 File Explorer", "open file explorer"), ("</> VS Code", "open vs code"),
            ("⬇ Downloads", "open downloads"), ("🖼 Pictures", "open pictures"),
            ("📄 Documents", "open documents"), ("❌ Close Explorer", "close file explorer"),
            ("❌ Close Downloads", "close downloads"), ("📸 Screenshot", "take screenshot"),
            ("🔋 Battery status", "battery status"), ("🔋 Battery health %", "battery health percentage"),
            ("📑 Battery health report", "battery health report"), ("🌐 Internet check", "internet check"),
            ("⏱ PC uptime", "pc uptime"), ("🧠 Top RAM apps", "top memory apps"),
            ("📊 System information", "system information"), ("🔊 Volume up", "volume up"), ("🔉 Volume down", "volume down"),
            ("🗂 Organize Downloads", "organize downloads"), ("🗂 Organize Desktop", "organize desktop"),
            ("📋 Clipboard history", "clipboard history"), ("📖 Read clipboard", "read clipboard"),
            ("🔒 Lock PC", "lock computer"), ("🧮 Calculator", "open calculator"),
            ("📝 Notepad", "open notepad"), ("⚙ Windows Settings", "open settings"),
            ("↻ Restart JARVIS", "restart jarvis"), ("🕒 Tell time", "what time is it"),
            ("📅 Today's date", "today date"), ("😀 Tell a joke", "tell me a joke"),
            ("🧰 Task Manager", "open task manager"), ("🪟 Show desktop", "show desktop"),
            ("🔀 Switch window", "switch window"), ("😊 Emoji panel", "open emoji panel"),
            ("🔎 Magnifier", "open magnifier"), ("🖥 Display settings", "open display settings"),
            ("🌐 Wi-Fi settings", "open wifi settings"), ("🎨 Personalization", "open personalization"),
        ]
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for i, (label, command) in enumerate(quick_commands):
            button = QPushButton(label)
            button.setMinimumHeight(38)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, cmd=command: self.send_command(cmd))
            grid.addWidget(button, i // 3, i % 3)
            self.voice_action_buttons.append((button, label.lower(), command.lower()))
        self.voice_action_search.textChanged.connect(self.filter_voice_actions)
        tv.addLayout(grid)
        tv.addWidget(QLabel("TYPE A COMMAND"))
        command_row = QHBoxLayout()
        self.voice_command_input = QLineEdit()
        self.voice_command_input.setMinimumHeight(42)
        self.voice_command_input.setPlaceholderText("Try: ChatGPT se kaho dark wallpaper ka code likhe…")
        self.voice_command_input.returnPressed.connect(self.send_typed_command)
        send_button = QPushButton("➤ SEND COMMAND")
        send_button.setObjectName("PrimaryButton")
        send_button.clicked.connect(self.send_typed_command)
        command_row.addWidget(self.voice_command_input, 1)
        command_row.addWidget(send_button)
        tv.addLayout(command_row)
        note = QLabel("Safety note: shutdown, restart Windows, deleting files, and other destructive actions should require explicit confirmation in the command handler.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8bbbd3;padding-top:6px;")
        tv.addWidget(note)
        layout.addWidget(tools_card)

        # Persistent reminder planner: reminders survive closing/reopening JARVIS.
        reminder_card = self.card()
        rv = QVBoxLayout(reminder_card)
        rv.setContentsMargins(18, 16, 18, 16)
        rv.addWidget(QLabel("⏰ PERSONAL REMINDERS", objectName="SectionTitle"))
        intro = QLabel("Speak or type: ‘remind me at 9 pm to study networking’, ‘remind me tomorrow at 7 pm to revise’, ‘remind me in 20 minutes to take a break’, or ‘Study networking 9 baje’. Reminders are saved on this PC. If the laptop is off at the due time, JARVIS alerts you after you turn it on and open the app.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#a9d9ef;")
        rv.addWidget(intro)
        reminder_row = QHBoxLayout()
        self.reminder_task_input = QLineEdit()
        self.reminder_task_input.setMinimumHeight(40)
        self.reminder_task_input.setPlaceholderText("What should JARVIS remind you about? e.g. Study networking")
        reminder_row.addWidget(self.reminder_task_input, 2)
        self.reminder_when = QDateTimeEdit()
        self.reminder_when.setMinimumHeight(40)
        self.reminder_when.setCalendarPopup(True)
        self.reminder_when.setDisplayFormat("dd MMM yyyy  hh:mm AP")
        self.reminder_when.setDateTime(datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=5))
        self.reminder_when.setMinimumDateTime(datetime.now())
        self.reminder_when.setToolTip("Choose the date and time for your reminder")
        reminder_row.addWidget(self.reminder_when, 1)
        add_reminder_btn = QPushButton("＋ ADD REMINDER")
        add_reminder_btn.setObjectName("PrimaryButton")
        add_reminder_btn.clicked.connect(self.add_reminder)
        reminder_row.addWidget(add_reminder_btn)
        rv.addLayout(reminder_row)
        self.reminder_list = QListWidget()
        self.reminder_list.setMinimumHeight(120)
        self.reminder_list.setMaximumHeight(220)
        rv.addWidget(self.reminder_list)
        reminder_actions = QHBoxLayout()
        delete_reminder_btn = QPushButton("REMOVE SELECTED")
        delete_reminder_btn.clicked.connect(self.remove_selected_reminder)
        reminder_actions.addWidget(delete_reminder_btn)
        reminder_actions.addStretch()
        rv.addLayout(reminder_actions)
        layout.addWidget(reminder_card)
        self.refresh_reminder_ui()
        layout.addStretch(1)
        content.setMinimumWidth(0)
        return page

    def load_reminders(self):
        """Load pending reminders from the user's AppData folder."""
        try:
            if self.reminder_file.exists():
                data = json.loads(self.reminder_file.read_text(encoding="utf-8"))
                self.reminders = [item for item in data if isinstance(item, dict) and item.get("text") and item.get("due")]
        except Exception as exc:
            self.reminders = []
            print(f"Could not load JARVIS reminders: {exc}")

    def save_reminders(self):
        try:
            self.reminder_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.reminder_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(self.reminders, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_file.replace(self.reminder_file)
        except Exception as exc:
            self.add_log(f"REMINDER SAVE ERROR: {exc}")
            QMessageBox.warning(self, "Reminder Save Error", f"Could not save reminders.\n{exc}")

    def add_voice_reminder(self, text, due_iso):
        """Persist a reminder created by speech or typed natural language."""
        try:
            due = datetime.fromisoformat(due_iso)
            self.reminders.append({"id": uuid.uuid4().hex, "text": text, "due": due.isoformat(), "created": datetime.now().isoformat()})
            self.reminders.sort(key=lambda item: item.get("due", ""))
            self.save_reminders()
            self.refresh_reminder_ui()
            self.add_log(f"🎙️ Voice reminder saved for {due:%d %b %Y %I:%M %p}: {text}")
        except Exception as exc:
            self.add_log(f"VOICE REMINDER ERROR: {exc}")
            QMessageBox.warning(self, "Reminder Error", f"Could not save this voice reminder.\n{exc}")

    def add_reminder(self):
        text = self.reminder_task_input.text().strip()
        if not text:
            QMessageBox.information(self, "Add a Reminder", "First type what JARVIS should remind you about.")
            self.reminder_task_input.setFocus()
            return
        due = self.reminder_when.dateTime().toPython()
        if due <= datetime.now():
            QMessageBox.warning(self, "Choose a Future Time", "Please choose a time in the future.")
            return
        self.reminders.append({"id": uuid.uuid4().hex, "text": text, "due": due.isoformat(), "created": datetime.now().isoformat()})
        self.reminders.sort(key=lambda item: item.get("due", ""))
        self.save_reminders()
        self.refresh_reminder_ui()
        self.add_log(f"Reminder added for {due:%d %b %Y %I:%M %p}: {text}")
        self.reminder_task_input.clear()

    def remove_selected_reminder(self):
        row = self.reminder_list.currentRow() if hasattr(self, "reminder_list") else -1
        if row < 0 or row >= len(self.reminders):
            QMessageBox.information(self, "Select a Reminder", "Select a reminder from the list first.")
            return
        item = self.reminders.pop(row)
        self.save_reminders()
        self.refresh_reminder_ui()
        self.add_log(f"Reminder removed: {item.get('text', '')}")

    def refresh_reminder_ui(self):
        if hasattr(self, "reminder_list"):
            self.reminder_list.clear()
            for item in sorted(self.reminders, key=lambda x: x.get("due", "")):
                try:
                    due = datetime.fromisoformat(item["due"])
                    display = f"{due:%a, %d %b • %I:%M %p}   —   {item['text']}"
                except Exception:
                    display = item.get("text", "Reminder")
                self.reminder_list.addItem(QListWidgetItem(display))
        self.refresh_reminder_center()
        if hasattr(self, "next_reminder_label"):
            upcoming = sorted((x for x in self.reminders if x.get("due")), key=lambda x: x["due"])
            if upcoming:
                try:
                    due = datetime.fromisoformat(upcoming[0]["due"])
                    self.next_reminder_label.setText(f"⏰ NEXT: {due:%d %b, %I:%M %p}  •  {upcoming[0]['text']}")
                except Exception:
                    self.next_reminder_label.setText("⏰ Upcoming reminder scheduled")
            else:
                self.next_reminder_label.setText("◷  No upcoming reminders")

    def check_reminders(self):
        now = datetime.now()
        due_items = []
        pending = []
        for item in self.reminders:
            try:
                due = datetime.fromisoformat(item["due"])
                if due <= now:
                    due_items.append(item)
                else:
                    pending.append(item)
            except Exception:
                pending.append(item)
        if not due_items:
            return
        self.reminders = pending
        self.save_reminders()
        self.refresh_reminder_ui()
        for item in due_items:
            message = f"Reminder: {item['text']}"
            self.add_log(f"⏰ REMINDER DUE: {item['text']}")
            QMessageBox.information(self, "JARVIS Reminder", f"It's time!\n\n{item['text']}")
            if jarvis_core is not None and hasattr(jarvis_core, "speak"):
                threading.Thread(target=jarvis_core.speak, args=(message,), daemon=True).start()

    def send_typed_command(self):
        command = self.voice_command_input.text().strip() if hasattr(self, "voice_command_input") else ""
        if not command:
            return
        self.send_command(command)
        self.voice_command_input.clear()

    def build_notes_page(self):
        """Persistent local notes with create, save, delete, and export actions."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = self.card()
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        title = QLabel("QUICK NOTES")
        title.setObjectName("SectionTitle")
        box.addWidget(title)
        box.addWidget(QLabel("Keep project ideas, study points, and reminders saved locally on this PC."))
        row = QHBoxLayout()
        self.notes_list = QListWidget()
        self.notes_list.setMinimumWidth(220)
        self.notes_list.currentRowChanged.connect(self.load_selected_note)
        row.addWidget(self.notes_list, 1)
        editor_col = QVBoxLayout()
        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Note title")
        editor_col.addWidget(self.note_title)
        self.note_editor = QTextEdit()
        self.note_editor.setPlaceholderText("Write your note here…")
        editor_col.addWidget(self.note_editor, 1)
        row.addLayout(editor_col, 3)
        box.addLayout(row, 1)
        buttons = QHBoxLayout()
        new_btn = QPushButton("＋ NEW NOTE")
        new_btn.clicked.connect(self.new_note)
        save_btn = QPushButton("✓ SAVE NOTE")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_current_note)
        delete_btn = QPushButton("✕ DELETE NOTE")
        delete_btn.setObjectName("DangerButton")
        delete_btn.clicked.connect(self.delete_current_note)
        export_btn = QPushButton("⇩ EXPORT NOTE")
        export_btn.clicked.connect(self.export_current_note)
        buttons.addWidget(new_btn)
        buttons.addWidget(save_btn)
        buttons.addWidget(delete_btn)
        buttons.addWidget(export_btn)
        box.addLayout(buttons)
        layout.addWidget(card)
        self.refresh_notes_list()
        return page

    def load_notes(self):
        try:
            if self.notes_file.exists():
                data = json.loads(self.notes_file.read_text(encoding="utf-8"))
                self.notes = data if isinstance(data, list) else []
        except Exception as exc:
            self.notes = []
            print(f"JARVIS notes load warning: {exc}")

    def persist_notes(self):
        self.notes_file.parent.mkdir(parents=True, exist_ok=True)
        self.notes_file.write_text(json.dumps(self.notes, ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh_notes_list(self, select_row=None):
        if not hasattr(self, "notes_list"):
            return
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        for note in self.notes:
            title = str(note.get("title") or "Untitled note").strip() or "Untitled note"
            self.notes_list.addItem(QListWidgetItem(title))
        self.notes_list.blockSignals(False)
        if self.notes:
            row = max(0, min(select_row if select_row is not None else 0, len(self.notes)-1))
            self.notes_list.setCurrentRow(row)
        else:
            self.note_title.clear()
            self.note_editor.clear()

    def load_selected_note(self, row):
        if row < 0 or row >= len(self.notes):
            return
        note = self.notes[row]
        self.note_title.setText(str(note.get("title", "")))
        self.note_editor.setPlainText(str(note.get("body", "")))

    def new_note(self):
        self.notes.append({"title": "New note", "body": "", "updated": datetime.now().isoformat(timespec="seconds")})
        try:
            self.persist_notes()
        except Exception as exc:
            self.add_log(f"NOTE SAVE ERROR: {exc}")
            QMessageBox.warning(self, "Quick Notes", f"Could not save the new note.\n{exc}")
            self.notes.pop()
            return
        self.refresh_notes_list(len(self.notes)-1)
        self.note_title.setFocus()
        self.note_title.selectAll()
        self.add_log("Created a new quick note.")

    def save_note_shortcut(self):
        # Ctrl+S saves a note only while the Quick Notes page is active.
        if self.pages.currentIndex() == 2:
            self.save_current_note()

    def save_current_note(self):
        row = self.notes_list.currentRow()
        if row < 0:
            self.new_note()
            row = self.notes_list.currentRow()
            if row < 0:
                return
        title = self.note_title.text().strip() or "Untitled note"
        self.notes[row] = {"title": title, "body": self.note_editor.toPlainText(), "updated": datetime.now().isoformat(timespec="seconds")}
        try:
            self.persist_notes()
            self.refresh_notes_list(row)
            self.add_log(f"Saved note: {title}")
        except Exception as exc:
            self.add_log(f"NOTE SAVE ERROR: {exc}")
            QMessageBox.warning(self, "Quick Notes", f"Could not save the note.\n{exc}")

    def delete_current_note(self):
        row = self.notes_list.currentRow()
        if row < 0:
            return
        title = self.notes[row].get("title", "Untitled note")
        answer = QMessageBox.question(self, "Delete Note", f"Delete '{title}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.notes.pop(row)
        try:
            self.persist_notes()
            self.refresh_notes_list(min(row, len(self.notes)-1))
            self.add_log(f"Deleted note: {title}")
        except Exception as exc:
            self.notes.insert(row, removed)
            self.refresh_notes_list(row)
            QMessageBox.warning(self, "Quick Notes", f"Could not delete the note.\n{exc}")

    def export_current_note(self):
        row = self.notes_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Export Note", "Select or create a note first.")
            return
        note = self.notes[row]
        suggested = str(note.get("title", "JARVIS note")).strip() or "JARVIS note"
        safe = re.sub(r"[^A-Za-z0-9 _-]", "", suggested).strip() or "JARVIS note"
        path, _ = QFileDialog.getSaveFileName(self, "Export Note", str(Path.home() / f"{safe}.txt"), "Text files (*.txt)")
        if not path:
            return
        content = f"{note.get('title', 'Untitled note')}\nUpdated: {note.get('updated', 'unknown')}\n\n{note.get('body', '')}"
        try:
            Path(path).write_text(content, encoding="utf-8")
            QMessageBox.information(self, "Export Note", "Note exported successfully.")
            self.add_log(f"Exported note to {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Note", f"Could not export the note.\n{exc}")

    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = self.card()
        v = QVBoxLayout(card)
        v.setContentsMargins(24, 22, 24, 22)
        title = QLabel("SETTINGS")
        title.setObjectName("SectionTitle")
        v.addWidget(title)
        v.addWidget(QLabel("Adjust how JARVIS listens. Changes apply to the current session."))
        form = QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.addWidget(QLabel("Speech recognition language"), 0, 0)
        self.language_combo = QComboBox()
        self.language_combo.addItem("English (Pakistan) — en-PK (recommended)", "en-PK")
        self.language_combo.addItem("English (India) — en-IN", "en-IN")
        self.language_combo.addItem("English (US) — en-US", "en-US")
        self.language_combo.addItem("English (UK) — en-GB", "en-GB")
        self.language_combo.addItem("Urdu (Pakistan) — ur-PK", "ur-PK")
        form.addWidget(self.language_combo, 0, 1)
        form.addWidget(QLabel("Maximum phrase length (seconds)"), 1, 0)
        self.phrase_spin = QSpinBox()
        self.phrase_spin.setRange(3, 20)
        self.phrase_spin.setValue(8)
        form.addWidget(self.phrase_spin, 1, 1)
        self.auto_scroll_setting = QCheckBox("Automatically scroll activity console")
        self.auto_scroll_setting.setChecked(True)
        self.auto_scroll_setting.toggled.connect(self.auto_scroll_box.setChecked)
        form.addWidget(self.auto_scroll_setting, 2, 0, 1, 2)

        self.startup_checkbox = QCheckBox("Enable Startup — launch JARVIS automatically when I sign in to Windows")
        self.startup_checkbox.setChecked(self.is_startup_enabled())
        form.addWidget(self.startup_checkbox, 3, 0, 1, 2)

        form.addWidget(QLabel("Dashboard theme"), 4, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Neon Blue + Purple", "neon")
        self.theme_combo.addItem("Ocean Cyan", "ocean")
        self.theme_combo.addItem("Emerald", "emerald")
        self.theme_combo.addItem("Rose / Magenta", "rose")
        self.theme_combo.addItem("Midnight Gold", "gold")
        self.theme_combo.addItem("Arctic Ice", "arctic")
        form.addWidget(self.theme_combo, 4, 1)

        self.start_minimized_checkbox = QCheckBox("Start JARVIS minimized to the taskbar")
        self.start_minimized_checkbox.setChecked(False)
        form.addWidget(self.start_minimized_checkbox, 5, 0, 1, 2)

        test_mic = QPushButton("🎙  TEST MICROPHONE")
        test_mic.clicked.connect(self.test_microphone)
        form.addWidget(test_mic, 6, 0)
        reset = QPushButton("↺  RESET SETTINGS")
        reset.clicked.connect(self.reset_settings)
        form.addWidget(reset, 6, 1)

        apply = QPushButton("✓  APPLY & SAVE SETTINGS")
        apply.setObjectName("PrimaryButton")
        apply.clicked.connect(self.apply_settings)
        form.addWidget(apply, 7, 0, 1, 2)
        help_text = QLabel("Tip: If voice recognition fails, use typed commands and check the Activity Console for the exact error.")
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#9dc8e8;padding:8px 2px;")
        v.addWidget(help_text)
        v.addLayout(form)
        v.addStretch()
        layout.addWidget(card)
        return page

    def build_about_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = self.card()
        v = QVBoxLayout(card)
        v.setContentsMargins(24, 22, 24, 22)
        title = QLabel("ABOUT JARVIS")
        title.setObjectName("SectionTitle")
        v.addWidget(title)
        name = QLabel("J.A.R.V.I.S — Personal AI Assistant")
        name.setStyleSheet("font-size:21px;font-weight:800;color:#e5f8ff;")
        v.addWidget(name)
        intro = QLabel("J.A.R.V.I.S is a personal Windows desktop assistant built to make everyday computer use simpler, faster, and more approachable. Instead of navigating through multiple menus for common tasks, you can speak to JARVIS or type a short command and receive a visible response. The dashboard brings together microphone controls, shortcuts, system telemetry, and an activity console so you can see what has been requested and how the assistant responds. The goal is to feel like a friendly, conversational desktop companion: natural Roman Urdu or English, attentive responses, and clear activity logs—not a mysterious tool working silently in the background.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        v.addSpacing(8)
        for heading, body in [
            ("VOICE & COMMANDS", "Use Start Listening to speak naturally, or type a command when the room is noisy or speech recognition gets a word wrong. JARVIS supports common English and Roman Urdu command variations for supported actions. Voice Control provides shortcuts for opening File Explorer, VS Code, Downloads, Calculator and Notepad; taking screenshots; checking battery and system information; adjusting volume; reading clipboard text; and opening Windows tools. A command is only supported when its handler is implemented in the core code, and an app may not launch if it is not installed."),
            ("DESKTOP PRODUCTIVITY", "Quick actions are designed to reduce repetitive clicks. You can launch frequently used tools, check basic device information, capture screenshots, and access folders without hunting through the Start menu. The activity console records command requests and responses to make troubleshooting easier. For file organization, review the target folder and the resulting structure; do not run a file-moving action if you are unsure what it will change."),
            ("SYSTEM AWARENESS", "The dashboard can display basic system telemetry such as CPU load, memory usage, and battery status when Windows and the hardware provide those readings. These values can fluctuate as programs start or stop, and battery information may be unavailable on some desktop computers. They are intended as a quick overview, not as a diagnostic or security guarantee."),
            ("PRIVACY & CONTROL", "The listening buttons let you start or stop microphone input. Typed commands are available as an alternative. Some features may depend on installed programs, Windows settings, network access, or permissions. JARVIS should work within normal Windows security boundaries. Important actions—such as shutting down the computer, deleting data, or changing startup behaviour—should be clearly explained and confirmed before they run. Always save important work before closing applications or restarting the system."),
            ("BUILT FOR LEARNING", "JARVIS is a hands-on project for exploring Python, PySide6 desktop interfaces, speech recognition, text-to-speech, process launching, and Windows automation. Its features can be expanded step by step, with each command tested independently. Because computer configurations differ, a feature may need a valid installation path or a compatible package to work on every machine."),
        ]:
            h = QLabel(heading)
            h.setStyleSheet("font-size:13px;font-weight:900;color:#37e2ff;padding-top:8px;")
            v.addWidget(h)
            paragraph = QLabel(body)
            paragraph.setWordWrap(True)
            paragraph.setStyleSheet("color:#c3e6f5;font-size:12px;line-height:1.3;")
            v.addWidget(paragraph)
        creator = QLabel("CREATED & DEVELOPED BY MEHAR TAHIR  •  JARVIS AI DESKTOP ASSISTANT")
        creator.setWordWrap(True)
        creator.setStyleSheet("font-size:16px;font-weight:900;color:#20dfff;padding-top:14px;")
        v.addWidget(creator)
        version = QLabel("Project status: Personal desktop project • Interface: PySide6 • Platform: Windows")
        version.setWordWrap(True)
        version.setStyleSheet("color:#8bbbd3;padding-top:5px;")
        v.addWidget(version)
        v.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        layout.addWidget(scroll)
        return page

    def start_worker(self):
        self.worker = JarvisWorker()
        self.worker.log_signal.connect(self.add_log)
        self.worker.answer_signal.connect(self.show_answer)
        self.worker.status_signal.connect(self.set_status)
        self.worker.reminder_request.connect(self.add_voice_reminder)
        self.worker.start()
        self.add_log("JARVIS dashboard started.")
        self.add_log("Dashboard is running. For Windows startup, launch desktop_app.py rather than main.py alone.")
        if jarvis_core is not None:
            self.add_log(f"Core loaded from: {getattr(jarvis_core, '__file__', 'unknown path')}")
        if CORE_IMPORT_ERROR:
            self.add_log(f"WARNING: main.py could not be imported: {CORE_IMPORT_ERROR}")

    def add_log(self, message):
        if not hasattr(self, "console"):
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.console.append(f'<span style="color:#00cfff">[{stamp}]</span> {self.escape_html(str(message))}')
        if self.auto_scroll:
            self.console.moveCursor(self.console.textCursor().MoveOperation.End)

    @staticmethod
    def escape_html(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def show_answer(self, message):
        self.add_log(f"JARVIS: {message}")

    def set_status(self, status):
        if hasattr(self, "top_status"):
            self.top_status.setText("● " + status)
        if hasattr(self, "core_status"):
            self.core_status.setText("● " + status)
        if hasattr(self, "voice_status_label"):
            self.voice_status_label.setText("Status: " + status)
        listening = status == "LISTENING"
        for name in ("listen_button", "voice_start"):
            button = getattr(self, name, None)
            if button:
                button.setEnabled(not listening)
        for name in ("stop_button", "voice_stop"):
            button = getattr(self, name, None)
            if button:
                button.setEnabled(listening)
        color = "#00ed9b" if status in ("READY", "LISTENING", "ONLINE") else ("#ff5268" if status in ("ERROR", "OFFLINE") else "#42e8ff")
        if hasattr(self, "core_status"):
            self.core_status.setStyleSheet(f"color:{color};font-size:15px;font-weight:bold;background:#041d32;border:1px solid #0878a5;border-radius:10px;padding:10px;")

    def toggle_listening(self):
        if self.worker and self.worker.listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if self.worker and self.worker.isRunning():
            self.worker.set_listening(True)
        else:
            self.add_log("Voice worker is not running. Check microphone and dependencies.")

    def stop_listening(self):
        if self.worker and self.worker.isRunning():
            self.worker.set_listening(False)

    def send_command(self, command):
        if self.worker and self.worker.isRunning():
            self.worker.send_command(command)
            self.add_log(f"ACTION REQUESTED: {command}")
        else:
            self.add_log("JARVIS worker is not running.")

    def test_voice(self):
        if jarvis_core is not None and hasattr(jarvis_core, "speak"):
            self.add_log("Voice test requested.")
            self.worker.answer_signal.emit("Hello! JARVIS voice test is working.") if self.worker else None
            try:
                threading.Thread(target=jarvis_core.speak, args=("Hello! JARVIS voice test is working.",), daemon=True).start()
            except Exception as exc:
                self.add_log(f"VOICE TEST ERROR: {exc}")
        else:
            QMessageBox.warning(self, "Voice Test", "Could not find speak() in main.py.")

    @staticmethod
    def is_startup_enabled():
        if os.name != "nt":
            return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "JARVIS AI")
            return True
        except (OSError, FileNotFoundError):
            return False

    def set_startup_enabled(self, enabled):
        if os.name != "nt":
            raise OSError("Windows startup is only available on Windows.")
        import winreg
        desktop_script = Path(__file__).resolve()
        python_exe = Path(sys.executable)
        pythonw = python_exe.with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else python_exe)
        command = f'"{executable}" "{desktop_script}"'
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "JARVIS AI", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, "JARVIS AI")
                except FileNotFoundError:
                    pass

    @staticmethod
    def settings_path():
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "settings.json"

    def apply_theme(self, theme):
        themes = {
            "neon": ("#10162d", "#20204b", "#725cff", "#56eaff"),
            "ocean": ("#071c2b", "#103b55", "#168aad", "#4df4e8"),
            "emerald": ("#071f1b", "#123d32", "#20a879", "#8cf5c5"),
            "rose": ("#241126", "#442044", "#db4b9b", "#ffb0df"),
            "gold": ("#17140b", "#352a12", "#d6a93a", "#ffe7a1"),
            "arctic": ("#071a2b", "#12344b", "#31bde6", "#b7f2ff"),
        }
        bg, panel, accent, glow = themes.get(theme, themes["neon"])
        self.setStyleSheet(STYLE + f"\nQWidget {{ selection-background-color:{accent}; }} QFrame#Card {{ background:{bg}; border:1px solid {accent}; }} QPushButton {{ border:1px solid {accent}; }} QPushButton:hover {{ background:{panel}; border:1px solid {glow}; }} QLabel#SectionTitle, QLabel#FocusTime {{ color:{glow}; }}")

    def load_saved_settings(self):
        """Restore saved preferences safely on every launch."""
        path = self.settings_path()
        try:
            if not path.exists():
                self.add_log("No saved settings found; using defaults.")
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            language = data.get("language", "en-PK")
            idx = self.language_combo.findData(language)
            if idx >= 0:
                self.language_combo.setCurrentIndex(idx)
            self.phrase_spin.setValue(max(3, min(20, int(data.get("phrase_limit", 8)))) )
            auto_scroll = bool(data.get("auto_scroll", True))
            self.auto_scroll_setting.setChecked(auto_scroll)
            self.auto_scroll_box.setChecked(auto_scroll)
            theme = data.get("theme", "neon")
            idx = self.theme_combo.findData(theme)
            if idx >= 0:
                self.theme_combo.setCurrentIndex(idx)
            self.start_minimized_checkbox.setChecked(bool(data.get("start_minimized", False)))
            self.startup_checkbox.setChecked(self.is_startup_enabled())
            self.auto_scroll = auto_scroll
            self.apply_theme(theme)
            if self.worker:
                self.worker.configure(language, self.phrase_spin.value())
            self.add_log("Saved preferences restored from settings.json.")
        except Exception as exc:
            self.add_log(f"SETTINGS LOAD WARNING: {exc}. Default settings will be used.")

    def apply_settings(self):
        language = self.language_combo.currentData()
        phrase_limit = self.phrase_spin.value()
        if self.worker:
            self.worker.configure(language, phrase_limit)
        self.auto_scroll = self.auto_scroll_setting.isChecked()
        theme = self.theme_combo.currentData() if hasattr(self, "theme_combo") else "neon"
        self.apply_theme(theme)
        try:
            self.set_startup_enabled(self.startup_checkbox.isChecked())
            startup_message = ("Windows startup enabled." if self.startup_checkbox.isChecked()
                               else "Windows startup disabled.")
            config_dir = self.settings_path().parent
            config_dir.mkdir(parents=True, exist_ok=True)
            settings = {
                "language": language,
                "phrase_limit": phrase_limit,
                "auto_scroll": self.auto_scroll,
                "startup_enabled": self.startup_checkbox.isChecked(),
                "theme": theme,
                "start_minimized": self.start_minimized_checkbox.isChecked(),
            }
            (config_dir / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
            self.add_log(startup_message)
            self.add_log(f"Theme applied: {theme}")
            self.add_log("Settings saved to AppData\\JARVIS\\settings.json")
        except Exception as exc:
            self.add_log(f"SETTINGS SAVE ERROR: {exc}")
            QMessageBox.warning(self, "Settings", f"Settings were applied, but saving encountered an error.\n{exc}")
            return
        QMessageBox.information(self, "Settings Saved", "Your settings were applied and saved. Theme changes apply now; speech settings apply to the current session.")

    def test_microphone(self):
        try:
            with sr.Microphone() as source:
                recognizer = sr.Recognizer()
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
            self.add_log("Microphone test passed: a microphone device opened successfully.")
            QMessageBox.information(self, "Microphone Test", "Microphone detected successfully. This checks device access, not online speech recognition.")
        except Exception as exc:
            self.add_log(f"MICROPHONE TEST FAILED: {exc}")
            QMessageBox.warning(self, "Microphone Test", f"JARVIS could not open a microphone.\n\n{exc}\n\nCheck Windows Settings > Privacy & security > Microphone.")

    def reset_settings(self):
        self.language_combo.setCurrentIndex(0)
        self.phrase_spin.setValue(8)
        self.auto_scroll_setting.setChecked(True)
        self.startup_checkbox.setChecked(self.is_startup_enabled())
        self.theme_combo.setCurrentIndex(0)
        self.start_minimized_checkbox.setChecked(False)
        self.add_log("Settings reset to recommended defaults. Click APPLY & SAVE SETTINGS to apply.")

    def set_auto_scroll(self, checked):
        self.auto_scroll = checked
        self.auto_scroll_setting.setChecked(checked)

    def export_console(self):
        """Export visible activity logs for troubleshooting."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JARVIS Activity Log",
            str(Path.home() / f"JARVIS_activity_{datetime.now():%Y%m%d_%H%M%S}.txt"),
            "Text files (*.txt)"
        )
        if not path:
            return
        try:
            plain_text = self.console.toPlainText()
            Path(path).write_text(plain_text, encoding="utf-8")
            self.add_log(f"Activity log exported to: {path}")
            QMessageBox.information(self, "Export Complete", "Your activity log was saved successfully.")
        except Exception as exc:
            self.add_log(f"LOG EXPORT ERROR: {exc}")
            QMessageBox.warning(self, "Export Failed", f"Could not save the activity log.\n{exc}")

    def clear_console(self):
        self.console.clear()
        self.add_log("Console cleared.")

    def show_page(self, name):
        mapping = {"Dashboard": 0, "Voice Control": 1, "Quick Notes": 2, "Settings": 3, "About": 4, "Quick Actions": 5, "System Monitor": 6, "Reminders": 7, "Focus Sprint": 8}
        self.pages.setCurrentIndex(mapping.get(name, 0))
        if name == "Reminders": self.refresh_reminder_center()
        for key, button in self.nav_buttons.items():
            button.setProperty("active", key == name)
            button.style().unpolish(button)
            button.style().polish(button)
        self.add_log(f"Opened {name} section.")

    def update_telemetry(self):
        """Refresh dashboard and monitor from one consistent system sample."""
        try:
            cpu = float(psutil.cpu_percent(interval=None))
            mem = psutil.virtual_memory()
            ram = float(mem.percent)
            try:
                battery = psutil.sensors_battery()
            except Exception:
                battery = None

            # Dashboard metrics
            if hasattr(self, "cpu_value"):
                self.cpu_value.setText(f"{cpu:.0f}%")
                self.cpu_bar.setValue(max(0, min(100, round(cpu))))
            if hasattr(self, "ram_value"):
                self.ram_value.setText(f"{ram:.0f}%")
                self.ram_bar.setValue(max(0, min(100, round(ram))))
            if hasattr(self, "battery_value"):
                if battery is not None:
                    self.battery_value.setText(f"{battery.percent:.0f}%")
                    self.battery_bar.setValue(max(0, min(100, round(battery.percent))))
                else:
                    self.battery_value.setText("AC / N/A")
                    self.battery_bar.setValue(0)

            # Automatic spoken alerts are rate-limited and latch until conditions recover.
            if battery is None or battery.power_plugged or battery.percent > 25:
                self._battery_alert_latched = False
            elif battery.percent <= 20 and not self._battery_alert_latched:
                self._battery_alert_latched = True
                alert = f"Battery low hai, {round(battery.percent)} percent baqi hai. Charger laga lo."
                self.add_log("BATTERY ALERT: " + alert)
                if self.worker:
                    self.worker.answer_signal.emit(alert)

            if cpu >= 90 or ram >= 90:
                self._performance_high_samples += 1
            else:
                self._performance_high_samples = 0
                self._performance_alert_latched = False
            if self._performance_high_samples >= 5 and not self._performance_alert_latched:
                self._performance_alert_latched = True
                causes = []
                if cpu >= 90: causes.append(f"CPU {round(cpu)} percent")
                if ram >= 90: causes.append(f"RAM {round(ram)} percent")
                alert = "PC performance alert. Resource usage high hai: " + " aur ".join(causes) + ". Task Manager check kar lo."
                self.add_log("PERFORMANCE ALERT: " + alert)
                if self.worker:
                    self.worker.answer_signal.emit(alert)

            # System Monitor page
            if hasattr(self, "system_cpu_value"):
                self.system_cpu_value.setText(f"{cpu:.0f}%")
                self.system_cpu_bar.setValue(max(0, min(100, round(cpu))))
                self.system_ram_value.setText(f"{ram:.0f}%")
                self.system_ram_bar.setValue(max(0, min(100, round(ram))))
                if battery is not None:
                    self.system_battery_value.setText(f"{battery.percent:.0f}%")
                    self.system_battery_bar.setValue(max(0, min(100, round(battery.percent))))
                else:
                    self.system_battery_value.setText("AC POWER")
                    self.system_battery_bar.setValue(0)
            # Storage and network throughput are sampled alongside CPU/RAM.
            try:
                drive_root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
                disk = psutil.disk_usage(drive_root)
                if hasattr(self, "system_disk_value"):
                    self.system_disk_value.setText(f"{disk.percent:.0f}%")
                    self.system_disk_bar.setValue(max(0, min(100, round(disk.percent))))
                disk_text = f"Disk: {disk.used / (1024**3):.1f} / {disk.total / (1024**3):.1f} GB ({disk.percent:.0f}% used)"
            except Exception as disk_exc:
                disk_text = f"Disk: unavailable ({disk_exc})"

            try:
                net_now = psutil.net_io_counters()
                now_sample = datetime.now()
                elapsed = max((now_sample - self._net_previous_at).total_seconds(), 0.25)
                down_rate = max(0, net_now.bytes_recv - self._net_previous.bytes_recv) / elapsed
                up_rate = max(0, net_now.bytes_sent - self._net_previous.bytes_sent) / elapsed
                self._net_previous, self._net_previous_at = net_now, now_sample
                def rate_text(value):
                    if value >= 1024 * 1024:
                        return f"{value / (1024 * 1024):.1f} MB/s"
                    if value >= 1024:
                        return f"{value / 1024:.0f} KB/s"
                    return f"{value:.0f} B/s"
                if hasattr(self, "system_net_down"):
                    self.system_net_down.setText(rate_text(down_rate))
                    self.system_net_down_bar.setValue(min(100, int(down_rate / (10 * 1024 * 1024) * 100)))
                    self.system_net_up.setText(rate_text(up_rate))
                    self.system_net_up_bar.setValue(min(100, int(up_rate / (5 * 1024 * 1024) * 100)))
                network_text = f"Network: ↓ {rate_text(down_rate)}  •  ↑ {rate_text(up_rate)}"
            except Exception as net_exc:
                network_text = f"Network: unavailable ({net_exc})"

            if hasattr(self, "system_details"):
                self.system_details.setText(
                    f"Device: {platform.node()}\nOS: {platform.system()} {platform.release()}\n"
                    f"CPU cores: {psutil.cpu_count(logical=True)}\n"
                    f"Memory: {mem.used / (1024**3):.1f} / {mem.total / (1024**3):.1f} GB used\n"
                    f"{disk_text}\n{network_text}"
                )
            if hasattr(self, "system_info_label"):
                batt_text = f"{battery.percent:.0f}%" if battery is not None else "Desktop / unavailable"
                self.system_info_label.setText(
                    f"OS: {platform.system()} {platform.release()}\n"
                    f"CPU: {cpu:.0f}%   •   RAM: {ram:.0f}%   •   Battery: {batt_text}"
                )

            if hasattr(self, "dashboard_note_preview"):
                if self.notes:
                    latest = self.notes[-1]
                    preview = str(latest.get("body", "")).strip().replace("\n", " ")
                    self.dashboard_note_preview.setText(
                        (str(latest.get("title", "Saved note")) + " — " + preview[:90])
                        if preview else str(latest.get("title", "Saved note"))
                    )
                else:
                    self.dashboard_note_preview.setText("No notes yet — create your first quick note.")
            if hasattr(self, "dashboard_reminders"):
                self.dashboard_reminders.clear()
                for item in self.reminders[:5]:
                    if isinstance(item, dict):
                        label = str(item.get("title") or item.get("text") or "Reminder")
                        when = str(item.get("when") or item.get("datetime") or item.get("time") or "")
                        self.dashboard_reminders.addItem("◉  " + label + ("   ·   " + when if when else ""))
                    else:
                        self.dashboard_reminders.addItem("◉  " + str(item))
                if not self.reminders:
                    self.dashboard_reminders.addItem("No reminders scheduled")
        except Exception as exc:
            self.add_log(f"TELEMETRY ERROR: {exc}")

    def fetch_weather_async(self):
        """Fetch weather off the UI thread and deliver it through a Qt signal."""
        if getattr(self, "_weather_fetching", False):
            return
        self._weather_fetching = True

        def task():
            try:
                url = (
                    "https://api.open-meteo.com/v1/forecast?latitude=31.5204&longitude=74.3587"
                    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                    "precipitation,weather_code,wind_speed_10m&timezone=Asia%2FKarachi"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Desktop/1.1"})
                with urllib.request.urlopen(req, timeout=12) as response:
                    data = json.loads(response.read().decode("utf-8"))
                current = data.get("current") or {}
                if "temperature_2m" not in current:
                    raise ValueError("Weather API did not return current conditions")
                code = int(current.get("weather_code", 0))
                labels = {
                    0: ("Clear sky", "☀"), 1: ("Mainly clear", "🌤"),
                    2: ("Partly cloudy", "⛅"), 3: ("Overcast", "☁"),
                    45: ("Fog", "🌫"), 48: ("Fog", "🌫"),
                    51: ("Light drizzle", "🌦"), 53: ("Drizzle", "🌦"), 55: ("Heavy drizzle", "🌧"),
                    61: ("Light rain", "🌦"), 63: ("Rain", "🌧"), 65: ("Heavy rain", "🌧"),
                    71: ("Snow", "❄"), 73: ("Snow", "❄"), 75: ("Heavy snow", "❄"),
                    80: ("Rain showers", "🌦"), 81: ("Rain showers", "🌧"), 82: ("Heavy showers", "🌧"),
                    95: ("Thunderstorm", "⛈"), 96: ("Thunderstorm with hail", "⛈"), 99: ("Thunderstorm with hail", "⛈"),
                }
                desc, icon = labels.get(code, ("Current conditions", "🌡"))
                result = {
                    "ok": True, "temp": current.get("temperature_2m", "--"),
                    "feels": current.get("apparent_temperature", current.get("temperature_2m", "--")),
                    "humidity": current.get("relative_humidity_2m", "--"),
                    "wind": current.get("wind_speed_10m", "--"),
                    "desc": desc, "icon": icon,
                }
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self.weather_result.emit(result)

        threading.Thread(target=task, daemon=True, name="JARVIS-Weather").start()

    def apply_weather_result(self, result):
        """Update weather labels on the GUI thread; signal delivery is thread-safe."""
        self._weather_fetching = False
        if not result.get("ok"):
            self.weather_temp.setText("--°C")
            error_text = str(result.get("error", "unknown error"))
            # Retry sooner after an outage instead of leaving the panel stale for 15 minutes.
            if hasattr(self, "weather_timer"):
                self.weather_timer.start(2 * 60 * 1000)
            short_error = error_text.replace("\n", " ").strip()
            if len(short_error) > 90:
                short_error = short_error[:87] + "..."
            self.weather_desc.setText("Weather unavailable • retrying soon\n" + (short_error or "Check internet connection."))
            self.add_log("WEATHER ERROR: " + error_text)
            return
        if hasattr(self, "weather_timer"):
            self.weather_timer.start(15 * 60 * 1000)
        self.weather_temp.setText(f"{result['temp']}°C")
        if hasattr(self, "weather_icon"):
            self.weather_icon.setText(result.get("icon", "🌤"))
        self.weather_desc.setText(
            f"{result['desc']} • Feels like {result['feels']}°C\n"
            f"Humidity {result['humidity']}% • Wind {result['wind']} km/h • Lahore\n"
            f"Updated {datetime.now().strftime('%I:%M %p')}"
        )
        self.add_log("Weather updated successfully for Lahore.")

    def update_clock(self):
        now = datetime.now()
        hour = now.hour
        if hour < 12:
            greeting = "Good Morning"
        elif hour < 17:
            greeting = "Good Afternoon"
        else:
            greeting = "Good Evening"
        if hasattr(self, "hero_label"):
            self.hero_label.setText(f"{greeting}, Muhammad Tahir!")
        self.clock_label.setText(now.strftime("%I:%M:%S %p\n%a, %d %b %Y"))

    def closeEvent(self, event):
        self.telemetry_timer.stop()
        self.clock_timer.stop()
        if hasattr(self, "reminder_timer"):
            self.reminder_timer.stop()
        if hasattr(self, "weather_timer"):
            self.weather_timer.stop()
        if hasattr(self, "focus_timer"):
            self.focus_timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop_worker()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))
    window = JarvisWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
