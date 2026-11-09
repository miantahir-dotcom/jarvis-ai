# JARVIS AI

A Windows desktop voice assistant with a PySide6 interface, local Ollama chat, speech recognition, text-to-speech, and an optional same-Wi-Fi mobile remote.

## Requirements

- Windows 10 or later
- Python 3.10+
- [Ollama](https://ollama.com/) for local AI chat; install a model such as `llama3.2:1b`
- A working microphone for voice input

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python desktop_app\desktop_app.py
```

Run `python main.py` to start the voice assistant directly. The Windows packaging script is `build_windows.bat`.

## Optional WhatsApp feature

Set `JARVIS_WHATSAPP_NUMBER` to the destination number in international format before starting JARVIS. The number is not stored in the source code. For example, in PowerShell:

```powershell
$env:JARVIS_WHATSAPP_NUMBER = "+<countrycode><number>"
python main.py
```

## Optional mobile remote

Install the included Flask dependency, then run `python mobile_remote.py`. The remote displays a temporary PIN in the terminal and is intended for a trusted local Wi-Fi network only. Do not expose it directly to the internet.

## Notes

Speech recognition and neural text-to-speech may use online services. Ollama runs locally. Review commands and permissions before using the assistant on a device with sensitive data.
