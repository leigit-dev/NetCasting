import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import dxcam
import cv2
import numpy as np
import lz4.block as lz4
import socket
import struct
import time
import win32api
import win32gui
import win32con
import io
import ctypes
import gc
import json
import os
import re
from collections import deque
from ctypes import windll, wintypes
from PIL import Image
from screeninfo import get_monitors
import sys

def get_resource_path(relative_path):
    """ 获取资源文件的绝对路径 (兼容开发和 PyInstaller 打包后) """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后运行时的临时目录
        return os.path.join(sys._MEIPASS, relative_path)
    # 开发环境下的当前目录
    return os.path.join(os.path.abspath("."), relative_path)

gc.disable()
cv2.setNumThreads(1)

CONFIG_FILE = "netcasting_config.json"

# ---------------- 光标提取 API ----------------
class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]
class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD), ("bmBits", wintypes.LPVOID)]
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
user32 = windll.user32; gdi32 = windll.gdi32

user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]; user32.GetIconInfo.restype = wintypes.BOOL
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]; gdi32.GetObjectW.restype = ctypes.c_int
user32.GetDC.argtypes = [wintypes.HWND]; user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]; user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]; gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]; gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]; gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]; gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]; gdi32.DeleteObject.restype = wintypes.BOOL
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]; user32.DrawIconEx.restype = wintypes.BOOL

def get_cursor_image_and_hotspot():
    ci = win32gui.GetCursorInfo()
    if not (ci[0] & 0x00000001): return None, 0, 0
    hcursor = ci[1]
    icon_info = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(icon_info)): return None, 0, 0
    hx, hy = icon_info.xHotspot, icon_info.yHotspot
    bm = BITMAP()
    gdi32.GetObjectW(icon_info.hbmMask, ctypes.sizeof(BITMAP), ctypes.byref(bm))
    w, h = bm.bmWidth, bm.bmHeight
    if not icon_info.hbmColor: h = h // 2
    hdc_screen = user32.GetDC(0); hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFOHEADER(); bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER); bmi.biWidth = w; bmi.biHeight = -h; bmi.biPlanes = 1; bmi.biBitCount = 32; bmi.biCompression = 0
    ppv_bits = ctypes.c_void_p(); hbitmap = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(ppv_bits), None, 0)
    old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap); ctypes.memset(ppv_bits, 0, w * h * 4)
    user32.DrawIconEx(hdc_mem, 0, 0, hcursor, w, h, 0, None, 0x0003)
    buffer = (ctypes.c_ubyte * (w * h * 4)).from_address(ppv_bits.value)
    img = Image.frombuffer('RGBA', (w, h), buffer, 'raw', 'BGRA', 0, 1)
    gdi32.SelectObject(hdc_mem, old_bitmap); gdi32.DeleteObject(hbitmap); gdi32.DeleteDC(hdc_mem); user32.ReleaseDC(0, hdc_screen)
    if icon_info.hbmMask: gdi32.DeleteObject(icon_info.hbmMask)
    if icon_info.hbmColor: gdi32.DeleteObject(icon_info.hbmColor)
    return img, hx, hy

try:
    IBEAM_IMG = Image.open(get_resource_path("ibeam.png")).convert("RGBA")
    buf = io.BytesIO(); IBEAM_IMG.save(buf, format='PNG'); IBEAM_BYTES = buf.getvalue(); IBEAM_HOTSPOT = (8, 9)
except Exception:
    IBEAM_BYTES = None; IBEAM_HOTSPOT = (0,0)

ARROW_HANDLE = windll.user32.LoadCursorW(0, win32con.IDC_ARROW)
IBEAM_HANDLE = windll.user32.LoadCursorW(0, win32con.IDC_IBEAM)

# ---------------- 智能解析硬件拓扑 ----------------
def scan_valid_screens(log_func):
    valid_screens = []
    log_func("正在自动解析 DXGI 硬件拓扑结构...")
    
    # 获取物理坐标
    monitors = get_monitors()
    monitors_sorted = sorted(monitors, key=lambda m: (m.x, m.y))
    
    try:
        # 核心：直接解析文本，绝不通过暴力创建实例去试错
        raw_info = dxcam.output_info()
        # 匹配类似: Device[1] Output[0]: Res:(1920, 1080) Rot:0 Primary:False
        pattern = re.compile(r"Device\[(\d+)\] Output\[(\d+)\]: Res:\((\d+),\s*(\d+)\)")
        
        raw_screens = []
        for line in raw_info.split('\n'):
            match = pattern.search(line)
            if match:
                dev = int(match.group(1))
                out = int(match.group(2))
                w = int(match.group(3))
                h = int(match.group(4))
                raw_screens.append({"device": dev, "output": out, "w": w, "h": h})
        
        # 绑定物理坐标
        for i, rs in enumerate(raw_screens):
            # 按顺序匹配物理坐标
            if i < len(monitors_sorted):
                mon = monitors_sorted[i]
                left, top = mon.x, mon.y
            else:
                left, top = 0, 0
                
            valid_screens.append({
                "desc": f"屏幕 {rs['w']}x{rs['h']} | 绑定坐标 (X:{left}, Y:{top}) | [Adapter:{rs['device']}, Output:{rs['output']}]",
                "device": rs["device"], "output": rs["output"],
                "width": rs["w"], "height": rs["h"], "left": left, "top": top
            })
            
    except Exception as e:
        log_func(f"❌ 解析拓扑异常: {e}")
        
    log_func(f"解析完成，共找到 {len(valid_screens)} 个可用的显示输出。")
    return valid_screens

# ---------------- 核心逻辑类 ----------------
class NetCasterSender:
    def __init__(self, target_ip, screen_info, log_queue, stats_queue):
        self.target_ip = target_ip
        self.log_queue = log_queue
        self.stats_queue = stats_queue
        
        self.VIDEO_PORT = 8088
        self.MOUSE_PORT = 8089
        self.FORCE_FULL_INTERVAL = 5.0
        
        self.stop_event = threading.Event()
        self.send_queue = queue.Queue(maxsize=5)
        
        self.device_idx = screen_info["device"]
        self.output_idx = screen_info["output"]
        self.mon_left = screen_info["left"]
        self.mon_top = screen_info["top"]
        self.mon_width = screen_info["width"]
        self.mon_height = screen_info["height"]
        
        self.camera = None
        
        self.capture_stats = deque(maxlen=60); self.diff_stats = deque(maxlen=60)
        self.compress_stats = deque(maxlen=60); self.send_stats = deque(maxlen=60)
        self.loop_count = 0; self.last_stat_time = time.time()

    def log(self, msg): self.log_queue.put(msg)

    def run(self):
        # 加入重试机制，防止 COMError
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.log(f"正在启动 DXcam (Adptr:{self.device_idx}, Out:{self.output_idx})... (尝试 {attempt+1}/{max_retries})")
                self.camera = dxcam.create(device_idx=self.device_idx, output_idx=self.output_idx, output_color="BGR")
                if self.camera:
                    self.camera.start(target_fps=0, video_mode=True)
                    break
            except Exception as e:
                self.log(f"⚠️ 初始化失败: {e}")
                if self.camera:
                    del self.camera
                    self.camera = None
                time.sleep(1.0)
        
        if not self.camera:
            self.log("❌ DXcam 初始化彻底失败，请重启虚拟显示器或电脑。")
            return

        self.log("✅ 捕获与发送线程已启动")
        threading.Thread(target=self.mouse_sender_thread, daemon=True).start()
        threading.Thread(target=self.video_send_worker, daemon=True).start()

        prev_frame = None
        frame_id = 0
        last_full_time = time.time()
        force_fullscreen = True

        while not self.stop_event.is_set():
            self.loop_count += 1
            t0 = time.perf_counter()
            try:
                frame = self.camera.get_latest_frame()
            except Exception as e:
                self.log(f"❌ 抓帧异常: {e}")
                time.sleep(0.1)
                continue
                
            self.capture_stats.append((time.perf_counter() - t0) * 1000)
            if frame is None: time.sleep(0.001); continue

            if time.time() - last_full_time >= self.FORCE_FULL_INTERVAL:
                force_fullscreen = True; last_full_time = time.time()

            t0 = time.perf_counter()
            if force_fullscreen:
                x, y, w, h = 0, 0, self.mon_width, self.mon_height
                roi = frame; prev_frame = frame; force_fullscreen = False; need_send = True
            else:
                diff = cv2.absdiff(frame, prev_frame)
                thresh = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(thresh, 10, 255, cv2.THRESH_BINARY)
                if cv2.countNonZero(thresh) == 0:
                    need_send = False
                else:
                    x, y, w, h = cv2.boundingRect(cv2.findNonZero(thresh))
                    pad = 4
                    x = max(0, x - pad); y = max(0, y - pad)
                    w = min(self.mon_width - x, w + 2*pad); h = min(self.mon_height - y, h + 2*pad)
                    roi = frame[y:y+h, x:x+w]; prev_frame = frame; need_send = True
            
            self.diff_stats.append((time.perf_counter() - t0) * 1000)
            if not need_send:
                time.sleep(0.001); self.update_stats_ui(); continue

            t0 = time.perf_counter()
            compressed = lz4.compress(roi.tobytes(), store_size=False)
            self.compress_stats.append((time.perf_counter() - t0) * 1000)
            
            frame_id += 1
            try: self.send_queue.put_nowait((frame_id, x, y, w, h, compressed))
            except queue.Full: pass
            self.update_stats_ui()

        self.cleanup()

    def cleanup(self):
        if self.camera:
            try:
                self.camera.stop()
                del self.camera
                self.camera = None
            except: pass
        self.log("🛑 捕获已停止，资源已释放")

    def update_stats_ui(self):
        now = time.time()
        if now - self.last_stat_time >= 1.0:
            fps = self.loop_count; self.loop_count = 0; self.last_stat_time = now
            def get_avg(lst): return sum(lst)/len(lst) if lst else 0.0
            self.stats_queue.put({"fps": fps, "capture": get_avg(self.capture_stats), "diff": get_avg(self.diff_stats), "compress": get_avg(self.compress_stats), "send": get_avg(self.send_stats)})

    def mouse_sender_thread(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        last_handle = 0; last_hx, last_hy = 0, 0
        cursor_cache = {} 
        if IBEAM_BYTES: cursor_cache[IBEAM_HANDLE] = (IBEAM_BYTES, IBEAM_HOTSPOT[0], IBEAM_HOTSPOT[1])

        while not self.stop_event.is_set():
            cx, cy = win32api.GetCursorPos()
            ci = win32gui.GetCursorInfo()
            is_showing = ci[0] & 0x00000001; hcursor = ci[1]
            
            is_in_screen = (self.mon_left <= cx < self.mon_left + self.mon_width and
                            self.mon_top <= cy < self.mon_top + self.mon_height)
            
            if not is_in_screen or not is_showing:
                ctype = 0; img_bytes = b""; hx, hy = 0, 0; last_handle = 0
            else:
                ctype = 1
                rel_x = cx - self.mon_left; rel_y = cy - self.mon_top
                
                if hcursor != last_handle:
                    if hcursor in cursor_cache: img_bytes, hx, hy = cursor_cache[hcursor]
                    else:
                        img, hx, hy = get_cursor_image_and_hotspot()
                        if img:
                            buf = io.BytesIO(); img.save(buf, format='PNG'); img_bytes = buf.getvalue()
                            cursor_cache[hcursor] = (img_bytes, hx, hy)
                        else: img_bytes = b""
                    last_handle = hcursor; last_hx, last_hy = hx, hy
                else: img_bytes = b""; hx, hy = last_hx, last_hy

            if ctype == 0: rel_x, rel_y = 0, 0
                
            header = struct.pack('<BiiHHI', ctype, rel_x, rel_y, hx, hy, len(img_bytes))
            try: sock.sendto(header + img_bytes, (self.target_ip, self.MOUSE_PORT))
            except Exception: pass
            time.sleep(0.008)

    def video_send_worker(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
        MAX_PAYLOAD = 1472
        while not self.stop_event.is_set():
            try:
                item = self.send_queue.get(timeout=0.1)
                if item is None: break
                frame_id, x, y, w, h, compressed = item
                total_chunks = (len(compressed) + MAX_PAYLOAD - 1) // MAX_PAYLOAD
                t0 = time.perf_counter()
                for i in range(total_chunks):
                    chunk = compressed[i*MAX_PAYLOAD : (i+1)*MAX_PAYLOAD]
                    header = struct.pack('!6H I', x, y, w, h, i, total_chunks, frame_id)
                    try: sock.sendto(header + chunk, (self.target_ip, self.VIDEO_PORT))
                    except Exception: pass
                self.send_stats.append((time.perf_counter() - t0) * 1000)
            except queue.Empty: pass

    def stop(self):
        self.stop_event.set(); self.send_queue.put(None)

# ---------------- GUI 界面 ----------------
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("NetCasting - Sender")
        self.root.geometry("600x480")
        self.root.resizable(False, False)
        
        self.sender = None
        self.log_queue = queue.Queue()
        self.stats_queue = queue.Queue()
        self.scanned_screens = []
        
        self.setup_ui()
        self.load_config()
        self.root.after(100, self.process_queues)
        
        threading.Thread(target=self.async_scan, daemon=True).start()

    def setup_ui(self):
        frame_top = ttk.LabelFrame(self.root, text="串流配置")
        frame_top.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_top, text="目标设备 IP:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.ip_entry = ttk.Entry(frame_top, width=20)
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(frame_top, text="选择要共享的屏幕:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.display_combo = ttk.Combobox(frame_top, state="readonly", width=55)
        self.display_combo.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        frame_btn = ttk.Frame(frame_top)
        frame_btn.grid(row=2, column=0, columnspan=2, pady=10)
        self.start_btn = ttk.Button(frame_btn, text="▶ 开始串流", command=self.start_stream, state="disabled")
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ttk.Button(frame_btn, text="⏹ 停止", command=self.stop_stream, state="disabled")
        self.stop_btn.pack(side="left", padx=10)

        frame_mid = ttk.LabelFrame(self.root, text="实时状态")
        frame_mid.pack(fill="x", padx=10, pady=5)
        self.lbl_fps = ttk.Label(frame_mid, text="FPS: -", font=("Arial", 10, "bold"))
        self.lbl_fps.pack(anchor="w", padx=10, pady=2)
        self.lbl_detail = ttk.Label(frame_mid, text="捕获: -  |  差分: -  |  压缩: -  |  发包: -")
        self.lbl_detail.pack(anchor="w", padx=10, pady=2)

        frame_bottom = ttk.LabelFrame(self.root, text="运行日志")
        frame_bottom.pack(fill="both", expand=True, padx=10, pady=5)
        self.log_text = tk.Text(frame_bottom, height=8, state="disabled", bg="#f0f0f0", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def async_scan(self):
        self.scanned_screens = scan_valid_screens(self.log)
        
        def update_ui():
            desc_list = [s["desc"] for s in self.scanned_screens]
            self.display_combo['values'] = desc_list
            if desc_list:
                self.display_combo.current(len(desc_list) - 1) 
                self.start_btn.config(state="normal")
            else:
                self.log("❌ 错误：未找到任何可用的 DXGI 抓取设备！")
                
        self.root.after(0, update_ui)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f: self.ip_entry.insert(0, json.load(f).get("target_ip", ""))
            except: pass

    def save_config(self, ip):
        with open(CONFIG_FILE, "w") as f: json.dump({"target_ip": ip}, f)

    def log(self, msg): self.log_queue.put(msg)

    def start_stream(self):
        ip = self.ip_entry.get().strip()
        idx = self.display_combo.current()
        if not ip: messagebox.showwarning("警告", "请输入目标 IP 地址！"); return
        if idx < 0: messagebox.showwarning("警告", "请选择屏幕！"); return

        self.save_config(ip)
        selected_screen = self.scanned_screens[idx]

        self.start_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.ip_entry.config(state="disabled"); self.display_combo.config(state="disabled")

        self.sender = NetCasterSender(ip, selected_screen, self.log_queue, self.stats_queue)
        threading.Thread(target=self.sender.run, daemon=True).start()

    def stop_stream(self):
        if self.sender: 
            self.sender.stop()
            self.sender = None
        self.start_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        self.ip_entry.config(state="normal"); self.display_combo.config(state="readonly")

    def process_queues(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log_text.see("end"); self.log_text.config(state="disabled")

        while not self.stats_queue.empty():
            stats = self.stats_queue.get()
            self.lbl_fps.config(text=f"主循环 FPS: {stats['fps']}")
            self.lbl_detail.config(text=f"捕获: {stats['capture']:.1f}ms | 差分: {stats['diff']:.1f}ms | 压缩: {stats['compress']:.1f}ms | 发包: {stats['send']:.1f}ms")

        self.root.after(50, self.process_queues)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_stream(), root.destroy()))
    root.mainloop()