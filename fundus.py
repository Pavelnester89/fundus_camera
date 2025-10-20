#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk
import threading
import datetime
import os
from time import sleep
from picamera2 import Picamera2

# ====== ПИНЫ СВЕТА (опционально) ======
IR_GPIO  = 17     # ИК-подсветка
VIS_GPIO = 27     # видимый свет/вспышка
ACTIVE_HIGH = True

try:
    from gpiozero import LED, Button
    ir_led  = LED(IR_GPIO,  active_high=ACTIVE_HIGH)
    vis_led = LED(VIS_GPIO, active_high=ACTIVE_HIGH)
    ir_led.off(); vis_led.off()
except Exception:
    class _Dummy:
        def on(self):  pass
        def off(self): pass
    ir_led, vis_led = _Dummy(), _Dummy()
    # Заглушка Button, если gpiozero недоступен
    class Button:  # noqa: N801
        def __init__(self, *a, **kw): pass
        def close(self): pass
        when_pressed = None

# ====== ДОП. КНОПКИ ПО GPIO ======
BTN_AUTO_GPIO  = 16  # Pin 36 (Автофокус)
BTN_RESET_GPIO = 20  # Pin 38 (Сброс Z/F)
BTN_OFF_GPIO   = 21  # Pin 40 (Выход/Назад)

# ====== ПАРАМЕТРЫ ======
VISIBLE_WINDOW = 1.0       # сек; снимок в середине окна
FOCUS_MIN, FOCUS_MAX = 0.0, 10.0
INITIAL_FOCUS = 5.0
ZOOM_MIN, ZOOM_MAX   = 1.0, 8.0     # допустимый диапазон зума
INITIAL_ZOOM         = 4.0          # стартуем с честного 4× от базового окна
STEP = 0.1  # фиксированный шаг для зума/фокуса

# ====== КАМЕРА ======
picam2 = Picamera2()
preview_config = picam2.create_preview_configuration(main={"size": (1280, 720)})
picam2.configure(preview_config)

controls      = getattr(picam2, "camera_controls", {})
HAS_LENSPOS   = "LensPosition" in controls
AF_AVAILABLE  = any(k in controls for k in ("AfMode", "AfTrigger"))

# состояния
zoom_factor = INITIAL_ZOOM
focus_position = INITIAL_FOCUS
last_auto_focus_position = None
af_mode = "manual"
save_dir = os.path.expanduser("~/Pictures")
running_preview = False
_is_capturing = False
BASE_CROP = None  # (x0, y0, w, h) — принятое за 1×

# ====== ВСПОМОГАТЕЛЬНОЕ ======
def _even(x):
    return int(x) & ~1  # ScalerCrop любит чётные значения

def resize_contain(img, box_w, box_h):
    """Без обрезки (letterbox): предпросмотр = фото по полю зрения."""
    if box_w <= 0 or box_h <= 0: return img
    img_w, img_h = img.size
    scale = min(box_w / img_w, box_h / img_h)
    new_w = max(1, int(img_w * scale))
    new_h = max(1, int(img_h * scale))
    return img.resize((new_w, new_h))

def toast(msg, ms=1200):
    toast_var.set(msg)
    toast_label.place(relx=0.5, rely=0.0, anchor="n")
    toast_label.after(ms, lambda: toast_label.place_forget())

def update_focus_label():
    try:
        meta = picam2.capture_metadata()
        lp = meta.get("LensPosition", None)
    except Exception:
        lp = None
    if af_mode == "auto":
        focus_value_var.set(f"auto: {lp:.2f}" if lp is not None else "auto")
    else:
        focus_value_var.set(f"{focus_position:.2f}" if HAS_LENSPOS else "нет")

def init_base_crop(retries=8, delay=0.05):
    """Считать стартовый ScalerCrop и принять его за 1×."""
    global BASE_CROP
    BASE_CROP = None
    for _ in range(retries):
        try:
            meta = picam2.capture_metadata() or {}
            sc = meta.get("ScalerCrop")
            if sc and len(sc) == 4 and sc[2] > 0 and sc[3] > 0:
                BASE_CROP = (_even(sc[0]), _even(sc[1]), _even(sc[2]), _even(sc[3]))
                break
        except Exception:
            pass
        sleep(delay)
    # фоллбэк — от конфигурации
    if BASE_CROP is None:
        try:
            w, h = picam2.camera_configuration()["main"]["size"]
            BASE_CROP = (0, 0, _even(w), _even(h))
        except Exception:
            BASE_CROP = (0, 0, 1280, 720)

# ====== АВТОФОКУС/РУЧНОЙ ======
def enable_autofocus():
    """GPIO16 / Кнопка: включить автофокус."""
    global af_mode, last_auto_focus_position
    if not AF_AVAILABLE:
        status_var.set("AF недоступен на этой камере"); return
    try:
        try: picam2.set_controls({"AfMode": 2})  # Continuous
        except Exception: pass
        try:
            picam2.set_controls({"AfTrigger": 0})
            picam2.set_controls({"AfTrigger": 1})
        except Exception: pass
        af_mode = "auto"
        status_var.set("Автофокус: ВКЛ")
        def read_af_pos():
            global last_auto_focus_position
            sleep(0.2)
            try:
                lp = picam2.capture_metadata().get("LensPosition", None)
                if lp is not None: last_auto_focus_position = lp
            except Exception: pass
            update_focus_label()
        threading.Thread(target=read_af_pos, daemon=True).start()
    except Exception as e:
        status_var.set(f"AF ошибка: {e}")

def switch_to_manual_from_current():
    """Перейти в ручной, стартуя с текущего авто-значения."""
    global af_mode, focus_position, last_auto_focus_position
    try:
        lp = picam2.capture_metadata().get("LensPosition", None)
        if lp is not None: last_auto_focus_position = lp
    except Exception:
        pass
    if last_auto_focus_position is not None:
        focus_position = last_auto_focus_position
    try:
        if AF_AVAILABLE:
            try: picam2.set_controls({"AfMode": 0})  # Manual
            except Exception: pass
        if HAS_LENSPOS:
            picam2.set_controls({"LensPosition": focus_position})
    except Exception:
        pass
    af_mode = "manual"
    update_focus_label()
    status_var.set("Фокус: РУЧНОЙ")

# ====== КАМЕРА/СВЕТ ======
def apply_zoom():
    """Цифровой зум относительно стартового поля (BASE_CROP = 1×)."""
    global zoom_factor
    if BASE_CROP is None:
        init_base_crop()

    zoom_factor = max(ZOOM_MIN, min(zoom_factor, ZOOM_MAX))
    bx, by, bw, bh = BASE_CROP

    try:
        crop_w = max(16, _even(bw / zoom_factor))
        crop_h = max(16, _even(bh / zoom_factor))
        x0 = _even(bx + (bw - crop_w) / 2)
        y0 = _even(by + (bh - crop_h) / 2)

        picam2.set_controls({"ScalerCrop": (x0, y0, crop_w, crop_h)})

        # Фактический зум относительно базы
        eff_zoom = bw / float(crop_w) if crop_w else zoom_factor
        zoom_value_var.set(f"{eff_zoom:.1f}x")
    except Exception as e:
        print("Zoom error:", e)
        zoom_value_var.set(f"{zoom_factor:.1f}x")

def apply_focus():
    global focus_position
    if af_mode == "auto":
        update_focus_label(); return
    if not HAS_LENSPOS:
        focus_value_var.set("нет"); return
    focus_position = max(FOCUS_MIN, min(focus_position, FOCUS_MAX))
    try:
        picam2.set_controls({"LensPosition": focus_position})
    except Exception as e:
        print("Focus error:", e)
    update_focus_label()

def update_frame():
    if not running_preview: return
    try:
        if _is_capturing:
            preview_label.after(50, update_frame); return
        frame = picam2.capture_array()
        img = Image.fromarray(frame)
        w, h = max(100, preview_area.winfo_width()), max(100, preview_area.winfo_height())
        img = resize_contain(img, w, h)  # без обрезки!
        imgtk = ImageTk.PhotoImage(image=img)
        preview_label.imgtk = imgtk
        preview_label.config(image=imgtk)
    except Exception as e:
        print("Preview update error:", e)
    update_focus_label()
    preview_label.after(80, update_frame)

def start_preview():
    """Старт предпросмотра (кнопка 'Включить камеру'), сразу AF."""
    global running_preview, zoom_factor, focus_position
    try:
        if running_preview:
            picam2.stop()
        picam2.configure(preview_config)
        zoom_factor = INITIAL_ZOOM
        focus_position = INITIAL_FOCUS
        picam2.start()
        running_preview = True
        ir_led.on(); vis_led.off()

        init_base_crop()   # база = 1×
        apply_zoom()       # сразу выставим INITIAL_ZOOM честно
        enable_autofocus()

        status_var.set("Предпросмотр включён (AF)")
        update_frame()
    except Exception as e:
        status_var.set(f"Ошибка камеры: {e}")

def stop_preview():
    global running_preview
    running_preview = False
    try: picam2.stop()
    except: pass
    ir_led.off(); vis_led.off()
    status_var.set("Предпросмотр остановлен")

def take_photo():
    """Фото БЕЗ смены режима. Замораживаем AE/AWB и фокус -> VIS вспышка -> кадр -> возврат."""
    def worker():
        global _is_capturing, af_mode, last_auto_focus_position, focus_position
        _is_capturing = True
        prev_af_mode = af_mode
        prev_awb = True
        prev_ae  = True
        try:
            import time
            # 1) зафиксировать автоэкспозицию/баланс и фокус
            try:
                picam2.set_controls({"AeEnable": 0})
            except Exception:
                prev_ae = None
            try:
                picam2.set_controls({"AwbEnable": 0})
            except Exception:
                prev_awb = None

            # если AF был включён — заморозим текущую позицию линзы
            if AF_AVAILABLE:
                try:
                    lp = picam2.capture_metadata().get("LensPosition", None)
                except Exception:
                    lp = None
                if lp is not None:
                    last_auto_focus_position = lp
                if HAS_LENSPOS and lp is not None:
                    try:
                        picam2.set_controls({"AfMode": 0, "LensPosition": lp})
                        af_mode = "manual"
                        focus_position = lp
                    except Exception:
                        pass

            # 2) вспышка и задержка до середины окна
            ir_led.off(); vis_led.on()
            t0 = time.monotonic()
            sleep(VISIBLE_WINDOW/2)

            # 3) сохранить кадр прямо из текущего режима
            base_dir = os.path.join(save_dir, "Fundus", "Видимый")
            os.makedirs(base_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(base_dir, f"fundus_{now}.jpg")
            picam2.capture_file(path)

            # добираем хвост окна
            sleep(max(0, VISIBLE_WINDOW - (time.monotonic()-t0)))

            status_var.set(f"Фото сохранено: {path}")
            toast("Фото сохранено")

        except Exception as e:
            status_var.set(f"Ошибка фото: {e}")
            toast("Ошибка фото")
        finally:
            # 4) вернуть как было
            vis_led.off(); ir_led.on()
            try:
                if prev_ae is not None:  picam2.set_controls({"AeEnable": 1})
            except Exception:
                pass
            try:
                if prev_awb is not None: picam2.set_controls({"AwbEnable": 1})
            except Exception:
                pass
            # вернуть AF, если раньше он был авто
            if AF_AVAILABLE and prev_af_mode == "auto":
                try:
                    picam2.set_controls({"AfMode": 2})
                    af_mode = "auto"
                except Exception:
                    pass
            _is_capturing = False
    threading.Thread(target=worker, daemon=True).start()

# ====== УПРАВЛЕНИЕ (фикс. шаг 0.1) ======
def zoom_in():
    global zoom_factor
    if af_mode == "auto": switch_to_manual_from_current()
    zoom_factor += STEP; apply_zoom()

def zoom_out():
    global zoom_factor
    if af_mode == "auto": switch_to_manual_from_current()
    zoom_factor -= STEP; apply_zoom()

def focus_near():
    global focus_position
    if af_mode == "auto": switch_to_manual_from_current()
    if not HAS_LENSPOS: status_var.set("Нет ручного фокуса"); return
    focus_position += STEP; apply_focus()

def focus_far():
    global focus_position
    if af_mode == "auto": switch_to_manual_from_current()
    if not HAS_LENSPOS: status_var.set("Нет ручного фокуса"); return
    focus_position -= STEP; apply_focus()

def reset_zoom_focus():
    """GPIO20 / Pin38 — Сброс."""
    global zoom_factor, focus_position, af_mode
    zoom_factor, focus_position = INITIAL_ZOOM, INITIAL_FOCUS
    af_mode = "manual"
    apply_zoom(); apply_focus()
    status_var.set("Сброс выполнен")
    toast("Сброс")

def back_to_start():
    """GPIO21 / Pin40 — Выкл/Назад."""
    stop_preview()
    shooting_frame.pack_forget()
    start_frame.pack(fill="both", expand=True)
    toast("Остановлено")

# ====== UI ======
root = tk.Tk()
root.title("Камера глазного дна")
root.geometry("800x480")

SMALL = ("Arial", 9)

status_var = tk.StringVar(value="")
save_dir_var = tk.StringVar(value=save_dir)
zoom_value_var = tk.StringVar(value=f"{INITIAL_ZOOM:.1f}x")
focus_value_var = tk.StringVar(value="—")
toast_var = tk.StringVar(value="")

# Экран 1
start_frame = tk.Frame(root, bg="black")
tk.Label(start_frame, text="Прототип камеры", font=("Arial", 18), fg="white", bg="black").pack(pady=10)

path_row = tk.Frame(start_frame, bg="black")
path_row.pack(pady=6, fill="x", padx=12)
tk.Label(path_row, text="Папка сохранения:", font=("Arial", 10), fg="white", bg="black").pack(side="left")
tk.Entry(path_row, textvariable=save_dir_var, font=("Arial", 10)).pack(side="left", expand=True, fill="x", padx=8)

def choose_folder():
    global save_dir
    folder = filedialog.askdirectory(initialdir=save_dir_var.get() or os.path.expanduser("~"))
    if folder: save_dir_var.set(folder); save_dir = folder

tk.Button(path_row, text="Выбрать…", font=("Arial", 10), command=choose_folder, height=1, width=9)\
    .pack(side="left", padx=4)

def go_to_shooting():
    global save_dir
    save_dir = save_dir_var.get() or save_dir
    start_frame.pack_forget()
    shooting_frame.pack(fill="both", expand=True)
    start_preview()

tk.Button(start_frame, text="Включить камеру", font=("Arial", 11), height=1, command=go_to_shooting)\
    .pack(pady=8, padx=12, fill="x")

tk.Label(start_frame, textvariable=status_var, font=SMALL, fg="gray80", bg="black").pack(pady=6)

# Экран 2
shooting_frame = tk.Frame(root)
preview_area = tk.Frame(shooting_frame, bg="black")
preview_area.pack(fill="both", expand=True)
preview_label = tk.Label(preview_area, bg="black")
preview_label.pack(fill="both", expand=True)

overlay_bar = tk.Frame(preview_area, bg="")
overlay_bar.place(relx=0, rely=0, relwidth=1, anchor="nw")

right_group = tk.Frame(overlay_bar, bg="")
right_group.pack(side="right", padx=4, pady=4)
tk.Label(right_group, text="Z:", font=SMALL).pack(side="left")
tk.Label(right_group, textvariable=zoom_value_var, font=SMALL).pack(side="left", padx=(0,8))
tk.Label(right_group, text="F:", font=SMALL).pack(side="left")
tk.Label(right_group, textvariable=focus_value_var, font=SMALL).pack(side="left")

toast_label = tk.Label(preview_area, textvariable=toast_var, font=("Arial", 11, "bold"),
                       bg="#222", fg="white", padx=12, pady=6)

tk.Label(shooting_frame, textvariable=status_var, font=SMALL).pack(side="bottom", pady=2)

# ====== КЛАВИАТУРА (gpio-key overlay) ======
#  Pin29 GPIO5 -> Enter  -> Фото
#  Pin31 GPIO6 -> Right  -> Зум+
#  Pin33 GPIO13-> Left   -> Зум-
#  Pin35 GPIO19-> Up     -> Фокус+
#  Pin37 GPIO26-> Down   -> Фокус-
root.focus_force()
root.bind_all('<Return>', lambda e: take_photo())
root.bind_all('<Right>',  lambda e: zoom_in())
root.bind_all('<Left>',   lambda e: zoom_out())
root.bind_all('<Up>',     lambda e: focus_near())
root.bind_all('<Down>',   lambda e: focus_far())

# ====== ДОП. ФИЗКНОПКИ НА GPIO16/20/21 ======
btn_auto  = Button(BTN_AUTO_GPIO,  pull_up=True, bounce_time=0.08)
btn_reset = Button(BTN_RESET_GPIO, pull_up=True, bounce_time=0.08)
btn_off   = Button(BTN_OFF_GPIO,   pull_up=True, bounce_time=0.08)

btn_auto.when_pressed  = lambda: enable_autofocus()
btn_reset.when_pressed = lambda: reset_zoom_focus()
btn_off.when_pressed   = lambda: back_to_start()

def on_close():
    stop_preview()
    try:
        btn_auto.close(); btn_reset.close(); btn_off.close()
    except: pass
    try: ir_led.off(); vis_led.off()
    except: pass
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
start_frame.pack(fill="both", expand=True)
root.mainloop()
