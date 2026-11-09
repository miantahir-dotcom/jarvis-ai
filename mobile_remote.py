"""Secure same-Wi-Fi mobile remote for the existing JARVIS assistant.
Run from the JARVIS project folder after installing Flask: pip install flask
"""
from __future__ import annotations
import hmac
import os
import secrets
import socket
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request, session, render_template_string
import psutil

try:
    import main as jarvis
except Exception as exc:
    raise SystemExit(
        "Could not import main.py. Put mobile_remote.py in the same folder as main.py. "
        f"Details: {exc}"
    )

APP = Flask(__name__)
APP.secret_key = secrets.token_hex(32)
ACCESS_PIN = os.environ.get("JARVIS_REMOTE_PIN", "").strip() or f"{secrets.randbelow(900000) + 100000}"
APP.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#07111f"><title>JARVIS Mobile</title>
<style>
:root{--bg:#07111f;--panel:#101f32;--line:#24415d;--cyan:#51e5ff;--text:#eff8ff;--muted:#9bb4c9;--green:#71f7bc}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#123354 0,#07111f 56%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh}
.wrap{max-width:680px;margin:auto;padding:22px 16px 38px}.brand{display:flex;align-items:center;gap:12px;margin:8px 0 20px}.orb{width:48px;height:48px;border:1px solid var(--cyan);border-radius:50%;display:grid;place-items:center;color:var(--cyan);box-shadow:0 0 24px #51e5ff40;font-weight:800}.title{font-size:25px;font-weight:800;letter-spacing:2px}.sub{font-size:12px;color:var(--muted)}.card{background:#101f32eF;border:1px solid var(--line);border-radius:20px;padding:17px;margin:13px 0;box-shadow:0 12px 32px #0003}.label{font-size:12px;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);margin-bottom:10px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.stat{background:#081726;border:1px solid #1b3b55;border-radius:13px;padding:12px 8px;text-align:center}.value{font-size:22px;font-weight:750;color:var(--cyan)}.small{font-size:11px;color:var(--muted);margin-top:4px}input,button{font:inherit}input{width:100%;padding:14px 13px;border-radius:12px;border:1px solid #31516b;background:#081726;color:white;outline:none}input:focus{border-color:var(--cyan)}button{border:1px solid #2b627e;border-radius:12px;background:linear-gradient(135deg,#143e5a,#0e2a43);color:white;padding:12px;cursor:pointer;font-weight:650}button:active{transform:scale(.98)}.primary{width:100%;background:linear-gradient(135deg,#147a9c,#1852a0);border-color:#42cdeb;padding:14px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.quick{min-height:58px}.row{display:flex;gap:8px}.row input{flex:1;min-width:0}.row button{white-space:nowrap}.status{font-size:13px;line-height:1.5;color:var(--green);min-height:20px;overflow-wrap:anywhere}.muted{color:var(--muted);font-size:12px;line-height:1.5}.hide{display:none!important}.pill{display:inline-block;border:1px solid #27516a;border-radius:99px;padding:5px 9px;font-size:11px;color:var(--green)}footer{text-align:center;color:#6f8ca4;font-size:11px;padding:12px}
</style></head><body><div class="wrap">
<div class="brand"><div class="orb">J</div><div><div class="title">JARVIS</div><div class="sub">MOBILE REMOTE CONTROL <span class="pill">LOCAL NETWORK</span></div></div></div>
<div id="login" class="card"><div class="label">Secure connection</div><p class="muted">Enter the 6-digit PIN shown in the JARVIS laptop terminal.</p><div class="row"><input id="pin" inputmode="numeric" autocomplete="one-time-code" placeholder="6-digit PIN" maxlength="6"><button onclick="login()">Connect</button></div><div id="loginmsg" class="status"></div></div>
<div id="app" class="hide">
<div class="card"><div class="label">Laptop status</div><div class="stats"><div class="stat"><div id="battery" class="value">—</div><div class="small">Battery</div></div><div class="stat"><div id="cpu" class="value">—</div><div class="small">CPU</div></div><div class="stat"><div id="ram" class="value">—</div><div class="small">RAM</div></div></div><div id="host" class="muted" style="margin-top:12px">Connected to JARVIS laptop</div></div>
<div class="card"><div class="label">Ask or command JARVIS</div><form id="cmdform" class="row"><input id="command" placeholder="e.g. battery health batao" autocomplete="off"><button type="submit">Send</button></form><div class="muted" style="margin-top:9px">Roman Urdu ya English likho. Voice input ke liye neeche microphone button use karo (browser support required).</div><div class="row" style="margin-top:10px"><button id="mic" type="button" style="flex:1" onclick="voiceInput()">🎙️ Speak command</button><button type="button" onclick="document.getElementById('command').value='';document.getElementById('command').focus()">Clear</button></div><div id="result" class="status" style="margin-top:12px"></div></div>
<div class="card"><div class="label">Quick actions</div><div class="grid">
<button class="quick" onclick="send('battery health percentage')">🔋 Battery health</button><button class="quick" onclick="send('battery remaining time')">⏳ Battery time</button>
<button class="quick" onclick="send('system health')">📊 System health</button><button class="quick" onclick="send('wifi status')">📶 Wi-Fi status</button>
<button class="quick" onclick="send('pc uptime')">🕒 PC uptime</button><button class="quick" onclick="send('top memory apps')">🧠 Top RAM apps</button>
<button class="quick" onclick="send('open chrome')">🌐 Open Chrome</button><button class="quick" onclick="send('open youtube')">▶️ Open YouTube</button>
<button class="quick" onclick="send('open settings')">⚙️ Open Settings</button><button class="quick" onclick="send('show desktop')">🖥️ Show desktop</button>
</div></div><button class="primary" onclick="logout()">Disconnect phone</button>
</div><footer>JARVIS stays on your laptop. Keep this connection on your trusted Wi-Fi only.</footer></div>
<script>
async function api(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let d={};try{d=await r.json()}catch{}if(r.status===401){showLogin();throw Error(d.error||'Please connect again')}if(!r.ok)throw Error(d.error||'Request failed');return d}
function showLogin(){document.getElementById('login').classList.remove('hide');document.getElementById('app').classList.add('hide')}
async function login(){const msg=document.getElementById('loginmsg');msg.textContent='Connecting…';try{await api('/api/login',{method:'POST',body:JSON.stringify({pin:document.getElementById('pin').value})});document.getElementById('login').classList.add('hide');document.getElementById('app').classList.remove('hide');refresh()}catch(e){msg.textContent=e.message}}
async function logout(){try{await api('/api/logout',{method:'POST',body:'{}'})}catch{}showLogin()}
async function refresh(){try{const d=await api('/api/status');document.getElementById('battery').textContent=d.battery===null?'—':d.battery+'%';document.getElementById('cpu').textContent=d.cpu+'%';document.getElementById('ram').textContent=d.ram+'%';document.getElementById('host').textContent='Laptop online · '+d.time}catch(e){document.getElementById('host').textContent=e.message}}
async function send(cmd){document.getElementById('command').value=cmd;const result=document.getElementById('result');result.textContent='Sending command to laptop…';try{const d=await api('/api/command',{method:'POST',body:JSON.stringify({command:cmd})});result.textContent=d.message;refresh()}catch(e){result.textContent=e.message}}
document.getElementById('cmdform').addEventListener('submit',e=>{e.preventDefault();const c=document.getElementById('command').value.trim();if(c)send(c)});
function voiceInput(){const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){document.getElementById('result').textContent='Voice input is not supported in this browser. Please type the command.';return}const r=new SR();r.lang='en-PK';r.interimResults=false;r.maxAlternatives=1;document.getElementById('result').textContent='Listening…';r.onresult=e=>send(e.results[0][0].transcript);r.onerror=e=>document.getElementById('result').textContent='Voice input error: '+e.error;r.start()}
setInterval(()=>{if(!document.getElementById('app').classList.contains('hide'))refresh()},7000);
</script></body></html>'''

# Intentionally allow only a set of low-risk laptop commands over mobile.
# Shutdown/restart, arbitrary shell commands, deleting files, and emptying Recycle Bin are blocked.
def command_allowed(text: str) -> bool:
    c = " ".join(text.lower().strip().split())
    if not c or len(c) > 180:
        return False
    blocked = ("shutdown", "shut down", "restart", "reboot", "format", "delete all", "empty recycle bin", "empty recycle", "run powershell", "cmd.exe", "terminal command", "execute command")
    if any(x in c for x in blocked):
        return False
    starts = (
        "open ", "close ", "launch ", "search ", "google ", "youtube ", "play ",
        "battery", "wifi", "wi-fi", "wi fi", "internet", "system health", "pc health",
        "pc uptime", "computer uptime", "top memory apps", "ram usage", "what time",
        "current time", "time batao", "abhi kitne", "date batao", "aaj kya date",
        "aaj ki tareekh", "show desktop", "desktop dikhao", "minimize", "maximize",
        "switch window", "window change", "volume", "mute", "unmute", "open notes",
        "create desktop folder", "find file", "find folder", "read clipboard", "clipboard",
        "system performance", "computer ka haal", "battery ki", "battery kitni", "battery kitna",
        "wifi ka status", "internet chal raha", "internet connected", "kaunsi apps ram",
        "ram sab se zyada", "laptop", "pc ki", "computer ki"
    )
    exact = {"hello", "hi jarvis", "jarvis", "help", "time", "date", "battery health", "battery health percentage", "system info"}
    return c in exact or c.startswith(starts)


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify(error="Not connected. Enter the PIN first."), 401
        return fn(*args, **kwargs)
    return wrapper

@APP.get("/")
def index():
    return render_template_string(PAGE)

@APP.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin", ""))[:32]
    if hmac.compare_digest(pin, ACCESS_PIN):
        session.clear()
        session["authenticated"] = True
        session.permanent = False
        return jsonify(ok=True)
    time.sleep(0.6)
    return jsonify(error="Wrong PIN. Check the PIN in the laptop terminal."), 401

@APP.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@APP.get("/api/status")
@require_login
def status():
    battery = None
    try:
        b = psutil.sensors_battery()
        if b is not None:
            battery = round(b.percent)
    except Exception:
        pass
    return jsonify(battery=battery, cpu=round(psutil.cpu_percent(interval=0.15)), ram=round(psutil.virtual_memory().percent), time=__import__('datetime').datetime.now().strftime('%I:%M:%S %p'))

@APP.post("/api/command")
@require_login
def command():
    data = request.get_json(silent=True) or {}
    text = str(data.get("command", "")).strip()
    if not command_allowed(text):
        return jsonify(error="This command is not allowed from mobile for safety. Try a Quick Action or a supported open/close/status command."), 400
    # Run separately so HTTP stays responsive while JARVIS carries out the command.
    threading.Thread(target=jarvis.process_command, args=(text,), daemon=True, name="JARVIS-Mobile-Command").start()
    return jsonify(ok=True, message=f'Command sent to laptop: “{text}”. Check the laptop for the result or voice response.')

if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "<laptop-local-IP>"
    print("\n" + "=" * 52)
    print("JARVIS MOBILE REMOTE")
    print("Open this address on your phone (same Wi-Fi):")
    print(f"  http://{local_ip}:8765")
    print(f"Mobile access PIN: {ACCESS_PIN}")
    print("Keep this PIN private. Stop server with Ctrl+C.")
    print("=" * 52 + "\n")
    APP.run(host="0.0.0.0", port=8765, debug=False, threaded=True)
