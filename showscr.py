import dxcam
cam = dxcam.create(device_idx=1, output_idx=0, output_color="BGR")
if cam:
    print("✅ 成功创建 DXCamera 实例！")
    frame = cam.grab()
    if frame is not None:
        print(f"✅ 成功抓取画面，大小: {frame.shape}")
    else:
        print("⚠️ 实例创建成功，但抓取返回 None（可能屏幕没变化或休眠了）")
    cam.stop()
else:
    print("❌ 创建 DXCamera 失败")