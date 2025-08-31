from flask import Flask, render_template, jsonify, request, redirect, url_for, send_file
import requests, os, threading, time
from gtts import gTTS

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------------- 路徑設定 ----------------
ADS_FOLDER = os.path.join(app.static_folder, "ads")
AUDIO_FOLDER = os.path.join(app.static_folder, "audio")
os.makedirs(ADS_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)

ORDER_FILE = os.path.join(ADS_FOLDER, "order.txt")
CONFIG_FILE = os.path.join(ADS_FOLDER, "ads_config.txt")        # muted/unmuted
VOICE_CONFIG_FILE = os.path.join(AUDIO_FOLDER, "voice_config.txt")  # on/off

API_URL = os.getenv("API_URL", "https://ticket-server-246181962314.asia-east1.run.app/status")

# ---------------- 工具 ----------------
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
        if rem == 0:
            return s
        elif rem < 10:
            # 101, 102... → 要補零
            return s + "零" + digits[rem]
        elif rem < 100:
            tens, ones = divmod(rem, 10)
            if tens == 0:
                # 105 → 一百零五
                return s + "零" + digits[ones]
            elif ones == 0:
                # 110, 120 → 一百一十、一百二十
                return s + digits[tens] + "十"
            else:
                # 111 → 一百一十一
                return s + digits[tens] + "十" + digits[ones]
        else:
            return s + num_to_chinese(rem)

def generate_audio(n: int, save_path: str):
    """生成音檔並存檔"""
    text = f"請 {num_to_chinese(n)} 號取餐"
    gTTS(text=text, lang="zh-tw").save(save_path)
    print(f"[生成音檔] {n}")

def cleanup_audio(keep_numbers):
    """刪除不在 keep_numbers 裡的檔案"""
    keep = {int(x) for x in keep_numbers if str(x).isdigit()}
    for f in os.listdir(AUDIO_FOLDER):
        if f.endswith(".mp3"):
            try:
                num = int(os.path.splitext(f)[0])
                if num not in keep:
                    os.remove(os.path.join(AUDIO_FOLDER, f))
                    print(f"[刪除音檔] {num}")
            except:
                continue

# ---------------- 設定檔 ----------------
def get_voice_enabled():
    if os.path.exists(VOICE_CONFIG_FILE):
        return open(VOICE_CONFIG_FILE).read().strip() == "on"
    return False

def set_voice_enabled(enabled: bool):
    with open(VOICE_CONFIG_FILE, "w") as f:
        f.write("on" if enabled else "off")

def get_muted():
    if os.path.exists(CONFIG_FILE):
        return open(CONFIG_FILE).read().strip() == "muted"
    return True

def set_muted(muted: bool):
    with open(CONFIG_FILE, "w") as f:
        f.write("muted" if muted else "unmuted")

# ---------------- Ads ----------------
def get_ads():
    files = [f for f in os.listdir(ADS_FOLDER) if f.lower().endswith(".mp4")]
    if os.path.exists(ORDER_FILE):
        order = [x.strip() for x in open(ORDER_FILE).read().splitlines() if x.strip()]
        files = [f for f in order if f in files] + [f for f in files if f not in order]
    return files

def save_order(files):
    with open(ORDER_FILE, "w") as f:
        f.write("\n".join(files))

# ---------------- 背景監控 ----------------
LAST_WAITING = set()

def monitor_waiting():
    global LAST_WAITING
    while True:
        try:
            r = requests.get(API_URL, timeout=3)
            data = r.json()
            waiting = data.get("waiting", []) or []
            current = data.get("current")

            if current is None and waiting:
                current = waiting[0]

            # 本輪要保留的號碼集合
            keep_numbers = set(waiting)
            if current is not None:
                keep_numbers.add(current)

            # 新號碼：只針對 waiting（避免 current 重複生成）
            new_numbers = set(waiting) - LAST_WAITING
            for n in new_numbers:
                try:
                    path = os.path.join(AUDIO_FOLDER, f"{n}.mp3")
                    if not os.path.exists(path):
                        generate_audio(int(n), path)
                except Exception as e:
                    print("[生成失敗]", n, e)

            cleanup_audio(keep_numbers)
            LAST_WAITING = set(waiting)

        except Exception as e:
            print("[監控錯誤]", e)
        time.sleep(2)

# ---------------- API ----------------
@app.route("/api/refresh")
def api_refresh():
    return jsonify({"status": "ok"})

@app.route("/api/status")
def status():
    try:
        r = requests.get(API_URL, timeout=3)
        data = r.json()
        waiting = data.get("waiting", []) or []
        current = data.get("current")
        if current is None and waiting:
            current = waiting[0]
        return jsonify({"current": current, "waiting": waiting})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/speak/<number>")
def speak(number):
    if not get_voice_enabled():
        return jsonify({"error": "voice disabled"}), 403
    try:
        n = int(number)
    except:
        return jsonify({"error": "invalid number"}), 400

    path = os.path.join(AUDIO_FOLDER, f"{n}.mp3")
    if not os.path.exists(path):
        try:
            generate_audio(n, path)  # 即時生成並存檔
        except Exception as e:
            return jsonify({"error": f"gTTS failed: {e}"}), 500

    return send_file(path, mimetype="audio/mpeg")

@app.route("/api/ads")
def api_ads():
    return {"ads": [f"/static/ads/{f}" for f in get_ads()]}

@app.route("/api/muted")
def api_muted():
    return {"muted": get_muted()}

# ---------------- 頁面 ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ads", methods=["GET", "POST"])
def ads_page():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    if request.method == "POST" and "file" in request.files:
        file = request.files["file"]
        if file.filename.lower().endswith(".mp4"):
            file.save(os.path.join(ADS_FOLDER, file.filename))
    return render_template("ads.html", files=get_ads(), get_muted=get_muted(), voice_enabled=get_voice_enabled())

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

@app.route("/ads/toggle_mute")
def toggle_mute():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    set_muted(not get_muted())
    return redirect(url_for("ads_page", pw="yellowgirl"))

@app.route("/ads/toggle_voice")
def toggle_voice():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    set_voice_enabled(not get_voice_enabled())
    return redirect(url_for("ads_page", pw="yellowgirl"))

# ---------------- 啟動 ----------------
if __name__ == "__main__":
    threading.Thread(target=monitor_waiting, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=True)
