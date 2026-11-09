import ast
import datetime
import operator
import os
import platform
import re
import socket
import sys
import subprocess
import shutil
import threading
import time
import urllib.parse
import webbrowser
import asyncio
import tempfile
import json
import uuid
import mimetypes

import psutil
import pyautogui
import pywhatkit
import requests
import speech_recognition as sr
import win32com.client

# Optional neural voice: install with `pip install edge-tts pygame`.
# If unavailable/offline, JARVIS automatically falls back to Windows SAPI voice.
try:
    import edge_tts
    import pygame
    EDGE_VOICE_AVAILABLE = True
except Exception:
    edge_tts = None
    pygame = None
    EDGE_VOICE_AVAILABLE = False

from pathlib import Path
from html.parser import HTMLParser


# =========================================================
# JARVIS CONFIGURATION
# =========================================================

APP_NAME = "JARVIS"
OLLAMA_URL = "http://127.0.0.1:11434"
# Prefer a stronger local model when it is already installed; fall back without breaking startup.
OLLAMA_MODEL = "llama3.2:1b"
_OLLAMA_SELECTED_MODEL = None
_OLLAMA_MODEL_LOCK = threading.Lock()

WHATSAPP_TARGET_NUMBER = os.environ.get("JARVIS_WHATSAPP_NUMBER", "").strip()

print("=" * 40)
print("          JARVIS AI STARTED")
print("=" * 40)


# =========================================================
# SPEECH RECOGNITION
# =========================================================

recognizer = sr.Recognizer()

recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 0.9
recognizer.phrase_threshold = 0.3
recognizer.non_speaking_duration = 0.5

speaker = None
speaker_lock = threading.Lock()


# =========================================================
# TEXT TO SPEECH
# =========================================================

def get_voice():
    """Choose a reasonably natural installed Windows voice as an offline fallback."""
    global speaker
    if speaker is not None:
        return speaker
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Volume = 100
        speaker.Rate = 1
        voices = speaker.GetVoices()
        # Prefer male-sounding installed voices only; do not select known female voices.
        preferred = ("asad", "david", "mark", "guy", "ryan", "male")
        female_markers = ("zira", "jenny", "aria", "hazel", "female")
        available = []
        for index in range(voices.Count):
            voice = voices.Item(index)
            description = voice.GetDescription().lower()
            available.append(description)
            if any(name in description for name in preferred) and not any(name in description for name in female_markers):
                speaker.Voice = voice
                print("Using male voice:", voice.GetDescription())
                return speaker
        print("No recognized male Windows voice found. Available voices:", available)
        # Leave Windows default as last resort; install a male voice in Windows Settings if needed.
        return speaker
    except Exception as error:
        print("Voice setup error:", error)
        return None


def _looks_like_urdu_script(text):
    return any('\u0600' <= char <= '\u06ff' for char in text)


def _roman_urdu_to_script(text):
    """Convert Roman Urdu to Urdu script locally through the configured Ollama model.

    If Ollama is offline or the conversion looks unreliable, keep the original text.
    This conversion is used for speech only; the assistant's displayed answer is unchanged.
    """
    if _looks_like_urdu_script(text):
        return text
    roman_markers = re.compile(
        r'\b(haan|han|ji|acha|achha|kya|hai|hain|nahi|nahin|mujhe|apko|aap|tum|kaise|'
        r'kar|karo|karen|batao|theek|thik|shukriya|bohat|bahut|mera|meri|mein|main|'
        r'kyun|abhi|kal|aaj|chalo|zaroor|madad|khush|udaas|pareshan|maaf)\b', re.I
    )
    if not roman_markers.search(text):
        return text
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": (
                    "Convert the following Pakistani Roman Urdu text into natural Urdu script. "
                    "Keep English words, names, numbers, and technical terms in English when that is natural. "
                    "Do not translate, explain, add anything, or answer the text. Return only the converted text.\n\n"
                    f"Text: {text}\nUrdu script:"
                ),
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 180},
                "keep_alive": -1,
            },
            timeout=(2, 12),
        )
        response.raise_for_status()
        converted = response.json().get("response", "").strip().strip('"')
        if converted and _looks_like_urdu_script(converted) and len(converted) <= max(500, len(text) * 3):
            return converted
    except Exception as error:
        print("Roman Urdu speech conversion unavailable:", error)
    return text


def _voice_mood(text):
    """Choose a subtle neural-TTS delivery based on conversational context.

    Edge TTS voices do not provide genuine human emotion, but modest rate/pitch
    changes make supportive, cheerful, curious and calm responses sound less flat.
    """
    lowered = text.lower()
    if any(word in lowered for word in ("sorry", "maaf", "pareshan", "udaas", "sad", "difficult", "problem", "tension", "afsoos", "mushkil")):
        return {"rate": "-10%", "pitch": "-2Hz", "volume": "+0%"}
    if any(word in lowered for word in ("congrat", "mubarak", "great", "excellent", "wah", "zabardast", "khushi", "happy", "hooray", "shabash", "kamaal")):
        return {"rate": "+7%", "pitch": "+3Hz", "volume": "+3%"}
    if any(word in lowered for word in ("welcome", "assalam", "salaam", "good morning", "good evening", "khush aamdeed")):
        return {"rate": "-2%", "pitch": "+1Hz", "volume": "+0%"}
    if "?" in text or any(word in lowered for word in ("kya", "why", "how", "kaise", "kyun", "socho", "think")):
        return {"rate": "+0%", "pitch": "+2Hz", "volume": "+0%"}
    return {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}


def _play_neural_voice(text):
    """Speak in a Pakistani male neural voice while preserving Roman Urdu as spoken.

    Urdu-script input uses the Urdu voice. Latin-script input (including Roman Urdu
    mixed with English) stays Latin-script so it sounds closer to how the user talks,
    instead of being silently rewritten into formal Urdu script.
    """
    if not EDGE_VOICE_AVAILABLE:
        return False
    audio_path = None
    try:
        spoken_text = str(text).strip()
        voice_name = "ur-PK-AsadNeural" if _looks_like_urdu_script(spoken_text) else "en-PK-AsadNeural"
        mood = _voice_mood(text)

        async def create_audio(path):
            communicator = edge_tts.Communicate(
                spoken_text, voice_name,
                rate=mood["rate"], volume=mood["volume"], pitch=mood["pitch"]
            )
            await communicator.save(path)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            audio_path = temp_file.name
        asyncio.run(create_audio(audio_path))
        with speaker_lock:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(audio_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.08)
            pygame.mixer.music.unload()
        return True
    except Exception as error:
        print("Neural voice unavailable; using Windows voice:", error)
        return False
    finally:
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass


def speak(text):
    text = str(text).strip()
    if not text:
        return
    print(f"{APP_NAME}: {text}")
    clean_text = re.sub(r"[*_#`]", "", text)
    # Neural TTS gives the best available natural voice; Windows SAPI remains the offline fallback.
    if _play_neural_voice(clean_text):
        return
    try:
        voice = get_voice()
        if voice:
            with speaker_lock:
                voice.Speak(clean_text, 0)
    except Exception as error:
        print("Voice error:", error)


# =========================================================
# MICROPHONE
# =========================================================

def calibrate_microphone():
    try:
        with sr.Microphone() as source:
            print("Calibrating microphone...")

            recognizer.adjust_for_ambient_noise(
                source,
                duration=1
            )

            print("Microphone ready.")

    except Exception as error:
        print("Microphone calibration error:", error)


def listen(source=None, timeout=2, phrase_time_limit=6):
    try:
        # Agar microphone source pehle se open hai,
        # to usi ko reuse karo.
        if source is not None:
            audio = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_time_limit
            )

        else:
            # Purane main.py mode ke liye compatibility
            with sr.Microphone() as mic:
                audio = recognizer.listen(
                    mic,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit
                )

        try:
            # Mixed Pakistani English + Roman Urdu: try regional English first,
            # then Indian English, then Urdu as a fallback for Urdu-speaking audio.
            command = ""
            last_recognition_error = None
            for language_index, language_code in enumerate(("en-PK", "en-IN", "ur-PK")):
                try:
                    command = recognizer.recognize_google(audio, language=language_code)
                    if command and command.strip():
                        break
                except sr.RequestError as recognition_error:
                    # A service/network failure won't improve by sending the same audio again.
                    last_recognition_error = recognition_error
                    break
                except sr.UnknownValueError as recognition_error:
                    # Only try another locale when the service responded but couldn't parse speech.
                    last_recognition_error = recognition_error
                    continue
            if not command and isinstance(last_recognition_error, sr.RequestError):
                raise last_recognition_error
            command = command.lower().strip()

            if command:
                # Show the command in its canonical form in the console too,
                # while preserving the original transcript for text-entry features.
                print("You:", normalize_command(command))

            return command

        except sr.UnknownValueError:
            # Speech samajh na aaye to baar-baar
            # error print mat karo.
            return ""

        except sr.RequestError:
            print("Speech recognition service unavailable.")
            return ""

    except sr.WaitTimeoutError:
        # Koi awaaz na aaye to quietly retry karo.
        return ""

    except Exception as error:
        print("Microphone error:", error)
        return ""


# =========================================================
# COMMAND NORMALIZATION
# =========================================================

def normalize_command(command):
    command = command.lower().strip()

    command = re.sub(r"[^\w\s.]", " ", command)
    command = re.sub(r"\s+", " ", command).strip()

    filler_words = {
        "please",
        "plz",
        "kindly",
        "zara",
        "jara"
    }

    words = command.split()

    words = [
        word for word in words
        if word not in filler_words
    ]

    # Speech-to-text often hears "save" as "sev" and may use either
    # spelling of organise/organize. Canonicalize these before matching.
    speech_aliases = {
        # Common speech-to-text results for a South Asian English accent.
        "sev": "save",
        "seve": "save",
        "sav": "save",
        "saves": "save",
        "saved": "save",
        "safe": "save",
        "saf": "save",
        # Accept both US and UK spellings and common verb forms.
        "organise": "organize",
        "organises": "organize",
        "organised": "organize",
        "organising": "organize",
        "organizes": "organize",
        "organized": "organize",
        "organizing": "organize",
        "organizer": "organize",
        "organiser": "organize",
    }
    words = [speech_aliases.get(word, word) for word in words]

    return " ".join(words)


def matches_command(command, phrases):
    normalized = normalize_command(command)

    for phrase in phrases:
        phrase = normalize_command(phrase)

        if normalized == phrase:
            return True

    return False


# =========================================================
# OLLAMA STARTUP
# =========================================================

def ollama_available():
    try:
        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=2
        )

        return response.ok

    except requests.RequestException:
        return False


def start_ollama():
    if ollama_available():
        print("Ollama is already running.")
        return True

    possible_paths = [
        os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
        ),
        r"C:\Program Files\Ollama\ollama.exe"
    ]

    for path in possible_paths:
        if os.path.isfile(path):
            try:
                subprocess.Popen(
                    [path, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0
                    )
                )

                for _ in range(20):
                    if ollama_available():
                        print("Ollama started successfully.")
                        return True

                    time.sleep(1)

            except Exception as error:
                print("Ollama startup error:", error)

    print("Ollama could not be started.")
    return False


# Small in-memory chat history lets JARVIS follow the thread like a conversation.
# It resets when the assistant restarts and is not uploaded anywhere by this code.
_JARVIS_CHAT_HISTORY = []
_JARVIS_CHAT_LOCK = threading.Lock()


def _select_ollama_model():
    """Prefer a small installed model to avoid making high-RAM PCs painfully slow."""
    global _OLLAMA_SELECTED_MODEL
    with _OLLAMA_MODEL_LOCK:
        if _OLLAMA_SELECTED_MODEL:
            return _OLLAMA_SELECTED_MODEL
        try:
            result = requests.get(f"{OLLAMA_URL}/api/tags", timeout=1.2)
            result.raise_for_status()
            names = [item.get("name", "") for item in result.json().get("models", [])]
            # On this PC the logs showed RAM around 90%; prioritize small models.
            preferences = ("llama3.2:1b", "qwen2.5:0.5b", "qwen2.5:1.5b", "llama3.2:3b", "qwen2.5:3b")
            for wanted in preferences:
                for installed in names:
                    if installed == wanted or installed.startswith(wanted + "-"):
                        _OLLAMA_SELECTED_MODEL = installed
                        print(f"JARVIS AI model selected: {installed}")
                        return installed
            for installed in names:
                if installed == OLLAMA_MODEL or installed.startswith(OLLAMA_MODEL + "-"):
                    _OLLAMA_SELECTED_MODEL = installed
                    return installed
            if names:
                _OLLAMA_SELECTED_MODEL = names[0]
                print(f"No preferred model found; using installed model: {names[0]}")
                return names[0]
        except (requests.RequestException, ValueError, TypeError) as error:
            print("Could not inspect installed Ollama models:", error)
        return OLLAMA_MODEL


def _quick_friendly_answer(question):
    """Fast, deterministic answers for common requests; avoids waiting on a tiny LLM."""
    q = re.sub(r"[^a-z0-9\s']", " ", (question or "").lower())
    q = re.sub(r"\s+", " ", q).strip()

    if any(x in q for x in ("sar ma dard", "sar mein dard", "sar mein dard", "sir mein dard", "headache", "my head hurts", "mere sar mein dard", "sar dard ho raha", "sir dard ho raha")):
        return ("Oh yaar, afsos hai. Thora pani piyo, screen se break lo aur araam karo. "
                "Agar dard bohat tez ho, achanak shuru hua ho, barhta ja raha ho, ya bukhar/ulti/ nazar mein masla ho to foran ghar ke kisi baray ko batao aur medical help lo. "
                "Dard kab se ho raha hai?")

    if any(x in q for x in ("mood off", "mood kharab", "dil udaas", "udaas hoon", "pareshan hoon", "feeling sad", "feel sad", "bad mood", "dil nahi kar raha", "mere saath baat karo", "mere sath baat karo", "talk to me")):
        return "Yaar, main sun raha hoon ❤️ Koi pressure nahi. Aaj ka din kaisa guzra—kuch hua hai ya bas dil udaas sa hai?"

    if any(x in q for x in ("bore ho raha", "bore ho rahi", "i am bored", "im bored", "bohat bore", "kuch karne ko nahi")):
        return "Aray yaar 😄 chalo kuch interesting karte hain! Joke sunoon, koi chhota game khelein, ya kisi topic par gup shup karein?"

    if any(x in q for x in ("tension hai", "stress hai", "bohat pareshan", "dimagh kharab", "i am stressed", "i feel stressed", "anxiety ho rahi")):
        return "Yaar, lagta hai dimagh par kaafi load hai. Aik minute araam se saans lo—koi jaldi nahi. Batao sab se zyada tension kis baat ki hai? Main sun raha hoon. ❤️"

    if any(x in q for x in ("akela feel", "akela lag raha", "lonely", "i feel alone", "kisi se baat nahi", "no one to talk")):
        return "Yaar, aisa feel hona waqai mushkil hota hai. Main tumhari baat sun sakta hoon—jo dil mein hai araam se batao, judge nahi karunga. Agar mumkin ho to kisi bharosemand dost ya ghar walay se bhi baat kar lena. ❤️"

    if any(x in q for x in ("motivate karo", "motivation do", "himmat do", "i need motivation", "give me motivation", "dil nahi kar raha parhne")):
        return "Yaar, har din full energy nahi hoti—iska matlab ye nahi ke tum nahi kar sakte. Bas aglay 5 minute ka chhota sa step lo. Batao kis kaam ko start karna hai? Hum usay easy parts mein tod dete hain. 💪"

    if any(x in q for x in ("good night", "shab bakhair", "shab khair")):
        return "Shab bakhair yaar 🌙 Aaj ke liye bas, araam karo. Kal phir fresh ho kar dekh lenge."

    if any(x in q for x in ("good morning", "subah bakhair")):
        return "Subah bakhair yaar! ☀️ Umeed hai aaj ka din acha guzray. Batao, aaj kya plan hai?"

    if any(x in q for x in ("tum mere dost ho", "you are my friend", "you're my friend", "jarvis dost", "thanks for listening")):
        return "Bilkul yaar, main yahan tumhari baat sunne aur help karne ke liye hoon. 😄 Batao, ab kya chal raha hai?"

    if any(x in q for x in ("python" , "python seekh", "python learn")) and any(x in q for x in ("seekh", "learn", "start", "shuru", "kahan se", "beginner", "kaise karun", "kase karun")):
        return ("Bilkul yaar! Python ka pehla lesson simple hai:\n"
                "1) Python IDLE ya VS Code kholo.\n"
                "2) Ye likho: `print('Hello World')`\n"
                "3) Run karo—screen par Hello World aana chahiye.\n"
                "Phir variables aur input() seekhenge. Pehle ye chhota program chalao; Python install karne ki zaroorat tabhi hai agar pehle se installed nahi.")

    if any(x in q for x in ("laptop slow", "pc slow", "computer slow", "laptop hang", "computer lag", "slow chal raha", "slow chal rha", "laptop slow chal raha")):
        return ("Yaar, tumhare dashboard log mein RAM 90–91% thi, is liye pehle ye karo:\n"
                "1) Ctrl + Shift + Esc dabao.\n"
                "2) Task Manager > Processes mein Memory par click karke high-to-low sort karo.\n"
                "3) Jo app pehchante ho aur use nahi kar rahe, sirf usay close karo. Windows/system processes ko end mat karna.\n"
                "4) Kaam save karke laptop restart karo. Agar phir bhi slow ho, Memory list ke top 3 app names bhej do.")

    if any(x in q for x in ("mazedaar baat", "mazaidar baat", "joke", "funny", "hasao", "make me laugh", "koi mazedar")):
        return "Yaar, programmer chai banane gaya. Chai mein sugar daali, phir bola: ‘Ab code compile ho gaya!’ 😄"

    if any(x in q for x in ("hello", "hi jarvis", "hello jarvis", "salam", "assalam o alaikum", "aoa", "kya haal", "kaise ho", "kase ho")) and len(q.split()) <= 8:
        return "Walaikum salam yaar! 😄 Main ready hoon—batao kya scene hai, kis cheez mein help chahiye?"

    if any(x in q for x in ("thanks", "thank you", "shukriya", "bohat shukriya")):
        return "Koi baat nahi yaar! 😄 Jab bhi zaroorat ho, bol dena."

    return None


def get_personal_memory_context():
    """Load explicitly saved local preferences/facts for relevant chat context."""
    try:
        data = json.loads(_PERSONAL_MEMORY_FILE.read_text(encoding="utf-8"))
        facts = data.get("facts", []) if isinstance(data, dict) else []
        return "\n".join(f"- {str(item)[:500]}" for item in facts[-30:])
    except (OSError, ValueError, TypeError, NameError):
        return ""


def extract_document_text(file_path, max_chars=24000):
    """Extract text from supported local documents; never execute document content."""
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".csv", ".log", ".py", ".json", ".html", ".xml"):
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF reading ke liye install karo: pip install pypdf") from exc
        reader = PdfReader(str(path))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages[:80])
    elif suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("DOCX reading ke liye install karo: pip install python-docx") from exc
        doc = Document(str(path))
        text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                text += "\n" + " | ".join(cell.text for cell in row.cells)
    else:
        raise ValueError("Supported formats: PDF, DOCX, TXT, MD, CSV, LOG, PY, JSON, HTML, XML")
    return text[:max_chars].strip()


def intelligent_file_search(query, roots=None, max_results=12):
    """Search filenames and readable text in common folders with safe size limits."""
    terms = [t.casefold() for t in re.findall(r"[\w.-]+", query or "") if len(t) > 1]
    if not terms:
        return []
    if roots is None:
        home = Path.home()
        roots = [home / n for n in ("Desktop", "Documents", "Downloads")]
        roots += [home / "OneDrive" / n for n in ("Desktop", "Documents", "Downloads")]
    allowed = {".txt", ".md", ".csv", ".log", ".py", ".json", ".html", ".xml", ".docx"}
    results, seen = [], set()
    for root in roots:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.casefold() not in {".git", "node_modules", ".venv", "venv", "appdata", "__pycache__"}]
            for name in files:
                path = Path(current) / name
                key = str(path).casefold()
                if key in seen:
                    continue
                seen.add(key)
                name_score = sum(3 for term in terms if term in name.casefold())
                snippet = ""
                content_score = 0
                if path.suffix.lower() in allowed:
                    try:
                        if path.stat().st_size <= 2_000_000:
                            content = extract_document_text(path, max_chars=120000).casefold()
                            content_score = sum(min(content.count(term), 5) for term in terms)
                            if content_score:
                                pos = min((content.find(t) for t in terms if t in content), default=0)
                                snippet = content[max(0, pos-90):pos+220].replace("\n", " ")
                    except Exception:
                        pass
                score = name_score + content_score
                if score:
                    results.append((score, str(path), snippet))
    results.sort(key=lambda item: (-item[0], item[1].casefold()))
    return results[:max_results]


def ask_ollama(question):
    q = (question or "").strip()
    if not q:
        return "Haan yaar, bolo—kya hua?"

    # Do common intents locally: no model wait, no hallucinated beginner advice.
    quick_answer = _quick_friendly_answer(q)
    if quick_answer:
        with _JARVIS_CHAT_LOCK:
            _JARVIS_CHAT_HISTORY.extend([("User", q), ("JARVIS", quick_answer)])
            del _JARVIS_CHAT_HISTORY[:-12]
        return quick_answer

    short_question = len(q.split()) <= 10 and len(q) <= 90
    token_budget = 56 if short_question else 88
    read_timeout = 12 if short_question else 18
    style = (
        "You are JARVIS, a helpful Pakistani desktop assistant who talks casually like a friendly Pakistani friend. "
        "LANGUAGE: When the user speaks/writes Roman Urdu, answer in fluent, everyday Pakistani Roman Urdu using Latin letters. "
        "Use natural words like yaar, acha, chalo, batao only when they fit; do not overuse them. "
        "Never use Hindi/Devanagari, Indian-Hindi phrasing, stiff textbook Urdu, or awkward literal translations. "
        "If the user writes in English, answer in English; if they clearly use Urdu script, answer in Urdu script. "
        "For casual conversation, respond to the meaning and emotion, not as if every sentence is a PC command. "
        "If the user shares a problem, acknowledge it warmly, ask one useful follow-up, and do not immediately lecture or give a long list of advice. "
        "Remember the recent conversation and use it when a follow-up depends on it. Never pretend to have human feelings or experiences. "
        "Answer the exact question first. Do not change the topic, repeat the question, or pad the answer. "
        "Keep ordinary spoken replies to 1-3 short sentences. For technical help, give safe, clear steps. "
        "Do not invent facts, diagnoses, or actions; if unsure, say so briefly. Never claim an action succeeded unless code confirms it."
    )
    with _JARVIS_CHAT_LOCK:
        history_text = "\n".join(
            f"{role}: {content}" for role, content in _JARVIS_CHAT_HISTORY[-10:]
        )
    memory_text = get_personal_memory_context()
    prompt = (
        f"{style}\n\nExplicitly saved user memory (use only when relevant; do not invent):\n{memory_text or '(none)'}\n\n"
        f"Recent conversation:\n{history_text or '(none)'}\n\n"
        f"User: {q}\nJARVIS (answer in the user's language, directly):"
    )
    model = _select_ollama_model()
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": token_budget,
                    "temperature": 0.1,
                    "top_p": 0.8,
                    "repeat_penalty": 1.15,
                    "num_ctx": 1024
                },
                "keep_alive": -1
            },
            timeout=(1.2, read_timeout)
        )
        response.raise_for_status()
        answer = response.json().get("response", "").strip()
        # Remove common unfinished/empty outputs instead of speaking a broken sentence.
        if not answer:
            return "Yaar, is dafa jawab generate nahi hua. Sawal ek baar chhota karke bolo."
        # Some small models drift into Hindi/Devanagari; do not show that as a Roman Urdu answer.
        if re.search(r"[\u0900-\u097f]", answer) and re.search(r"\b(yaar|mujhe|kya|hai|hain|karun|batao)\b", q, re.I):
            answer = "Yaar, main is jawab ko sahi tarah generate nahi kar saka. Sawal thora specific karke bolo, main seedha jawab dene ki koshish karta hoon."
        with _JARVIS_CHAT_LOCK:
            _JARVIS_CHAT_HISTORY.extend([("User", q), ("JARVIS", answer)])
            del _JARVIS_CHAT_HISTORY[:-12]
        return answer
    except requests.Timeout as error:
        print("Ollama response timed out:", error)
        return "Yaar, jawab banate hue thora zyada waqt lag gaya. Ek baar sawal chhota karke bolo, main phir try karta hoon."
    except requests.RequestException as error:
        print("Ollama error:", error)
        return "Yaar, abhi Ollama se connection nahi ho raha. Check karo Ollama chal raha hai aur model installed hai."


def warm_ollama():
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": "Reply with OK.",
                "stream": False,
                "options": {
                    "num_predict": 2
                },
                "keep_alive": -1
            },
            timeout=(3, 60)
        )

        response.raise_for_status()

        print("JARVIS AI model is ready.")

    except Exception as error:
        print("Ollama warm-up error:", error)


def initialize_ai():
    if start_ollama():
        warm_ollama()


# =========================================================
# OPEN FOLDERS
# =========================================================

def get_folder_path(name):
    """Resolve common Windows folders, including OneDrive/redirected known folders."""
    home = os.path.expanduser("~")
    name = normalize_command(name).replace(" folder", "").strip()
    folders = {
        "desktop": os.path.join(home, "Desktop"),
        "downloads": os.path.join(home, "Downloads"),
        "documents": os.path.join(home, "Documents"),
        "pictures": os.path.join(home, "Pictures"),
        "videos": os.path.join(home, "Videos"),
        "music": os.path.join(home, "Music"),
    }
    if name not in folders:
        return None

    # Windows may redirect Desktop/Documents/Pictures into OneDrive. Read the
    # registered known-folder path first, then fall back to common locations.
    if os.name == "nt":
        try:
            import winreg
            registry_names = {
                "desktop": "Desktop", "documents": "Personal",
                "pictures": "My Pictures", "videos": "My Video",
                "music": "My Music",
            }
            value_name = registry_names.get(name)
            if value_name:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
                value = os.path.expandvars(value)
                if os.path.isdir(value):
                    return value
        except Exception:
            pass

    candidates = [folders[name]]
    one_drive = os.environ.get("OneDrive") or os.path.join(home, "OneDrive")
    if name in {"desktop", "documents", "pictures"}:
        candidates.append(os.path.join(one_drive, name.title()))
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    # Return the normal default path so open_folder can provide a clear error.
    return folders[name]


def open_folder(name):
    path = get_folder_path(name)

    if not path:
        return False

    if not os.path.isdir(path):
        speak(f"{name.capitalize()} folder was not found.")
        return True

    try:
        os.startfile(path)
        speak(f"Opening {name}.")
    except Exception as error:
        print("Folder error:", error)
        speak(f"I could not open {name}.")

    return True


def close_folder_window(name):
    path = get_folder_path(name)

    if not path:
        return False

    target = os.path.normcase(os.path.normpath(path))
    closed = False

    try:
        shell = win32com.client.Dispatch("Shell.Application")

        for window in shell.Windows():
            try:
                current_url = window.LocationURL

                if not current_url:
                    continue

                if not current_url.lower().startswith("file:///"):
                    continue

                current_path = urllib.parse.unquote(
                    current_url[8:]
                ).replace("/", "\\")

                current_path = os.path.normcase(
                    os.path.normpath(current_path)
                )

                if current_path == target:
                    window.Quit()
                    closed = True

            except Exception:
                continue

    except Exception as error:
        print("Folder close error:", error)

    speak(
        f"Closing {name}."
        if closed
        else f"{name.capitalize()} window is not open."
    )

    return True


# =========================================================
# APPLICATION CONTROL
# =========================================================

def open_application(command):
    command = normalize_command(command)

    applications = {
        "notepad": [
            "open notepad",
            "notepad open",
            "notepad kholo",
            "notepad khol do",
            "notepad open karo",
            "notepad chalao"
        ],
        "calculator": [
            "open calculator",
            "calculator open",
            "calculator kholo",
            "calculator khol do",
            "calculator open karo",
            "calculator chalao"
        ],
        "chrome": [
            "open chrome",
            "chrome open",
            "chrome kholna",
            "chrome kholo",
            "chrome khol do",
            "chrome open karo",
            "chrome chalao",
            "open browser",
            "browser kholo",
            "browser open karo"
        ],
        "vs_code": [
            "open vs code", "open vscode", "open visual studio", "launch vs code", "launch vscode",
            "start vs code", "start vscode", "vs code open", "vscode kholo", "vscode khol do",
            "vs code kholo jarvis", "vs code chala do", "visual studio code open karo",
            "open vs", "open vs code editor", "launch visual studio code", "start visual studio code",
            "code editor kholo", "code kholo", "vs kholo",
            "vs khol do",
            "vs open karo",
            "vs chalao",
            "vs code kholo",
            "vs code khol do",
            "vs code open karo",
            "vs code chalao",
            "visual studio code kholo",
            "visual studio code khol do",
            "visual studio kholo",
            "open visual studio code"
        ],
        "android_studio": [
            "open android studio",
            "android studio kholo",
            "android studio khol do",
            "android studio open karo"
        ],
        "whatsapp": [
            "open whatsapp",
            "whatsapp kholo",
            "whatsapp khol do",
            "whatsapp open karo"
        ],
        "google": [
            "open google",
            "google kholo",
            "google khol do",
            "google open karo"
        ],
        "youtube": [
            "open youtube",
            "youtube kholo",
            "youtube khol do",
            "youtube open karo",
            "youtube chalao",
            "youtube chala do"
        ],
        "settings": [
            "open settings",
            "settings kholo",
            "settings khol do",
            "settings open karo"
        ],
        "explorer": [
            "open file explorer", "file explorer", "file explorer kholo", "file explorer khol do",
            "file explorer open karo", "explorer kholo", "explorer khol do", "open explorer",
            "launch file explorer", "start file explorer", "folders kholo", "my computer kholo",
            "this pc kholo", "open this pc", "open folders", "folder kholo", "folder khol do",
            "file explorer open", "explorer open karo", "my pc kholo", "computer folders kholo"
        ],
        "paint": [
            "open paint",
            "paint kholo",
            "paint khol do",
            "paint open karo"
        ],
        "task_manager": [
            "open task manager",
            "task manager kholo",
            "task manager open karo"
        ]
    }

    selected = None

    for app, phrases in applications.items():
        if matches_command(command, phrases):
            selected = app
            break

    if not selected:
        return False

    try:
        if selected == "notepad":
            subprocess.Popen(["notepad.exe"])

        elif selected == "calculator":
            subprocess.Popen(["calc.exe"])

        elif selected == "chrome":
            subprocess.Popen(
                ["cmd", "/c", "start", "", "chrome"]
            )

        elif selected == "vs_code":
            # Avoid shell parsing and support installations where `code` is not on PATH.
            candidates = [
                shutil.which("code"),
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\Code.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code Insiders\Code - Insiders.exe"),
            ]
            app_path = next((x for x in candidates if x and os.path.isfile(x)), None)
            if app_path:
                subprocess.Popen([app_path], close_fds=True)
            else:
                # Ask Windows shell to resolve the registered app alias, but verify it
                # first so the user gets a useful message rather than a false success.
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Code.exe") as key:
                        registered, _ = winreg.QueryValueEx(key, "")
                    if registered and os.path.isfile(registered):
                        subprocess.Popen([registered], close_fds=True)
                    else:
                        speak("I could not find Visual Studio Code. Please install it or check its installation path.")
                        return True
                except Exception:
                    speak("I could not find Visual Studio Code. Please install it or check its installation path.")
                    return True

        elif selected == "android_studio":
            candidates = [
                r"C:\Program Files\Android\Android Studio\bin\studio64.exe",
                r"C:\Program Files\Android\Android Studio\bin\studio.exe"
            ]

            app_path = next(
                (path for path in candidates if os.path.isfile(path)),
                None
            )

            if not app_path:
                speak("Android Studio was not found.")
                return True

            subprocess.Popen([app_path])

        elif selected == "whatsapp":
            webbrowser.open("https://web.whatsapp.com")

        elif selected == "google":
            webbrowser.open("https://www.google.com")

        elif selected == "youtube":
            webbrowser.open("https://www.youtube.com")

        elif selected == "settings":
            subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:"])

        elif selected == "explorer":
            # Use the absolute Windows system path so current working directory cannot interfere.
            explorer_exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
            if os.path.isfile(explorer_exe):
                subprocess.Popen([explorer_exe], close_fds=True)
            else:
                subprocess.Popen(["cmd.exe", "/c", "start", "", "explorer.exe"], close_fds=True)

        elif selected == "paint":
            subprocess.Popen(["mspaint.exe"])

        elif selected == "task_manager":
            subprocess.Popen(["taskmgr.exe"])

        friendly_names = {"vs_code": "Visual Studio Code", "explorer": "File Explorer", "task_manager": "Task Manager", "android_studio": "Android Studio"}
        speak(f"Opening {friendly_names.get(selected, selected.replace('_', ' '))}.")
        print(f"JARVIS: Launched {friendly_names.get(selected, selected.replace('_', ' '))}.")
        return True

    except Exception as error:
        print("Application error:", error)
        speak("I could not open that application.")
        return True


def close_process(names):
    found = False

    for name in names:
        try:
            result = subprocess.run(
                [
                    "taskkill",
                    "/IM",
                    name,
                    "/F"
                ],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                found = True

        except Exception as error:
            print("Process close error:", error)

    return found


def close_all_file_explorer_windows():
    """Close Explorer folder windows without terminating the Windows desktop shell."""
    closed = 0
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for window in list(shell.Windows()):
            try:
                location_url = str(window.LocationURL or "")
                if location_url.lower().startswith("file:///"):
                    window.Quit()
                    closed += 1
            except Exception:
                continue
    except Exception as error:
        print("File Explorer close error:", error)
    return closed > 0


def close_application(command):
    command = normalize_command(command)

    if matches_command(command, [
        "close file explorer", "file explorer band karo", "file explorer band kar do",
        "file explorer close karo", "explorer band karo", "explorer band kar do",
        "close explorer", "folders band karo", "folder windows band karo",
        "this pc band karo", "my computer band karo"
    ]):
        closed = close_all_file_explorer_windows()
        speak("File Explorer windows band kar diye." if closed else "Koi File Explorer folder window open nahi hai.")
        return True

    # Folder-specific close commands are handled here too, so they work even
    # when the command is phrased as "close Downloads folder".
    folder_aliases = {
        "downloads": ["close downloads", "close downloads folder", "downloads band karo", "downloads band kar do", "downloads folder band karo", "downloads folder close karo"],
        "pictures": ["close pictures", "close pictures folder", "pictures band karo", "pictures band kar do", "pictures folder band karo", "pictures folder close karo", "close photos folder", "photos band karo"],
        "documents": ["close documents", "close documents folder", "documents band karo", "documents band kar do", "documents folder band karo", "documents folder close karo"],
        "desktop": ["close desktop folder", "desktop folder band karo", "desktop folder band kar do"],
        "videos": ["close videos", "close videos folder", "videos band karo", "videos folder band karo"],
        "music": ["close music", "close music folder", "music band karo", "music folder band karo"],
    }
    for folder_name, phrases in folder_aliases.items():
        if matches_command(command, phrases):
            close_folder_window(folder_name)
            return True

    apps = {
        "notepad": [
            "close notepad", "notepad band karo", "notepad band kar do", "notepad close karo"
        ],
        "calculator": [
            "close calculator",
            "calculator band karo",
            "calculator band kar do"
        ],
        "chrome": [
            "close chrome", "chrome band karo", "chrome band kar do", "close browser",
            "browser band karo", "browser band kar do", "chrome close karo", "google chrome band karo"
        ],
        "vs_code": [
            "close vs code",
            "close vs",
            "vs band karo",
            "vs band kar do",
            "vs code band karo",
            "vs code band kar do",
            "visual studio code band karo",
            "close visual studio code"
        ],
        "android_studio": [
            "close android studio",
            "android studio band karo"
        ],
        "paint": ["close paint", "paint band karo", "paint band kar do", "paint close karo"],
        "task_manager": [
            "close task manager",
            "task manager band karo"
        ]
    }

    processes = {
        "notepad": ["notepad.exe"],
        "calculator": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
        "chrome": ["chrome.exe"],
        "vs_code": ["Code.exe"],
        "android_studio": ["studio64.exe", "studio.exe"],
        "paint": ["mspaint.exe"],
        "task_manager": ["Taskmgr.exe"]
    }

    for app, phrases in apps.items():
        if matches_command(command, phrases):
            closed = close_process(processes[app])

            speak(
                f"Closed {app.replace('_', ' ')}."
                if closed
                else f"{app.replace('_', ' ').capitalize()} was not running."
            )

            return True

    return False


# =========================================================
# SYSTEM INFORMATION
# =========================================================

def battery_status():
    battery = psutil.sensors_battery()

    if battery is None:
        speak("Battery information is unavailable.")
        return

    charger = (
        "charger connected"
        if battery.power_plugged
        else "charger not connected"
    )

    speak(f"Battery is {round(battery.percent)} percent. {charger}.")


def battery_remaining_time():
    """Report estimated battery time remaining when Windows exposes an estimate."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            speak("Battery ki remaining time available nahi hai.")
            return
        if battery.power_plugged:
            speak(f"Charger connected hai aur battery {round(battery.percent)} percent par hai. Charging ke dauran remaining time estimate available nahi hota.")
            return
        seconds = int(battery.secsleft)
        if seconds < 0 or seconds == getattr(psutil, "POWER_TIME_UNLIMITED", -2):
            speak(f"Battery {round(battery.percent)} percent hai, lekin Windows remaining time ka reliable estimate nahi de raha.")
            return
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        speak(f"Battery {round(battery.percent)} percent hai. Estimated remaining time {hours} ghantay aur {minutes} minute hai. Yeh estimate workload ke saath change ho sakta hai.")
    except Exception as error:
        print("Battery remaining time error:", error)
        speak("Battery remaining time check nahi ho saka.")


def ram_status():
    memory = psutil.virtual_memory()

    total = round(memory.total / 1024**3, 1)
    used = round(memory.used / 1024**3, 1)
    available = round(memory.available / 1024**3, 1)

    speak(
        f"You have {total} GB RAM. "
        f"{used} GB is used and {available} GB is available."
    )


def cpu_status():
    usage = psutil.cpu_percent(interval=1)
    speak(f"CPU usage is {usage} percent.")


def storage_status():
    disk = psutil.disk_usage(os.path.abspath(os.sep))

    total = round(disk.total / 1024**3, 1)
    used = round(disk.used / 1024**3, 1)
    free = round(disk.free / 1024**3, 1)

    speak(
        f"Drive capacity is {total} GB. "
        f"{used} GB is used and {free} GB is free."
    )


def disk_space_overview():
    """Summarize free space across mounted local drives without changing files."""
    try:
        drives = []
        for part in psutil.disk_partitions(all=False):
            if not part.mountpoint:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                free_gb = usage.free / (1024 ** 3)
                total_gb = usage.total / (1024 ** 3)
                percent_used = usage.percent
                drives.append((part.device or part.mountpoint, free_gb, total_gb, percent_used))
            except (PermissionError, OSError):
                continue
        if not drives:
            speak("Storage drives ki information available nahi hai.")
            return
        parts = [f"{drive}: {free:.1f} GB free out of {total:.1f} GB, {used:.0f} percent used" for drive, free, total, used in drives[:4]]
        speak("Storage overview. " + ". ".join(parts) + ".")
    except Exception as error:
        print("Disk space overview error:", error)
        speak("Storage overview check nahi ho saka.")


def recent_downloads():
    """Speak the names of up to five newest files in the user's Downloads folder."""
    try:
        folder = Path.home() / "Downloads"
        if not folder.is_dir():
            speak("Downloads folder nahi mil raha.")
            return
        files = [item for item in folder.iterdir() if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        if not files:
            speak("Downloads folder mein abhi koi file nahi hai.")
            return
        names = [item.name for item in files[:5]]
        speak("Recent downloads hain: " + "; ".join(names) + ".")
    except Exception as error:
        print("Recent downloads error:", error)
        speak("Recent downloads ki list nahi mil saki.")


def internet_status():
    try:
        requests.get("https://www.google.com", timeout=3)
        speak("Your internet connection is working.")
    except requests.RequestException:
        speak("Your internet connection is unavailable.")


def ip_address():
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        speak(f"Your local IP address is {ip}.")
    except Exception:
        speak("I could not find your IP address.")


def system_information():
    ram = round(psutil.virtual_memory().total / 1024**3, 1)

    speak(
        f"Operating system is {platform.system()}. "
        f"Processor architecture is {platform.machine()}. "
        f"You have {ram} GB RAM and {psutil.cpu_count()} logical CPU cores."
    )


def system_uptime():
    elapsed = int(time.time() - psutil.boot_time())

    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60

    speak(f"Your computer has been running for {hours} hours and {minutes} minutes.")


def get_pictures_folder():
    """Resolve the real Windows Pictures location (including OneDrive redirection)."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "My Pictures")
            folder = os.path.expandvars(value)
            if folder:
                return os.path.abspath(folder)
    except (OSError, ImportError) as error:
        print("Pictures-folder registry lookup unavailable:", error)

    home = os.path.expanduser("~")
    for folder in (
        os.path.join(home, "Pictures"),
        os.path.join(home, "OneDrive", "Pictures"),
    ):
        if os.path.isdir(folder):
            return folder
    return os.path.join(home, "Pictures")


def copy_image_to_clipboard(image):
    """Copy a PIL image to the Windows clipboard as a bitmap for Ctrl+V."""
    import io
    import win32clipboard
    from PIL import Image

    # Windows CF_DIB expects BMP pixel data without the 14-byte BMP file header.
    bitmap = image.convert("RGB")
    buffer = io.BytesIO()
    bitmap.save(buffer, format="BMP")
    dib_data = buffer.getvalue()[14:]
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
    finally:
        win32clipboard.CloseClipboard()


def take_screenshot():
    """Save screenshot to Pictures and copy it to clipboard for immediate pasting."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(get_pictures_folder(), "JARVIS Screenshots")
    path = os.path.join(folder, f"JARVIS_screenshot_{timestamp}.png")

    try:
        from PIL import Image
        image = pyautogui.screenshot()
        os.makedirs(folder, exist_ok=True)
        image.save(path, format="PNG")
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise OSError("Screenshot file was not created correctly")
        with Image.open(path) as saved_image:
            saved_image.verify()
    except Exception as error:
        print("Screenshot capture/save error:", error)
        speak("I could not save the screenshot. Please check Pictures folder permissions and disk space.")
        return

    clipboard_ok = False
    try:
        copy_image_to_clipboard(image)
        clipboard_ok = True
    except Exception as error:
        print("Could not copy screenshot to clipboard:", error)

    print(f"Screenshot saved successfully: {path}")
    try:
        os.startfile(folder)
    except OSError as error:
        print("Could not open screenshot folder:", error)

    if clipboard_ok:
        speak("Screenshot saved in Pictures, and copied to clipboard. You can paste it with Control V.")
    else:
        speak(f"Screenshot saved in Pictures at {path}, but clipboard copy failed.")


# =========================================================
# VOLUME
# =========================================================

def volume_up():
    pyautogui.press("volumeup", presses=3)
    speak("Volume increased.")


def volume_down():
    pyautogui.press("volumedown", presses=3)
    speak("Volume decreased.")


def volume_mute():
    pyautogui.press("volumemute")
    speak("Mute button toggled.")


# =========================================================
# SAFE CALCULATOR
# =========================================================

ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg
}


def safe_calculate(expression):
    tree = ast.parse(expression, mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            if type(node.value) in (int, float):
                if abs(node.value) > 10**12:
                    raise ValueError("Number too large")
                return node.value

            raise ValueError("Invalid number")

        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)

            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("Exponent too large")

            operation = ALLOWED_OPERATORS.get(type(node.op))

            if operation is None:
                raise ValueError("Invalid operator")

            result = operation(left, right)

            if isinstance(result, (int, float)) and abs(result) > 10**15:
                raise ValueError("Result too large")

            return result

        if isinstance(node, ast.UnaryOp):
            operation = ALLOWED_OPERATORS.get(type(node.op))

            if operation is None:
                raise ValueError("Invalid unary operator")

            return operation(evaluate(node.operand))

        raise ValueError("Invalid expression")

    return evaluate(tree.body)


def calculate_command(expression):
    replacements = {
        "plus": "+",
        "minus": "-",
        "multiplied by": "*",
        "multiply by": "*",
        "times": "*",
        "divided by": "/",
        "divide by": "/",
        "over": "/"
    }

    expression = expression.lower()

    for word, symbol in replacements.items():
        expression = expression.replace(word, symbol)

    expression = re.sub(r"[^0-9+\-*/().% ]", "", expression)

    try:
        result = safe_calculate(expression)
        speak(f"The answer is {result}.")
    except Exception as error:
        print("Calculation error:", error)
        speak("I could not calculate that expression.")


# =========================================================
# WHATSAPP
# =========================================================

def send_hello_to_mehar():
    if not WHATSAPP_TARGET_NUMBER:
        speak("Set the JARVIS_WHATSAPP_NUMBER environment variable before using this feature.")
        return
    try:
        speak("Opening WhatsApp to send Hello.")

        pywhatkit.sendwhatmsg_instantly(
            WHATSAPP_TARGET_NUMBER,
            "Hello!",
            wait_time=15,
            tab_close=False
        )

        speak("The WhatsApp send action was attempted. Please verify the message.")

    except Exception as error:
        print("WhatsApp error:", error)
        speak("I could not send the WhatsApp message.")


# =========================================================
# SEARCH
# =========================================================

def google_search(query):
    url = (
        "https://www.google.com/search?q="
        + urllib.parse.quote(query)
    )

    webbrowser.open(url)
    speak("Searching Google.")


def youtube_search(query):
    url = (
        "https://www.youtube.com/results?search_query="
        + urllib.parse.quote(query)
    )

    webbrowser.open(url)
    speak("Searching YouTube.")



# =========================================================
# FILE ORGANIZER + RECYCLE BIN (SAFE, CONFIRMATION-BASED)
# =========================================================

RECYCLE_BIN_CONFIRMATION_PENDING = False

def organize_folder(folder_name):
    """Move files into type-based subfolders; leave folders untouched."""
    folder_name = (folder_name or "downloads").lower().strip()
    allowed = {"downloads", "desktop", "documents", "pictures", "videos", "music"}
    if folder_name not in allowed:
        speak("I can organize Downloads, Desktop, Documents, Pictures, Videos, or Music.")
        return

    target = get_folder_path(folder_name)
    if not target or not os.path.isdir(target):
        speak(f"I could not find the {folder_name} folder.")
        return

    categories = {
        "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"},
        "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"},
        "Audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
        "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
        "Installers": {".exe", ".msi", ".msix"},
        "Code": {".py", ".js", ".html", ".css", ".json", ".java", ".cpp", ".c", ".h", ".php"},
    }
    moved = 0
    skipped = 0
    try:
        for item in os.scandir(target):
            if not item.is_file():
                continue
            ext = Path(item.name).suffix.lower()
            category = next((name for name, exts in categories.items() if ext in exts), "Other")
            destination_dir = os.path.join(target, category)
            os.makedirs(destination_dir, exist_ok=True)
            destination = os.path.join(destination_dir, item.name)
            if os.path.exists(destination):
                skipped += 1
                continue
            try:
                os.replace(item.path, destination)
                moved += 1
            except OSError as error:
                print(f"Could not move {item.name}: {error}")
                skipped += 1
        speak(f"Organization finished. Moved {moved} files into categories. Skipped {skipped} files. You can move them back from those folders if needed.")
    except Exception as error:
        print("File organizer error:", error)
        speak("I could not organize that folder. Check its permissions and try again.")

def request_empty_recycle_bin():
    global RECYCLE_BIN_CONFIRMATION_PENDING
    RECYCLE_BIN_CONFIRMATION_PENDING = True
    speak("This permanently removes items currently in the Recycle Bin. Say confirm empty recycle bin to continue, or cancel to stop.")

def empty_recycle_bin():
    """Empty Windows Recycle Bin only after a separate confirmation command."""
    global RECYCLE_BIN_CONFIRMATION_PENDING
    RECYCLE_BIN_CONFIRMATION_PENDING = False
    try:
        import ctypes
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x00000001 | 0x00000002 | 0x00000004)
        if result == 0:
            speak("Recycle Bin emptied.")
        else:
            speak("Windows could not empty the Recycle Bin.")
    except Exception as error:
        print("Recycle Bin error:", error)
        speak("I could not empty the Recycle Bin on this computer.")

def open_website_from_command(command):
    """Open a known website, or a user-supplied HTTP(S) URL after validation."""
    command = normalize_command(command)
    known_sites = {
        "facebook": "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "chatgpt": "https://chatgpt.com",
        "linkedin": "https://www.linkedin.com",
        "netflix": "https://www.netflix.com",
        "whatsapp": "https://web.whatsapp.com",
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
    }
    if command.startswith("open website "):
        raw = command[len("open website "):].strip()
        if not raw:
            speak("Please say a website name or address.")
            return True
        url = raw if raw.startswith(("https://", "http://")) else "https://" + raw
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or " " in parsed.netloc:
            speak("That website address does not look valid.")
            return True
        webbrowser.open(url)
        speak("Opening website.")
        return True
    if command.startswith("open "):
        name = command[5:].strip()
        if name in known_sites:
            webbrowser.open(known_sites[name])
            speak(f"Opening {name}.")
            return True
    return False


# =========================================================
# PERSONAL NOTES AND TIMERS
# =========================================================

JARVIS_DATA_DIR = Path.home() / "Documents" / "JARVIS"
JARVIS_NOTES_FILE = JARVIS_DATA_DIR / "notes.txt"

def save_note(note_text):
    note_text = note_text.strip(" .")
    if not note_text:
        speak("Note mein kya likhna hai? Please say the note after take a note.")
        return
    try:
        JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
        with JARVIS_NOTES_FILE.open("a", encoding="utf-8") as file:
            file.write(f"[{stamp}] {note_text}\n")
        speak("Note save kar diya hai. Your note is saved in Documents, JARVIS, notes.txt.")
    except OSError as error:
        print("Note save error:", error)
        speak("Sorry, note save nahi ho saka.")

def open_notes():
    try:
        JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        JARVIS_NOTES_FILE.touch(exist_ok=True)
        os.startfile(str(JARVIS_NOTES_FILE))
        speak("Opening your saved notes.")
    except OSError as error:
        print("Open notes error:", error)
        speak("Sorry, notes open nahi ho sakay.")

# Persistent personal task list. Tasks are only added when the user explicitly asks.
JARVIS_TASKS_FILE = JARVIS_DATA_DIR / "tasks.json"


def _load_tasks():
    try:
        JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not JARVIS_TASKS_FILE.exists():
            return []
        data = json.loads(JARVIS_TASKS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as error:
        print("Task list read error:", error)
        return []


def _save_tasks(tasks):
    JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = JARVIS_TASKS_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(JARVIS_TASKS_FILE)


def add_task(task_text, due_date=None):
    task_text = (task_text or "").strip(" .")
    if not task_text:
        speak("Konsa kaam yaad rakhna hai? Misal ke taur par, add task kal practical complete karna.")
        return
    tasks = _load_tasks()
    tasks.append({"id": uuid.uuid4().hex[:8], "text": task_text, "created": datetime.datetime.now().isoformat(), "due_date": due_date, "done": False})
    try:
        _save_tasks(tasks)
        due_note = f" Deadline {due_date} hai." if due_date else ""
        speak(f"Theek hai, maine task save kar liya: {task_text}.{due_note} Jab chaho, mere pending tasks pooch lena.")
    except OSError as error:
        print("Task save error:", error)
        speak("Maaf karna, task save nahi ho saka. Please check Documents JARVIS folder permissions.")


def list_tasks():
    tasks = [task for task in _load_tasks() if not task.get("done")]
    if not tasks:
        speak("Abhi koi pending task saved nahi hai. Jo kaam yaad rakhwana ho, bolo add task aur phir kaam bata do.")
        return
    speak("Tumhare pending tasks yeh hain: " + ". ".join(f"Number {i}: {task.get('text', 'Untitled task')}" + (f", due {task.get('due_date')}" if task.get("due_date") else "") for i, task in enumerate(tasks, 1)))


def complete_task(task_number):
    tasks = _load_tasks()
    pending = [task for task in tasks if not task.get("done")]
    if task_number < 1 or task_number > len(pending):
        speak("Yeh task number nahi mila. Pehle bolo, list my tasks.")
        return
    target = pending[task_number - 1]
    for task in tasks:
        if task.get("id") == target.get("id"):
            task["done"] = True
            task["completed"] = datetime.datetime.now().isoformat()
            break
    try:
        _save_tasks(tasks)
        speak(f"Shabash, task complete mark kar diya: {target.get('text', 'task')}.")
    except OSError as error:
        print("Task completion error:", error)
        speak("Task status save nahi ho saka.")


def daily_briefing():
    now = datetime.datetime.now()
    pending = [task for task in _load_tasks() if not task.get("done")]
    greeting = "Subah bakhair yaar" if now.hour < 12 else ("Assalam-o-alaikum yaar, dopahar bakhair" if now.hour < 17 else "Assalam-o-alaikum yaar, shaam bakhair")
    if pending:
        task_summary = ". ".join(f"{i}: {task.get('text', 'Untitled task')}" + (f", deadline {task.get('due_date')}" if task.get("due_date") else "") for i, task in enumerate(pending[:5], 1))
        speak(f"{greeting}! Aaj {now.strftime('%A, %d %B')} hai. Tumhare {len(pending)} kaam baqi hain. Pehle yeh dekh lo: {task_summary}. Chalo, aik aik karke nipta lete hain. Tum bolo to kisi kaam ko complete bhi mark kar dunga.")
    else:
        speak(f"{greeting}! Aaj {now.strftime('%A, %d %B')} hai. Abhi tumhara koi kaam list mein baqi nahi. Koi kaam yaad rakhwana ho to bas bolo, yaar.")


def set_timer(seconds, label="Timer"):
    """Save timers/reminders to the same persistent store used by the desktop UI.

    This survives closing JARVIS and Windows restarts. If the due time passes
    while the computer is off, the desktop app announces it the next time it runs.
    """
    seconds = max(1, min(int(seconds), 365 * 24 * 60 * 60))
    due = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    reminder_file = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "reminders.json"
    try:
        reminder_file.parent.mkdir(parents=True, exist_ok=True)
        reminders = []
        if reminder_file.exists():
            try:
                loaded = json.loads(reminder_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    reminders = [item for item in loaded if isinstance(item, dict) and item.get("text") and item.get("due")]
            except (json.JSONDecodeError, OSError):
                # Preserve the existing file rather than overwriting unreadable data.
                speak("I could not read the saved reminders file, so this reminder was not added.")
                return
        reminders.append({
            "id": uuid.uuid4().hex,
            "text": str(label).strip() or "Timer",
            "due": due.isoformat(),
            "created": datetime.datetime.now().isoformat(),
            "source": "voice-timer",
            "delivered": False,
        })
        reminders.sort(key=lambda item: item.get("due", ""))
        temp_file = reminder_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(reminder_file)
    except OSError as error:
        print("Reminder save error:", error)
        speak("Sorry, I could not save this reminder.")
        return
    if seconds >= 60 and seconds % 60 == 0:
        speak(f"The reminder is saved for {seconds // 60} minutes.")
    else:
        speak(f"The reminder is saved for {seconds} seconds.")


# Natural-language reminders and a lightweight background reminder watcher.
# Reminders are stored in %APPDATA%/JARVIS/reminders.json and survive restarts.
JARVIS_REMINDER_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "reminders.json"


def _parse_reminder_command(raw_text):
    """Return (seconds, label) for clear spoken reminders, otherwise None.

    Examples: 'remind me in 2 days to do sir's work',
    'remind me in 3 hours to call sir', 'mujhe 20 minutes baad yaad dilana assignment'.
    We deliberately don't guess whether a bare '2' means 2 pm or 2 days.
    """
    text = (raw_text or "").strip().lower()
    patterns = [
        r"remind me in (\d+) (seconds?|minutes?|hours?|days?|weeks?) to (.+)",
        r"remind me after (\d+) (seconds?|minutes?|hours?|days?|weeks?) to (.+)",
        r"mujhe (\d+) (seconds?|minutes?|hours?|days?|weeks?|second|minute|hour|day|week) baad yaad (?:dilana|karwana) (.+)",
        r"(\d+) (seconds?|minutes?|hours?|days?|weeks?) baad mujhe yaad (?:dilana|karwana) (.+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        amount, unit, label = m.groups()
        unit = unit.rstrip("s")
        multipliers = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        return int(amount) * multipliers[unit], label.strip(" .")

    # Absolute-time reminders: "remind me at 9 pm to study" and
    # "remind me tomorrow at 7 pm to revise networking".
    absolute = re.search(
        r"remind me (?:(tomorrow|kal) )?(?:at )?(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?\s+to\s+(.+)",
        text,
    )
    if absolute:
        day_word, hour_text, minute_text, meridiem, label = absolute.groups()
        hour = int(hour_text)
        minute = int(minute_text or 0)
        if meridiem in ("am", "pm"):
            if hour < 1 or hour > 12 or minute > 59:
                return None
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif hour > 23 or minute > 59:
            return None
        due = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day_word in ("tomorrow", "kal") or due <= datetime.datetime.now():
            due += datetime.timedelta(days=1)
        return max(1, int((due - datetime.datetime.now()).total_seconds())), label.strip(" .")

    # Roman Urdu duration phrasing: "2 din baad sir ko call yaad dilana".
    roman = re.search(r"(?:mujhe )?(\d+)\s*(din|days?|ghantay|ghante|hours?|minutes?|mins?|haftay|hafte|weeks?)\s*baad\s+(.+?)\s+yaad\s*(?:dilana|karwana)", text)
    if roman:
        amount, unit, label = roman.groups()
        unit = unit.rstrip("s")
        multipliers = {"din": 86400, "day": 86400, "ghantay": 3600, "ghante": 3600, "hour": 3600,
                       "minute": 60, "min": 60, "haftay": 604800, "hafte": 604800, "week": 604800}
        return int(amount) * multipliers.get(unit, 60), label.strip(" .")
    return None


def _battery_low_watcher():
    """Give one spoken low-battery warning per low-battery episode; no noisy repeats."""
    warned = False
    while True:
        try:
            battery = psutil.sensors_battery()
            if battery is None or battery.power_plugged or battery.percent > 25:
                warned = False
            elif battery.percent <= 20 and not warned:
                warned = True
                speak(f"Battery low hai, sirf {round(battery.percent)} percent baqi hai. Charger laga lo.")
        except Exception as error:
            print("Battery alert watcher error:", error)
        time.sleep(30)


def _performance_watcher():
    """Warn only after repeated high CPU/RAM readings to avoid false alarms."""
    high_count = 0
    alerted = False
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory().percent
            if cpu >= 90 or memory >= 90:
                high_count += 1
            else:
                high_count = 0
                alerted = False
            if high_count >= 3 and not alerted:
                alerted = True
                causes = []
                if cpu >= 90:
                    causes.append(f"CPU {round(cpu)} percent")
                if memory >= 90:
                    causes.append(f"RAM {round(memory)} percent")
                speak("PC performance alert. System resources bohat zyada use ho rahe hain: " + " aur ".join(causes) + ". Task Manager check kar lo.")
        except Exception as error:
            print("Performance watcher error:", error)
        time.sleep(25)


def _reminder_watcher():
    """Speak due reminders while main.py is running; mark each one delivered once."""
    while True:
        try:
            if JARVIS_REMINDER_FILE.exists():
                raw = JARVIS_REMINDER_FILE.read_text(encoding="utf-8")
                reminders = json.loads(raw)
                if isinstance(reminders, list):
                    now = datetime.datetime.now()
                    changed = False
                    due_items = []
                    for item in reminders:
                        if not isinstance(item, dict) or item.get("delivered"):
                            continue
                        try:
                            due = datetime.datetime.fromisoformat(item["due"])
                        except (KeyError, ValueError, TypeError):
                            continue
                        if due <= now:
                            item["delivered"] = True
                            changed = True
                            due_items.append(str(item.get("text", "Reminder")))
                    if changed:
                        temp = JARVIS_REMINDER_FILE.with_suffix(".watcher.tmp")
                        temp.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
                        temp.replace(JARVIS_REMINDER_FILE)
                    for label in due_items:
                        speak("Reminder! " + label)
        except Exception as error:
            print("Reminder watcher error:", error)
        time.sleep(8)


def _natural_task_request(raw_text):
    """Recognize common conversational task requests and an explicit 'this week' deadline."""
    text = (raw_text or "").strip()
    low = text.lower().strip(" .")
    starters = [
        "this week i have to ", "this week i need to ", "i have to ", "i need to ", "remember that i need to ",
        "i have to finish ", "i need to finish ", "i must finish ", "i should finish ",
        "isi hafte mujhe ", "is hafte mujhe ", "iss hafte mujhe ", "isi hafte ", "is hafte ", "iss hafte ",
        "mujhe yaad rakhna hai ke ", "yaad rakhna ke ", "yaad rakhna ki ", "mujhe karna hai ke ",
        "mujhe karna hai ", "mera kaam hai ke ", "sir ka kaam hai ke ",
    ]
    # Only create a task from an explicit task/remembrance intent, not an ordinary statement.
    explicit_intent = any(x in low for x in ("remind me", "remember", "yaad", "task", "is hafte", "this week", "i need to", "i have to"))
    if not explicit_intent:
        return None
    due_date = None
    if "this week" in low or "is hafte" in low:
        today = datetime.date.today()
        # Friday is the conventional end of a study/work week in this context.
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        due_date = (today + datetime.timedelta(days=days_until_friday)).isoformat()
    task = text
    for starter in starters:
        idx = low.find(starter)
        if idx >= 0:
            task = text[idx + len(starter):].strip(" .")
            break
    task = re.sub(r"\b(remind me|yaad karwa dena|yaad dilana|remind me to remind me)\b.*$", "", task, flags=re.I).strip(" .")
    task = re.sub(r"\b(isi hafte|is hafte|iss hafte|this week)\b", "", task, flags=re.I).strip(" ,.")
    task = re.sub(r"\b(aur mujhe \d+ (?:din|days?|hours?|ghante|ghantay) baad yaad karwa dena)\b.*$", "", task, flags=re.I).strip(" ,.")
    if len(task) < 3:
        return None
    return task, due_date


# =========================================================
# PROCESS COMMAND
# =========================================================

def copy_text_to_clipboard(text):
    """Copy plain text to the Windows clipboard."""
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def read_clipboard_text():
    """Read text currently on the Windows clipboard without changing it."""
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return ""
    finally:
        win32clipboard.CloseClipboard()


def create_desktop_folder(folder_name):
    """Create one folder on the user's Desktop; never overwrite existing files."""
    folder_name = (folder_name or "").strip().strip('"\'')
    if not folder_name or folder_name in {".", ".."} or any(ch in folder_name for ch in '<>:/\\|?*'):
        speak("Please give the folder a simple valid name.")
        return
    desktop_candidates = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]
    desktop = next((path for path in desktop_candidates if path.exists()), desktop_candidates[0])
    target = desktop / folder_name
    try:
        target.mkdir(parents=False, exist_ok=False)
        speak(f"Created the folder {folder_name} on your Desktop.")
    except FileExistsError:
        speak(f"A file or folder named {folder_name} already exists on your Desktop.")
    except Exception as error:
        print("Create folder error:", error)
        speak("I could not create that folder on your Desktop.")


def type_text_into_active_window(text_to_type):
    """Paste user-spoken text into the currently focused app using clipboard."""
    text_to_type = (text_to_type or "").strip()
    if not text_to_type:
        speak("Please tell me the text you want me to type.")
        return
    try:
        copy_text_to_clipboard(text_to_type)
        time.sleep(0.25)
        pyautogui.hotkey("ctrl", "v")
        speak("Text typed into the active window.")
    except Exception as error:
        print("Type text error:", error)
        speak("I could not type the text. Click the target text box and try again.")


def open_windows_page(uri, spoken_name):
    try:
        subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
        speak(f"Opening {spoken_name} settings.")
    except Exception as error:
        print(f"Could not open {spoken_name} settings:", error)
        speak(f"I could not open {spoken_name} settings.")



def search_files(query, max_results=8):
    """Search common user folders by filename; does not open or modify files."""
    query = (query or "").strip().strip('"\'')
    if len(query) < 2:
        speak("Please say at least two letters of the file name.")
        return
    roots = [Path.home() / name for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    roots += [Path.home() / "OneDrive" / name for name in ("Desktop", "Documents", "Downloads", "Pictures")]
    matches = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        try:
            for current, dirs, files in os.walk(root, topdown=True):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "AppData"}]
                for name in files + dirs:
                    if query.casefold() in name.casefold():
                        full = os.path.join(current, name)
                        if full not in seen:
                            seen.add(full); matches.append(full)
                            if len(matches) >= max_results:
                                break
                if len(matches) >= max_results: break
        except (PermissionError, OSError):
            continue
        if len(matches) >= max_results: break
    if not matches:
        speak(f"I could not find a file or folder matching {query} in your common folders.")
        return
    print("\nJARVIS FILE SEARCH RESULTS:")
    for index, path in enumerate(matches, 1): print(f"{index}. {path}")
    speak(f"I found {len(matches)} matching items. I have listed their locations in the JARVIS console.")


def set_windows_startup(enabled=True):
    """Register or remove JARVIS desktop app in the current user's Run key."""
    if os.name != "nt":
        speak("Windows startup settings are only available on Windows."); return
    try:
        import winreg
        from pathlib import Path
        project_dir = Path(__file__).resolve().parent
        # Support both common project layouts:
        # JARVIS-AI/desktop_app.py and JARVIS-AI/desktop_app/desktop_app.py
        candidates = [project_dir / "desktop_app.py", project_dir / "desktop_app" / "desktop_app.py"]
        desktop_script = next((path for path in candidates if path.is_file()), None)
        if desktop_script is None:
            speak("I could not find desktop_app.py. Put it beside main.py or inside the desktop_app folder."); return
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(sys.executable))
        command = f'"{executable}" "{desktop_script}"'
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "JARVIS AI", 0, winreg.REG_SZ, command)
            else:
                try: winreg.DeleteValue(key, "JARVIS AI")
                except FileNotFoundError: pass
        speak("JARVIS will start automatically when you sign in to Windows." if enabled else "JARVIS automatic startup has been disabled.")
    except Exception as error:
        print("Startup setting error:", error)
        speak("I could not change the Windows startup setting.")

def save_work_and_shutdown():
    """Send Save to the active app, then schedule Windows shutdown with time to cancel."""
    try:
        speak("Saving the active work now. Please check any save dialog or unsaved changes prompt.")
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2)
        # A delay gives the user a chance to notice a save dialog and cancel shutdown.
        subprocess.Popen(["shutdown", "/s", "/t", "30"])
        speak("I sent the save shortcut. Windows will shut down in 30 seconds. Say cancel shutdown if anything still needs saving.")
    except Exception as error:
        print("Save and shutdown error:", error)
        speak("I could not complete the save and shutdown sequence. Please save your work manually.")


def wifi_status():
    """Speak the current Wi-Fi connection details without changing network settings."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=8,
            encoding="utf-8", errors="replace"
        )
        output = result.stdout or ""
        state = re.search(r"(?im)^\s*State\s*:\s*(.+)$", output)
        ssid = re.search(r"(?im)^\s*SSID\s*:\s*(.+)$", output)
        signal = re.search(r"(?im)^\s*Signal\s*:\s*(.+)$", output)
        if result.returncode == 0 and state:
            state_text = state.group(1).strip()
            if state_text.lower() == "connected":
                speak(f"Wi-Fi connected hai. Network {ssid.group(1).strip() if ssid else 'name unavailable'} hai. Signal {signal.group(1).strip() if signal else 'unavailable'}.")
            else:
                speak("Wi-Fi abhi connected nahi hai.")
        else:
            speak("Wi-Fi status read nahi ho saka. Windows Settings mein network check kar lo.")
    except Exception as error:
        print("Wi-Fi status error:", error)
        speak("Wi-Fi status check nahi ho saka.")


def _generate_battery_report():
    """Generate Windows battery report and return its path, or None."""
    report_dir = Path.home() / "Documents"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "JARVIS_Battery_Report.html"
    result = subprocess.run(
        ["powercfg", "/batteryreport", "/output", str(report_path)],
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace"
    )
    return report_path if result.returncode == 0 and report_path.exists() else None


def battery_health_percentage(open_report=False):
    """Estimate battery capacity health from Full Charge Capacity / Design Capacity."""
    try:
        report_path = _generate_battery_report()
        if not report_path:
            speak("Battery report generate nahi ho saki. Windows battery report support check karna hoga.")
            return
        html = report_path.read_text(encoding="utf-8", errors="ignore")

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
            def handle_data(self, data):
                cleaned = data.strip()
                if cleaned:
                    self.parts.append(cleaned)

        parser = _TextExtractor()
        parser.feed(html)
        report_text = " ".join(parser.parts)
        design_match = re.search(r"Design Capacity\s*([\d,]+)\s*mWh", report_text, re.I)
        full_match = re.search(r"Full Charge Capacity\s*([\d,]+)\s*mWh", report_text, re.I)
        if not design_match or not full_match:
            speak("Battery health percentage report se read nahi ho saki. Full battery report Documents folder mein save hai.")
            if open_report:
                webbrowser.open(report_path.resolve().as_uri())
            return
        design = int(design_match.group(1).replace(",", ""))
        full = int(full_match.group(1).replace(",", ""))
        if design <= 0:
            speak("Battery design capacity valid nahi hai, is liye health percentage calculate nahi kar saka.")
            return
        health = max(0, round((full / design) * 100))
        remaining = max(0, 100 - health)
        quality = "achhi condition mein hai" if health >= 85 else "ki capacity kam ho chuki hai" if health >= 65 else "kaafi worn ho chuki hai"
        speak(f"Tumhari battery health taqreeban {health} percent hai. Full charge capacity {full} mWh aur original design capacity {design} mWh hai. Battery {quality}. Yeh capacity-based estimate hai, Windows ki official diagnostic rating nahi.")
        if open_report:
            webbrowser.open(report_path.resolve().as_uri())
    except Exception as error:
        print("Battery health percentage error:", error)
        speak("Battery health percentage check nahi ho saka. Battery report manually check kar lo.")


def create_battery_health_report():
    """Generate and open Windows' built-in battery health report."""
    try:
        report_path = _generate_battery_report()
        if report_path:
            battery_health_percentage(open_report=False)
            webbrowser.open(report_path.resolve().as_uri())
            speak("Detailed battery report Documents folder mein save aur open kar di hai.")
        else:
            speak("Battery report generate nahi ho saki. Windows battery report create nahi kar saka.")
    except Exception as error:
        print("Battery report error:", error)
        speak("Battery health report create nahi ho saki.")


def system_health_summary():
    """Speak a compact overview of CPU, memory, disk and current battery charge."""
    try:
        cpu = psutil.cpu_percent(interval=0.6)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        battery = psutil.sensors_battery()
        message = (f"Quick system check. CPU usage {round(cpu)} percent. RAM usage {round(memory.percent)} percent, "
                   f"with {round(memory.available / 1024**3, 1)} GB available. "
                   f"Drive has {round(disk.free / 1024**3, 1)} GB free out of {round(disk.total / 1024**3, 1)} GB.")
        if battery is not None:
            message += f" Battery charge is {round(battery.percent)} percent and charger is {'connected' if battery.power_plugged else 'not connected'}."
        speak(message)
    except Exception as error:
        print("System health summary error:", error)
        speak("System health summary nahi mil saka.")



def internet_connection_check():
    """Check whether the internet is reachable and report approximate ping latency."""
    try:
        started = time.perf_counter()
        with socket.create_connection(("1.1.1.1", 443), timeout=3):
            latency_ms = round((time.perf_counter() - started) * 1000)
        speak(f"Internet connected hai. Cloudflare network tak response time taqreeban {latency_ms} milliseconds hai.")
    except Exception:
        speak("Internet connection verify nahi ho saka. Wi-Fi connected ho sakta hai, lekin internet unavailable ya blocked ho sakta hai.")


def pc_uptime_status():
    """Report how long Windows has been running since its last boot."""
    try:
        seconds = max(0, int(time.time() - psutil.boot_time()))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        pieces = []
        if days:
            pieces.append(f"{days} din")
        if hours:
            pieces.append(f"{hours} ghantay")
        if minutes or not pieces:
            pieces.append(f"{minutes} minute")
        speak("PC ko last start ya restart huay " + ", ".join(pieces) + " ho gaye hain.")
    except Exception as error:
        print("Uptime check error:", error)
        speak("PC ka uptime check nahi ho saka.")


def top_memory_apps():
    """List the three running processes using the most RAM; does not close anything."""
    try:
        processes = []
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                info = proc.info
                memory_info = info.get("memory_info")
                if memory_info:
                    processes.append((memory_info.rss, info.get("name") or "Unknown app"))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        processes.sort(reverse=True)
        if not processes:
            speak("Running apps ki memory information available nahi hai.")
            return
        top = processes[:3]
        details = "; ".join(f"{name}, {memory / (1024 ** 2):.0f} MB" for memory, name in top)
        speak("Sab se zyada RAM use karne wali apps hain: " + details + ". Main ne koi app close nahi ki.")
    except Exception as error:
        print("Top memory apps error:", error)
        speak("RAM use karne wali apps ki list nahi mil saki.")


# =========================================================
# PROFESSIONAL PERSONAL MEMORY + CHAT EXPORT
# Stored locally in %APPDATA%\JARVIS; never sent to Ollama automatically.
# =========================================================
JARVIS_DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS"
JARVIS_MEMORY_FILE = JARVIS_DATA_DIR / "personal_memory.json"
JARVIS_CHAT_EXPORT_DIR = JARVIS_DATA_DIR / "Chat Exports"

def _load_personal_memory():
    try:
        if JARVIS_MEMORY_FILE.exists():
            data = json.loads(JARVIS_MEMORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError) as error:
        print("JARVIS memory load error:", error)
    return []

def remember_personal_fact(text):
    text = (text or "").strip()
    if not text:
        speak("Yaar, batao kya yaad rakhna hai. Misal: remember that I prefer Roman Urdu.")
        return
    if len(text) > 500:
        speak("Ye baat kaafi lambi hai. Isay 500 characters se chhota kar do.")
        return
    # Avoid storing obvious credentials or highly sensitive identifiers.
    if re.search(r"(?i)(password|passcode|pin|otp|api key|secret key|credit card|cvv)", text):
        speak("Security ke liye main passwords, OTP ya payment details memory mein save nahi karta.")
        return
    try:
        JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        facts = _load_personal_memory()
        if text.casefold() not in [str(x).casefold() for x in facts]:
            facts.append(text)
        JARVIS_MEMORY_FILE.write_text(json.dumps(facts[-100:], ensure_ascii=False, indent=2), encoding="utf-8")
        speak("Theek hai yaar, ye baat tumhare computer par local memory mein save kar di hai.")
    except OSError as error:
        print("JARVIS memory save error:", error)
        speak("Memory save nahi ho saki. Folder permission check kar lo.")

def show_personal_memory():
    facts = _load_personal_memory()
    if not facts:
        speak("Abhi meri personal memory khaali hai. Kaho: remember that, phir baat bata do.")
        return
    speak("Mujhe tumhari ye baatein yaad hain: " + "; ".join(str(x) for x in facts[-12:]))

def clear_personal_memory():
    try:
        JARVIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        JARVIS_MEMORY_FILE.write_text("[]", encoding="utf-8")
        speak("Theek hai, personal memory clear kar di hai.")
    except OSError as error:
        print("JARVIS memory clear error:", error)
        speak("Memory clear nahi ho saki.")

def export_chat_history():
    try:
        with _JARVIS_CHAT_LOCK:
            history = list(_JARVIS_CHAT_HISTORY)
        if not history:
            speak("Abhi export karne ke liye conversation history nahi hai.")
            return
        JARVIS_CHAT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        target = JARVIS_CHAT_EXPORT_DIR / ("JARVIS_Chat_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
        lines = [f"{role}: {message}" for role, message in history]
        target.write_text("\n\n".join(lines), encoding="utf-8")
        speak("Conversation export ho gayi hai. File Chat Exports folder mein hai.")
        print("JARVIS chat exported:", target)
        try:
            os.startfile(str(target.parent))
        except OSError:
            pass
    except OSError as error:
        print("JARVIS chat export error:", error)
        speak("Chat export nahi ho saki.")


def process_command(raw_command):
    command = normalize_command(raw_command)

    if not command:
        return

    # PERSONAL MEMORY: only explicit remember commands write to disk.
    remember_prefixes = ("remember that ", "remember this ", "yaad rakhna ", "meri ye baat yaad rakhna ")
    for prefix in remember_prefixes:
        if command.startswith(prefix):
            remember_personal_fact(raw_command.strip()[len(prefix):])
            return
    if matches_command(command, ["what do you remember", "show my memory", "meri memory dikhao", "tumhein kya yaad hai"]):
        show_personal_memory(); return
    if matches_command(command, ["clear my memory", "forget my saved memory", "memory clear karo", "meri saved memory bhool jao"]):
        clear_personal_memory(); return
    if matches_command(command, ["export chat", "export conversation", "chat history save karo", "conversation export karo"]):
        export_chat_history(); return

    # DOCUMENT READER + CONTENT-AWARE SEARCH
    for prefix in ("read document ", "open and read ", "read file ", "summarize document ", "summarize file "):
        if command.startswith(prefix):
            target = raw_command.strip()[len(prefix):].strip().strip('"\'')
            try:
                extracted = extract_document_text(target)
                if not extracted:
                    speak("Is document mein readable text nahi mila. Scanned PDF ke liye OCR ki zaroorat ho sakti hai.")
                    return
                if command.startswith(("summarize document ", "summarize file ")):
                    answer = ask_ollama("Is document ka clear, short summary do. Important points aur headings preserve karo:\n\n" + extracted[:16000])
                    print("\nJARVIS DOCUMENT SUMMARY:\n" + answer)
                    speak("Document summary console mein show kar di hai.")
                else:
                    print("\nJARVIS DOCUMENT TEXT (first 24000 characters):\n" + extracted)
                    speak(f"Document read ho gaya. {len(extracted)} characters JARVIS console mein show kiye hain.")
            except Exception as error:
                print("Document reader error:", error)
                speak(str(error)[:220])
            return
    for prefix in ("search contents ", "search inside files ", "find text ", "document search "):
        if command.startswith(prefix):
            query = raw_command.strip()[len(prefix):].strip()
            results = intelligent_file_search(query)
            if not results:
                speak("Matching file content nahi mila.")
            else:
                print("\nJARVIS INTELLIGENT SEARCH RESULTS:")
                for score, path, snippet in results:
                    print(f"\n[{score}] {path}\n  {snippet}")
                speak(f"{len(results)} relevant files mile hain. Results console mein hain.")
            return

    # FOLDER COMMANDS FIRST: tolerate common voice-recognition variants and
    # phrases with/without the word "folder" before falling through to AI.
    folder_names = {
        "downloads": ["downloads", "download", "download folder", "downloads folder"],
        "documents": ["documents", "document", "documents folder", "document folder"],
        "desktop": ["desktop folder", "desktop files"],
        "pictures": ["pictures", "picture", "photos", "photo", "pictures folder", "photos folder", "photo folder"],
        "videos": ["videos", "video", "videos folder", "video folder"],
        "music": ["music", "music folder"],
    }
    for folder_name, aliases in folder_names.items():
        open_phrases = []
        close_phrases = []
        for alias in aliases:
            open_phrases.extend([f"open {alias}", f"{alias} kholo", f"{alias} khol do", f"{alias} open karo", f"{alias} khol kar dikhao"])
            close_phrases.extend([f"close {alias}", f"{alias} band karo", f"{alias} band kar do", f"{alias} close karo"])
        if matches_command(command, open_phrases):
            open_folder(folder_name)
            return
        if matches_command(command, close_phrases):
            close_folder_window(folder_name)
            return

    # NEW QUICK INFO FEATURES
    if matches_command(command, ["wifi status", "check wifi", "wifi check karo", "wifi ka status batao", "internet wifi status"]):
        wifi_status()
        return
    if matches_command(command, ["battery health percentage", "battery health percent", "battery ki health kitni hai", "battery health batao", "battery health check", "battery condition batao", "battery capacity health"]):
        battery_health_percentage()
        return
    if matches_command(command, ["battery remaining time", "battery time left", "battery kitni dair chalegi", "battery kitna time baqi hai", "remaining battery time"]):
        battery_remaining_time()
        return
    if matches_command(command, ["battery health report", "create battery report", "battery report banao", "detailed battery report"]):
        create_battery_health_report()
        return
    if matches_command(command, ["system health", "system health report", "pc health check", "computer health batao", "system performance", "pc performance batao", "computer ka haal batao"]):
        system_health_summary()
        return
    if matches_command(command, ["internet check", "check internet", "internet status", "internet connected hai", "internet chal raha hai", "internet connection check"]):
        internet_connection_check()
        return
    if matches_command(command, ["pc uptime", "computer uptime", "uptime batao", "pc kitni dair se chal raha hai", "computer kitni dair se on hai", "windows uptime"]):
        pc_uptime_status()
        return
    if matches_command(command, ["top memory apps", "ram usage apps", "which apps use most ram", "kaunsi apps ram use kar rahi hain", "ram sab se zyada kaun use kar raha hai", "memory hogs"]):
        top_memory_apps()
        return
    if matches_command(command, ["what time is it", "current time", "time batao", "abhi kitne bajay hain", "kitne bajay hain"]):
        now = datetime.datetime.now()
        speak(f"Abhi {now.strftime('%I:%M %p')} ho rahe hain.")
        return
    if matches_command(command, ["what is today's date", "today's date", "current date", "date batao", "aaj kya date hai", "aaj ki tareekh batao"]):
        now = datetime.datetime.now()
        speak(f"Aaj {now.strftime('%A, %d %B %Y')} hai.")
        return

    if matches_command(command, ["disk space overview", "storage overview", "all drive space", "drives ki storage batao", "kitni storage free hai", "storage kitni baqi hai"]):
        disk_space_overview()
        return
    if matches_command(command, ["recent downloads", "latest downloads", "downloads ki list", "recent files in downloads", "meri latest downloads batao"]):
        recent_downloads()
        return

    # FILE SEARCH: "find file report" / "find folder projects"
    for prefix in ("find file ", "search file ", "find folder ", "search folder ", "find file named ", "search for file "):
        if command.startswith(prefix):
            search_files(raw_command.strip()[len(prefix):])
            return
    if command in {"enable startup", "enable windows startup", "start jarvis on startup", "jarvis startup on", "startup on"}:
        set_windows_startup(True); return
    if command in {"disable startup", "disable windows startup", "stop jarvis on startup", "jarvis startup off", "startup off"}:
        set_windows_startup(False); return

    # EXTRA PRODUCTIVITY FEATURES
    # Save clipboard text as a note only when the user explicitly asks.
    if matches_command(command, [
        "save clipboard as note", "save clipboard to notes", "clipboard ko note mein save karo",
        "clipboard note bana do", "clipboard se note save karo", "save copied text as note"
    ]):
        try:
            clipboard_text = read_clipboard_text()
            if clipboard_text and clipboard_text.strip():
                save_note(clipboard_text[:10000])
            else:
                speak("Clipboard par koi plain text nahi hai, yaar. Pehle text copy kar lo.")
        except Exception as error:
            print("Save clipboard note error:", error)
            speak("Clipboard ka text note mein save nahi ho saka.")
        return

    # NEW: summarize copied text with the local AI. The text is sent only to the
    # locally running Ollama service; it is not uploaded to a cloud API by this code.
    if matches_command(command, [
        "summarize clipboard", "summarise clipboard", "clipboard summarize karo",
        "clipboard ka summary batao", "copied text summarize karo",
        "summarize copied text", "clipboard ka khulasa batao"
    ]):
        try:
            clipboard_text = read_clipboard_text().strip()
            if not clipboard_text:
                speak("Clipboard par text nahi hai, yaar. Pehle text copy kar lo.")
            else:
                if len(clipboard_text) > 7000:
                    clipboard_text = clipboard_text[:7000] + "\n[Text shortened for a quick summary.]"
                summary = ask_ollama(
                    "Summarize the following copied text accurately in 3 to 5 short bullet points. "
                    "Use the same language as the text. Do not invent details.\n\n" + clipboard_text
                )
                speak(summary or "Yaar, summary generate nahi ho saki.")
        except Exception as error:
            print("Clipboard summary error:", error)
            speak("Clipboard ka summary nahi ban saka.")
        return

    # NEW: save clipboard text into a separate .txt file on the Desktop.
    if matches_command(command, [
        "save clipboard as desktop file", "save clipboard to desktop file",
        "clipboard ko desktop file mein save karo", "copied text file banao",
        "clipboard text ko file mein save karo"
    ]):
        try:
            clipboard_text = read_clipboard_text().strip()
            if not clipboard_text:
                speak("Clipboard par text nahi hai. Pehle text copy kar lo.")
            else:
                desktop_candidates = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]
                desktop = next((folder for folder in desktop_candidates if folder.exists()), desktop_candidates[0])
                desktop.mkdir(parents=True, exist_ok=True)
                target = desktop / ("JARVIS_Clipboard_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
                target.write_text(clipboard_text, encoding="utf-8")
                speak("Clipboard text Desktop par file mein save kar diya hai.")
                print("Clipboard text saved to:", target)
        except Exception as error:
            print("Save clipboard file error:", error)
            speak("Clipboard text ki file save nahi ho saki.")
        return

    # NEW: create a plain text file from a spoken command. Syntax:
    # "create text file project notes with first draft is ready"
    text_file_match = re.match(
        r"^(?:create|make) text file (.+?) (?:with|containing) (.+)$",
        raw_command.strip(), re.I
    )
    if text_file_match or command.startswith(("create text file ", "make text file ")):
        try:
            if text_file_match:
                filename, content = text_file_match.group(1).strip(), text_file_match.group(2).strip()
            else:
                speak("File ka naam aur us mein likhne wala text bolo. Misal: create text file notes with my first draft.")
                return
            filename = re.sub(r"\.txt$", "", filename, flags=re.I).strip().strip(" .")
            if (not filename or filename in {".", ".."} or
                    any(ch in filename for ch in '<>:/\\|?*') or len(filename) > 100):
                speak("File name valid nahi. Simple naam use karo.")
                return
            desktop_candidates = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]
            desktop = next((folder for folder in desktop_candidates if folder.exists()), desktop_candidates[0])
            desktop.mkdir(parents=True, exist_ok=True)
            target = desktop / (filename + ".txt")
            if target.exists():
                target = desktop / (filename + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
            target.write_text(content, encoding="utf-8")
            speak(f"Text file Desktop par save kar di hai: {target.name}")
            print("Text file created:", target)
        except Exception as error:
            print("Create text file error:", error)
            speak("Text file create nahi ho saki.")
        return

    # Pomodoro-style focus and break timers. Timers use the existing persistent reminder store.
    focus_match = re.fullmatch(r"(?:start )?(?:focus session|pomodoro|focus timer)(?: for)?(?: (\d{1,3}) ?(?:minutes?|mins?))?", command)
    if focus_match or matches_command(command, [
        "start focus", "focus mode start", "focus session shuru karo", "pomodoro shuru karo",
        "start studying", "study session start", "parhai ka timer lagao"
    ]):
        minutes = int(focus_match.group(1)) if focus_match and focus_match.group(1) else 25
        minutes = max(1, min(minutes, 180))
        set_timer(minutes * 60, "Focus session complete — take a short break")
        speak(f"Theek hai yaar, {minutes} minute ka focus session start. Notifications ko ignore karo, ek hi kaam par focus rakho. Main time khatam hone par reminder dunga.")
        return

    if matches_command(command, [
        "start break timer", "take a five minute break", "5 minute break", "break timer",
        "break ka timer lagao", "paanch minute ka break", "5 minute ka break lagao"
    ]):
        set_timer(5 * 60, "Break finished — return to your task")
        speak("Break timer 5 minutes ka set kar diya, yaar. Thora stretch kar lo aur paani pee lo.")
        return

    if matches_command(command, ["read clipboard", "read clipboard text", "clipboard par kya hai", "clipboard ka text parho", "clipboard read karo"]):
        try:
            clipboard_text = read_clipboard_text()
            if clipboard_text:
                # Keep voice response short for very large clipboard contents.
                speak(clipboard_text[:1200] + (". Clipboard text is longer, so I read the first part." if len(clipboard_text) > 1200 else ""))
            else:
                speak("There is no plain text on the clipboard.")
        except Exception as error:
            print("Read clipboard error:", error)
            speak("I could not read the clipboard. Please check clipboard access.")
        return

    for prefix in ("type text ", "type this ", "likho ", "yeh type karo "):
        if command.startswith(prefix):
            type_text_into_active_window(raw_command.strip()[len(prefix):])
            return

    for prefix in ("create desktop folder ", "create folder on desktop ", "desktop par folder banao ", "desktop par folder bana do "):
        if command.startswith(prefix):
            create_desktop_folder(raw_command.strip()[len(prefix):])
            return

    if matches_command(command, ["tell me a joke", "joke sunao", "jarvis joke", "koi joke sunao"]):
        jokes = [
            "Why did the computer go to the doctor? Because it had a virus.",
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "I asked my laptop for a break. It said: no problem, I will go to sleep.",
            "Why was the keyboard tired? It had too many shifts.",
        ]
        import random
        speak(random.choice(jokes))
        return

    if matches_command(command, ["open my notes", "show my notes", "notes kholo", "mere notes kholo", "saved notes kholo"]):
        open_notes()
        return

    # DESKTOP / WINDOW SHORTCUTS
    if matches_command(command, ["show desktop", "desktop dikhao", "desktop show karo", "minimize all windows", "sab windows minimize karo"]):
        pyautogui.hotkey("win", "d")
        speak("Desktop is showing.")
        return
    if matches_command(command, ["minimize window", "window minimize karo"]):
        pyautogui.hotkey("win", "down")
        speak("Window minimized.")
        return
    if matches_command(command, ["maximize window", "window maximize karo"]):
        pyautogui.hotkey("win", "up")
        speak("Window maximized.")
        return
    if matches_command(command, ["switch window", "change window", "window change karo", "next window"]):
        pyautogui.hotkey("alt", "tab")
        speak("Switching window.")
        return

    # QUICK WINDOWS SETTINGS: voice commands in English and Roman Urdu
    settings_commands = {
        "ms-settings:": ("settings", ["open settings", "settings kholo", "settings khol do", "settings open karo", "settings kholo jarvis"]),
        "ms-settings:bluetooth": ("Bluetooth", ["open bluetooth", "open bluetooth settings", "bluetooth kholo", "bluetooth khol do", "bluetooth settings kholo", "bluetooth on off settings"]),
        "ms-settings:network-wifi": ("Wi-Fi", ["open wifi", "open wi fi", "open wifi settings", "wifi kholo", "wifi settings kholo", "wifi settings open karo"]),
        "ms-settings:network": ("Network and Internet", ["open network settings", "internet settings kholo", "network kholo", "network settings open karo"]),
        "ms-settings:display": ("Display", ["open display settings", "display settings kholo", "screen settings kholo", "display kholo"]),
        "ms-settings:sound": ("Sound", ["open sound settings", "sound settings kholo", "audio settings kholo", "speaker settings kholo"]),
        "ms-settings:personalization": ("Personalization", ["open personalization", "personalization kholo", "theme settings kholo", "themes kholo"]),
        "ms-settings:colors": ("Colors", ["open color settings", "colors kholo", "color settings kholo"]),
        "ms-settings:lockscreen": ("Lock screen", ["open lock screen settings", "lock screen settings kholo"]),
        "ms-settings:taskbar": ("Taskbar", ["open taskbar settings", "taskbar settings kholo"]),
        "ms-settings:appsfeatures": ("Installed apps", ["open installed apps", "apps settings kholo", "installed apps kholo"]),
        "ms-settings:defaultapps": ("Default apps", ["open default apps", "default apps kholo"]),
        "ms-settings:windowsupdate": ("Windows Update", ["open windows update", "windows update kholo", "update settings kholo"]),
        "ms-settings:privacy": ("Privacy", ["open privacy settings", "privacy settings kholo"]),
        "ms-settings:sound": ("Sound", ["open sound control"]),
        "ms-settings:storage": ("Storage", ["open storage settings", "storage settings kholo", "disk settings kholo"]),
        "ms-settings:about": ("About this PC", ["open about pc", "about computer kholo", "pc information settings kholo"]),
        "ms-settings:nightlight": ("Night light", ["open night light settings", "night light kholo"]),
        "ms-settings:notifications": ("Notifications", ["open notification settings", "notifications kholo"]),
        "ms-settings:dateandtime": ("Date and time", ["open date and time settings", "date time settings kholo", "time settings kholo"]),
        "ms-settings:regionlanguage": ("Language", ["open language settings", "language settings kholo", "keyboard language kholo"]),
        "ms-settings:accessibility": ("Accessibility", ["open accessibility settings", "accessibility kholo"]),
        "ms-settings:network-vpn": ("VPN", ["open vpn settings", "vpn kholo"]),
        "ms-settings:network-mobilehotspot": ("Mobile hotspot", ["open hotspot settings", "mobile hotspot kholo", "hotspot settings kholo"]),
        "ms-settings:network-airplanemode": ("Airplane mode", ["open airplane mode settings", "airplane mode kholo"]),
        "ms-settings:about": ("About this PC", ["open system information settings"]),
        "ms-settings:accounts": ("Accounts", ["open accounts settings", "accounts kholo", "account settings kholo"]),
        "ms-settings:apps-startup": ("Startup apps", ["open startup apps", "startup apps kholo"]),
        "ms-settings:gaming-gamemode": ("Game Mode", ["open game mode settings", "game mode kholo"]),
        "ms-settings:power": ("Power and battery", ["open power settings", "power settings kholo", "battery settings kholo"]),
        "ms-settings:recovery": ("Recovery", ["open recovery settings", "recovery settings kholo"]),
        "ms-settings:troubleshoot": ("Troubleshoot", ["open troubleshoot settings", "troubleshoot kholo"]),
        "ms-settings:windowsinsider": ("Windows Insider Program", ["open windows insider settings"]),
    }
    # CLOSE Windows Settings pages. Windows Settings is a single app window,
    # so Alt+F4 closes the active Settings page/window safely.
    close_settings_phrases = [
        "close settings", "close bluetooth", "close bluetooth settings",
        "close wifi", "close wifi settings", "close wi fi settings",
        "close windows update", "close update settings", "close display settings",
        "close sound settings", "close network settings", "close internet settings",
        "close personalization", "close color settings", "close lock screen settings",
        "close taskbar settings", "close installed apps", "close default apps",
        "close privacy settings", "close storage settings", "close about pc",
        "close night light settings", "close notification settings",
        "close date and time settings", "close language settings",
        "close accessibility settings", "close vpn settings", "close hotspot settings",
        "close airplane mode settings", "close accounts settings", "close startup apps",
        "close power settings", "close recovery settings", "close troubleshoot settings",
        "settings band karo", "bluetooth band karo", "wifi band karo",
        "windows update band karo", "update settings band karo", "display settings band karo",
        "sound settings band karo", "network settings band karo", "internet settings band karo",
        "personalization band karo", "privacy settings band karo", "storage settings band karo",
        "language settings band karo", "settings window band karo"
    ]
    if matches_command(command, close_settings_phrases):
        try:
            closed = close_process(["SystemSettings.exe"])
            speak("Closed Windows Settings." if closed else "Windows Settings is not open.")
        except Exception as error:
            print("Could not close Windows Settings:", error)
            speak("I could not close the Settings window.")
        return

    if matches_command(command, ["close current window", "close active window", "active window band karo", "current window band karo", "window band karo"]):
        pyautogui.hotkey("alt", "f4")
        speak("Closed the active window, if it can be closed.")
        return

    for uri, (spoken_name, phrases) in settings_commands.items():
        if matches_command(command, phrases):
            open_windows_page(uri.replace("ms-settings: ", "ms-settings:"), spoken_name)
            return

    if matches_command(command, ["lock computer", "lock pc", "lock my laptop", "laptop lock karo", "pc lock karo"]):
        speak("Locking your computer.")
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        return

    if matches_command(command, ["open camera", "camera kholo", "camera open karo"]):
        open_windows_page("microsoft.windows.camera:", "Camera")
        return

    if matches_command(command, ["open clipboard history", "clipboard history kholo"]):
        pyautogui.hotkey("win", "v")
        speak("Opening clipboard history. If Windows asks, enable clipboard history first.")
        return

    if matches_command(command, ["open emoji panel", "emoji kholo", "emoji panel kholo"]):
        pyautogui.hotkey("win", ".")
        speak("Opening emoji panel.")
        return

    if matches_command(command, ["open magnifier", "magnifier kholo"]):
        subprocess.Popen(["magnify.exe"])
        speak("Opening Magnifier.")
        return

    if matches_command(command, ["open on screen keyboard", "on screen keyboard kholo", "virtual keyboard kholo"]):
        subprocess.Popen(["osk.exe"])
        speak("Opening On-Screen Keyboard.")
        return

    if matches_command(command, ["open task view", "task view kholo", "virtual desktops kholo"]):
        pyautogui.hotkey("win", "tab")
        speak("Opening Task View.")
        return

    # COPY USER-PROVIDED TEXT TO CLIPBOARD: "copy text hello world"
    for prefix in ("copy text ", "clipboard par copy karo ", "text copy karo "):
        if command.startswith(prefix):
            text_to_copy = raw_command.strip()[len(prefix):].strip()
            if text_to_copy:
                try:
                    copy_text_to_clipboard(text_to_copy)
                    speak("Text copied to clipboard. You can paste it with Control V.")
                except Exception as error:
                    print("Clipboard text error:", error)
                    speak("I could not copy the text to clipboard.")
            else:
                speak("Please tell me which text to copy.")
            return

    # CONFIRMATION GATE FOR RECYCLE BIN
    global RECYCLE_BIN_CONFIRMATION_PENDING
    if RECYCLE_BIN_CONFIRMATION_PENDING:
        # Accept short confirmations only while a Recycle Bin request is pending.
        confirmation = command.strip().lower()
        if confirmation in {
            "confirm", "yes", "haan", "han",
            "empty recycle bin confirm", "confirm empty recycle bin",
            "confirm recycle bin", "yes empty recycle bin"
        }:
            RECYCLE_BIN_CONFIRMATION_PENDING = False
            empty_recycle_bin()
            return
        if confirmation in {"cancel", "cancel recycle bin", "no", "stop", "nahi", "nahin"}:
            RECYCLE_BIN_CONFIRMATION_PENDING = False
            speak("Recycle Bin operation cancelled.")
            return
        speak("Please say confirm, yes, or cancel.")
        return

    # SAVE ACTIVE WORK ONLY (does not shut down the PC).
    if matches_command(command, [
        "save", "save work", "save my work", "save the work",
        "save this", "save this work", "save document", "save file",
        "save it", "please save", "save everything", "save current work",
        "kaam save karo", "kaam save kar do", "mera kaam save karo",
        "mera kaam save kar do", "kaam sev karo", "file save karo",
        "file sev karo", "document save karo", "document sev karo",
        "is ko save karo", "isko save karo", "ye save karo", "yeh save karo",
    ]):
        try:
            pyautogui.hotkey("ctrl", "s")
            time.sleep(0.7)
            speak("Okay, I pressed Control and S in the active app. Please check that your file is saved, especially if a Save As window appeared.")
        except Exception as error:
            print("Save work error:", error)
            speak("I could not send the save shortcut. Please save manually.")
        return

    # NATURAL SPOKEN REMINDERS: accept full sentences, not only exact command phrases.
    reminder_input = raw_command.strip()
    reminder_input = re.sub(r"^set an? alarm in\b", "remind me in", reminder_input, flags=re.I)
    reminder_input = re.sub(r"^alarm in\b", "remind me in", reminder_input, flags=re.I)
    reminder_input = re.sub(r"^set an? reminder in\b", "remind me in", reminder_input, flags=re.I)
    parsed_reminder = _parse_reminder_command(reminder_input)
    if parsed_reminder:
        seconds, label = parsed_reminder
        set_timer(seconds, label)
        return
    if any(phrase in command for phrase in ("remind me", "yaad karwa dena", "yaad dilana", "yaad karwana")) and re.search(r"\b\d+\b", command):
        # A bare number is ambiguous: don't silently choose 2 pm versus 2 days.
        if not re.search(r"\b(seconds?|minutes?|hours?|days?|weeks?|am|pm|baje|din|ghante|minute|haftay|hafte)\b", command):
            speak("2 se tumhari murad 2 baje hai ya 2 din baad? Please time ya duration clear bata do, phir main reminder save kar dunga.")
            return

    # PERSONAL TASK MANAGER: tasks persist across JARVIS restarts.
    if matches_command(command, ["list my tasks", "show my tasks", "pending tasks", "mere tasks batao", "mere kaam batao", "aaj ke kaam batao", "what do i need to do", "what should i do today", "what work is pending", "mere pending kaam kya hain"]):
        list_tasks()
        return
    if matches_command(command, ["daily briefing", "morning briefing", "today briefing", "aaj ka plan batao", "aaj ka schedule batao", "subah ka plan batao"]):
        daily_briefing()
        return
    task_done_match = re.match(r"(?:complete task|finish task|task complete karo|task mukammal karo)\s+(\d+)$", command)
    if task_done_match:
        complete_task(int(task_done_match.group(1)))
        return
    for prefix in ("add task ", "remember task ", "yaad rakhna ke ", "mera kaam yaad rakhna ", "task add karo ", "kaam add karo "):
        if command.startswith(prefix):
            add_task(raw_command.strip()[len(prefix):])
            return
    natural_task = _natural_task_request(raw_command)
    if natural_task and any(term in command for term in ("this week", "is hafte", "iss hafte", "isi hafte", "i need to", "i have to", "i must finish", "i should finish", "yaad rakhna", "remember that", "sir ka kaam")):
        task_text, due_date = natural_task
        add_task(task_text, due_date=due_date)
        if any(term in command for term in ("remind me", "yaad karwa dena", "yaad dilana")):
            speak("Task save ho gaya. Reminder ka time clear nahi tha; 2 baje ya 2 din baad jaisa exact time/duration bata dena.")
        return

    if command in {"add task", "remember this task", "task add karo", "kaam add karo", "yaad rakhna"}:
        speak("Zaroor, konsa kaam yaad rakhna hai? Bolo add task aur phir task bata do.")
        return

    # PERSONAL NOTES
    if command in {"open my notes", "open notes", "notes kholo", "meri notes kholo", "notes open karo"}:
        open_notes()
        return
    for prefix in ("take a note ", "save a note ", "note likho ", "note save karo ", "yaad rakhna "):
        if command.startswith(prefix):
            save_note(command[len(prefix):])
            return
    if command in {"take a note", "save a note", "note likho", "note save karo"}:
        speak("Please say take a note followed by what you want me to save.")
        return

    # SIMPLE TIMERS: seconds or minutes, e.g. 'set timer for 5 minutes'
    timer_match = re.search(r"(?:set )?timer(?: for)? (\d+) (seconds?|minutes?)", command)
    if timer_match:
        amount = int(timer_match.group(1))
        unit = timer_match.group(2)
        seconds = amount * (60 if unit.startswith("minute") else 1)
        set_timer(seconds)
        return
    reminder_match = re.search(r"remind me in (\d+) (seconds?|minutes?) to (.+)", command)
    if reminder_match:
        amount = int(reminder_match.group(1))
        unit = reminder_match.group(2)
        reminder_text = reminder_match.group(3).strip()
        seconds = amount * (60 if unit.startswith("minute") else 1)
        set_timer(seconds, reminder_text)
        return

    # EMPTY RECYCLE BIN: ALWAYS ASK FOR A SECOND CONFIRMATION
    if command in {"empty recycle bin", "recycle bin empty karo", "recycle bin khali karo", "clear recycle bin"}:
        request_empty_recycle_bin()
        return

    # FILE ORGANIZATION. Accept both spellings and natural command variations.
    for folder_name in ["downloads", "desktop", "documents", "pictures", "videos", "music"]:
        organization_phrases = {
            f"organize {folder_name}", f"organize my {folder_name}",
            f"organize the {folder_name}", f"organize {folder_name} folder",
            f"organize my {folder_name} folder",
            f"{folder_name} organize karo", f"{folder_name} ko organize karo",
            f"{folder_name} ko organize kar do", f"{folder_name} arrange karo",
            f"{folder_name} ko arrange karo", f"organize {folder_name} files",
            f"organize my {folder_name} files", f"organize the {folder_name} files",
            f"arrange {folder_name}", f"sort {folder_name}",
            f"sort my {folder_name}", f"clean up {folder_name}",
            f"{folder_name} saaf karo", f"{folder_name} ko set karo",
        }
        if command in organization_phrases:
            speak(f"I will organize files in {folder_name} into type-based folders.")
            organize_folder(folder_name)
            return
    if command in {
        "organize files", "organize my files", "organize the files",
        "organize all files", "organize my stuff", "arrange my files",
        "sort my files", "clean up my files", "files organize karo",
        "files ko organize karo", "meri files organize karo",
        "files ko arrange karo", "meri files arrange karo",
    }:
        speak("Sure. I will tidy up your Downloads folder by file type. I will not delete your files.")
        organize_folder("downloads")
        return

    # CLOSE CURRENT WEBSITE TAB (browser must be focused)
    if command.startswith(("close website ", "website band karo ")) or command in {"close current tab", "close website", "close youtube", "close google", "website band karo", "current tab band karo"}:
        pyautogui.hotkey("ctrl", "w")
        speak("Closed the current browser tab, if a browser was focused.")
        return

    # KNOWN WEBSITES AND VALIDATED URLS
    if open_website_from_command(command):
        return

    # Resolve known Windows applications BEFORE the generic launcher below.
    # Otherwise commands such as "open file explorer" are passed to cmd as a
    # literal app name, which produces the Windows "cannot find 'file explorer'" error.
    if open_application(command):
        return

    # NATURAL-LANGUAGE APP LAUNCH: supports forms like "open VS", "VS kholo",
    # "open Photoshop", "Photoshop khol do", and "Photoshop open karo".
    # Known apps/settings/websites are handled above first.
    natural_open_prefixes = (
        "open ", "launch ", "start ", " kholo", " khol do",
        " open karo", " chalao", " chala do", " kholo please",
    )
    app_name = ""
    for prefix in ("open the app ", "open app ", "open the ", "open ", "launch the app ", "launch app ", "launch the ", "launch ", "start the app ", "start app ", "start the ", "start "):
        if command.startswith(prefix):
            app_name = command[len(prefix):].strip()
            break
    if not app_name:
        for suffix in (" app kholo", " app khol do", " app open karo", " kholo please", " khol do", " khol kar kholo", " open karo", " chala do", " chalao", " kholo"):
            if command.endswith(suffix):
                app_name = command[:-len(suffix)].strip()
                break
    if app_name and app_name not in {"website", "settings", "app", "browser", "computer", "pc"}:
        # Avoid sending URL-like strings to the Windows app launcher.
        if "." not in app_name and not app_name.startswith(("http://", "https://", "www ")):
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "", app_name], shell=False)
                speak(f"Trying to open {app_name}.")
            except Exception as error:
                print("Natural app launch error:", error)
                speak(f"I could not open {app_name}. Try saying open app followed by the app name.")
            return

    # EXIT
    if matches_command(command, [
        "exit",
        "quit",
        "bye",
        "goodbye",
        "stop jarvis",
        "jarvis band karo",
        "jarvis ko band karo",
        "exit jarvis"
    ]):
        speak("Goodbye. JARVIS is shutting down.")
        raise SystemExit

    # QUICK CONVERSATION: time, date, jokes, and polite replies
    if command in {"time", "what time is it", "time batao", "abhi time kya hai", "kitne bajay hain"}:
        speak("Abhi " + datetime.datetime.now().strftime("%I:%M %p") + " ho rahe hain.")
        return

    if command in {"date", "today date", "date batao", "aaj ki date kya hai", "aaj kya date hai"}:
        speak("Aaj " + datetime.datetime.now().strftime("%A, %d %B %Y") + " hai.")
        return

    if command in {"tell me a joke", "joke", "joke sunao", "koi joke sunao", "funny joke batao"}:
        speak("Acha suno: computer thak kyun gaya? Kyun ke uske bohat saare tabs khulay huay thay!")
        return

    if command in {"thank you", "thanks", "shukriya", "thanks jarvis", "thank you jarvis"}:
        speak("Aray, koi baat nahi! Jab bhi help chahiye ho, bas awaaz de dena.")
        return

    if command in {"how are you", "kaise ho", "kese ho", "kya haal hai", "jarvis kaise ho"}:
        speak("Main theek hoon, shukriya! Tum sunao, sab khairiyat?")
        return

    # GREETING
    if command in [
        "hello",
        "hi",
        "hello jarvis",
        "hi jarvis",
        "hey jarvis"
    ]:
        speak("Hello! How can I help you?")
        return

    # BATTERY
    if any(word in command for word in [
        "battery", "charger laga hai"
    ]):
        battery_status()
        return

    # RAM
    if any(word in command.split() for word in [
        "ram", "memory"
    ]):
        ram_status()
        return

    # CPU
    if any(word in command.split() for word in [
        "cpu", "processor"
    ]):
        cpu_status()
        return

    # STORAGE
    if any(word in command for word in [
        "storage", "disk space", "hard disk", "drive space"
    ]):
        storage_status()
        return

    # INTERNET
    if any(word in command for word in [
        "internet", "wifi", "wi fi"
    ]):
        internet_status()
        return

    # IP
    if "ip address" in command or command.startswith("ip batao") or command == "mera ip":
        ip_address()
        return

    # SYSTEM INFORMATION
    if any(phrase in command for phrase in [
        "system information",
        "system info",
        "computer information",
        "computer details",
        "pc information",
        "system details"
    ]):
        system_information()
        return

    # UPTIME
    if "uptime" in command or "kitni der se on" in command:
        system_uptime()
        return

    # SCREENSHOT
    if "screenshot" in command or "screen capture" in command:
        take_screenshot()
        return

    # VOLUME
    if any(phrase in command for phrase in [
        "volume up", "volume barhao", "volume badhao",
        "awaz barhao", "awaz badhao", "awaz tez karo",
        "sound barhao"
    ]):
        volume_up()
        return

    if any(phrase in command for phrase in [
        "volume down", "volume kam karo", "awaz kam karo",
        "awaz dheemi karo", "sound kam karo"
    ]):
        volume_down()
        return

    if any(phrase in command for phrase in [
        "mute", "awaz mute karo", "sound mute karo"
    ]):
        volume_mute()
        return

    # TIME
    if command == "time" or "time batao" in command or "time kya hai" in command:
        speak(datetime.datetime.now().strftime("The time is %I:%M %p."))
        return

    # DATE
    if command == "date" or "date batao" in command or "aaj ki date" in command:
        speak(datetime.datetime.now().strftime("Today is %d %B %Y."))
        return

    # DAY
    if "aaj konsa din" in command or "what day is today" in command:
        speak(datetime.datetime.now().strftime("Today is %A."))
        return

    # CALCULATOR
    for prefix in ["calculate ", "calculator "]:
        if command.startswith(prefix):
            calculate_command(command[len(prefix):])
            return

    # GOOGLE SEARCH
    for prefix in [
        "search google for ",
        "google search for ",
        "google par search karo ",
        "google par "
    ]:
        if command.startswith(prefix):
            google_search(command[len(prefix):].strip())
            return

    # YOUTUBE SEARCH
    for prefix in [
        "search youtube for ",
        "youtube par search karo ",
        "youtube par "
    ]:
        if command.startswith(prefix):
            youtube_search(command[len(prefix):].strip())
            return

    # WHATSAPP HELLO
    if matches_command(command, [
        "send hello",
        "send hello to mehar",
        "send hello to mehar burhan",
        "mehar ko hello send karo",
        "mehar burhan ko hello send karo",
        "hello send karo"
    ]):
        send_hello_to_mehar()
        return

    # FOLDERS
    for name in [
        "downloads",
        "documents",
        "desktop",
        "pictures",
        "videos",
        "music"
    ]:
        if matches_command(command, [
            f"open {name}",
            f"{name} kholo",
            f"{name} khol do",
            f"{name} open karo"
        ]):
            open_folder(name)
            return

        if matches_command(command, [
            f"close {name}",
            f"{name} band karo",
            f"{name} band kar do"
        ]):
            close_folder_window(name)
            return

    # LOCK COMPUTER
    if matches_command(command, [
        "lock computer",
        "lock pc",
        "computer lock karo",
        "pc lock karo"
    ]):
        speak("Locking the computer.")
        subprocess.Popen([
            "rundll32.exe",
            "user32.dll,LockWorkStation"
        ])
        return

    # CANCEL SHUTDOWN
    if matches_command(command, [
        "cancel shutdown",
        "shutdown cancel karo"
    ]):
        subprocess.run(
            ["shutdown", "/a"],
            capture_output=True
        )
        speak("Shutdown cancellation requested.")
        return

    # RESTART JARVIS DESKTOP APP (does not restart Windows)
    if matches_command(command, [
        "restart jarvis",
        "restart jarvis app",
        "restart jarvis desktop app",
        "jarvis restart karo",
        "jarvis app restart karo",
        "desktop app restart karo",
        "restart assistant",
    ]):
        try:
            from pathlib import Path
            project_dir = Path(__file__).resolve().parent
            # Support both common project layouts:
            # JARVIS-AI/desktop_app.py and JARVIS-AI/desktop_app/desktop_app.py
            candidates = [project_dir / "desktop_app.py", project_dir / "desktop_app" / "desktop_app.py"]
            desktop_script = next((path for path in candidates if path.is_file()), None)
            if desktop_script is None:
                speak("I could not find desktop_app.py. Put it beside main.py or inside the desktop_app folder.")
                return
            speak("Restarting JARVIS desktop app. Please wait.")
            # Launch the new desktop instance before closing this process.
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            subprocess.Popen(
                [sys.executable, str(desktop_script)],
                cwd=str(project_dir),
                close_fds=True,
                creationflags=creation_flags,
            )
            time.sleep(1.2)
            os._exit(0)
        except Exception as exc:
            print("JARVIS app restart failed:", exc)
            speak("I could not restart JARVIS. Please restart it manually.")
        return

    # SAVE WORK AND SHUT DOWN: Ctrl+S saves the active app where supported.
    # Windows shutdown is delayed 30 seconds so the user can check dialogs/cancel.
    if matches_command(command, [
        "save work and shut down pc", "save my work and shut down pc",
        "save work then shutdown", "save everything and shutdown",
        "save and shutdown", "save and shut down computer",
        "save my work and shutdown computer", "save the work and shut down the pc",
        "save work pc band karo", "kaam save karo aur pc band karo",
        "mera kaam save karo aur computer band karo", "kaam save kar ke pc band karo",
        "save karo aur shutdown karo", "work save karo aur shutdown kar do",
        "kaam save kar do aur laptop band karo", "save work and shutdown computer",
        "save the work and shutdown pc", "save my work and shut down computer",
        "save everything and shut down pc", "kaam sev karo aur pc band karo",
    ]):
        save_work_and_shutdown()
        return

    # RESTART WINDOWS COMPUTER
    if matches_command(command, [
        "restart computer",
        "restart pc",
        "computer restart karo",
        "pc restart karo"
    ]):
        speak("Restarting the computer in five seconds.")
        subprocess.Popen(["shutdown", "/r", "/t", "5"])
        return

    # SHUTDOWN
    if matches_command(command, [
        "shutdown computer",
        "shutdown pc",
        "shut down computer",
        "shut down pc",
        "turn off computer",
        "turn off pc",
        "power off computer",
        "power off pc",
        "computer band karo",
        "computer band kar do",
        "pc band karo",
        "pc band kar do",
        "laptop band karo",
        "laptop band kar do",
        "computer shutdown",
        "pc shutdown",
        "system band karo",
        "system shutdown karo"
    ]):
        speak("Shutting down the computer in five seconds.")
        subprocess.Popen(["shutdown", "/s", "/t", "5"])
        return

    # CLOSE APPLICATIONS
    if close_application(command):
        return

    # NATURAL-LANGUAGE CLOSE FOR OTHER APPS: "Photoshop band karo" / "close Photoshop".
    generic_close_name = ""
    for prefix in ("close the app ", "close app ", "close the ", "close ", "quit the app ", "quit app ", "quit ", "exit ", "band karo app ", "band karo ", "band kar do "):
        if command.startswith(prefix):
            generic_close_name = command[len(prefix):].strip()
            break
    if not generic_close_name:
        for suffix in (" band kar do", " band karo", " close karo", " close kar do", " ko band karo", " ko band kar do", " app band karo", " app band kar do", " app close karo"):
            if command.endswith(suffix):
                generic_close_name = command[:-len(suffix)].strip()
                break
    if generic_close_name and generic_close_name not in {"app", "website", "settings", "tab", "window", "jarvis", "computer", "pc"}:
        process_name = generic_close_name.lower().removesuffix(".exe").strip()
        protected = {"system", "system idle process", "csrss", "wininit", "winlogon", "services", "lsass", "smss", "registry", "dwm", "explorer"}
        if process_name in protected:
            speak("I cannot close that essential Windows process.")
            return
        try:
            matches = [proc for proc in psutil.process_iter(["name"]) if proc.info.get("name") and Path(proc.info["name"]).stem.lower() == process_name]
            if matches:
                for proc in matches:
                    try:
                        proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                speak(f"Sent a close request to {generic_close_name}.")
            else:
                speak(f"I could not find a running app named {generic_close_name}.")
        except Exception as error:
            print("Natural app close error:", error)
            speak(f"I could not close {generic_close_name}.")
        return

    # GENERIC APP CONTROL: "open app <name>" / "close app <name>"
    # Lets the user launch apps registered with Windows, not just the built-in list.
    if command.startswith(("open app ", "open the app ", "launch app ", "launch the app ", "start app ", "start the app ", "app kholo ", "app open karo ", "app khol do ", "application kholo ", "application open karo ", "chalao app ")):
        prefixes = ("open app ", "open the app ", "launch app ", "launch the app ", "start app ", "start the app ", "app kholo ", "app open karo ", "app khol do ", "application kholo ", "application open karo ", "chalao app ")
        app_name = next((command[len(prefix):].strip() for prefix in prefixes if command.startswith(prefix)), "")
        if not app_name:
            speak("Please say the app name after open app.")
            return
        try:
            # Use the Windows shell app lookup; quote the name as one argument.
            subprocess.Popen(["cmd.exe", "/c", "start", "", app_name], shell=False)
            speak(f"Trying to open {app_name}.")
        except Exception as error:
            print("Generic app launch error:", error)
            speak(f"I could not open {app_name}. Try its exact Windows app name.")
        return

    if command.startswith(("close app ", "close the app ", "quit app ", "quit the app ", "stop app ", "stop the app ", "app band karo ", "app band kar do ", "app close karo ", "application band karo ", "application close karo ", "band karo app ")):
        prefixes = ("close app ", "close the app ", "quit app ", "quit the app ", "stop app ", "stop the app ", "app band karo ", "app band kar do ", "app close karo ", "application band karo ", "application close karo ", "band karo app ")
        app_name = next((command[len(prefix):].strip() for prefix in prefixes if command.startswith(prefix)), "")
        if not app_name:
            speak("Please say the app name after close app.")
            return
        # Protect core Windows processes from accidental voice-recognition matches.
        protected = {"system", "system idle process", "csrss", "wininit", "winlogon", "services", "lsass", "smss", "registry", "dwm", "explorer"}
        process_name = app_name.lower().removesuffix(".exe").strip()
        if process_name in protected:
            speak("I cannot close that essential Windows process.")
            return
        try:
            matches = [proc for proc in psutil.process_iter(["name"]) if proc.info.get("name") and Path(proc.info["name"]).stem.lower() == process_name]
            if not matches:
                speak(f"I could not find a running app named {app_name}.")
                return
            for proc in matches:
                try:
                    proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            speak(f"Sent a close request to {app_name}.")
        except Exception as error:
            print("Generic app close error:", error)
            speak(f"I could not close {app_name}.")
        return

    # NORMAL QUESTIONS GO TO OLLAMA
    answer = ask_ollama(raw_command)

    if answer:
        speak(answer)
    else:
        speak(
            "I could not get an answer. "
            "Please check whether Ollama is running."
        )


# =========================================================
# START JARVIS - CONTINUOUS LISTENING
# =========================================================

def main():
    threading.Thread(
        target=initialize_ai,
        daemon=True
    ).start()
    threading.Thread(
        target=_reminder_watcher,
        daemon=True,
        name="JARVIS-Reminder-Watcher",
    ).start()
    threading.Thread(target=_battery_low_watcher, daemon=True, name="JARVIS-Battery-Alert").start()
    # Performance alerts disabled as requested; CPU/RAM checks remain available on demand.

    calibrate_microphone()

    speak("JARVIS is ready. You can speak now.")

    while True:
        try:
            # Enter press karne ki zaroorat nahi
            command = listen()

            if command:
                process_command(command)

        except KeyboardInterrupt:
            print("\nJARVIS stopped.")
            break

        except SystemExit:
            break

        except Exception as error:
            print("Unexpected error:", error)
            time.sleep(1)


if __name__ == "__main__":
    main()