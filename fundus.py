import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk
import threading
import datetime
import os
from gpiozero import LED
from picamera2 import Picamera2

# --- Аппаратные объекты ---
led = LED(5)
try:
    led.off()
except Exception:
    pass

picam2 = Picamera2()
preview_config = picam2.create_preview_configuration(main={"size": (640, 480)})
picam2.configure(preview_config)

controls = getattr(picam2, "camera_controls", {})
HAS_LENSPOS = "LensPosition" in controls

# --- Глобальные состояния ---
zoom_factor = 1.0
focus_position = 1.0
save_dir = os.path.expanduser("~/Pictures")
running_preview = False

# --- UI переменные ---
status_var = None
zoom_value_var = None
focus_value_var = None
save_dir_var = None
preview_label = None
preview_area = None
overlay_bar = None

# ---------- Вспомогательные функции ----------
def resize_cover(img, box_w, box_h):
    """
    Масштабируем изображение так, чтобы оно ЗАКРЫВАЛО всю область (cover),
    с сохранением пропорций. Лишнее по краям обрезаем.
    """
    if box_w <= 0 or box_h <= 0:
        return img
    img_w, img_h = img.size
    scale = max(box_w / img_w, box_h / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    img = img.resize((new_w, new_h))
    # Обрезаем по центру до размеров бокса
    x0 = max(0, (new_w - box_w) // 2)
    y0 = max(0, (new_h - box_h) // 2)
    img = img.crop((x0, y0, x0 + box_w, y0 + box_h))
    return img

# --- Камера ---
def apply_zoom():
    global zoom_factor
    zoom_factor = max(1.0, min(zoom_factor, 4.0))
    try:
        sensor_size = picam2.sensor_resolution
        new_w = int(sensor_size[0] / zoom_factor)
        new_h = int(sensor_size[1] / zoom_factor)
        x0 = (sensor_size[0] - new_w) // 2
        y0 = (sensor_size[1] - new_h) // 2
        picam2.set_controls({"ScalerCrop": (x0, y0, new_w, new_h)})
    except Exception as e:
        print("Zoom error:", e)
    zoom_value_var.set(f"{zoom_factor:.1f}x")

def apply_focus():
    if not HAS_LENSPOS:
        focus_value_var.set("авто")
        return
    global focus_position
    focus_position = max(0.0, min(focus_position, 10.0))
    try:
        picam2.set_controls({"LensPosition": focus_position})
        focus_value_var.set(f"{focus_position:.2f}")
    except Exception as e:
        print("Focus error:", e)
        focus_value_var.set("недоступен")

def update_frame():
    if not running_preview:
        return
    try:
        frame = picam2.capture_array()
        img = Image.fromarray(frame)

        # Размер текущей области предпросмотра
        w = max(100, preview_area.winfo_width())
        h = max(100, preview_area.winfo_height())

        # Растягиваем "cover" на весь экран (без чёрных полей)
        img = resize_cover(img, w, h)

        imgtk = ImageTk.PhotoImage(image=img)
        preview_label.imgtk = imgtk
        preview_label.config(image=imgtk)
    except Exception as e:
        print("Preview update error:", e)
    preview_label.after(50, update_frame)  # ~20 FPS

def start_preview():
    global running_preview
    try:
        picam2.start()
        apply_focus()
        apply_zoom()
        running_preview = True
        update_frame()
        led.on()
        status_var.set("Предпросмотр включён")
    except Exception as e:
        status_var.set(f"Ошибка запуска камеры: {e}")

def stop_preview():
    global running_preview
    running_preview = False
    try:
        picam2.stop()
    except Exception:
        pass
    led.off()
    status_var.set("Предпросмотр остановлен")

def take_photo():
    def capture():
        try:
            os.makedirs(save_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(save_dir, f"fundus_{now}.jpg")
            picam2.capture_file(path)
            status_var.set(f"Фото сохранено: {path}")
        except Exception as e:
            status_var.set(f"Ошибка при съёмке: {e}")
            print("Capture error:", e)
    threading.Thread(target=capture, daemon=True).start()

# --- Управление ---
def zoom_in():
    global zoom_factor
    zoom_factor += 0.1
    apply_zoom()

def zoom_out():
    global zoom_factor
    zoom_factor -= 0.1
    apply_zoom()

def focus_near():
    if not HAS_LENSPOS:
        status_var.set("Ручной фокус не поддерживается")
        return
    global focus_position
    focus_position += 0.1
    apply_focus()

def focus_far():
    if not HAS_LENSPOS:
        status_var.set("Ручной фокус не поддерживается")
        return
    global focus_position
    focus_position -= 0.1
    apply_focus()

def back_to_start():
    stop_preview()
    shooting_frame.pack_forget()
    start_frame.pack(fill="both", expand=True)

# --- UI ---
root = tk.Tk()
root.title("Камера глазного дна")
root.geometry("800x480")

# Шрифты: в 2 раза меньше прежних
LARGE = ("Arial", 12)
MID   = ("Arial", 12)
SMALL = ("Arial", 10)

status_var = tk.StringVar(value="")
save_dir_var = tk.StringVar(value=save_dir)
zoom_value_var = tk.StringVar(value=f"{zoom_factor:.1f}x")
focus_value_var = tk.StringVar(value=("авто" if not HAS_LENSPOS else f"{focus_position:.2f}"))

# Экран 1
start_frame = tk.Frame(root, bg="black")
tk.Label(start_frame, text="Прототип камеры", font=("Arial", 18), fg="white", bg="black").pack(pady=10)

path_row = tk.Frame(start_frame, bg="black")
path_row.pack(pady=6, fill="x", padx=12)
tk.Label(path_row, text="Папка сохранения:", font=MID, fg="white", bg="black").pack(side="left")
path_entry = tk.Entry(path_row, textvariable=save_dir_var, font=MID)
path_entry.pack(side="left", expand=True, fill="x", padx=8)

def choose_folder():
    global save_dir
    folder = filedialog.askdirectory(initialdir=save_dir_var.get() or os.path.expanduser("~"))
    if folder:
        save_dir = folder
        save_dir_var.set(folder)

tk.Button(path_row, text="Выбрать…", font=MID, command=choose_folder).pack(side="left")

def go_to_shooting():
    global save_dir
    save_dir = save_dir_var.get() or save_dir
    start_frame.pack_forget()
    shooting_frame.pack(fill="both", expand=True)
    start_preview()

tk.Button(start_frame, text="Включить камеру", font=LARGE, height=1, command=go_to_shooting)\
    .pack(pady=8, padx=12, fill="x")

tk.Label(start_frame, textvariable=status_var, font=SMALL, fg="gray80", bg="black").pack(pady=6)

# Экран 2 (полноэкранная картинка + панель сверху поверх картинки)
shooting_frame = tk.Frame(root, bg="black")

# область предпросмотра занимает весь экран
preview_area = tk.Frame(shooting_frame, bg="black")
preview_area.pack(fill="both", expand=True)

# картинка
preview_label = tk.Label(preview_area, bg="black")
preview_label.pack(fill="both", expand=True)

# полупрозрачности в Tkinter нет, поэтому делаем чёрную панель без рамок
overlay_bar = tk.Frame(preview_area, bg="black")
overlay_bar.place(relx=0, rely=0, relwidth=1, anchor="nw")  # приклеиваем к верхнему краю

# левая группа кнопок
left_group = tk.Frame(overlay_bar, bg="black")
left_group.pack(side="left", padx=6, pady=6)

tk.Button(left_group, text="Фото", command=take_photo, font=LARGE, height=1).pack(side="left", padx=3)
tk.Button(left_group, text="Зум −", command=zoom_out, font=LARGE, height=1).pack(side="left", padx=3)
tk.Button(left_group, text="Зум +", command=zoom_in, font=LARGE, height=1).pack(side="left", padx=3)

# средняя группа (фокус, если доступен)
mid_group = tk.Frame(overlay_bar, bg="black")
mid_group.pack(side="left", padx=6, pady=6)
if HAS_LENSPOS:
    tk.Button(mid_group, text="Ближе", command=focus_near, font=LARGE, height=1).pack(side="left", padx=3)
    tk.Button(mid_group, text="Дальше", command=focus_far, font=LARGE, height=1).pack(side="left", padx=3)

# правая часть — статусы и выключить
right_group = tk.Frame(overlay_bar, bg="black")
right_group.pack(side="right", padx=6, pady=6)

tk.Label(right_group, text="Зум:", font=SMALL, fg="white", bg="black").pack(side="left")
tk.Label(right_group, textvariable=zoom_value_var, font=SMALL, fg="white", bg="black").pack(side="left", padx=(0,6))
tk.Label(right_group, text="Фокус:", font=SMALL, fg="white", bg="black").pack(side="left")
tk.Label(right_group, textvariable=focus_value_var, font=SMALL, fg="white", bg="black").pack(side="left", padx=(0,6))
tk.Label(right_group, textvariable=status_var, font=SMALL, fg="gray80", bg="black").pack(side="left", padx=(0,6))

tk.Button(right_group, text="Выключить", command=back_to_start, font=LARGE, height=1).pack(side="left", padx=3)

def on_close():
    stop_preview()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)

start_frame.pack(fill="both", expand=True)
root.mainloop()
