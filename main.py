"""
STM32F746G-DISCO 串口通信工具

依赖安装（建议使用虚拟环境）：
    pip install PySide6 pyserial
"""
import sys
import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

import serial
from serial import SerialException
from serial.tools import list_ports

from PIL import Image
import struct


class SerialMonitorWindow(QMainWindow):
    """STM32F746G-DISCO 串口通信 GUI 主窗口。"""

    def __init__(self) -> None:
        super().__init__()

        self.serial_port: serial.Serial | None = None
        self.is_connected = False  # 串口连接状态标记
        self.baud_rate = 115200    # STM32F746默认波特率（匹配硬件）

        self.setWindowTitle("STM32F746G-DISCO 串口通信工具")
        self.resize(600, 400)

        self._init_ui()
        self._init_timer()
        self.refresh_ports()

    # ---------- UI 初始化 ----------
    def _init_ui(self) -> None:
        """初始化界面布局和控件。"""
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # 顶部栏：串口选择 + 刷新 + 连接/断开 + 清屏
        top_layout = QHBoxLayout()

        port_label = QLabel("串口:")
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("refresh")
        self.connect_button = QPushButton("connect")
        self.clear_log_button = QPushButton("清屏")

        top_layout.addWidget(port_label)
        top_layout.addWidget(self.port_combo, stretch=1)
        top_layout.addWidget(self.refresh_button)
        top_layout.addWidget(self.connect_button)
        top_layout.addWidget(self.clear_log_button)

        # 中间：日志显示区
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        # 设置日志区字体和颜色（提升可读性）
        self.log_edit.setStyleSheet("font-family: Consolas; font-size: 12px;")

        # 图片发送区域
        image_layout = QHBoxLayout()
        self.image_path_label = QLabel("未选择图片")
        self.select_image_button = QPushButton("选择图片")
        self.send_image_button = QPushButton("发送图片")

        image_layout.addWidget(self.image_path_label, stretch=1)
        image_layout.addWidget(self.select_image_button)
        image_layout.addWidget(self.send_image_button)

        # 底部栏：输入框 + 发送
        bottom_layout = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入指令，按回车或点击发送（自动添加换行）")
        self.send_button = QPushButton("发送")

        bottom_layout.addWidget(self.input_edit, stretch=1)
        bottom_layout.addWidget(self.send_button)

        # 组合布局
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.log_edit, stretch=1)
        main_layout.addLayout(image_layout)
        main_layout.addLayout(bottom_layout)

        # 信号连接
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.toggle_connection)
        self.send_button.clicked.connect(self.send_command)
        self.input_edit.returnPressed.connect(self.send_command)

        self.clear_log_button.clicked.connect(self.clear_log)

        self.select_image_button.clicked.connect(self.choose_image_file)
        self.send_image_button.clicked.connect(self.send_image_to_mcu)

    # ---------- 定时器初始化（非阻塞读串口） ----------
    def _init_timer(self) -> None:
        """初始化串口读取定时器（避免UI卡顿）。"""
        self.read_timer = QTimer(self)
        self.read_timer.setInterval(100)  # 100ms读取一次串口（平衡实时性和性能）
        self.read_timer.timeout.connect(self.read_serial_data)

    # ---------- 串口相关功能 ----------
    def refresh_ports(self) -> None:
        """刷新可用串口列表（适配Windows系统）。"""
        self.port_combo.clear()
        try:
            # 枚举所有串口，显示「COM口 - 设备描述」（方便识别STM32）
            ports = list_ports.comports()
            if not ports:
                self.port_combo.addItem("无可用串口")
                self.append_log("提示：未检测到任何串口设备")
                return

            for port in ports:
                port_info = f"{port.device} - {port.description}"
                self.port_combo.addItem(port_info, port.device)  # 存储纯COM口名到数据区
        except Exception as e:
            self.append_log(f"刷新串口失败：{str(e)}", is_error=True)

    def toggle_connection(self) -> None:
        """切换串口连接/断开状态。"""
        if self.is_connected:
            # 断开串口
            self._disconnect_serial()
        else:
            # 连接串口
            self._connect_serial()

    def _connect_serial(self) -> None:
        """建立串口连接（核心逻辑）。"""
        # 检查是否选中有效串口
        if self.port_combo.currentIndex() < 0 or self.port_combo.currentText() == "无可用串口":
            QMessageBox.warning(self, "警告", "请选择有效的串口！")
            return

        # 获取选中的COM口（纯设备名，如COM5）
        selected_port = self.port_combo.currentData()
        if not selected_port:
            selected_port = self.port_combo.currentText().split(" - ")[0]  # 兼容无数据的情况

        try:
            # 打开串口（timeout=0 非阻塞模式）
            self.serial_port = serial.Serial(
                port=selected_port,
                baudrate=self.baud_rate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0
            )
            self.is_connected = True
            self.connect_button.setText("断开")
            self.read_timer.start()  # 启动定时器，开始读取串口数据
            self.append_log(f"成功连接：{selected_port} (波特率：{self.baud_rate})")
        except SerialException as e:
            # 捕获串口异常（被占用、不存在等）
            self.append_log(f"连接失败：{str(e)}", is_error=True)
            QMessageBox.critical(self, "错误", f"串口连接失败：{str(e)}")

    def _disconnect_serial(self) -> None:
        """断开串口连接。"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.read_timer.stop()  # 停止定时器
        self.is_connected = False
        self.connect_button.setText("连接")
        self.append_log("已断开串口连接")

    # ---------- 数据收发功能 ----------
    def send_command(self) -> None:
        """发送指令到STM32（自动添加换行符）。"""
        if not self.is_connected:
            QMessageBox.warning(self, "警告", "请先连接串口！")
            return

        # 获取输入框内容并去空
        command = self.input_edit.text().strip()
        if not command:
            return

        try:
            # 发送数据（添加\n结尾，匹配STM32按行解析的习惯）
            send_data = (command + "\n").encode("utf-8")
            self.serial_port.write(send_data)
            self.append_log(f"TX: {command}")
            self.input_edit.clear()  # 清空输入框
        except SerialException as e:
            self.append_log(f"发送失败：{str(e)}", is_error=True)
            self._disconnect_serial()  # 发送失败自动断开

    def read_serial_data(self) -> None:
        """读取STM32发来的串口数据（定时器触发）。"""
        if not self.is_connected or not self.serial_port:
            return

        try:
            # 读取所有可用数据（按行解析）
            while self.serial_port.in_waiting > 0:
                # 读取一行数据，忽略编码错误，去除换行符
                data = self.serial_port.readline().decode("utf-8", errors="ignore").rstrip("\r\n")
                if data:  # 仅显示非空数据
                    self.append_log(f"RX: {data}")
        except SerialException as e:
            self.append_log(f"读取数据失败：{str(e)}", is_error=True)
            self._disconnect_serial()  # 读取失败自动断开

    # ---------- 日志辅助功能 ----------
    def append_log(self, text: str, is_error: bool = False) -> None:
        """添加日志到显示区（带时间戳，错误标红）。"""
        # 生成时间戳（格式：YYYY-MM-DD HH:MM:SS）
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 日志格式：[时间] 内容
        log_text = f"[{timestamp}] {text}"

        # 错误日志标红，普通日志默认颜色
        self.log_edit.moveCursor(QTextCursor.End)
        if is_error:
            self.log_edit.setTextColor(Qt.red)
        else:
            self.log_edit.setTextColor(Qt.black)
        self.log_edit.insertPlainText(log_text + "\n")
        # 恢复默认颜色，避免后续日志继承红色
        self.log_edit.setTextColor(Qt.black)
        # 自动滚动到最新日志
        self.log_edit.ensureCursorVisible()

    def clear_log(self) -> None:
        """清空日志显示区。"""
        self.log_edit.clear()

    # ---------- 图片发送相关 ----------
    def choose_image_file(self) -> None:
        """选择图片文件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片文件",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if file_path:
            self.image_path_label.setText(file_path)

    def send_image_to_mcu(self) -> None:
        """将选择的图片转换为数组并通过串口发送。"""
        if not self.is_connected or not self.serial_port:
            QMessageBox.warning(self, "警告", "请先连接串口！")
            return

        image_path = self.image_path_label.text()
        if not image_path or image_path == "未选择图片":
            QMessageBox.warning(self, "警告", "请先选择图片！")
            return

        try:
            # 1. 读入图片并转换为灰度，缩放到合适分辨率（示例：128x128）
            target_width, target_height = 128, 128
            img = Image.open(image_path).convert("L")
            img = img.resize((target_width, target_height))

            # 2. 转为一维字节数组（每像素 1 字节）
            pixel_bytes = img.tobytes()

            # 3. 构造简单协议头
            # [2字节 固定帧头 0x55AA]
            # [2字节 宽度]
            # [2字节 高度]
            # [4字节 数据长度]
            header = struct.pack(
                "<H H H I",
                0x55AA,
                target_width,
                target_height,
                len(pixel_bytes),
            )

            # 4. 发送
            self.serial_port.write(header)
            self.serial_port.write(pixel_bytes)
            self.serial_port.flush()

            self.append_log(
                f"已发送图片：{image_path}，尺寸 {target_width}x{target_height}，字节数 {len(pixel_bytes)}"
            )
        except Exception as e:
            self.append_log(f"发送图片失败：{str(e)}", is_error=True)
            QMessageBox.critical(self, "错误", f"发送图片失败：{str(e)}")

    # ---------- 窗口关闭时清理资源 ----------
    def closeEvent(self, event) -> None:
        """窗口关闭时自动断开串口，释放资源。"""
        if self.is_connected:
            self._disconnect_serial()
        event.accept()


# ---------- 程序入口 ----------
def main() -> None:
    app = QApplication(sys.argv)
    window = SerialMonitorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

