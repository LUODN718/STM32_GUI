# STM32_GUI · MCUNet 图片推理工作台

基于 PySide6 的桌面端上位机：把图片（或摄像头画面）通过串口发送给运行 TinyEngine
视觉唤醒词（VWW）模型的 STM32 开发板，实时显示 **PERSON / NO PERSON** 推理结果。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![PySide6](https://img.shields.io/badge/UI-PySide6-41cd52)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

## 功能

- **串口连接**：自动枚举串口并支持刷新，波特率可选 921600 / 460800 / 115200
- **单张推理**：拖拽或选择 PNG / JPG / BMP / GIF，自动 EXIF 校正、缩放为 80 × 80 RGB
- **文件夹批量**：按顺序逐张发送整个文件夹，界面实时显示 `批量 i / n` 进度
- **循环测试**：同一张图片反复推理，用于压力与稳定性测试
- **摄像头实时**：调用本机摄像头连续取帧推理
- **结果历史**：表格记录时间、模式、图片名、两类分数、判定结果与往返耗时
- **CSV 导出 / 实时记录**：会话结束后导出，或边推理边写入 CSV
- **运行日志**：可选十六进制收发调试，便于排查通信问题

## 环境要求

- Python 3.10 及以上
- 依赖：`pip install -r requirements.txt`（PySide6、pyserial、Pillow）
- 开发板：STM32 + TinyEngine VWW 固件，输入为 80 × 80 RGB INT8

## 运行

```bash
pip install -r requirements.txt
python main.py
```

界面操作顺序：选择串口 → 连接设备 → 选择图片 → 开始推理。

连接 ESP32 时，串口打开会保持 DTR/RTS 非使能，避免部分 macOS USB-UART
驱动通过这两根控制线触发开发板复位。若使用打包的 `.app`，修改源码后需
在 Mac 本机重新打包安装，旧 `.app` 不会自动更新。

## 串口协议

### 请求（上位机 → 开发板）

| 字段 | 长度（字节） | 说明 |
| --- | --- | --- |
| 帧头 | 2 | `0xAA 0xBB` |
| 图像数据 | 19200 | 80 × 80 × 3，每像素减 128 后按 uint8 发送 |
| 校验 | 2 | 图像数据的逐字节 XOR，小端 uint16 |

### 响应（开发板 → 上位机，固定 6 字节）

| 偏移 | 说明 |
| --- | --- |
| 0 | `no_person` 分数原始值（实际分数 = 原始值 − 128） |
| 1 | `person` 分数原始值（实际分数 = 原始值 − 128） |
| 2–3 | 保留 |
| 4–5 | 帧尾 `0xCC 0xDD` |

判定规则：`person` 分数大于 `no_person` 分数即判为 **PERSON**。

## 目录结构

```
main.py               # 全部界面与串口逻辑（单文件）
requirements.txt      # 运行依赖
assets/               # 应用图标：MCUNet.iconset（10 张 png）与 SVG 源文件
scripts/make_app.sh   # 生成 macOS .app 应用包
tests/                # 协议与状态回归测试（无需硬件）
```

## 打包为 macOS 应用

```bash
bash scripts/make_app.sh                 # 默认输出到 ~/Applications
bash scripts/make_app.sh /path/to/dir    # 或指定输出目录
PYTHON_BIN=/path/to/python bash scripts/make_app.sh   # 指定解释器
```

脚本会生成 `MCUNet 图片推理.app`：`Contents/MacOS/MCUNet` 是一段 zsh 启动器，
用指定的 Python 解释器运行 `Contents/Resources/main.py`，日志写入
`~/Library/Logs/MCUNet/launcher.log`，图标由 `assets/MCUNet.iconset` 经 `iconutil` 生成。

> 启动器里写死了 Python 解释器的绝对路径，默认是 `/opt/miniconda3/envs/STM32-GUI/bin/python`。
> 换机器或换环境时，用 `PYTHON_BIN` 环境变量重新打包即可。

## 测试

```bash
python -m unittest discover -s tests
```

测试使用伪串口对象，不需要连接硬件或摄像头。

## License

个人学习项目，未指定开源许可证。
