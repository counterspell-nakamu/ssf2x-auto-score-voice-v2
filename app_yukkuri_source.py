import os
import time
import socket
import random
import threading
import traceback
import cv2
import numpy as np

from windows_capture import WindowsCapture, Frame, InternalCaptureControl

# ==========================================
# --- キャプチャ対象ウィンドウの設定 ---
# ==========================================
WINDOW_TITLE_KEYWORD = "ゲームキャプチャ"

# ==========================================
# --- 判定パラメータ設定 ---
# ==========================================
THRESHOLD = 0.50
COOLDOWN_SECONDS = 3.0 

SCALE_MIN = 0.4
SCALE_MAX = 1.2
SCALE_STEP = 0.1

ROI_Y1_RATIO = 0.05
ROI_Y2_RATIO = 0.35

SHOW_DEBUG_WINDOW = True

RANDOM_PHRASES = [
    "こあしみてからしょうりゅうよゆうでした！",
    "いいよー！みてからだよー！",
    "まぁかくりつだからねー！",
    "スタブおおふな！",
    "しょけんはまけねーよーっ！",
    "いいでしょう！",
    "マジやべーっす！",
    "わざがでない！",
    "あるよー！",
    "たのむ！",
    "だいたいわかった！"
]

TARGET_NUM = 10
GAME_MODE = "FIRST_TO"
P1_NAME = "1P"
P2_NAME = "2P"
DISABLE_RANDOM = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(BASE_DIR, 'stars2.png')
set_score_path = os.path.join(BASE_DIR, 'set_score.txt')
scores_path = os.path.join(BASE_DIR, 'scores.txt')

_frame_lock = threading.Lock()
_latest_frame = None
_last_capture_time = 0.0

def imread_japanese(filepath, flags=cv2.IMREAD_COLOR):
    try:
        buf = np.fromfile(filepath, dtype=np.uint8)
        return cv2.imdecode(buf, flags)
    except Exception as e:
        print(f"画像読み込み例外: {e}")
        return None

def speak_bouyomi(text, allow_random=False):
    if not text:
        return
    if allow_random and not DISABLE_RANDOM and random.randint(1, 1) == 1:
        extra = random.choice(RANDOM_PHRASES)
        text = f"{text} {extra}"
    try:
        print(f"[ゆっくり発話] {text}")
        host = '127.0.0.1'
        port = 50001
        command = (1).to_bytes(2, 'little')
        speed = (-1).to_bytes(2, 'little', signed=True)
        tone = (-1).to_bytes(2, 'little', signed=True)
        volume = (-1).to_bytes(2, 'little', signed=True)
        voice = (0).to_bytes(2, 'little')
        encoding = (0).to_bytes(1, 'little')
        text_bytes = text.encode('utf-8')
        length = len(text_bytes).to_bytes(4, 'little')
        data = command + speed + tone + volume + voice + encoding + length + text_bytes
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
            s.sendall(data)
    except Exception as e:
        print(f"[エラー] 棒読みちゃんへの送信失敗: {e}")

def create_initial_files():
    if not os.path.exists(set_score_path):
        with open(set_score_path, 'w', encoding='utf-8') as f:
            f.write("0-0")
    if not os.path.exists(scores_path):
        with open(scores_path, 'w', encoding='utf-8') as f:
            f.write("(10先) 1P 0-0 2P")

def update_score_display(p1_wins, p2_wins, target, mode, p1_name, p2_name):
    mode_text = f"{target}先" if mode == "FIRST_TO" else f"全{target}本"
    text = f"({mode_text}) {p1_name} {p1_wins}-{p2_wins} {p2_name}"
    with open(scores_path, 'w', encoding='utf-8') as f:
        f.write(text)

def parse_set_score_content(content):
    global TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME, DISABLE_RANDOM
    lines = content.splitlines()
    p1_w, p2_w = None, None
    reset_flag = False
    name_changed = False
    rule_changed = False

    if any(k in content for k in ["ランダムなし", "ランダムオフ", "no_random", "norandom"]):
        DISABLE_RANDOM = True
    else:
        DISABLE_RANDOM = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "," in line or "1P:" in line or "2P:" in line:
            old_p1, old_p2 = P1_NAME, P2_NAME
            if "1P:" in line and "2P:" in line:
                parts = line.split(",")
                for p in parts:
                    if "1P:" in p:
                        P1_NAME = p.replace("1P:", "").strip()
                    elif "2P:" in p:
                        P2_NAME = p.replace("2P:", "").strip()
            elif "," in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    P1_NAME = parts[0].strip()
                    P2_NAME = parts[1].strip()
            if old_p1 != P1_NAME or old_p2 != P2_NAME:
                name_changed = True
        elif "先" in line:
            num_str = "".join(filter(str.isdigit, line))
            if num_str:
                new_target = int(num_str)
                if TARGET_NUM != new_target or GAME_MODE != "FIRST_TO":
                    TARGET_NUM = new_target
                    GAME_MODE = "FIRST_TO"
                    rule_changed = True
        elif "本" in line:
            num_str = "".join(filter(str.isdigit, line))
            if num_str:
                new_target = int(num_str)
                if TARGET_NUM != new_target or GAME_MODE != "TOTAL_GAMES":
                    TARGET_NUM = new_target
                    GAME_MODE = "TOTAL_GAMES"
                    rule_changed = True
        elif line.lower() == "reset" or line == "0-0":
            reset_flag = True
        elif "-" in line:
            parts = line.split("-")
            if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                p1_w = int(parts[0].strip())
                p2_w = int(parts[1].strip())

    return p1_w, p2_w, reset_flag, name_changed, rule_changed

def announce_reach_or_win(winner_name, winner_wins, loser_wins, p1_wins, p2_wins, target_num, game_mode):
    if game_mode == "FIRST_TO" and winner_wins >= target_num:
        speak_bouyomi(
            f"{winner_name}しょうり！ {p1_wins}対{p2_wins}でしょうしゃ{winner_name}ー！おめでとうございますー！よいしょうぶでしたー！",
            allow_random=False
        )
    elif game_mode == "FIRST_TO" and winner_wins == target_num - 1:
        if loser_wins == target_num - 1:
            speak_bouyomi(
                f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}おっかけリーチです！",
                allow_random=True
            )
        else:
            speak_bouyomi(
                f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}リーチです！",
                allow_random=True
            )
    else:
        speak_bouyomi(
            f"{winner_name}しょうり、{p1_wins}ー{p2_wins}です！",
            allow_random=True
        )

def start_capture_thread():
    try:
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            monitor_index=None,
            window_name=WINDOW_TITLE_KEYWORD,
        )

        @capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            global _latest_frame, _last_capture_time
            now = time.time()
            if now - _last_capture_time < 0.2:
                return
            try:
                buf = frame.frame_buffer
                bgr = cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)
                with _frame_lock:
                    _latest_frame = bgr
                    _last_capture_time = now
            except Exception as e:
                print(f"[エラー] フレーム変換失敗: {e}")

        @capture.event
        def on_closed():
            print("[情報] 対象ウィンドウが閉じられました。")

        print(f"★ キャプチャ開始(検索キーワード: 「{WINDOW_TITLE_KEYWORD}」)")
        capture.start()
    except Exception as e:
        print(f"[エラー] WindowsCapture起動失敗: {e}")
        traceback.print_exc()

def get_latest_frame():
    with _frame_lock:
        return None if _latest_frame is None else _latest_frame.copy()

def main():
    global TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME, SHOW_DEBUG_WINDOW
    create_initial_files()

    print("★ 実況システム起動...\n")

    if not os.path.exists(template_path):
        print(f"エラー: 判定用画像 '{template_path}' が見つかりません。")
        input("Enterキーを押して終了...")
        return

    base_template = imread_japanese(template_path, cv2.IMREAD_COLOR)
    if base_template is None:
        print("エラー: テンプレート画像を読み込めませんでした。")
        input("Enterキーを押して終了...")
        return

    t = threading.Thread(target=start_capture_thread, daemon=True)
    t.start()

    print("ウィンドウの最初のフレームを待っています...")
    wait_start = time.time()
    while get_latest_frame() is None:
        time.sleep(0.2)
        if time.time() - wait_start > 15:
            print(f"エラー: 「{WINDOW_TITLE_KEYWORD}」を含むウィンドウが見つかりません。")
            input("Enterキーを押して終了...")
            return
    print("フレーム取得OK。監視を開始します。")

    p1_wins = 0
    p2_wins = 0
    p1_active = False
    p2_active = False
    last_detected_time = 0.0
    last_set_score_mtime = os.path.getmtime(set_score_path) if os.path.exists(set_score_path) else 0

    update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)

    try:
        while True:
            if os.path.exists(set_score_path):
                current_mtime = os.path.getmtime(set_score_path)
                if current_mtime != last_set_score_mtime:
                    last_set_score_mtime = current_mtime
                    time.sleep(0.1)
                    try:
                        with open(set_score_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()

                        p1_w, p2_w, reset_flag, name_changed, rule_changed = parse_set_score_content(content)
                        mode_str = f"{TARGET_NUM}先" if GAME_MODE == "FIRST_TO" else f"全{TARGET_NUM}本"

                        if reset_flag:
                            p1_wins, p2_wins = 0, 0
                            speak_bouyomi(f"1P、{P1_NAME}。2P、{P2_NAME}。{mode_str}です。ぜったいにまけられないたたかいがそこにはある！", allow_random=False)
                        else:
                            if p1_w is not None and p2_w is not None:
                                p1_wins, p2_wins = p1_w, p2_w
                                speak_bouyomi(f"スコア修正、{p1_wins}ー{p2_wins}です！", allow_random=False)
                            if name_changed:
                                speak_bouyomi(f"名前変更、{P1_NAME}対{P2_NAME}です！", allow_random=False)
                            if rule_changed:
                                speak_bouyomi(f"形式変更、{mode_str}です！", allow_random=False)

                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                    except Exception as e:
                        print(f"ファイル読み込みエラー: {e}")

            frame = get_latest_frame()
            if frame is not None:
                h, w = frame.shape[:2]

                roi_y1 = int(h * ROI_Y1_RATIO)
                roi_y2 = int(h * ROI_Y2_RATIO)
                roi_frame = frame[roi_y1:roi_y2, :].copy()

                mid_x = w // 2
                detected_p1 = False
                detected_p2 = False
                best_val = 0.0
                match_rect = None

                scales = np.arange(SCALE_MIN, SCALE_MAX + 0.01, SCALE_STEP)
                for scale in scales:
                    tw = int(base_template.shape[1] * scale)
                    th = int(base_template.shape[0] * scale)

                    if tw > roi_frame.shape[1] or th > roi_frame.shape[0] or tw == 0 or th == 0:
                        continue

                    resized_template = cv2.resize(base_template, (tw, th), interpolation=cv2.INTER_AREA)
                    res = cv2.matchTemplate(roi_frame, resized_template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

                    if max_val > best_val:
                        best_val = max_val
                        match_rect = (max_loc[0], max_loc[1], tw, th)

                    locs = np.where(res >= THRESHOLD)
                    for pt_x in locs[1]:
                        if pt_x < mid_x:
                            detected_p1 = True
                        else:
                            detected_p2 = True

                if SHOW_DEBUG_WINDOW:
                    debug_img = roi_frame.copy()
                    cv2.putText(debug_img, f"Max Match: {best_val:.2f} (Thresh: {THRESHOLD})", 
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    
                    if match_rect is not None:
                        rx, ry, rw, rh = match_rect
                        color = (0, 255, 0) if best_val >= THRESHOLD else (0, 0, 255)
                        cv2.rectangle(debug_img, (rx, ry), (rx + rw, ry + rh), color, 2)
                    
                    cv2.imshow("Debug View (Press Q to close)", debug_img)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        SHOW_DEBUG_WINDOW = False
                        cv2.destroyAllWindows()

                match_over = (GAME_MODE == "FIRST_TO" and (p1_wins >= TARGET_NUM or p2_wins >= TARGET_NUM))
                current_time = time.time()

                if current_time - last_detected_time > COOLDOWN_SECONDS:
                    winner_detected = False

                    if detected_p1 and not p1_active and not match_over:
                        p1_wins += 1
                        last_detected_time = current_time
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        announce_reach_or_win(P1_NAME, p1_wins, p2_wins, p1_wins, p2_wins, TARGET_NUM, GAME_MODE)
                        p1_active = True
                        winner_detected = True
                    elif not detected_p1:
                        p1_active = False

                    if detected_p2 and not p2_active and not match_over:
                        p2_wins += 1
                        last_detected_time = current_time
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        announce_reach_or_win(P2_NAME, p2_wins, p1_wins, p1_wins, p2_wins, TARGET_NUM, GAME_MODE)
                        p2_active = True
                        winner_detected = True
                    elif not detected_p2:
                        p2_active = False

                    if winner_detected and GAME_MODE == "FIRST_TO" and (p1_wins >= TARGET_NUM or p2_wins >= TARGET_NUM):
                        print("[情報] 決着を検知しました。15秒後にスコアを自動リセットします...")
                        time.sleep(15)
                        p1_wins, p2_wins = 0, 0
                        p1_active, p2_active = False, False
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        speak_bouyomi("セットカウント、リセットします！", allow_random=False)
                        print("[情報] スコアを 0-0 に自動リセットしました。")

            time.sleep(0.3)
    except Exception as e:
        print(f"\n[致命的エラー] メインループで例外が発生しました: {e}")
        traceback.print_exc()
        input("\nEnterキーを押して終了...")
    finally:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[致命的エラー] 起動失敗: {e}")
        traceback.print_exc()
        input("\nEnterキーを押して終了...")