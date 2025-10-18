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

# ================== ПИНЫ СВЕТА (опционально) ==================
IR_GPIO  = 17     # ИК-подсветка (через транзистор), если есть
VIS_GPIO = 27     # видимый свет/вспышка, если есть
ACTIVE_HIGH = True

try:
    from gpiozero import LED
    ir_led  = LED(IR_GPIO,  active_high=ACTIVE_HIGH)
    vis_led = LED(VIS_GPIO, active_high=ACTIVE_HIGH)
    ir_led.off(); vis_led.off()
except Exception:
    class _Dummy:
        def on(self):  pass
        def off(self): pass
    ir_led, vis_led = _Dummy(), _Dummy()

# ================== ПАРАМЕТРЫ ==================
VISIBLE_WINDOW = 1.0  # сек: длительность включения видимого света; снимок в середине окна
FOCUS_MIN, FOCUS_MAX = 0.0, 10.0
INITIAL_FOCUS = 5.0
ZOOM_MIN, ZOOM_MAX   = 1.0, 4.0
INITIAL_ZOOM         = 4.0
STEP_CHOICES = [0.1, 0.5, 1.0, 2.5]

# ================== КАМЕРА ==================
picam2 = Picamera2()
still_config   = picam2.create_still_configuration(main={"size": (1280, 720)})
preview_config = picam2.create_preview_configuration(main={"size": (1280, 720)})
picam2.configure(still_config)

controls      = getattr(picam2, "camera_controls", {})
HAS_LENSPOS   = "LensPosition" in controls
AF_AVAILABLE  = any(k in controls for k in ("AfMode", "AfTrigger"))

# Состояния
zoom_factor = INITIAL_ZOOM
focus_position = INITIAL_FOCUS
last_auto_focus_position = None
af_mode = "manual"
save_dir = os.path.expanduser("~/Pictures")
running_preview = False

# ================== ВСПОМОГАТЕЛЬНОЕ ==================
def resize_cover(img, box_w, box_h):
    if box_w <= 0 or box_h <= 0: return img
    img_w, img_h = img.size
    scale = max(box_w / img_w, box_h / img_h)
    img = img.resize((int(img_w * scale), int(img_h * scale)))
    x0, y0 = max(0, (img.width - box_w)//2), max(0, (img.height - box_h)//2)
    return img.crop((x0, y0, x0 + box_w, y0 + box_h))

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

# ================== АВТОФОКУС / РУЧНОЙ ==================
def enable_autofocus():
    """Включить автофокус (если поддерживается)."""
    global af_mode, last_auto_focus_position
    if not AF_AVAILABLE:
        status_var.set("AF недоступен на этой камере"); return
    try:
        try: picam2.set_controls({"AfMode": 2})  # Continuous, если доступен
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
    """Перейти в ручной, стартовав с текущего значения, выставленного автофокусом."""
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

# ================== КАМЕРА/СВЕТ ==================
def apply_zoom():
    global zoom_factor
    zoom_factor = max(ZOOM_MIN, min(zoom_factor, ZOOM_MAX))
    try:
        w, h = picam2.camera_configuration()["main"]["size"]
        new_w = int(w / zoom_factor); new_h = int(h / zoom_factor)
        x0 = (w - new_w)//2; y0 = (h - new_h)//2
        picam2.set_controls({"ScalerCrop": (x0, y0, new_w, new_h)})
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
        frame = picam2.capture_array()
        img = Image.fromarray(frame)
        w, h = max(100, preview_area.winfo_width()), max(100, preview_area.winfo_height())
        img = resize_cover(img, w, h)
        imgtk = ImageTk.PhotoImage(image=img)
        preview_label.imgtk = imgtk
        preview_label.config(image=imgtk)
    except Exception as e:
        print("Preview update error:", e)
    update_focus_label()
    preview_label.after(100, update_frame)

def start_preview():
    """Старт предпросмотра с автофокусом."""
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
        apply_zoom()
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
    """Фото с включением VIS на середине окна."""
    def worker():
        try:
            import time
            ir_led.off(); vis_led.on()
            t0 = time.monotonic()
            sleep(VISIBLE_WINDOW/2)
            base_dir = os.path.join(save_dir, "Fundus", "Видимый")
            os.makedirs(base_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(base_dir, f"fundus_{now}.jpg")

            # переключимся на still-конфиг для кадра
            picam2.stop()
            picam2.configure(still_config)
            picam2.start()
            picam2.capture_file(path)

            sleep(max(0, VISIBLE_WINDOW - (time.monotonic()-t0)))
            status_var.set(f"Фото сохранено: {path}")
        except Exception as e:
            status_var.set(f"Ошибка фото: {e}")
        finally:
            vis_led.off()
            # вернуться в превью
            if True:
                try:
                    picam2.stop()
                    picam2.configure(preview_config)
                    picam2.start()
                    ir_led.on()
                except: pass
    threading.Thread(target=worker, daemon=True).start()

# ================== УПРАВЛЕНИЕ ==================
def get_step():
    try: return float(step_var.get())
    except: return 0.1

def zoom_in():
    global zoom_factor
    if af_mode == "auto": switch_to_manual_from_current()
    zoom_factor += get_step(); apply_zoom()

def zoom_out():
    global zoom_factor
    if af_mode == "auto": switch_to_manual_from_current()
    zoom_factor -= get_step(); apply_zoom()

def focus_near():
    global focus_position
    if af_mode == "auto": switch_to_manual_from_current()
    if not HAS_LENSPOS: status_var.set("Нет ручного фокуса"); return
    focus_position += get_step(); apply_focus()

def focus_far():
    global focus_position
    if af_mode == "auto": switch_to_manual_from_current()
    if not HAS_LENSPOS: status_var.set("Нет ручного фокуса"); return
    focus_position -= get_step(); apply_focus()

def reset_zoom_focus():
    global zoom_factor, focus_position
    zoom_factor, focus_position = INITIAL_ZOOM, INITIAL_FOCUS
    apply_zoom(); apply_focus()
    status_var.set("Сброс: зум 4.0×, фокус 5.00")

def back_to_start():
    stop_preview()
    shooting_frame.pack_forget()
    start_frame.pack(fill="both", expand=True)

# ================== UI ==================
root = tk.Tk()
root.title("Камера глазного дна")
root.geometry("800x480")

LARGE = ("Arial", 11)
MID   = ("Arial", 10)
SMALL = ("Arial", 9)

status_var = tk.StringVar(value="")
save_dir_var = tk.StringVar(value=save_dir)
zoom_value_var = tk.StringVar(value=f"{INITIAL_ZOOM:.1f}x")
focus_value_var = tk.StringVar(value="—")
step_var = tk.StringVar(value=str(STEP_CHOICES[0]))

# Экран 1
start_frame = tk.Frame(root, bg="black")
tk.Label(start_frame, text="Прототип камеры", font=("Arial", 18), fg="white", bg="black").pack(pady=10)

path_row = tk.Frame(start_frame, bg="black")
path_row.pack(pady=6, fill="x", padx=12)
tk.Label(path_row, text="Папка сохранения:", font=MID, fg="white", bg="black").pack(side="left")
tk.Entry(path_row, textvariable=save_dir_var, font=MID).pack(side="left", expand=True, fill="x", padx=8)

def choose_folder():
    global save_dir
    folder = filedialog.askdirectory(initialdir=save_dir_var.get() or os.path.expanduser("~"))
    if folder: save_dir_var.set(folder); save_dir = folder

tk.Button(path_row, text="Выбрать…", font=MID, command=choose_folder, height=1, width=9).pack(side="left", padx=4)

def go_to_shooting():
    global save_dir
    save_dir = save_dir_var.get() or save_dir
    start_frame.pack_forget()
    shooting_frame.pack(fill="both", expand=True)
    start_preview()

tk.Button(start_frame, text="Включить камеру", font=LARGE, height=1, command=go_to_shooting)\
    .pack(pady=8, padx=12, fill="x")

tk.Label(start_frame, textvariable=status_var, font=SMALL, fg="gray80", bg="black").pack(pady=6)

# Экран 2
shooting_frame = tk.Frame(root)
preview_area = tk.Frame(shooting_frame)
preview_area.pack(fill="both", expand=True)
preview_label = tk.Label(preview_area)
preview_label.pack(fill="both", expand=True)

overlay_bar = tk.Frame(preview_area)
overlay_bar.place(relx=0, rely=0, relwidth=1, anchor="nw")

# Левая часть: «Авто»
left_group = tk.Frame(overlay_bar)
left_group.pack(side="left", padx=4, pady=4)
tk.Button(left_group, text="Авто", command=enable_autofocus, font=LARGE, height=1, width=6)\
    .pack(side="left", padx=2)

# Правая часть: индикаторы/шаг/сброс/выкл
right_group = tk.Frame(overlay_bar)
right_group.pack(side="right", padx=4, pady=4)
tk.Label(right_group, text="Z:", font=SMALL).pack(side="left")
tk.Label(right_group, textvariable=zoom_value_var, font=SMALL).pack(side="left", padx=(0,4))
tk.Label(right_group, text="F:", font=SMALL).pack(side="left")
tk.Label(right_group, textvariable=focus_value_var, font=SMALL).pack(side="left", padx=(0,6))
tk.Label(right_group, text="Шаг:", font=SMALL).pack(side="left")
step_menu = tk.OptionMenu(right_group, step_var, *map(str, STEP_CHOICES))
step_menu.config(font=SMALL)
step_menu.pack(side="left", padx=(2,4))
tk.Button(right_group, text="Сброс", command=reset_zoom_focus, font=SMALL, height=1, width=6)\
    .pack(side="left", padx=(2,4))
tk.Button(right_group, text="Выкл", command=back_to_start, font=SMALL, height=1, width=6)\
    .pack(side="left", padx=2)

tk.Label(shooting_frame, textvariable=status_var, font=SMALL).pack(side="bottom", pady=2)

# ================== КЛАВИАТУРНЫЕ БИНДИНГИ ==================
# Назначения (через gpio-key overlay):
#  Pin29 GPIO5 -> Enter  -> Фото
#  Pin31 GPIO6 -> Right  -> Зум +
#  Pin33 GPIO13-> Left   -> Зум -
#  Pin35 GPIO19-> Up     -> Фокус +
#  Pin37 GPIO26-> Down   -> Фокус -
root.focus_force()
root.bind_all('<Return>', lambda e: take_photo())
root.bind_all('<Right>',  lambda e: zoom_in())
root.bind_all('<Left>',   lambda e: zoom_out())
root.bind_all('<Up>',     lambda e: focus_near())
root.bind_all('<Down>',   lambda e: focus_far())

def on_close():
    stop_preview()
    try: ir_led.off(); vis_led.off()
    except: pass
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
start_frame.pack(fill="both", expand=True)
root.mainloop()
