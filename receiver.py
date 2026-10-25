import socket
import struct
import lz4.block as lz4
import numpy as np
import pygame
import threading
import io
import time
import sys
import queue

# ================= 配置 =================
LISTEN_PORT_VIDEO = 8088
LISTEN_PORT_MOUSE = 8089
TIMEOUT_SECONDS = 15.0 
# ========================================

a_width, a_height = 0, 0
display_surf = None 
mouse_state = {"type": 0, "x": 0, "y": 0, "hotspot_x": 0, "hotspot_y": 0, "surface": None, "scaled_surface": None}

frame_queue = queue.Queue(maxsize=5) 
# 🔥 缓存：避免重复加载和计算
cursor_surface_cache = {}
last_scale_ratio = -1 # 用于检测缩放比例是否变化

last_packet_time = 0.0
is_streaming = False

def get_all_ips():
    ips = []
    try:
        hostname = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(hostname)
        for ip in ip_list:
            if not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips if ips else ["无法获取局域网 IP"]

def video_receiver_thread():
    global last_packet_time
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024) 
    
    try:
        sock.bind(("0.0.0.0", LISTEN_PORT_VIDEO))
    except Exception as e:
        sys.exit(1)

    frame_buffer = {}
    last_frame_id_displayed = 0

    while True:
        data, _ = sock.recvfrom(65536)
        if len(data) < 16: continue
        
        x, y, w, h, chunk_idx, total_chunks, frame_id = struct.unpack('!6H I', data[:16])
        chunk_data = data[16:]

        if frame_id <= last_frame_id_displayed: continue 
        now = time.time()
        last_packet_time = now

        # A 端重启检测
        if last_frame_id_displayed > 100 and frame_id < last_frame_id_displayed - 100:
            frame_buffer.clear()
            last_frame_id_displayed = 0
            continue

        if len(frame_buffer) > 500 or (frame_id in frame_buffer and now - frame_buffer[frame_id]["time"] > 2.0):
            frame_buffer.clear()

        if frame_id not in frame_buffer:
            frame_buffer[frame_id] = {"total": total_chunks, "chunks": {}, "time": now}
        frame_buffer[frame_id]["chunks"][chunk_idx] = chunk_data
        
        if len(frame_buffer[frame_id]["chunks"]) == total_chunks:
            full_data = b"".join(frame_buffer[frame_id]["chunks"][i] for i in range(total_chunks))
            try:
                raw_bytes = lz4.decompress(full_data, uncompressed_size=(w*h*3))
                if not frame_queue.full():
                    frame_queue.put(("FRAME", x, y, w, h, raw_bytes))
                last_frame_id_displayed = frame_id
            except Exception:
                pass
            del frame_buffer[frame_id]

def mouse_receiver_thread():
    global mouse_state
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", LISTEN_PORT_MOUSE))
    except Exception as e:
        sys.exit(1)
    
    while True:
        data, _ = sock.recvfrom(65536)
        if len(data) < 17: continue
        ctype, mx, my, hx, hy, img_size = struct.unpack('<BiiHHI', data[:17])
        
        mouse_state["type"] = ctype
        mouse_state["x"] = mx
        mouse_state["y"] = my
        mouse_state["hotspot_x"] = hx
        mouse_state["hotspot_y"] = hy
        
        if img_size > 0 and len(data) >= 17 + img_size:
            img_data = data[17:17+img_size]
            img_key = hash(img_data)
            if img_key not in cursor_surface_cache:
                try:
                    img_stream = io.BytesIO(img_data)
                    # 🔥 优化1：加载时开启 RLEACCEL 硬件加速，极速透明融合
                    surface = pygame.image.load(img_stream).convert_alpha()
                    surface = surface.convert_alpha() 
                    cursor_surface_cache[img_key] = surface
                except Exception:
                    pass
            
            # 当鼠标图片更新时，重置缓存
            if mouse_state["surface"] != cursor_surface_cache[img_key]:
                mouse_state["surface"] = cursor_surface_cache[img_key]
                mouse_state["scaled_surface"] = None 

def draw_waiting_screen(screen, font_title, font_ip, font_desc, logo_surf, ips, screen_w, screen_h):
    screen.fill((30, 30, 34))
    
    if logo_surf:
        logo_rect = logo_surf.get_rect(center=(screen_w//2, screen_h//2 - 150))
        screen.blit(logo_surf, logo_rect)
    else:
        text_logo = font_title.render("NetCasting", True, (0, 200, 255))
        logo_rect = text_logo.get_rect(center=(screen_w//2, screen_h//2 - 150))
        screen.blit(text_logo, logo_rect)

    wait_text = font_desc.render("等待串流连接...", True, (200, 200, 200))
    wait_rect = wait_text.get_rect(center=(screen_w//2, screen_h//2 -80))
    screen.blit(wait_text, wait_rect)

    start_x = screen_w // 2 - 150 
    ip_y = screen_h // 2 + 120
    
    title_text = font_ip.render("可用 IP：", True, (150, 150, 150))
    title_rect = title_text.get_rect(left=start_x, top=ip_y - 60)
    screen.blit(title_text, title_rect)

    for ip in ips:
        ip_text = font_ip.render(f"●  {ip}", True, (0, 255, 100))
        ip_rect = ip_text.get_rect(left=start_x, top=ip_y)
        screen.blit(ip_text, ip_rect)
        ip_y += 45
        
    desc_text = font_desc.render("请在 Sender 端输入 IP 建立连接。按 ESC 退出。", True, (100, 100, 100))
    desc_rect = desc_text.get_rect(center=(screen_w//2, screen_h - 50))
    screen.blit(desc_text, desc_rect)

def main():
    global is_streaming, last_packet_time, display_surf, a_width, a_height, last_scale_ratio
    
    pygame.init()
    info = pygame.display.Info()
    SCREEN_W, SCREEN_H = info.current_w, info.current_h
    
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN | pygame.DOUBLEBUF)
    pygame.display.set_caption("NetCasting Receiver")
    pygame.mouse.set_visible(False) 
    clock = pygame.time.Clock()

    try:
        font_title = pygame.font.SysFont("Microsoft YaHei", 60, bold=True)
        font_ip = pygame.font.SysFont("Microsoft YaHei", 24, bold=True)
        font_desc = pygame.font.SysFont("Microsoft YaHei", 24)
    except:
        font_title = pygame.font.Font(None, 60)
        font_ip = pygame.font.Font(None, 40)
        font_desc = pygame.font.Font(None, 24)

    logo_surf = None
    try:
        logo_surf = pygame.image.load("logo.png").convert_alpha()
    except: pass

    local_ips = get_all_ips()
    threading.Thread(target=video_receiver_thread, daemon=True).start()
    threading.Thread(target=mouse_receiver_thread, daemon=True).start()
    
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False

        # 1. 渲染网络数据
        while not frame_queue.empty():
            msg = frame_queue.get()
            if msg[0] == "FRAME":
                _, x, y, w, h, raw_bytes = msg
                try:
                    new_w = x + w
                    new_h = y + h
                    
                    if new_w > a_width or new_h > a_height:
                        a_width = max(a_width, new_w)
                        a_height = max(a_height, new_h)
                        
                        new_surf = pygame.Surface((a_width, a_height))
                        new_surf.fill((0, 0, 0))
                        if display_surf:
                            new_surf.blit(display_surf, (0, 0))
                        display_surf = new_surf
                        
                        # 画面尺寸变了，强制鼠标下次重算缩放
                        last_scale_ratio = -1 

                    roi_surf = pygame.image.frombuffer(raw_bytes, (w, h), "BGR")
                    display_surf.blit(roi_surf, (x, y))
                except Exception:
                    pass

        # 2. 状态判断
        now = time.time()
        if now - last_packet_time > TIMEOUT_SECONDS:
            if is_streaming:
                mouse_state["type"] = 0 
                is_streaming = False
        else:
            if not is_streaming:
                is_streaming = True

        # 3. 渲染逻辑
        if is_streaming and display_surf is not None:
            screen.fill((0, 0, 0)) 
            
            is_1_to_1 = (a_width == SCREEN_W and a_height == SCREEN_H)
            
            if is_1_to_1:
                screen.blit(display_surf, (0, 0))
            else:
                scale = min(SCREEN_W / a_width, SCREEN_H / a_height)
                scaled_w = int(a_width * scale)
                scaled_h = int(a_height * scale)
                offset_x = (SCREEN_W - scaled_w) // 2
                offset_y = (SCREEN_H - scaled_h) // 2
                
                scaled_surf = pygame.transform.scale(display_surf, (scaled_w, scaled_h))
                screen.blit(scaled_surf, (offset_x, offset_y))

            # 鼠标渲染
            if mouse_state["type"] == 1 and mouse_state["surface"] is not None:
                mx = mouse_state["x"]
                my = mouse_state["y"]
                hx = mouse_state["hotspot_x"]
                hy = mouse_state["hotspot_y"]
                
                if is_1_to_1:
                    draw_x = mx - hx
                    draw_y = my - hy
                    # 🔥 优化2：直接贴图，不做任何转换
                    screen.blit(mouse_state["surface"], (draw_x, draw_y))
                else:
                    scale = min(SCREEN_W / a_width, SCREEN_H / a_height)
                    offset_x = (SCREEN_W - int(a_width * scale)) // 2
                    offset_y = (SCREEN_H - int(a_height * scale)) // 2
                    
                    draw_x = int((mx - hx) * scale) + offset_x
                    draw_y = int((my - hy) * scale) + offset_y
                    
                    # 🔥 优化3：只有当缩放比例变化，或者鼠标图片变了，才重新计算 scale
                    if scale != last_scale_ratio or mouse_state.get("scaled_surface") is None:
                        cw = max(1, int(mouse_state["surface"].get_width() * scale))
                        ch = max(1, int(mouse_state["surface"].get_height() * scale))
                        mouse_state["scaled_surface"] = pygame.transform.scale(mouse_state["surface"], (cw, ch))
                        last_scale_ratio = scale
                        
                    screen.blit(mouse_state["scaled_surface"], (draw_x, draw_y))
        else:
            draw_waiting_screen(screen, font_title, font_ip, font_desc, logo_surf, local_ips, SCREEN_W, SCREEN_H)

        pygame.display.flip()
        clock.tick(0)

    pygame.quit()

if __name__ == "__main__":
    main()