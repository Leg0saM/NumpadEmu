import keyboard
import time
import ctypes
import logging
import sys
import os
import tkinter as tk
import threading
import collections
import atexit

# ---------------------------------------------------------
# 0. Логирование: полный debug + последние 20 действий
# ---------------------------------------------------------
LOG_FILE = "numpad_debug.log"
ACTION_LOG_FILE = "recent_actions.log"

logger = logging.getLogger("numpad")
logger.setLevel(logging.DEBUG)
logger.handlers.clear()

# Файловый лог (полный)
fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter("[%(asctime)s.%(msecs)03d] %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
fh.setFormatter(formatter)
logger.addHandler(fh)

# Консольный лог (только если есть stdout, иначе игнорируем для --windowed)
if sys.stdout is not None:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

class ActionLog:
    """Сохраняет только последние N строк в отдельный файл"""
    def __init__(self, filename, max_lines=20):
        self.filename = filename
        self.max_lines = max_lines
        self.buffer = collections.deque(maxlen=max_lines)
        self.lock = threading.Lock()
        # Загружаем предыдущие строки, если файл существует
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
                self.buffer.extend(lines)
        self._save()

    def log(self, message):
        with self.lock:
            ts = time.strftime("%H:%M:%S")
            self.buffer.append(f"[{ts}] {message}")
            self._save()

    def _save(self):
        with open(self.filename, "w", encoding="utf-8") as f:
            f.write("\n".join(self.buffer) + "\n")

action_log = ActionLog(ACTION_LOG_FILE)
atexit.register(action_log._save)  # Гарантируем запись при выходе

# ---------------------------------------------------------
# 1. Патч библиотеки
# ---------------------------------------------------------
try:
    import keyboard._canonical_names as cn
    for i in range(10):
        cn.canonical_names[f'num {i}'] = f'num {i}'
except ImportError:
    pass

# ---------------------------------------------------------
# 2. Переключение NumLock
# ---------------------------------------------------------
def force_numlock_toggle():
    VK_NUMLOCK = 0x90
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(VK_NUMLOCK, 0x45, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)
    logger.info("  ✅ NumLock переключён системным вызовом")

# ---------------------------------------------------------
# 3. Маппинги клавиш
# ---------------------------------------------------------
NUMPAD_DATA_NUM = {
    '0': (0x60, 0x52), '1': (0x61, 0x4F), '2': (0x62, 0x50), '3': (0x63, 0x51),
    '4': (0x64, 0x4B), '5': (0x65, 0x4C), '6': (0x66, 0x4D),
    '7': (0x67, 0x47), '8': (0x68, 0x48), '9': (0x69, 0x49)
}
TOPROW_DIGITS = {'0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34, '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39}
NAVIGATION_VK = {'0': 0x2D, '1': 0x23, '2': 0x28, '3': 0x22, '4': 0x25, '5': 0x65, '6': 0x27, '7': 0x24, '8': 0x26, '9': 0x21}

numpad_mode = False
alt_pressed = False
last_press_time = {}
REPEAT_DELAY = 0.12

def get_numlock_state():
    return bool(ctypes.windll.user32.GetKeyState(0x90) & 1)

def show_osd(message):
    def _render():
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.85)
        win.configure(bg="#1a1a1a", bd=2, relief="solid")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        win.geometry(f"+{sw - 240}+{sh - 150}")
        color = "#ff4444" if "OFF" in message else "#00ff88"
        tk.Label(win, text=message, bg="#1a1a1a", fg=color, 
                 font=("Segoe UI", 16, "bold"), padx=12, pady=6).pack()
        win.after(1200, win.destroy)
        win.mainloop()
    threading.Thread(target=_render, daemon=True).start()

def send_numpad_key(digit):
    numlock_on = get_numlock_state()
    KEYEVENTF_KEYUP = 0x0002

    if numlock_on:
        if alt_pressed:
            vk, scan = NUMPAD_DATA_NUM[digit]
            flags = 0
            logger.debug(f"  📤 NUMLOCK ON + ALT: {digit} -> VK_NUMPAD")
            action_log.log(f"Альткод: нажата '{digit}' (NumPad)")
        else:
            vk = TOPROW_DIGITS[digit]
            scan = 0
            flags = 0
            logger.debug(f"  📤 NUMLOCK ON: {digit} -> VK_TOPROW")
            action_log.log(f"Цифра: '{digit}' (верхний ряд)")
    else:
        vk = NAVIGATION_VK[digit]
        scan = 0
        flags = 0
        logger.debug(f"  📤 NUMLOCK OFF: {digit} -> VK_NAV")
        action_log.log(f"Навигация: '{digit}' (стрелки/редактирование)")

    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)
    time.sleep(0.005)
    ctypes.windll.user32.keybd_event(vk, scan, flags | KEYEVENTF_KEYUP, 0)

def on_key_event(event):
    global numpad_mode, alt_pressed
    if not event.name: return True

    logger.debug(f"📥 RAW: name='{event.name}' | type={event.event_type} | scan=0x{event.scan_code:02X} | mode={'NUMPAD' if numpad_mode else 'TOP'} | NumLock={get_numlock_state()} | Alt={alt_pressed}")

    if 'alt' in event.name:
        alt_pressed = (event.event_type == keyboard.KEY_DOWN)
        return True

    if event.event_type == keyboard.KEY_DOWN:
        if event.name in ('pause', 'break'):
            numpad_mode = not numpad_mode
            status = "NUMPAD ON" if numpad_mode else "NUMPAD OFF"
            logger.info(f"\n🔄 Режим переключён: {status}\n")
            action_log.log(f"РЕЖИМ: {status}")
            show_osd(status)
            return False

        if event.name in ('=', 'plus') and numpad_mode:
            logger.info("  🔄 Trigger: Toggle NumLock")
            action_log.log("NUMLOCK: принудительное переключение")
            force_numlock_toggle()
            return False

        if event.name in NUMPAD_DATA_NUM:
            now = time.time()
            if event.name in last_press_time and now - last_press_time[event.name] < REPEAT_DELAY:
                logger.debug("  🛑 Debounce: пропуск")
                return False

            if numpad_mode:
                last_press_time[event.name] = now
                logger.info(f"  ⌨️  Нажата '{event.name}' -> эмуляция")
                send_numpad_key(event.name)
                return False
            else:
                logger.debug(f"  ⏩ Нажата '{event.name}' -> пропуск в ОС")
                last_press_time[event.name] = now
                return True

    elif event.event_type == keyboard.KEY_UP:
        if event.name in last_press_time: del last_press_time[event.name]
        if event.name in NUMPAD_DATA_NUM and numpad_mode: return False
        if event.name in ('=', 'plus') and numpad_mode: return False

    return True

if __name__ == "__main__":
    logger.info("⚡ Обработчик запущен.")
    logger.info("💡 Лог действий пишется в: recent_actions.log")
    try:
        keyboard.hook(on_key_event, suppress=True)
        logger.info("🎧 Хук установлен. Ожидание ввода...")
        action_log.log("СИСТЕМА: программа запущена")
        keyboard.wait('esc')
    except KeyboardInterrupt:
        logger.info("⛔ Прервано (Ctrl+C)")
    except Exception as e:
        logger.critical(f"❌ ОШИБКА: {e}", exc_info=True)
        action_log.log(f"ОШИБКА: {e}")
    finally:
        logger.info("👋 Программа завершена.")
        action_log.log("СИСТЕМА: программа остановлена")
        logging.shutdown()