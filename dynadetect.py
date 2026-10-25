import dxcam
import cv2
import numpy as np
import lz4.frame as lz4
import time

# ================= 配置 =================
DEVICE_IDX = 1            # 虚拟显卡所在的适配器索引
OUTPUT_IDX = 0            # 该适配器上的输出索引（副屏）
TARGET_FPS = 60

DIFF_THRESHOLD = 10       # 像素差异阈值
CHANGE_RATIO_THRESHOLD = 0.6  # 60% 面积变化判定为剧变
LZ4_LEVEL = 0
PAD = 4
# ========================================

def get_changed_rect(frame_bgra, prev_bgra, threshold):
    # 1. 快速差分
    diff_bgra = cv2.absdiff(frame_bgra, prev_bgra)
    diff_gray = cv2.cvtColor(diff_bgra, cv2.COLOR_BGRA2GRAY)
    _, thresh = cv2.threshold(diff_gray, threshold, 255, cv2.THRESH_BINARY)

    # 2. 变化率计算
    nonzero = cv2.countNonZero(thresh)
    total_pixels = thresh.shape[0] * thresh.shape[1]
    changed_ratio = nonzero / total_pixels if total_pixels else 0.0

    if nonzero == 0:
        return None, changed_ratio

    # 3. 外接矩形
    pts = cv2.findNonZero(thresh)
    x, y, w, h = cv2.boundingRect(pts)

    # 扩展矩形防止边缘截断
    x = max(0, x - PAD)
    y = max(0, y - PAD)
    w = min(frame_bgra.shape[1] - x, w + 2 * PAD)
    h = min(frame_bgra.shape[0] - y, h + 2 * PAD)

    return (x, y, w, h), changed_ratio

def main():
    print(f"正在初始化 DXcam (Device={DEVICE_IDX}, Output={OUTPUT_IDX})...")
    # 强制指定 output_color="BGRA"，以便与 OpenCV 的默认处理逻辑兼容
    camera = dxcam.create(device_idx=DEVICE_IDX, output_idx=OUTPUT_IDX, output_color="BGRA")
    
    if camera is None:
        print("❌ 初始化失败，请检查设备索引。")
        return

    # 开启后台 60fps 连续捕获
    camera.start(target_fps=TARGET_FPS, video_mode=True)
    print("✅ 捕获已启动。按 Ctrl+C 停止。\n")

    prev_frame = None
    fps_start = time.time()
    fps_counter = 0

    print("=" * 80)
    print(f"{'FPS':>5} | {'变化率':>8} | {'矩形 (x,y,w,h)':>22} | {'模式':>6} | "
          f"{'获取帧ms':>8} {'差分ms':>7} {'LZ4ms':>6} | {'LZ4数据量':>10}")
    print("-" * 80)

    try:
        while True:
            t0 = time.perf_counter()
            # 从后台缓冲区获取最新帧（非阻塞）
            frame = camera.get_latest_frame()
            t_capture = (time.perf_counter() - t0) * 1000

            if frame is None:
                continue

            if prev_frame is None:
                h, w = frame.shape[:2]
                print(f"首帧就绪，分辨率: {w}x{h}\n")
                prev_frame = frame
                continue

            # 差分与矩形检测
            t0 = time.perf_counter()
            rect, changed_ratio = get_changed_rect(frame, prev_frame, DIFF_THRESHOLD)
            t_diff = (time.perf_counter() - t0) * 1000

            # 模式判定
            is_dramatic = changed_ratio > CHANGE_RATIO_THRESHOLD
            mode = "剧变" if is_dramatic else "交互"

            # LZ4 压缩耗时与数据量测算
            t_lz4 = 0.0
            compressed_size = 0
            if rect is not None:
                x, y, w, h = rect
                roi = frame[y:y + h, x:x + w]
                raw_bytes = roi.tobytes()

                t0 = time.perf_counter()
                compressed_data = lz4.compress(raw_bytes, compression_level=LZ4_LEVEL)
                t_lz4 = (time.perf_counter() - t0) * 1000
                compressed_size = len(compressed_data) / 1024  # KB

            # FPS 统计
            fps_counter += 1
            now = time.time()
            if now - fps_start >= 1.0:
                fps = fps_counter / (now - fps_start)
                fps_start = now
                fps_counter = 0
            else:
                fps = 0

            # 打印输出
            rect_str = f"({x:4d},{y:4d},{w:4d},{h:4d})" if rect else "      ---"
            fps_str = f"{fps:5.1f}" if fps > 0 else "     "
            size_str = f"{compressed_size:8.1f} KB" if rect else "      ---"
            
            print(f"{fps_str} | {changed_ratio*100:6.1f}% | {rect_str:>22} | "
                  f"{mode:>4} | {t_capture:8.2f} {t_diff:7.2f} {t_lz4:6.2f} | {size_str:>10}")

            prev_frame = frame

    except KeyboardInterrupt:
        print("\n停止捕获。")
    finally:
        camera.stop()
        del camera # 显式释放 DXGI 资源

if __name__ == "__main__":
    main()