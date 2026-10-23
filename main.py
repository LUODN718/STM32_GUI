"""MCUNet Studio: serial image inference workbench for TinyEngine VWW."""
import csv
import datetime
import importlib.util
from pathlib import Path
import struct
import sys
import time

from PIL import Image, ImageOps
import serial
from serial import SerialException
from serial.tools import list_ports
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QSplitter,
)

IMG_W, IMG_H, IMG_CH = 80, 80, 3
IMG_SIZE = IMG_W * IMG_H * IMG_CH
HEADER, RESP_MAGIC = b'\xaa\xbb', b'\xcc\xdd'
IMG_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
MODES = {'single': '单张图片', 'folder': '文件夹', 'loop': '循环测试', 'camera': '摄像头', 'benchmark': '推理基准', 'selftest': '设备自检'}
CSV_FIELDS = ['timestamp', 'mode', 'image', 'person_score', 'no_person_score', 'detection', 'round_trip_ms']


def checksum(data):
    c = 0
    for b in data:
        c ^= b
    return c & 0xFFFF


def preprocess(path):
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert('RGB').resize((IMG_W, IMG_H), Image.Resampling.BILINEAR)
        return bytes((b - 128) & 0xFF for b in image.tobytes())


def packet(data):
    return HEADER + data + struct.pack('<H', checksum(data))


def parse_resp(buf):
    if len(buf) < 6 or buf[4:6] != RESP_MAGIC:
        return None
    return {'ps': buf[1] - 128, 'ns': buf[0] - 128, 'det': buf[1] > buf[0], 'raw0': buf[0], 'raw1': buf[1], 'infer_ms': int.from_bytes(buf[2:4], 'little')}


def text(value, name=None):
    label = QLabel(value)
    if name:
        label.setObjectName(name)
    return label


def button(value, slot, kind=None):
    result = QPushButton(value)
    result.setCursor(Qt.PointingHandCursor)
    if kind:
        result.setObjectName(kind)
    result.clicked.connect(slot)
    return result


def card():
    result = QFrame()
    result.setObjectName('card')
    layout = QVBoxLayout(result)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(10)
    return result, layout


class Preview(QWidget):
    dropped = Signal(str)
    browse = Signal()

    def __init__(self):
        super().__init__()
        self.image = QPixmap()
        self.dragging = False
        self.setAcceptDrops(True)
        self.setMinimumSize(300, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('点击选择图片，或将图片拖到这里')

    def show_image(self, image):
        image = image.convert('RGB')
        qimage = QImage(image.tobytes(), image.width, image.height, image.width * 3, QImage.Format_RGB888).copy()
        self.image = QPixmap.fromImage(qimage)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QColor('#eaf8f5' if self.dragging else '#f4f7f9'))
        painter.setPen(QPen(QColor('#15a58c' if self.dragging else '#dfe7ec'), 1, Qt.DashLine))
        painter.drawRoundedRect(rect, 12, 12)
        if not self.image.isNull():
            scaled = self.image.scaled(self.size() - QSize(32, 32), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)
            return
        painter.setFont(QFont('PingFang SC', 13))
        painter.setPen(QColor('#72828f'))
        painter.drawText(self.rect(), Qt.AlignCenter, '点击选择图片，或将图片拖到这里')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.browse.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(Path(u.toLocalFile()).suffix.lower() in IMG_EXTS for u in event.mimeData().urls()):
            self.dragging = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.dragging = False
        self.update()

    def dropEvent(self, event):
        self.dragging = False
        self.update()
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in IMG_EXTS:
                self.dropped.emit(path)
                event.acceptProposedAction()
                break


STYLE = '''
QWidget { font-family: "PingFang SC", "Helvetica Neue"; font-size: 12px; color: #263544; }
QMainWindow, QWidget#workspace { background: #f3f4f6; }
QFrame#card { background: white; border: 1px solid #dce1e6; border-radius: 8px; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; color: #243647; }
QLabel#muted { font-size: 11px; color: #71808e; }
QPushButton { background: white; border: 1px solid #cfd7de; border-radius: 5px; padding: 7px 12px; }
QPushButton:hover { background: #eef2f5; border-color: #a2b0bc; }
QPushButton:pressed { background: #dfe7ed; }
QPushButton:disabled { background: #f6f7f8; border-color: #e2e6ea; color: #9aa5af; }
QPushButton#primary { background: #247e6b; border-color: #247e6b; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #1c6959; }
QPushButton#primary:disabled { background: #b2ccc4; border-color: #b2ccc4; color: white; }
QPushButton#danger { color: #ad4c52; }
QPushButton#danger:disabled { color: #aab3ba; }
QComboBox { background: white; border: 1px solid #cfd7de; border-radius: 5px; padding: 6px 10px; min-height: 18px; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background: white; color: #263544; selection-background-color: #e3eee9; }
QProgressBar { background: #edf0f3; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #459782; border-radius: 3px; }
QProgressBar#noPerson::chunk { background: #8297ae; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: transparent; color: #7d8994; padding: 3px 14px 9px 0; margin-right: 12px; }
QTabBar::tab:selected { color: #247e6b; font-weight: 600; }
QTableWidget { border: none; background: white; selection-background-color: #e7f1ec; selection-color: #263544; }
QHeaderView::section { background: #f3f5f7; border: none; color: #667684; padding: 7px; font-size: 11px; }
QTableWidget::item { padding: 5px; border-bottom: 1px solid #eef1f4; }
QTextEdit { background: #f6f7f9; border: none; border-radius: 4px; font-family: Menlo; font-size: 10px; padding: 6px; }
QCheckBox { font-size: 11px; color: #71808e; spacing: 5px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #cbd3da; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #263544; background: white; border: 1px solid #d2d9df; padding: 5px; }
QSplitter::handle:vertical { background: #dfe6ea; height: 8px; }
QSplitter::handle:vertical:hover { background: #247e6b; }
'''


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sp = None
        self.conn = False
        self.baud = 921600
        self.port_meta = {}
        self.device_profile = {}
        self.device_caps = {}
        self.rbuf = bytearray()
        self.pending = None
        # Hardware information is an optional capability.  Keep its request
        # separate from an inference request so boards without the command do
        # not sit in an indefinite wait state.
        self.info_pending = None
        self.image_path = None
        self.folder_imgs = []
        self.folder_idx = 0
        self.loop_data = None
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self.history = []
        self.bench_count = 0
        self.bench_times = []
        self.bench_source = '内置灰度测试图'
        self._mode = None
        self._cap = None
        self._cam_frame = 0
        self.recording = False
        self.rec_file = None
        self.rec_writer = None
        self.setWindowTitle('MCUNet · 图片推理')
        self.resize(1040, 760)
        self.setMinimumSize(900, 700)
        self._ui()
        self._rt = QTimer(self)
        self._rt.setInterval(30)
        self._rt.timeout.connect(self._read)
        self._rt.start()
        self._stimer = QTimer(self)
        self._stimer.setInterval(600)
        self._stimer.timeout.connect(self._tick)
        self.refresh_ports()
        self._sync_controls()
        self._log('工作台已就绪。连接设备后，选择一张图片开始推理。')

    def _ui(self):
        root = QWidget()
        root.setObjectName('workspace')
        self.setCentralWidget(root)
        body = QVBoxLayout(root)
        body.setContentsMargins(16, 16, 16, 12)
        body.setSpacing(12)

        connection, cl = card()
        cl.setContentsMargins(14, 12, 14, 12)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(text('串口'))
        self.pcb = QComboBox()
        self.pcb.setMinimumWidth(245)
        self.pcb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pcb.setMinimumContentsLength(15)
        self.pcb.currentIndexChanged.connect(lambda: self.pcb.setToolTip(self.pcb.currentData() or '未发现串口'))
        row.addWidget(self.pcb, 1)
        row.addWidget(text('波特率'))
        self.bcb = QComboBox()
        self.bcb.addItems(['921600', '460800', '115200'])
        self.bcb.setFixedWidth(110)
        self.bcb.currentTextChanged.connect(lambda value: setattr(self, 'baud', int(value)))
        row.addWidget(self.bcb)
        self.rbtn = button('刷新', self.refresh_ports)
        self.cbtn = button('连接设备', self._toggle, 'primary')
        row.addWidget(self.rbtn)
        row.addWidget(self.cbtn)
        self.infobtn = button('设备信息', self._request_hardware_info)
        row.addWidget(self.infobtn)
        self.status_pill = text('未连接', 'muted')
        self.status_pill.setMinimumWidth(66)
        self.status_pill.setAlignment(Qt.AlignCenter)
        row.addWidget(self.status_pill)
        cl.addLayout(row)
        body.addWidget(connection)

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(12)
        upper.setMinimumHeight(300)

        middle = QHBoxLayout()
        middle.setSpacing(12)
        input_card, il = card()
        il.setContentsMargins(16, 14, 16, 14)
        title_row = QHBoxLayout()
        title_row.addWidget(text('图片预览', 'sectionTitle'))
        title_row.addStretch()
        title_row.addWidget(text('输入 80 × 80 · RGB', 'muted'))
        il.addLayout(title_row)
        self.prev = Preview()
        self.prev.browse.connect(self._sel_img)
        self.prev.dropped.connect(self._select_image)
        il.addWidget(self.prev, 1)
        info = QHBoxLayout()
        self.ilbl = text('未选择图片', 'muted')
        self.ilbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info.addWidget(self.ilbl, 1)
        self.image_info = text('', 'muted')
        info.addWidget(self.image_info)
        il.addLayout(info)
        actions = QHBoxLayout()
        self.selbtn = button('选择图片…', self._sel_img)
        self.sendbtn = button('发送并推理', self._send_one, 'primary')
        actions.addWidget(self.selbtn)
        actions.addWidget(self.sendbtn, 1)
        il.addLayout(actions)
        middle.addWidget(input_card, 3)

        result_card, rl = card()
        rl.setContentsMargins(18, 14, 18, 14)
        rl.setSpacing(10)
        rl.addWidget(text('推理结果', 'sectionTitle'))
        rl.addStretch(1)
        self.result_mark = text('')
        self.result_mark.hide()
        self.rlbl = text('等待推理')
        self.rlbl.setAlignment(Qt.AlignCenter)
        self.rlbl.setStyleSheet('font-size: 22px; font-weight: 600; color: #25394b;')
        self.rlbl.setMinimumHeight(34)
        rl.addWidget(self.rlbl)
        self.result_hint = text('连接设备并选择图片后，点击发送', 'muted')
        self.result_hint.setAlignment(Qt.AlignCenter)
        rl.addWidget(self.result_hint)
        rl.addStretch(1)
        self.score_labels, self.score_bars = [], []
        for idx, label in enumerate(['有人', '无人']):
            score_row = QHBoxLayout()
            score_row.addWidget(text(label))
            score_row.addStretch()
            value = text('—')
            self.score_labels.append(value)
            score_row.addWidget(value)
            rl.addLayout(score_row)
            bar = QProgressBar()
            bar.setRange(0, 255)
            bar.setValue(0)
            bar.setTextVisible(False)
            if idx:
                bar.setObjectName('noPerson')
            self.score_bars.append(bar)
            rl.addWidget(bar)
        rl.addWidget(text('模型原始分数，较高的一类为检测结果。', 'muted'))
        stat_row = QHBoxLayout()
        self.metric_values = [text('0'), text('0'), text('—'), text('0')]
        for label, index in [('完成', 0), ('有人', 1), ('异常', 3)]:
            stat_row.addWidget(text(label, 'muted'))
            stat_row.addWidget(self.metric_values[index])
            stat_row.addSpacing(8)
        stat_row.addStretch()
        rl.addLayout(stat_row)
        middle.addWidget(result_card, 2)
        upper_layout.addLayout(middle, 4)

        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.fbtn = button('文件夹批量…', self._start_folder)
        self.loopbtn = button('循环当前图片', self._start_loop)
        self.cambtn = button('摄像头', self._start_cam)
        self.camera_available = all(importlib.util.find_spec(module) for module in ('cv2', 'numpy'))
        if not self.camera_available:
            self.cambtn.setToolTip('当前环境未安装 OpenCV 和 NumPy，暂不可用')
            self.cambtn.hide()
        self.stopbtn = button('停止', self._stop, 'danger')
        self.selftestbtn = button('设备自检', self._start_self_test)
        self.benchbtn = button('推理基准', self._start_benchmark)
        for widget in (self.fbtn, self.loopbtn, self.cambtn, self.selftestbtn, self.benchbtn, self.stopbtn):
            modes.addWidget(widget)
        self.mode_label = text('单张模式', 'muted')
        modes.addWidget(self.mode_label)
        modes.addStretch()
        upper_layout.addLayout(modes)

        bottom, bl = card()
        bl.setContentsMargins(14, 12, 14, 10)
        bl.setSpacing(8)
        controls = QHBoxLayout()
        controls.addWidget(text('运行记录', 'sectionTitle'))
        controls.addStretch()
        self.debug_check = QCheckBox('串口原始数据')
        controls.addWidget(self.debug_check)
        self.recbtn = button('实时记录…', self._toggle_record)
        self.exportbtn = button('导出 CSV…', self._export)
        self.lbtn = button('清空当前页', self._clear_view)
        for widget in (self.recbtn, self.exportbtn, self.lbtn):
            controls.addWidget(widget)
        bl.addLayout(controls)
        self.tabs = QTabWidget()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['时间', '图片 / 来源', '结果', '有人 / 无人分数', '收发耗时'])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(70)
        self.tabs.addTab(self.table, '结果历史  0')
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log.setMinimumHeight(150)
        self.log.document().setMaximumBlockCount(500)
        self.tabs.addTab(self.log, '运行日志')
        bl.addWidget(self.tabs, 1)
        self.recpath_lbl = text('可导出本次会话的推理结果。', 'muted')
        bl.addWidget(self.recpath_lbl)
        bottom.setMinimumHeight(205)
        splitter = QSplitter(Qt.Vertical)
        splitter.setObjectName('workbenchSplitter')
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(upper)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([470, 250])
        self.splitter = splitter
        body.addWidget(splitter, 1)
        self.footer = text('就绪 · 连接设备后开始', 'muted')
        self.footer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.addWidget(self.footer)

    def refresh_ports(self):
        selected = self.pcb.currentData()
        self.pcb.clear()
        self.port_meta = {}
        ports = sorted(list_ports.comports(), key=lambda port: ('usb' not in port.device.lower(), port.device))
        for port in ports:
            self.port_meta[port.device] = port
            name = f'{port.description} · {port.device.rsplit("/", 1)[-1]}'
            self.pcb.addItem(name, port.device)
        index = self.pcb.findData(selected)
        if index >= 0:
            self.pcb.setCurrentIndex(index)
        if not ports:
            self.pcb.addItem('未发现串口设备', None)

    def _sync_controls(self):
        running = self._mode is not None
        busy = self.pending is not None or self.info_pending is not None
        self.cbtn.setText('断开连接' if self.conn else '连接设备')
        self.pcb.setEnabled(not self.conn)
        self.bcb.setEnabled(not self.conn)
        self.rbtn.setEnabled(not self.conn)
        self.sendbtn.setEnabled(self.conn and self.image_path is not None and not running and not busy)
        self.selbtn.setEnabled(not running and not busy)
        self.prev.setEnabled(not running and not busy)
        self.fbtn.setEnabled(self.conn and not running and not busy)
        self.loopbtn.setEnabled(self.conn and self.image_path is not None and not running and not busy)
        self.cambtn.setEnabled(self.conn and self.camera_available and not running and not busy)
        self.infobtn.setEnabled(self.conn and not running and not busy and not self.info_pending)
        self.selftestbtn.setEnabled(self.conn and not running and not busy)
        self.benchbtn.setEnabled(self.conn and not running and not busy)
        self.stopbtn.setEnabled(running)
        self.exportbtn.setEnabled(bool(self.history))
        self.mode_label.setText(f'批量 {self.folder_idx} / {len(self.folder_imgs)}' if self._mode == 'folder' else MODES.get(self._mode, '单张模式') + (' · 运行中' if running else ''))
        self.status_pill.setText('● 已连接' if self.conn else '未连接')
        self.status_pill.setStyleSheet('color: #247e6b;' if self.conn else '')

    def _toggle(self):
        if self.conn:
            self._disconnect()
            return
        port = self.pcb.currentData()
        if not port:
            QMessageBox.information(self, '连接设备', '请先插入开发板，再刷新串口列表。')
            return
        try:
            # macOS's built-in CH340 path is reliable at 460800 but corrupts
            # 921600 traffic. Keep STM32 at its selected rate and transparently
            # lower only the ESP32 USB-serial port.
            if (sys.platform == 'darwin' and self.baud == 921600 and
                    'usbserial' in port.lower()):
                self.baud = 460800
                self.bcb.setCurrentText('460800')
                self._log('检测到 macOS CH340，自动切换到 460800 baud')
            # Some USB-UART bridges wire DTR/RTS to ESP32 reset/boot circuitry.
            # Set both inactive before opening, since opening can reset the chip.
            self.sp = serial.Serial()
            self.sp.port = port
            self.sp.baudrate = self.baud
            self.sp.timeout = 0
            self.sp.write_timeout = 2
            self.sp.dtr = False
            self.sp.rts = False
            self.sp.open()
            self.sp.reset_input_buffer()
            self.rbuf.clear()
            self.conn = True
            self.footer.setText(f'已连接 {port}  ·  {self.baud:,} baud')
            self._log(f'设备连接成功：{port} @ {self.baud}')
            self._log_port_metadata(port)
            self._log('连接已建立；点击“设备信息”读取能力，点击“设备自检”或“推理基准”执行测试')
            self._sync_controls()
        except (SerialException, OSError) as exc:
            self._log(f'连接失败：{exc}', True)
            QMessageBox.warning(self, '连接失败', str(exc))

    def _disconnect(self):
        self._stop()
        self.info_pending = None
        if self.pending:
            self._fail_pending('连接已断开，本次推理未完成')
        if self.sp:
            self.sp.close()
        self.sp = None
        self.conn = False
        self.rbuf.clear()
        self.device_profile = {}
        self.device_caps = {}
        self.footer.setText('设备已断开')
        self._log('设备已断开')
        self._sync_controls()

    def _log_port_metadata(self, device):
        """Log USB-level information available without board firmware support."""
        port = self.port_meta.get(device)
        if port is None:
            return
        fields = []
        description = getattr(port, 'description', None)
        if description:
            fields.append(f'description={description}')
        vid, pid = getattr(port, 'vid', None), getattr(port, 'pid', None)
        if vid is not None and pid is not None:
            fields.append(f'vid=0x{vid:04x} pid=0x{pid:04x}')
        for name in ('manufacturer', 'product', 'serial_number', 'location'):
            value = getattr(port, name, None)
            if value:
                fields.append(f'{name}={value}')
        if fields:
            self._log('USB设备信息：' + ' | '.join(fields))

    def _sel_img(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择图片', str(Path.home()), '图片 (*.png *.jpg *.jpeg *.bmp *.gif)')
        if path:
            self._select_image(path)

    def _select_image(self, path):
        if self._mode or self.pending:
            return
        self._show_image(path)
        self._sync_controls()

    def _show_image(self, path):
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                width, height = image.size
                image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                self.prev.show_image(image)
            self.image_path = str(path)
            self.ilbl.setText(Path(path).name)
            self.ilbl.setToolTip(str(path))
            self.image_info.setText(f'{width} × {height} → 80 × 80')
            self.footer.setText(f'已选择 {Path(path).name}  ·  RGB / INT8')
            return True
        except Exception as exc:
            self._log(f'无法读取图片：{exc}', True)
            return False

    def _load(self, path):
        try:
            return preprocess(path)
        except Exception as exc:
            self._error(f'图片处理失败：{exc}')
            return None

    def _send_one(self):
        if self.image_path and self.conn and not self.pending:
            data = self._load(self.image_path)
            if data is not None:
                self._write(data, Path(self.image_path).name, 'single')

    def _request_hardware_info(self):
        if not self.conn or self.pending or self.info_pending:
            return
        try:
            self.info_pending = time.monotonic()
            self.sp.write(b'\xa5\x5a\x01')
            self.sp.flush()
            self._log('正在读取硬件信息…')
            self._sync_controls()
        except (SerialException, OSError) as exc:
            self.info_pending = None
            self._log(f'读取硬件信息失败：{exc}', True)

    def _start_benchmark(self):
        if not self.conn:
            return
        if self.image_path:
            self.loop_data = self._load(self.image_path)
            if self.loop_data is None:
                return
            self.bench_source = Path(self.image_path).name
        else:
            self.loop_data = bytes(IMG_SIZE)
            self.bench_source = '内置灰度测试图'
            self._log('未选择图片，推理基准使用内置灰度测试图')
        self.bench_count = 0
        self.bench_times = []
        self._start_mode('benchmark')

    def _start_self_test(self):
        if not self.conn or self.pending or self._mode:
            return
        # A neutral 80x80 RGB frame exercises the transport, checksum,
        # preprocessing contract and model invocation without requiring a
        # user-selected image.
        self._write(bytes(IMG_SIZE), '内置灰度测试图', 'selftest')

    def _write(self, data, name, mode):
        if not self.conn or self.pending:
            return False
        self.rbuf.clear()
        try:
            self.sp.reset_input_buffer()
            self.pending = {'name': name, 'mode': mode, 'started': time.monotonic()}
            frame = packet(data)
            if self.sp.write(frame) != len(frame):
                raise SerialException('图片未完整发送')
            self.rlbl.setText('正在推理…')
            self.result_hint.setText('等待开发板返回结果')
            self.footer.setText(f'已发送 {name}  ·  {len(data):,} 字节')
            self._log(f'发送图片：{name} [{MODES[mode]}]')
            self._sync_controls()
            return True
        except (SerialException, OSError) as exc:
            self._fail_pending(f'发送失败：{exc}')
            self._stop()
            return False

    def _read(self):
        if not self.conn or not self.sp:
            return
        try:
            if self.sp.in_waiting:
                chunk = self.sp.read(self.sp.in_waiting)
                self.rbuf.extend(chunk)
                if self.debug_check.isChecked():
                    self._log(f'RX [{len(chunk)}] {chunk[:48].hex(" ")}')
                self._read_profile_lines()
                while len(self.rbuf) >= 6:
                    result = parse_resp(self.rbuf[:6])
                    if result:
                        del self.rbuf[:6]
                        if self.pending:
                            self._on_result(result)
                        else:
                            self._log('收到无对应请求的数据帧，已忽略')
                    else:
                        del self.rbuf[0]
            if self.info_pending and time.monotonic() - self.info_pending > 2:
                self.info_pending = None
                self._log('当前固件未返回硬件信息；该功能需要设备固件实现 A5 5A 01 查询命令。', True)
                self._sync_controls()
            if self.pending and time.monotonic() - self.pending['started'] > 10:
                message = ('设备自检超时：未收到有效推理结果。'
                           if self.pending['mode'] == 'selftest'
                           else '等待结果超时。请检查波特率和开发板固件。')
                self._fail_pending(message)
                self._stop()
        except (SerialException, OSError) as exc:
            if self.info_pending:
                self.info_pending = None
                self._log(f'读取硬件信息失败：{exc}（设备可能已重启或重新枚举）', True)
            else:
                self._log(f'串口读取失败：{exc}', True)
            self._disconnect()

    def _read_profile_lines(self):
        prefixes = (b'INFO ', b'CAPS ', b'MODEL ')
        while True:
            matches = [(self.rbuf.find(prefix), prefix) for prefix in prefixes]
            matches = [(index, prefix) for index, prefix in matches if index >= 0]
            if not matches:
                return
            start, prefix = min(matches, key=lambda item: item[0])
            newline = self.rbuf.find(b'\n', start)
            if newline < 0:
                return
            line = bytes(self.rbuf[start:newline]).decode('ascii', 'replace').strip()
            del self.rbuf[:newline + 1]
            kind, _, payload = line.partition(' ')
            values = {}
            for token in payload.split():
                key, separator, value = token.partition('=')
                if separator:
                    values[key] = value
            if kind == 'INFO':
                self.info_pending = None
                self.device_profile.update(values)
                self._log(f'硬件信息：{payload}')
            elif kind == 'CAPS':
                self.device_caps.update(values)
                self._log(f'设备能力：{payload}')
            else:
                self.device_profile.update({f'model_{key}': value for key, value in values.items()})
                self._log(f'当前模型：{payload}')
            self._sync_controls()

    def _on_result(self, result):
        pending, self.pending = self.pending, None
        if pending['mode'] == 'benchmark':
            self.bench_count += 1
            self.bench_times.append(result['infer_ms'])
        elapsed = (time.monotonic() - pending['started']) * 1000
        detected = result['det']
        label = '检测到人' if detected else '未检测到人'
        self.stats['person' if detected else 'no_person'] += 1
        self.result_mark.setText('●' if detected else '○')
        self.result_mark.setStyleSheet(f'font-size: 32px; color: {"#15957d" if detected else "#7894ae"};')
        self.rlbl.setText(label)
        self.result_hint.setText(f'收发耗时 {elapsed:.0f} ms')
        for value, bar, score, raw in zip(self.score_labels, self.score_bars, [result['ps'], result['ns']], [result['raw1'], result['raw0']]):
            value.setText(f'{score:+d}')
            bar.setValue(raw)
        timestamp = datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')
        record = [timestamp, pending['mode'], pending['name'], result['ps'], result['ns'], 'PERSON' if detected else 'NO PERSON', round(elapsed, 1)]
        self.history.append(record)
        if self.recording:
            try:
                self.rec_writer.writerow(record)
                self.rec_file.flush()
            except OSError as exc:
                self._stop_record()
                self._log(f'记录写入失败：{exc}', True)
        self.table.insertRow(0)
        for column, value in enumerate([timestamp[11:19], pending['name'], label, f'{result["ps"]:+d} / {result["ns"]:+d}', f'{elapsed:.0f} ms']):
            item = QTableWidgetItem(value)
            item.setToolTip(str(value))
            if column == 2:
                item.setForeground(QColor('#16836c' if detected else '#6e87a2'))
            self.table.setItem(0, column, item)
        if self.table.rowCount() > 300:
            self.table.removeRow(300)
        self.tabs.setTabText(0, f'结果历史  {len(self.history)}')
        self.metric_values[2].setText(f'{elapsed:.0f} ms')
        self._update_stats()
        self._log(f'{pending["name"]} → {label} | 有人 {result["ps"]:+d} / 无人 {result["ns"]:+d} | {elapsed:.0f} ms')
        if pending['mode'] == 'selftest':
            self._log(f'设备自检通过：推理 {result["infer_ms"]} ms，收发耗时 {elapsed:.0f} ms')
        if pending['mode'] == 'benchmark' and self.bench_count >= 5:
            average = sum(self.bench_times) / len(self.bench_times)
            self._stop()
            self._log(f'推理基准完成：5 次平均推理 {average:.1f} ms，约 {1000 / average:.2f} FPS')
        self.footer.setText(f'{pending["name"]}  ·  {label}  ·  收发耗时 {elapsed:.0f} ms')
        self._sync_controls()

    def _update_stats(self):
        self.metric_values[0].setText(str(self.stats['person'] + self.stats['no_person']))
        self.metric_values[1].setText(str(self.stats['person']))
        self.metric_values[3].setText(str(self.stats['err']))

    def _error(self, message):
        self.stats['err'] += 1
        self._update_stats()
        self._log(message, True)
        self.footer.setText(message)

    def _fail_pending(self, message):
        self.pending = None
        self.rlbl.setText('推理未完成')
        self.result_hint.setText('请查看运行日志')
        self._error(message)
        self._sync_controls()

    def _start_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择图片文件夹', str(Path.home()))
        if not folder:
            return
        self.folder_imgs = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS)
        if not self.folder_imgs:
            QMessageBox.information(self, '批量推理', '该文件夹内没有支持的图片。')
            return
        self.folder_idx = 0
        self._start_mode('folder')

    def _start_loop(self):
        if not self.image_path:
            return
        self.loop_data = self._load(self.image_path)
        if self.loop_data is not None:
            self._start_mode('loop')

    def _start_cam(self):
        if not self.camera_available:
            return
        import cv2
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            QMessageBox.warning(self, '摄像头', '无法打开摄像头，请检查连接和系统权限。')
            return
        self._cam_frame = 0
        self._start_mode('camera')

    def _start_mode(self, mode):
        if not self.conn:
            return
        self._mode = mode
        self._stimer.start(250 if mode == 'camera' else 600)
        self._sync_controls()
        self._log(f'开始{MODES[mode]}推理')
        self._tick()

    def _tick(self):
        if self.pending or not self.conn:
            return
        if self._mode == 'folder':
            if self.folder_idx >= len(self.folder_imgs):
                self._stop()
                self.footer.setText(f'批量推理已完成，共处理 {len(self.folder_imgs)} 张图片')
                return
            path = self.folder_imgs[self.folder_idx]
            self.folder_idx += 1
            self.mode_label.setText(f'批量 {self.folder_idx} / {len(self.folder_imgs)}')
            if self._show_image(path):
                data = self._load(path)
                if data is not None:
                    self._write(data, path.name, 'folder')
            else:
                self._error(f'跳过无法读取的图片：{path.name}')
        elif self._mode == 'loop':
            self._write(self.loop_data, Path(self.image_path).name, 'loop')
        elif self._mode == 'benchmark':
            self._write(self.loop_data, f'基准 #{self.bench_count + 1}', 'benchmark')
        elif self._mode == 'camera' and self._cap:
            import cv2
            ok, frame = self._cap.read()
            if not ok:
                self._error('摄像头读取失败')
                self._stop()
                return
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.prev.show_image(image)
            self._cam_frame += 1
            name = f'摄像头 #{self._cam_frame:04d}'
            self.ilbl.setText(name)
            self.image_info.setText(f'{image.width} × {image.height} → 80 × 80')
            raw = image.resize((IMG_W, IMG_H), Image.Resampling.BILINEAR).tobytes()
            self._write(bytes((b - 128) & 0xFF for b in raw), name, 'camera')

    def _stop(self):
        was_running = self._mode is not None
        self._stimer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None
        self._mode = None
        if was_running:
            self._log('连续推理已停止' + ('，等待当前图片返回' if self.pending else ''))
        self._sync_controls()

    def _export(self):
        if not self.history:
            return
        default = Path.home() / f'MCUNet_结果_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv'
        path, _ = QFileDialog.getSaveFileName(self, '导出推理记录', str(default), 'CSV (*.csv)')
        if not path:
            return
        if not Path(path).suffix:
            path += '.csv'
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as output:
                writer = csv.writer(output)
                writer.writerow(CSV_FIELDS)
                writer.writerows(self.history)
            self._log(f'已导出 {len(self.history)} 条记录：{path}')
            self.footer.setText(f'已导出 {len(self.history)} 条记录至 {Path(path).name}')
        except OSError as exc:
            QMessageBox.warning(self, '导出失败', str(exc))

    def _toggle_record(self):
        if self.recording:
            self._stop_record()
            return
        default = Path.home() / f'MCUNet_实时记录_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv'
        path, _ = QFileDialog.getSaveFileName(self, '实时记录到 CSV', str(default), 'CSV (*.csv)')
        if not path:
            return
        if not Path(path).suffix:
            path += '.csv'
        try:
            self.rec_file = open(path, 'w', newline='', encoding='utf-8-sig')
            self.rec_writer = csv.writer(self.rec_file)
            self.rec_writer.writerow(CSV_FIELDS)
            self.rec_file.flush()
            self.recording = True
            self.recbtn.setText('● 停止记录')
            self.recpath_lbl.setText(f'正在实时写入：{path}')
            self._log(f'已开启实时记录：{path}')
        except OSError as exc:
            self._stop_record()
            QMessageBox.warning(self, '记录失败', str(exc))

    def _stop_record(self):
        if self.rec_file:
            self.rec_file.close()
        self.rec_file = self.rec_writer = None
        self.recording = False
        self.recbtn.setText('实时记录…')
        self.recpath_lbl.setText('可导出本次会话的推理结果。')

    def _clear_view(self):
        if self.tabs.currentIndex() == 1:
            self.log.clear()
            return
        if self.pending or self._mode:
            self.footer.setText('请先停止推理并等待当前结果，再清空历史。')
            return
        self.history.clear()
        self.table.setRowCount(0)
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self._update_stats()
        self.metric_values[2].setText('—')
        self.tabs.setTabText(0, '结果历史  0')
        self.rlbl.setText('等待下一张图片')
        self.result_hint.setText('本次会话统计已清空')
        self.result_mark.setText('◎')
        for label, bar in zip(self.score_labels, self.score_bars):
            label.setText('—')
            bar.setValue(0)
        self._sync_controls()

    def _log(self, message, err=False):
        self.log.moveCursor(QTextCursor.End)
        self.log.setTextColor(QColor('#bf5c65' if err else '#607888'))
        self.log.insertPlainText(f'{datetime.datetime.now():%H:%M:%S}  {message}\n')
        self.log.ensureCursorVisible()

    def closeEvent(self, event):
        self._stop()
        self._rt.stop()
        self._stop_record()
        if self.sp:
            self.sp.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('MCUNet Studio')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
