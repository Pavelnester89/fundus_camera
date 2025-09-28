#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk
import threading
import datetime
import os
from time import sleep
from gpiozero import LED
from picamera2 import Picamera2

# ================== ЖЕЛЕЗО ==================
IR_GPIO  = 17
VIS_GPIO = 27
ACTIVE_HIGH = True

# окно видимого света: включён VISIBLE_WINDOW, снимок в середине
VISIBLE_WINDOW = 1.0

# диапазоны/стартовые значения
FOCUS_MIN, FOCUS_MAX = 0.0, 10.0
INITIAL_FOCUS = 5.0
ZOOM_MIN, ZOOM_MAX = 1.0, 4.0
INITIAL_ZOOM = 4.0

# общий шаг (и для зума и для фокуса)
STEP_CHOICES = [0.1, 0.5, 1.0, 2.5]

# ================== ИНИЦИАЛИЗАЦИЯ ==================
ir_led  = LED(IR_GPIO,  active_high=ACTIVE_HIGH)
vis_led = LED(VIS_GPIO, active_high=ACTIVE_HIGH)
ir_led.off(); vis_led.off()

picam2 = Picamera2()
still_config = picam2.create_still_configuration(main={"size": (1280, 720)})
picam2.configure(still_config)

controls = getattr(picam2, "camera_controls", {})
HAS_LENSPOS = "LensPosition" in controls

# состояния
zoom_factor = INITIAL_ZOOM
focus_position = INITIAL_FOCUS
save_dir = os.path.expanduser("~/Pictures")
running_preview = False

# UI vars
status_var = None
zoom_value_var = None
focus_value_var = None
save_dir_var = None
preview_label = None
preview_area = None
step_var = None  # общий шаг

# ================== ВСПОМОГАТЕЛЬНОЕ ==================
def resize_cover(img, box_w, box_h):
    if box_w <= 0 or box_h <= 0: return img
    img_w, img_h = img.size
    scale = max(box_w / img_w, box_h / img_h)
    new_w, new_h = int(img_w * scale), int(img_h * scale)
    img = img.resize((new_w, new_h))
    x0, y0 = max(0, (new_w - box_w) // 2), max(0, (new_h - box_h) // 2)
    return img.crop((x0, y0, x0 + box_w, y0 + box_h))

# ================== КАМЕРА/СВЕТ ==================
def apply_zoom():
    global zoom_factor
    zoom_factor = max(ZOOM_MIN, min(zoom_factor, ZOOM_MAX))
    try:
        sensor_size = picam2.sensor_resolution  # (w, h)
        new_w = int(sensor_size[0] / zoom_factor)
        new_h = int(sensor_size[1] / zoom_factor)
        x0 = (sensor_size[0] - new_w) // 2
        y0 = (sensor_size[1] - new_h) // 2
        picam2.set_controls({"ScalerCrop": (x0, y0, new_w, new_h)})
    except Exception as e:
        print("Zoom error:", e)
    if zoom_value_var is not None:
        zoom_value_var.set(f"{zoom_factor:.1f}x")

def apply_focus():
    if not HAS_LENSPOS:
        if focus_value_var: focus_value_var.set("авто")
        return
    global focus_position
    focus_position = max(FOCUS_MIN, min(focus_position, FOCUS_MAX))
    try:
        picam2.set_controls({"LensPosition": focus_position})
        if focus_value_var: focus_value_var.set(f"{focus_position:.2f}")
    except Exception as e:
        print("Focus error:", e)
        if focus_value_var: focus_value_var.set("нет")

def update_frame():
    if not running_preview: return
    try:
        frame = picam2.capture_array()
        img = Image.fromarray(frame)
        w = max(100, preview_area.winfo_width())
        h = max(100, preview_area.winfo_height())
        img = resize_cover(img, w, h)
        imgtk = ImageTk.PhotoImage(image=img)
        preview_label.imgtk = imgtk
        preview_label.config(image=imgtk)
    except Exception as e:
        print("Preview update error:", e)
    preview_label.after(50, update_frame)

def start_preview():
    global running_preview, zoom_factor, focus_position
    try:
        zoom_factor = INITIAL_ZOOM
        focus_position = INITIAL_FOCUS
        picam2.start()
        apply_focus(); apply_zoom()
        running_preview = True
        update_frame()
        vis_led.off(); ir_led.on()
        status_var.set("Предпросмотр включён")
    except Exception as e:
        status_var.set(f"Ошибка запуска камеры: {e}")

def stop_preview():
    global running_preview
    running_preview = False
    try: picam2.stop()
    except Exception: pass
    ir_led.off(); vis_led.off()
    status_var.set("Предпросмотр остановлен")

def take_photo():
    """Видимый включён VISIBLE_WINDOW сек; кадр — в середине окна."""
    def capture():
        import time
        try:
            ir_led.off()
            vis_led.on()
            start_t = time.monotonic()
            sleep(VISIBLE_WINDOW / 2.0)

            base_dir = os.path.join(save_dir, "Fundus", "Видимый")
            os.makedirs(base_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(base_dir, f"fundus_{now}.jpg")
            picam2.capture_file(path)

            elapsed = time.monotonic() - start_t
            remaining = max(0.0, VISIBLE_WINDOW - elapsed)
            sleep(remaining)

            status_var.set(f"Фото (видимый) сохранено: {path}")
        except Exception as e:
            status_var.set(f"Ошибка при съёмке: {e}")
            print("Capture error:", e)
        finally:
            try: vis_led.off()
            except: pass
            if running_preview:
                try: ir_led.on()
                except: pass
    threading.Thread(target=capture, daemon=True).start()

def take_photo_ir():
    """ИК-фото: без видимого света, с ИК-подсветкой."""
    def capture_ir():
        try:
            vis_led.off()
            base_dir = os.path.join(save_dir, "Fundus", "ИК")
            os.makedirs(base_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(base_dir, f"fundusIR_{now}.jpg")
            picam2.capture_file(path)
            status_var.set(f"ИК-фото сохранено: {path}")
        except Exception as e:
            status_var.set(f"Ошибка при ИК-съёмке: {e}")
            print("IR capture error:", e)
    threading.Thread(target=capture_ir, daemon=True).start()

# ================== УПРАВЛЕНИЕ ==================
def get_step():
    try:
        return float(step_var.get())
    except Exception:
        return 0.1

def zoom_in():
    global zoom_factor
    zoom_factor += get_step()
    apply_zoom()

def zoom_out():
    global zoom_factor
    zoom_factor -= get_step()
    apply_zoom()

def focus_near():
    if not HAS_LENSPOS:
        status_var.set("Ручной фокус не поддерживается"); return
    global focus_position
    focus_position += get_step()
    apply_focus()

def focus_far():
    if not HAS_LENSPOS:
        status_var.set("Ручной фокус не поддерживается"); return
    global focus_position
    focus_position -= get_step()
    apply_focus()

def reset_zoom_focus():
    global zoom_factor, focus_position
    zoom_factor = INITIAL_ZOOM
    focus_position = INITIAL_FOCUS
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

# компактные шрифты/кнопки для узкой линии
LARGE = ("Arial", 11)
MID   = ("Arial", 10)
SMALL = ("Arial", 9)

status_var = tk.StringVar(value="")
save_dir_var = tk.StringVar(value=save_dir)
zoom_value_var = tk.StringVar(value=f"{zoom_factor:.1f}x")
focus_value_var = tk.StringVar(value=("авто" if not HAS_LENSPOS else f"{focus_position:.2f}"))
step_var = tk.StringVar(value=str(STEP_CHOICES[0]))  # "0.1"

# Экран 1 (можно оставить тёмный фон для читаемости)
start_frame = tk.Frame(root, bg="black")
tk.Label(start_frame, text="Прототип камеры", font=("Arial", 18), fg="white", bg="black").pack(pady=10)

path_row = tk.Frame(start_frame, bg="black")
path_row.pack(pady=6, fill="x", padx=12)
tk.Label(path_row, text="Папка сохранения:", font=MID, fg="white", bg="black").pack(side="left")
tk.Entry(path_row, textvariable=save_dir_var, font=MID).pack(side="left", expand=True, fill="x", padx=8)

def choose_folder():
    global save_dir
    folder = filedialog.askdirectory(initialdir=save_dir_var.get() or os.path.expanduser("~"))
    if folder:
        save_dir = folder
        save_dir_var.set(folder)

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
shooting_frame = tk.Frame(root)  # без фона
preview_area = tk.Frame(shooting_frame)  # без фона
preview_area.pack(fill="both", expand=True)

preview_label = tk.Label(preview_area)  # без bg
preview_label.pack(fill="both", expand=True)

# ВЕРХНЯЯ «ПРОЗРАЧНАЯ» ПАНЕЛЬ (без bg)
overlay_bar = tk.Frame(preview_area, highlightthickness=0, bd=0)
overlay_bar.place(relx=0, rely=0, relwidth=1, anchor="nw")

# ЛЕВО: Фото / ИК / Зум
left_group = tk.Frame(overlay_bar)
left_group.pack(side="left", padx=4, pady=4)
tk.Button(left_group, text="Фото", command=take_photo, font=LARGE, height=1, width=6)\
    .pack(side="left", padx=2)
tk.Button(left_group, text="ИК", command=take_photo_ir, font=LARGE, height=1, width=4)\
    .pack(side="left", padx=2)
tk.Button(left_group, text="Зум−", command=zoom_out, font=LARGE, height=1, width=5)\
    .pack(side="left", padx=2)
tk.Button(left_group, text="Зум+", command=zoom_in, font=LARGE, height=1, width=5)\
    .pack(side="left", padx=2)

# СЕРЕДИНА: Фокус
mid_group = tk.Frame(overlay_bar)
mid_group.pack(side="left", padx=6, pady=4)
if HAS_LENSPOS:
    tk.Button(mid_group, text="хуй",  command=focus_near, font=LARGE, height=1, width=6)\
        .pack(side="left", padx=2)
    tk.Button(mid_group, text="Дальше", command=focus_far,  font=LARGE, height=1, width=7)\
        .pack(side="left", padx=2)

# ПРАВО: индикаторы + шаг + Сброс + Выкл — одной линией
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

# статус — отдельной строкой снизу
tk.Label(shooting_frame, textvariable=status_var, font=SMALL)\
    .pack(side="bottom", pady=2)

def on_close():
    stop_preview()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
start_frame.pack(fill="both", expand=True)
root.mainloop()

