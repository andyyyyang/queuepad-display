from flask import Flask, render_template, jsonify, request, redirect, url_for
import requests, os

app = Flask(__name__, template_folder="templates", static_folder="static")

ADS_FOLDER = os.path.join(app.static_folder, "ads")
AUDIO_FOLDER = os.path.join(app.static_folder, "audio")
os.makedirs(AUDIO_FOLDER, exist_ok=True)
ORDER_FILE = os.path.join(ADS_FOLDER, "order.txt")
os.makedirs(ADS_FOLDER, exist_ok=True)
CONFIG_FILE = os.path.join(ADS_FOLDER, "ads_config.txt")

# --- Utils ---
VOICE_CONFIG_FILE = os.path.join(AUDIO_FOLDER, "voice_config.txt")
from io import BytesIO

@app.route("/api/refresh")
def api_refresh():
    return jsonify({"status": "ok"})

import threading

MAX_VOICE_CACHE = 5  # 最多保留 5 個快取

def num_to_chinese(n: int) -> str:
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    elif n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    elif n < 100:
        tens, ones = divmod(n, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    else:
        hundreds, rem = divmod(n, 100)
        s = digits[hundreds] + "百"
        if rem:
            if rem < 10:
                s += digits[0] + digits[rem]
            else:
                s += num_to_chinese(rem)
        return s

def pre_generate(n: int):
    """背景下載下一號語音"""
    try:
        next_text = f"請 {num_to_chinese(n)} 號取餐"
        next_path = os.path.join(AUDIO_FOLDER, f"{n}.mp3")
        if not os.path.exists(next_path):
            gTTS(text=next_text, lang="zh-tw").save(next_path)

        # 保持快取不超過 MAX_VOICE_CACHE
        files = sorted(
            [f for f in os.listdir(AUDIO_FOLDER) if f.endswith(".mp3")],
            key=lambda x: os.path.getmtime(os.path.join(AUDIO_FOLDER, x))
        )
        while len(files) > MAX_VOICE_CACHE:
            old = files.pop(0)
            os.remove(os.path.join(AUDIO_FOLDER, old))
    except Exception as e:
        print("背景預生成語音失敗:", e)

@app.route("/api/speak/<number>")
def speak(number):
    if not get_voice_enabled():
        return jsonify({"error": "voice disabled"}), 403

    try:
        n = int(number)
        chinese_num = num_to_chinese(n)
    except:
        chinese_num = number

    text = f"請 {chinese_num} 號取餐"

    # 當前號碼 → 立刻產生在記憶體 (不卡)
    mp3_fp = BytesIO()
    gTTS(text=text, lang="zh-tw").write_to_fp(mp3_fp)
    mp3_fp.seek(0)

    # 背景產生下一號
    threading.Thread(target=pre_generate, args=(n+1,)).start()

    return send_file(mp3_fp, mimetype="audio/mpeg")




def get_voice_enabled():
    if os.path.exists(VOICE_CONFIG_FILE):
        with open(VOICE_CONFIG_FILE, "r") as f:
            return f.read().strip() == "on"
    return False  # 預設關閉

def set_voice_enabled(enabled: bool):
    with open(VOICE_CONFIG_FILE, "w") as f:
        f.write("on" if enabled else "off")

from gtts import gTTS
from flask import send_file

@app.route("/ads/toggle_voice")
def toggle_voice():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    enabled = not get_voice_enabled()
    set_voice_enabled(enabled)
    return redirect(url_for("ads_page", pw="yellowgirl"))

def get_muted():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return f.read().strip() == "muted"
    return True  # 預設靜音

def set_muted(muted: bool):
    with open(CONFIG_FILE, "w") as f:
        f.write("muted" if muted else "unmuted")

@app.route("/ads/toggle_mute")
def toggle_mute():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    muted = not get_muted()
    set_muted(muted)
    return redirect(url_for("ads_page", pw="yellowgirl"))

@app.route("/api/muted")
def api_muted():
    return {"muted": get_muted()}

def get_ads():
    files = [f for f in os.listdir(ADS_FOLDER) if f.endswith(".mp4")]
    if os.path.exists(ORDER_FILE):
        with open(ORDER_FILE, "r") as f:
            order = [line.strip() for line in f.readlines()]
        files = [f for f in order if f in files]  # 按順序過濾
    return files

def save_order(files):
    with open(ORDER_FILE, "w") as f:
        f.write("\n".join(files))

# --- Ads API ---
@app.route("/api/ads")
def api_ads():
    return {"ads": [f"/static/ads/{f}" for f in get_ads()]}

# --- Upload & Manage ---
@app.route("/ads", methods=["GET", "POST"])
def ads_page():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403

    if request.method == "POST":
        if "file" in request.files:
            file = request.files["file"]
            if file.filename.endswith(".mp4"):
                path = os.path.join(ADS_FOLDER, file.filename)
                file.save(path)
                files = get_ads()
                if file.filename not in files:
                    files.append(file.filename)
                    save_order(files)
        return redirect(url_for("ads_page", pw="yellowgirl"))

    files = get_ads()
    return render_template("ads.html", files=files, get_muted=get_muted(), voice_enabled=get_voice_enabled())


@app.route("/ads/delete/<name>")
def delete_ad(name):
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    path = os.path.join(ADS_FOLDER, name)
    if os.path.exists(path):
        os.remove(path)
    files = get_ads()
    if name in files:
        files.remove(name)
        save_order(files)
    return redirect(url_for("ads_page", pw="yellowgirl"))

@app.route("/ads/move/<name>/<direction>")
def move_ad(name, direction):
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    files = get_ads()
    if name in files:
        idx = files.index(name)
        if direction == "up" and idx > 0:
            files[idx], files[idx-1] = files[idx-1], files[idx]
        elif direction == "down" and idx < len(files)-1:
            files[idx], files[idx+1] = files[idx+1], files[idx]
        save_order(files)
    return redirect(url_for("ads_page", pw="yellowgirl"))

# --- QueuePad UI ---
API_URL = os.getenv("API_URL", "https://ticket-server-246181962314.asia-east1.run.app/status")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def status():
    try:
        r = requests.get(API_URL, timeout=5)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
