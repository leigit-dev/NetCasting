import ctypes
from ctypes import wintypes
from PIL import Image
import time

# 定义 Windows API 结构体和常量
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]

class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", wintypes.LPVOID),
    ]

# 常量
DI_NORMAL = 0x0003
CURSOR_SHOWING = 0x00000001

def extract_cursor(save_path="current_cursor.png"):
    # 1. 获取当前光标信息
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        print("❌ 获取光标信息失败")
        return

    if not (ci.flags & CURSOR_SHOWING):
        print("⚠️ 光标当前处于隐藏状态 (可能正在打字或未在窗口内)")
        return

    hcursor = ci.hCursor

    # 2. 获取图标信息（包含位图和热点坐标）
    icon_info = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(icon_info)):
        print("❌ 获取光标图标信息失败")
        return

    # 获取热点 (Hotspot)
    hotspot_x = icon_info.xHotspot
    hotspot_y = icon_info.yHotspot
    print(f"✅ 捕捉到光标！热点坐标 (Hotspot): ({hotspot_x}, {hotspot_y})")
    print(f"   这意味着鼠标真正的点击位置在图像的 ({hotspot_x}, {hotspot_y}) 处。")

    # 3. 获取位图尺寸
    bm = BITMAP()
    gdi32.GetObjectW(icon_info.hbmMask, ctypes.sizeof(BITMAP), ctypes.byref(bm))
    width = bm.bmWidth
    height = bm.bmHeight
    
    # 如果是单色光标，hbmColor 为 NULL，高度是 Mask 的两倍 (包含 AND/XOR Mask)
    if not icon_info.hbmColor:
        height = height // 2

    # 4. 创建一个 32 位带 Alpha 通道的内存 DC
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    
    # 创建 DIB Section 用于接收像素
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height # 负数表示自上而下的位图
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0 # BI_RGB

    ppv_bits = ctypes.c_void_p()
    hbitmap = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(ppv_bits), None, 0)
    
    # 将 DIB 选入内存 DC
    old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap)

    # 5. 将光标绘制到内存 DC 中
    # 先清空背景为透明（全 0）
    ctypes.memset(ppv_bits, 0, width * height * 4)
    
    # 绘制光标
    if not user32.DrawIconEx(hdc_mem, 0, 0, hcursor, width, height, 0, None, DI_NORMAL):
        print("❌ 绘制光标失败")
    
    # 6. 从内存中提取 BGRA 数据，并使用 Pillow 保存
    # 此时 ppv_bits 指向的数据格式是 BGRA (Blue, Green, Red, Alpha)
    buffer_size = width * height * 4
    buffer = (ctypes.c_ubyte * buffer_size).from_address(ppv_bits.value)
    
    # Pillow 需要 RGBA
    img = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1)
    
    # 保存图片
    img.save(save_path)
    print(f"✅ 光标图像已保存至: {save_path} (尺寸: {width}x{height})")

    # 7. 清理资源
    gdi32.SelectObject(hdc_mem, old_bitmap)
    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    user32.DestroyIcon(hcursor) # 注意：如果是系统共享光标，不要随意 Destroy，这里为了安全通常不 Destroy，系统会自己回收

if __name__ == "__main__":
    print("请在 3 秒内将鼠标移动到本窗口上方（确保可见）...")
    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)
    
    extract_cursor("my_cursor.png")