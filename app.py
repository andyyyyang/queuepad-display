from flask import Flask, render_template, jsonify, request, redirect, url_for, send_file
import requests, os, threading, time, urllib.parse, socket, math
from io import BytesIO
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------------- 路徑設定 ----------------
ADS_FOLDER   = os.path.join(app.static_folder, "ads")
AUDIO_FOLDER = os.path.join(app.static_folder, "audio")
PRINT_FOLDER = os.path.join(app.static_folder, "print")
os.makedirs(ADS_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)
os.makedirs(PRINT_FOLDER, exist_ok=True)

ORDER_FILE        = os.path.join(ADS_FOLDER, "order.txt")
CONFIG_FILE       = os.path.join(ADS_FOLDER, "ads_config.txt")            # muted/unmuted
VOICE_CONFIG_FILE = os.path.join(AUDIO_FOLDER, "voice_config.txt")        # on/off

QR_URL_FILE       = os.path.join(PRINT_FOLDER, "qr_url.txt")              # {number},{waiting}
PRINTER_IP_FILE   = os.path.join(PRINT_FOLDER, "printer_ip.txt")          # 例如 192.168.0.151
PRINT_BG_FILE     = os.path.join(PRINT_FOLDER, "bg.jpg")                  # 票面滿版背景(16:9 cover)
SERVER_URL_FILE   = os.path.join(PRINT_FOLDER, "server_url.txt")          # 伺服器網址
PRINT_COUNT_FILE  = os.path.join(PRINT_FOLDER, "print_count.txt")         # 預設列印張數

# 你的上游叫號狀態 API
# API_URL 已被 get_server_url() 函數取代，可通過設定頁面配置

# 熱敏機最大寬度(點)；多數 80mm 機種為 576，可依實際機型微調
PRINTER_MAX_DOTS = int(os.getenv("PRINTER_MAX_DOTS", "384"))

# ---------------- 中文數字 ----------------
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
            return s + "零" + digits[rem]
        else:
            tens, ones = divmod(rem, 10)
            if tens == 0:
                return s + "零" + digits[ones]
            elif ones == 0:
                return s + digits[tens] + "十"
            else:
                return s + digits[tens] + "十" + digits[ones]

# ---------------- 語音 ----------------
def generate_audio(n: int, save_path: str):
    text = f"請 {num_to_chinese(n)} 號取餐"
    gTTS(text=text, lang="zh-tw").save(save_path)
    print(f"[生成音檔] {n}")

def cleanup_audio(keep_numbers):
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

# ---------------- 狀態設定（語音/影片） ----------------
def get_voice_enabled():
    return os.path.exists(VOICE_CONFIG_FILE) and open(VOICE_CONFIG_FILE).read().strip() == "on"

def set_voice_enabled(enabled: bool):
    open(VOICE_CONFIG_FILE, "w").write("on" if enabled else "off")

def get_muted():
    return os.path.exists(CONFIG_FILE) and open(CONFIG_FILE).read().strip() == "muted"

def set_muted(muted: bool):
    open(CONFIG_FILE, "w").write("muted" if muted else "unmuted")

# ---------------- 列印設定 ----------------
def get_qr_url_template():
    return open(QR_URL_FILE).read().strip() if os.path.exists(QR_URL_FILE) \
        else "https://example.com/?no={number}&waiting={waiting}"

def set_qr_url_template(url: str):
    open(QR_URL_FILE, "w").write(url.strip())

def get_printer_ip():
    return open(PRINTER_IP_FILE).read().strip() if os.path.exists(PRINTER_IP_FILE) \
        else "192.168.0.151"

def set_printer_ip(ip: str):
    open(PRINTER_IP_FILE, "w").write(ip.strip())

def get_server_url():
    return open(SERVER_URL_FILE).read().strip() if os.path.exists(SERVER_URL_FILE) \
        else "https://ticket-server-246181962314.asia-east1.run.app/status"

def set_server_url(url: str):
    open(SERVER_URL_FILE, "w").write(url.strip())

def get_print_count():
    try:
        return int(open(PRINT_COUNT_FILE).read().strip()) if os.path.exists(PRINT_COUNT_FILE) else 1
    except:
        return 1

def set_print_count(count: int):
    open(PRINT_COUNT_FILE, "w").write(str(count))

def save_print_bg(file_storage):
    # 將上傳圖轉成 1280x720 的 cover 圖
    img = Image.open(file_storage.stream).convert("RGB")
    target_w, target_h = 720, 1280
    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - target_w)//2, (nh - target_h)//2
    img = img.crop((left, top, left + target_w, top + target_h))
    img.save(PRINT_BG_FILE, "JPEG", quality=92)
    print("[列印背景] 已更新")

# ---------------- 票面合成 ----------------
def _load_font(size: int):
    # 專案自帶字體
    proj_font = os.path.join(app.static_folder, "fonts", "NotoSansTC-SemiBold.ttf")
    if os.path.exists(proj_font):
        try:
            return ImageFont.truetype(proj_font, size)
        except Exception as e:
            print("[字體載入失敗]", e)

    # 如果自帶字體失敗，才退回系統字體
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansTC-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/PingFang.ttc"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except:
                pass

    # 最後 fallback
    return ImageFont.load_default()



def build_qr_img(number: int, waiting: int):
    # 使用線上服務產 QR（PNG）
    url_tpl = get_qr_url_template()
    final_url = url_tpl.format(number=number, waiting=waiting)
    # 改善 QR code 品質：增加尺寸、邊距，使用更高解析度
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=800x800&format=png&margin=2&ecc=M&data={urllib.parse.quote(final_url, safe='')}"
    r = requests.get(qr_url, timeout=6)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def draw_centered_text(draw, text, font, y, fill=(0,0,0), canvas_width=384):
    """在指定 y 座標，文字水平置中繪製"""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (canvas_width - text_w) // 2
    draw.text((x, y), text, font=font, fill=fill)


def compose_ticket_image(number: int, waiting: int):
    # 58mm 出單機：寬度 384 dots，高度 640
    W, H = 384, 640
    canvas = Image.new("RGB", (W, H), (255, 255, 255))

    # 背景 (cover 到 384x640)
    if os.path.exists(PRINT_BG_FILE):
        bg = Image.open(PRINT_BG_FILE).convert("RGB")
        sw, sh = bg.size
        scale = max(W / sw, H / sh)
        bg = bg.resize((int(sw * scale), int(sh * scale)), Image.LANCZOS)

        # === 調整垂直偏移 ===
        vertical_offset = 0   # 負數=往上移，正數=往下移
        left = (bg.size[0] - W) // 2
        top  = (bg.size[1] - H) // 2 + vertical_offset

        # 避免超界
        if top < 0:
            top = 0
        if top + H > bg.size[1]:
            top = bg.size[1] - H

        bg = bg.crop((left, top, left + W, top + H))
        canvas.paste(bg, (0, 0))

    draw = ImageDraw.Draw(canvas)
    f_big = _load_font(90)
    f_mid = _load_font(20)

    # 號碼置中
    draw_centered_text(draw, str(number), f_big, 140, fill=(255, 255, 255), canvas_width=W)

    # 等候人數置中
    draw_centered_text(draw, f"目前 {waiting} 人等候中", f_mid, 290, fill=(0, 0, 0), canvas_width=W)

    # QR code 底部留白
    qr = build_qr_img(number, waiting)
    qr_size = int(W * 0.45)  # 保持原本尺寸
    qr = qr.resize((qr_size, qr_size), Image.LANCZOS)
    qr_x = (W - qr_size) // 2
    qr_y = H - qr_size - 100  # 保持原本位置
    canvas.paste(qr, (qr_x, qr_y), qr)

    out_path = os.path.join(PRINT_FOLDER, f"ticket_{number}.png")
    canvas.save(out_path, "PNG")
    return out_path




# ---------------- 影像 → ESC/POS (GS v 0) ----------------
def _img_to_1bpp(img: Image.Image, target_width=PRINTER_MAX_DOTS, high_quality=False) -> Image.Image:
    # 轉寬度到印表機最大，等比縮放；二值化成黑白
    w, h = img.size
    if w != target_width:
        nh = int(h * (target_width / w))
        img = img.resize((target_width, nh), Image.LANCZOS)
    
    # 高品質模式：保持原本尺寸，只優化處理參數
    if high_quality:
        # 不進行尺寸調整，只優化處理參數
        pass
    
    img = img.convert("L")
    
    # 改善二值化處理：使用更寬鬆的閾值，並加入銳化處理
    # 先進行銳化處理
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.5)  # 銳化 1.5 倍
    
    # 使用更寬鬆的閾值，並加入抖動處理
    if high_quality:
        threshold = 105  # 高品質模式使用更寬鬆的閾值
    else:
        threshold = 110  # 標準模式
    
    img = img.point(lambda x: 0 if x < threshold else 255, '1')
    
    return img  # mode '1'

def _pack_bits_raster(img_1b: Image.Image) -> bytes:
    # 依 GS v 0 Raster 格式（每列打包成 bytes）
    w, h = img_1b.size
    width_bytes = (w + 7) // 8
    out = bytearray(width_bytes * h)
    px = img_1b.load()
    o = 0
    for y in range(h):
        for xb in range(width_bytes):
            b = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w:
                    # mode '1'：黑=0，白=255；黑點要印 → bit=1
                    if px[x, y] == 0:
                        b |= (1 << (7 - bit))
            out[o] = b
            o += 1
    return bytes(out), width_bytes, h

def _send_escpos_raster(ip: str, img: Image.Image):
    """使用 GS v 0 raster bit image 列印 (相容 XPrinter 58mm)"""
    try:
        target_width = 384
        w, h = img.size
        if w != target_width:
            nh = int(h * (target_width / w))
            img = img.resize((target_width, nh), Image.LANCZOS)

        # 黑白化
        img = img.convert("L")
        img = img.point(lambda x: 0 if x < 128 else 255, '1')

        px = img.load()
        width_bytes = target_width // 8
        height = h

        # 打包像素
        raster = bytearray()
        for y in range(height):
            for xb in range(width_bytes):
                b = 0
                for bit in range(8):
                    x = xb * 8 + bit
                    if px[x, y] == 0:
                        b |= (1 << (7 - bit))
                raster.append(b)

        # ESC/POS 指令
        init = b'\x1B\x40'            # 初始化
        line_spacing = b'\x1B\x32'    # 標準行距
        xL = width_bytes & 0xFF
        xH = (width_bytes >> 8) & 0xFF
        yL = height & 0xFF
        yH = (height >> 8) & 0xFF
        header = b'\x1D\x76\x30\x00' + bytes([xL, xH, yL, yH])  # GS v 0
        feed_cut = b'\n\n\n' + b'\x1D\x56\x00'  # 走紙 + 切紙

        # 傳送
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((ip, 9100))
            s.sendall(init + line_spacing + header + raster + feed_cut)
            return True

    except Exception as e:
        print(f"[GS v 0 列印失敗] {e}")
        return False




def print_ticket(number: int, waiting: int, count: int = None):
    """合成票面 → 送到 XPrinter (9100)"""
    if count is None:
        count = get_print_count()
    
    try:
        ip = get_printer_ip()
        img_path = compose_ticket_image(number, waiting)
        img = Image.open(img_path)
        
        # 列印指定張數
        for i in range(count):
            success = _send_escpos_raster(ip, img)
            if not success:
                print(f"[列印失敗] {number}")
                return
            
            if i < count - 1:
                time.sleep(0.5)
        
        print(f"[列印成功] {number} x{count}張")

    except Exception as e:
        print("[列印失敗]", e)

def _test_printer_connection(ip: str):
    """測試印表機連線和基本功能"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((ip, 9100))
            
            # 發送簡單的測試指令
            test_command = b'\x1B\x40' + b'\x1B\x32' + b'Test Print\n\n\n' + b'\x1D\x56\x00'
            s.sendall(test_command)
            
            print(f"[印表機測試] 連線成功，發送測試指令")
            return True
            
    except Exception as e:
        print(f"[印表機測試] 連線失敗: {e}")
        return False

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
PRINTED_FILE = os.path.join(PRINT_FOLDER, "printed.log")

def load_printed_numbers():
    if os.path.exists(PRINTED_FILE):
        with open(PRINTED_FILE) as f:
            return set(int(x.strip()) for x in f if x.strip().isdigit())
    return set()

def save_printed_number(n: int):
    with open(PRINTED_FILE, "a") as f:
        f.write(f"{n}\n")

def has_printed(n: int) -> bool:
    """檢查號碼是否已經列印過"""
    if n in PRINTED_NUMBERS:
        return True
    # 如果記憶體沒有，就再查 log
    if os.path.exists(PRINTED_FILE):
        with open(PRINTED_FILE) as f:
            for line in f:
                try:
                    if int(line.strip()) == n:
                        PRINTED_NUMBERS.add(n)  # 補進記憶體快取
                        return True
                except:
                    continue
    return False


PRINTED_NUMBERS = load_printed_numbers()
LAST_WAITING = set()

def monitor_waiting():
    global LAST_WAITING, PRINTED_NUMBERS

    while True:
        try:
            # 使用可設定的伺服器網址
            server_url = get_server_url()
            r = requests.get(server_url, timeout=3)
            data = r.json()
            waiting = data.get("waiting", []) or []
            current = data.get("current")
            # === 偵測從 1 開始 ===
            if waiting and min(waiting) == 1:
                clear_logs_and_prints()

            if current is None and waiting:
                current = waiting[0]

            keep_numbers = set(waiting)
            if current is not None:
                keep_numbers.add(current)

            new_numbers = set(waiting) - LAST_WAITING

            # === 列印新號碼 (一定要查 log) ===
            for n in sorted(new_numbers):
                if not has_printed(int(n)):
                    try:
                        print_ticket(int(n), len(waiting))
                        PRINTED_NUMBERS.add(n)
                        save_printed_number(n)   # 寫入 log
                    except Exception as e:
                        print("[列印新號碼失敗]", n, e)

            # === 生成語音 ===
            for n in sorted(new_numbers):
                try:
                    path = os.path.join(AUDIO_FOLDER, f"{n}.mp3")
                    if not os.path.exists(path):
                        generate_audio(int(n), path)
                except Exception as e:
                    print("[生成語音失敗]", n, e)

            cleanup_audio(keep_numbers)
            LAST_WAITING = set(waiting)

        except Exception as e:
            print("[監控錯誤]", e)

        time.sleep(2)



def clear_logs_and_prints():
    # 清空 printed.log
    if os.path.exists(PRINTED_FILE):
        os.remove(PRINTED_FILE)
    # 清空暫存的票面圖片
    for f in os.listdir(PRINT_FOLDER):
        if f.startswith("ticket_") and f.endswith(".png"):
            try:
                os.remove(os.path.join(PRINT_FOLDER, f))
            except:
                pass
    print("[清理] 已清除 printed.log 和票面圖片")
    # 重置記憶體紀錄
    global PRINTED_NUMBERS
    PRINTED_NUMBERS = set()


# ---------------- API ----------------
@app.route("/api/refresh")
def api_refresh():
    """控制樹莓派 Chromium 瀏覽器更新的 API"""
    try:
        # 方法1: 使用 xdotool 發送 F5 鍵到 Chromium 視窗
        import subprocess
        
        # 嘗試使用 xdotool 發送 F5 鍵到 Chromium
        try:
            # 查找 Chromium 視窗並發送 F5 鍵
            subprocess.run([
                "xdotool", "search", "--name", "Chromium", "key", "F5"
            ], check=True, capture_output=True, timeout=5)
            print("[Chromium 刷新] 使用 xdotool 發送 F5 鍵成功")
            return jsonify({
                "status": "success", 
                "method": "xdotool_F5",
                "message": "Chromium 已刷新 (F5)"
            })
        except subprocess.CalledProcessError:
            print("[Chromium 刷新] xdotool 發送 F5 失敗，嘗試其他方法")
        except FileNotFoundError:
            print("[Chromium 刷新] xdotool 未安裝，嘗試其他方法")
        
        # 方法2: 使用 wmctrl 和 xdotool 組合
        try:
            # 先找到 Chromium 視窗，然後發送 F5
            result = subprocess.run([
                "wmctrl", "-l"
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'chromium' in line.lower() or 'chrome' in line.lower():
                        # 提取視窗 ID
                        window_id = line.split()[0]
                        # 發送 F5 鍵到該視窗
                        subprocess.run([
                            "xdotool", "windowactivate", window_id, "key", "F5"
                        ], check=True, capture_output=True, timeout=5)
                        print(f"[Chromium 刷新] 使用 wmctrl + xdotool 成功，視窗 ID: {window_id}")
                        return jsonify({
                            "status": "success", 
                            "method": "wmctrl_xdotool",
                            "window_id": window_id,
                            "message": "Chromium 已刷新 (F5)"
                        })
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("[Chromium 刷新] wmctrl 方法失敗")
        
        # 方法3: 使用 pkill 重啟 Chromium 進程
        try:
            # 檢查是否有 Chromium 進程在運行
            result = subprocess.run([
                "pgrep", "-f", "chromium"
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                # 殺死 Chromium 進程
                subprocess.run([
                    "pkill", "-f", "chromium"
                ], check=True, capture_output=True, timeout=5)
                
                # 等待一下再重啟
                import time
                time.sleep(2)
                
                # 重啟 Chromium（假設在 kiosk 模式下運行）
                subprocess.Popen([
                    "chromium-browser", "--kiosk", "--disable-web-security", 
                    "--user-data-dir=/tmp/chromium-kiosk"
                ], start_new_session=True)
                
                print("[Chromium 刷新] 使用 pkill 重啟成功")
                return jsonify({
                    "status": "success", 
                    "method": "pkill_restart",
                    "message": "Chromium 已重啟"
                })
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("[Chromium 刷新] pkill 方法失敗")
        
        # 如果所有方法都失敗，返回錯誤
        return jsonify({
            "status": "error",
            "message": "無法刷新 Chromium，請檢查系統環境"
        }), 500
        
    except Exception as e:
        print(f"[Chromium 刷新錯誤] {e}")
        return jsonify({
            "status": "error",
            "message": f"刷新過程中發生錯誤: {str(e)}"
        }), 500

@app.route("/api/close_chromium")
def close_chromium():
    """控制樹莓派關閉 Chromium 瀏覽器的 API"""
    try:
        import subprocess
        
        # 方法1: 使用 pkill 關閉所有 Chromium 進程
        try:
            # 檢查是否有 Chromium 進程在運行
            result = subprocess.run([
                "pgrep", "-f", "chromium"
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                # 獲取進程數量
                process_count = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
                
                # 使用 pkill 關閉所有 Chromium 進程
                subprocess.run([
                    "pkill", "-f", "chromium"
                ], check=True, capture_output=True, timeout=10)
                
                print(f"[Chromium 關閉] 成功關閉 {process_count} 個 Chromium 進程")
                return jsonify({
                    "status": "success",
                    "method": "pkill",
                    "processes_closed": process_count,
                    "message": f"已關閉 {process_count} 個 Chromium 進程"
                })
            else:
                print("[Chromium 關閉] 沒有找到運行中的 Chromium 進程")
                return jsonify({
                    "status": "info",
                    "message": "沒有運行中的 Chromium 進程"
                })
                
        except subprocess.CalledProcessError as e:
            print(f"[Chromium 關閉] pkill 執行失敗: {e}")
            # 嘗試使用 killall 作為備用方法
            try:
                subprocess.run([
                    "killall", "chromium-browser"
                ], check=True, capture_output=True, timeout=10)
                
                print("[Chromium 關閉] 使用 killall 成功關閉")
                return jsonify({
                    "status": "success",
                    "method": "killall",
                    "message": "已關閉 Chromium 瀏覽器"
                })
            except subprocess.CalledProcessError:
                print("[Chromium 關閉] killall 也失敗")
                
        except FileNotFoundError:
            print("[Chromium 關閉] 系統命令不可用")
            return jsonify({
                "status": "error",
                "message": "系統命令不可用，無法關閉 Chromium"
            }), 500
        
        # 如果所有方法都失敗
        return jsonify({
            "status": "error",
            "message": "無法關閉 Chromium，請檢查系統權限"
        }), 500
        
    except Exception as e:
        print(f"[Chromium 關閉錯誤] {e}")
        return jsonify({
            "status": "error",
            "message": f"關閉過程中發生錯誤: {str(e)}"
        }), 500

@app.route("/api/status")
def status():
    try:
        # 使用可設定的伺服器網址
        server_url = get_server_url()
        r = requests.get(server_url, timeout=3)
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
            generate_audio(n, path)  # 即時補檔（避免 race）
        except Exception as e:
            return jsonify({"error": f"gTTS failed: {e}"}), 500
    return send_file(path, mimetype="audio/mpeg")

@app.route("/api/ads")
def api_ads():
    return {"ads": [f"/static/ads/{f}" for f in get_ads()]}

@app.route("/api/muted")
def api_muted():
    return {"muted": get_muted()}

# （可選）後台測試列印
@app.route("/api/print_test", methods=["POST"])
def api_print_test():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        # 獲取要列印的張數
        count = request.args.get("count")
        if count and count.isdigit():
            count = int(count)
        else:
            count = get_print_count()
        
        r = requests.get(get_server_url(), timeout=3)
        data = r.json()
        wlen = len(data.get("waiting", []) or [])
    except:
        wlen = 0
    
    print_ticket(999, wlen, count)
    return jsonify({"ok": True, "printed_count": count})

# 測試端點
@app.route("/api/test", methods=["GET", "POST"])
def api_test():
    return jsonify({
        "method": request.method,
        "args": dict(request.args),
        "form": dict(request.form),
        "json": request.get_json(silent=True),
        "message": "測試端點正常工作"
    })

# 測試打印品質
@app.route("/api/test_print_quality", methods=["POST"])
def api_test_print_quality():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        data = request.json or {}
        number = data.get("number", 999)
        waiting = data.get("waiting", 5)
        count = data.get("count", 1)
        high_quality = data.get("high_quality", True)
        
        print_ticket(number, waiting, count)
        
        quality_text = "高品質" if high_quality else "標準"
        return jsonify({
            "ok": True, 
            "message": f"測試列印完成 ({quality_text})",
            "number": number,
            "waiting": waiting,
            "count": count,
            "quality": quality_text
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存伺服器網址
@app.route("/api/save_server_url", methods=["POST"])
def api_save_server_url():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        server_url = request.json.get("server_url")
        if server_url:
            set_server_url(server_url)
            return jsonify({"ok": True, "message": "伺服器網址已保存"})
        else:
            return jsonify({"error": "伺服器網址不能為空"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存列印設定
@app.route("/api/save_print_settings", methods=["POST"])
def api_save_print_settings():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        data = request.json
        saved_items = []
        
        if "qr_url" in data:
            set_qr_url_template(data["qr_url"])
            saved_items.append("QR Code 網址模板")
        
        if "printer_ip" in data:
            set_printer_ip(data["printer_ip"])
            saved_items.append("印表機 IP")
        
        if "print_count" in data:
            try:
                count = int(data["print_count"])
                if 1 <= count <= 10:
                    set_print_count(count)
                    saved_items.append("列印張數")
                else:
                    return jsonify({"error": "列印張數必須在 1-10 之間"}), 400
            except:
                return jsonify({"error": "列印張數必須是數字"}), 400
        
        if saved_items:
            return jsonify({"ok": True, "message": f"已保存：{', '.join(saved_items)}"})
        else:
            return jsonify({"error": "沒有要保存的設定"}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存背景圖片
@app.route("/api/save_print_bg", methods=["POST"])
def api_save_print_bg():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        if "print_bg" in request.files and request.files["print_bg"].filename:
            save_print_bg(request.files["print_bg"])
            return jsonify({"ok": True, "message": "背景圖片已保存"})
        else:
            return jsonify({"error": "沒有選擇檔案"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存 QR Code 網址模板
@app.route("/api/save_qr_url", methods=["POST"])
def api_save_qr_url():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        qr_url = request.args.get("qr_url")
        if qr_url:
            set_qr_url_template(qr_url)
            return jsonify({"ok": True, "qr_url": qr_url, "message": "QR Code 網址模板已保存"})
        else:
            return jsonify({"error": "QR Code 網址不能為空"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存印表機 IP
@app.route("/api/save_printer_ip", methods=["POST"])
def api_save_printer_ip():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        printer_ip = request.args.get("printer_ip")
        if printer_ip:
            set_printer_ip(printer_ip)
            return jsonify({"ok": True, "printer_ip": printer_ip, "message": "印表機 IP 已保存"})
        else:
            return jsonify({"error": "印表機 IP 不能為空"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 保存列印張數
@app.route("/api/save_print_count", methods=["POST"])
def api_save_print_count():
    print(f"[DEBUG] 收到保存列印張數請求")
    print(f"[DEBUG] 請求方法: {request.method}")
    print(f"[DEBUG] 請求參數: {request.args}")
    print(f"[DEBUG] 請求表單: {request.form}")
    print(f"[DEBUG] 請求 JSON: {request.get_json(silent=True)}")
    
    if request.args.get("pw") != "yellowgirl":
        return jsonify({"error": "未授權"}), 403
    
    try:
        # 嘗試從不同來源獲取列印張數
        print_count = None
        
        # 從 URL 參數獲取
        if request.args.get("print_count"):
            print_count = request.args.get("print_count")
            print(f"[DEBUG] 從 URL 參數獲取: {print_count}")
        
        # 從表單數據獲取
        elif request.form.get("print_count"):
            print_count = request.form.get("print_count")
            print(f"[DEBUG] 從表單數據獲取: {print_count}")
        
        # 從 JSON 獲取
        elif request.get_json(silent=True) and request.get_json().get("print_count"):
            print_count = request.get_json().get("print_count")
            print(f"[DEBUG] 從 JSON 獲取: {print_count}")
        
        if not print_count:
            return jsonify({"error": "缺少列印張數參數"}), 400
            
        if not str(print_count).isdigit():
            return jsonify({"error": "列印張數必須是數字"}), 400
            
        count = int(print_count)
        print(f"[DEBUG] 解析後的列印張數: {count}")
        
        if count < 1 or count > 10:
            return jsonify({"error": "列印張數必須在 1-10 之間"}), 400
        
        set_print_count(count)
        print(f"[DEBUG] 列印張數已保存: {count}")
        
        return jsonify({
            "ok": True, 
            "print_count": count, 
            "message": f"列印張數已保存為 {count} 張"
        })
        
    except Exception as e:
        print(f"[ERROR] 保存列印張數時發生錯誤: {e}")
        return jsonify({"error": f"保存失敗: {str(e)}"}), 500

# 上傳背景圖片
@app.route("/api/upload_print_bg", methods=["POST"])
def api_upload_print_bg():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    try:
        if "file" in request.files and request.files["file"].filename:
            file = request.files["file"]
            if file.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                save_print_bg(file)
                return jsonify({"success": True, "filename": file.filename, "message": "背景圖片上傳成功"})
            else:
                return jsonify({"success": False, "message": "只支援 JPG、PNG、GIF 格式"}), 400
        else:
            return jsonify({"success": False, "message": "沒有選擇檔案"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------- 頁面 ----------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ads", methods=["GET", "POST"])
def ads_page():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403

    if request.method == "POST":
        # 上傳影片
        if "file" in request.files and request.files["file"].filename:
            file = request.files["file"]
            if file.filename.lower().endswith(".mp4"):
                file.save(os.path.join(ADS_FOLDER, file.filename))
                files = get_ads()
                if file.filename not in files:
                    files.append(file.filename)
                    save_order(files)
                return redirect(url_for("ads_page", pw="yellowgirl"))

        # 列印設定
        if "qr_url" in request.form:
            set_qr_url_template(request.form["qr_url"])
        if "printer_ip" in request.form:
            set_printer_ip(request.form["printer_ip"])
        if "server_url" in request.form:
            set_server_url(request.form["server_url"])
        if "print_count" in request.form:
            try:
                count = int(request.form["print_count"])
                if count > 0 and count <= 10:  # 限制最大列印張數為 10
                    set_print_count(count)
            except:
                pass
        if "print_bg" in request.files and request.files["print_bg"].filename:
            try:
                save_print_bg(request.files["print_bg"])
            except Exception as e:
                print("[背景圖更新失敗]", e)

        return redirect(url_for("ads_page", pw="yellowgirl"))

    files = get_ads()
    return render_template(
        "ads.html",
        files=files,
        get_muted=get_muted(),
        voice_enabled=get_voice_enabled(),
        qr_url=get_qr_url_template(),
        printer_ip=get_printer_ip(),
        server_url=get_server_url(),
        print_count=get_print_count(),
        has_print_bg=os.path.exists(PRINT_BG_FILE)
    )

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

@app.route("/ads/clear_cache")
def clear_cache():
    if request.args.get("pw") != "yellowgirl":
        return "Unauthorized", 403
    clear_logs_and_prints()
    return redirect(url_for("ads_page", pw="yellowgirl"))

# ---------------- 啟動 ----------------
if __name__ == "__main__":
    print("[系統啟動] 啟動 Flask + 監控線程 (僅一次)")
    t = threading.Thread(target=monitor_waiting, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)


