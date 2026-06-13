import sys, datetime, struct, time
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
    QWidget, QFileDialog, QGroupBox, QGridLayout,
)
import serial
from serial import SerialException
from serial.tools import list_ports
from PIL import Image

IMG_W, IMG_H, IMG_CH = 80, 80, 3
IMG_SIZE = IMG_W * IMG_H * IMG_CH
HEADER = b'\xaa\xbb'
RESP_MAGIC = b'\xcc\xdd'
IMG_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}

def checksum(data):
    c = 0
    for b in data: c ^= b
    return c & 0xFFFF

def preprocess(path):
    img = Image.open(path).convert('RGB')
    img = img.resize((IMG_W, IMG_H), Image.BILINEAR)
    raw = img.tobytes()
    data = bytearray(IMG_SIZE)
    for i in range(IMG_SIZE):
        data[i] = (raw[i] - 128) & 0xFF
    return bytes(data)

def packet(data):
    c = checksum(data)
    return HEADER + data + struct.pack('<H', c)

def parse_resp(buf):
    if len(buf) < 4 or buf[2:4] != RESP_MAGIC: return None
    return {'ps': buf[0]-128, 'ns': buf[1]-128, 'det': buf[0] <= buf[1]}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sp = None
        self.conn = False
        self.baud = 921600
        self.rbuf = bytearray()
        self.folder_imgs = []
        self.folder_idx = 0
        self.loop_data = None
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self._cap = None
        self._stimer = None
        self._mode = None
        self.setWindowTitle('TinyEngine VWW - Person Detection')
        self.resize(620, 520)
        self._ui()
        self._rt = QTimer(self)
        self._rt.setInterval(50)
        self._rt.timeout.connect(self._read)
        self.refresh_ports()

    def _ui(self):
        cw = QWidget(self); self.setCentralWidget(cw)
        lo = QVBoxLayout(); cw.setLayout(lo)

        h1 = QHBoxLayout()
        h1.addWidget(QLabel('[Serial]'))
        self.pcb = QComboBox(); self.pcb.setMinimumWidth(150)
        h1.addWidget(self.pcb, 1)
        h1.addWidget(QLabel('Baud:'))
        self.bcb = QComboBox()
        self.bcb.addItems(['921600','460800','115200'])
        self.bcb.setCurrentText('921600')
        h1.addWidget(self.bcb)
        self.rbtn = QPushButton('Refresh')
        self.cbtn = QPushButton('Connect')
        self.lbtn = QPushButton('Clear')
        h1.addWidget(self.rbtn); h1.addWidget(self.cbtn); h1.addWidget(self.lbtn)
        lo.addLayout(h1)

        g1 = QGroupBox('Image Sender')
        gl = QVBoxLayout(); g1.setLayout(gl)
        r2 = QHBoxLayout()
        self.ilbl = QLabel('No image selected')
        self.ilbl.setStyleSheet('color: gray;')
        self.selbtn = QPushButton('Select Image')
        self.sendbtn = QPushButton('Send to MCU')
        self.sendbtn.setStyleSheet('font-weight: bold;')
        r2.addWidget(self.ilbl, 1); r2.addWidget(self.selbtn); r2.addWidget(self.sendbtn)
        gl.addLayout(r2)
        r3 = QHBoxLayout()
        self.fbtn = QPushButton('Send Folder')
        self.cambtn = QPushButton('Camera')
        self.loopbtn = QPushButton('Loop Test')
        self.stopbtn = QPushButton('Stop')
        self.stopbtn.setEnabled(False)
        self.stopbtn.setStyleSheet('color: #c62828; font-weight: bold;')
        r3.addWidget(self.fbtn); r3.addWidget(self.cambtn)
        r3.addWidget(self.loopbtn); r3.addWidget(self.stopbtn)
        r3.addStretch(); gl.addLayout(r3); lo.addWidget(g1)

        g2 = QGroupBox('Inference Result')
        gl2 = QGridLayout(); g2.setLayout(gl2)
        self.rlbl = QLabel('Waiting for image...')
        self.rlbl.setAlignment(Qt.AlignCenter)
        self.rlbl.setStyleSheet('font-size: 20px; font-weight: bold; padding: 8px;')
        gl2.addWidget(self.rlbl, 0, 0, 1, 2)
        gl2.addWidget(QLabel('Person score:'), 1, 0)
        self.psl = QLabel('--'); gl2.addWidget(self.psl, 1, 1)
        gl2.addWidget(QLabel('No-person score:'), 2, 0)
        self.nsl = QLabel('--'); gl2.addWidget(self.nsl, 2, 1)
        self.stl = QLabel('Person: 0 | No Person: 0 | Errors: 0')
        self.stl.setStyleSheet('color: #666; font-size: 10px;')
        gl2.addWidget(self.stl, 3, 0, 1, 2)
        lo.addWidget(g2)

        self.prev = QLabel()
        self.prev.setFixedSize(160, 160)
        self.prev.setAlignment(Qt.AlignCenter)
        self.prev.setStyleSheet('border: 1px solid #bbb; background: #f5f5f5;')
        self.prev.setText('Preview')
        lo.addWidget(self.prev)

        g3 = QGroupBox('Log')
        gl3 = QVBoxLayout(); g3.setLayout(gl3)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setStyleSheet('font-family: Consolas; font-size: 10px;')
        gl3.addWidget(self.log)
        lo.addWidget(g3)

        self.rbtn.clicked.connect(self.refresh_ports)
        self.cbtn.clicked.connect(self._toggle)
        self.lbtn.clicked.connect(lambda: self.log.clear())
        self.bcb.currentTextChanged.connect(lambda t: setattr(self, 'baud', int(t)))
        self.selbtn.clicked.connect(self._sel_img)
        self.sendbtn.clicked.connect(self._send_one)
        self.fbtn.clicked.connect(self._start_folder)
        self.cambtn.clicked.connect(self._start_cam)
        self.loopbtn.clicked.connect(self._start_loop)
        self.stopbtn.clicked.connect(self._stop)

    def refresh_ports(self):
        self.pcb.clear()
        for p in list_ports.comports():
            self.pcb.addItem(f'{p.device} - {p.description}', p.device)

    def _toggle(self):
        if self.conn:
            self._stop(); self._rt.stop()
            if self.sp:
                try: self.sp.close()
                except: pass
                self.sp = None
            self.conn = False; self.cbtn.setText('Connect')
            self._log('Disconnected')
        else:
            pn = self.pcb.currentData()
            if not pn:
                QMessageBox.warning(self, 'Warning', 'Select a serial port first.')
                return
            try:
                self.sp = serial.Serial(pn, self.baud, timeout=0.1)
                time.sleep(0.3)
                self.conn = True; self.cbtn.setText('Disconnect')
                self._rt.start()
                self._log(f'Connected {pn} @ {self.baud}')
            except SerialException as e:
                self._log(f'Error: {e}', err=True)
                QMessageBox.critical(self, 'Error', str(e))

    def _read(self):
        if not self.conn or not self.sp: return
        try:
            while self.sp.in_waiting > 0:
                self.rbuf.extend(self.sp.read(self.sp.in_waiting))
                while len(self.rbuf) >= 4:
                    r = parse_resp(bytes(self.rbuf[:4]))
                    if r:
                        self._on_result(r)
                        self.rbuf = self.rbuf[4:]
                    else:
                        self.rbuf = self.rbuf[1:]
                if len(self.rbuf) > 512:
                    self.rbuf = bytearray()
        except SerialException as e:
            self._log(f'Read error: {e}', err=True)
            self._toggle()

    def _on_result(self, r):
        label = 'PERSON' if r['det'] else 'NO PERSON'
        color = '#c62828' if r['det'] else '#2e7d32'
        bg = '#ffcdd2' if r['det'] else '#c8e6c9'
        key = 'person' if r['det'] else 'no_person'
        self.stats[key] += 1
        self.stl.setText(f"Person: {self.stats['person']} | No Person: {self.stats['no_person']} | Errors: {self.stats['err']}")
        self.rlbl.setText(label)
        self.rlbl.setStyleSheet(f'font-size: 24px; font-weight: bold; padding: 8px; color: {color}; background: {bg}; border-radius: 6px;')
        self.psl.setText(str(r['ps']))
        self.nsl.setText(str(r['ns']))
        self._log(f'>>> {label} | person={r["ps"]} no_person={r["ns"]}')

    def _sel_img(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Select Image', '', 'Images (*.png *.jpg *.jpeg *.bmp *.gif)')
        if p:
            self.ilbl.setText(p)
            self.ilbl.setStyleSheet('color: black;')
            self.loop_data = None
            try:
                pix = QPixmap(p).scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.prev.setPixmap(pix)
            except:
                pass

    def _check(self):
        if not self.conn or not self.sp:
            QMessageBox.warning(self, 'Warning', 'Connect serial port first.')
            return False
        return True

    def _send_one(self):
        if not self._check(): return
        p = self.ilbl.text()
        if not p or p == 'No image selected':
            QMessageBox.warning(self, 'Warning', 'Select an image first.')
            return
        data = self._load(p)
        if data:
            self._write(data)
            self._log(f'Sent: {Path(p).name}')

    def _start_folder(self):
        if not self._check(): return
        folder = QFileDialog.getExistingDirectory(self, 'Select Image Folder')
        if not folder: return
        self.folder_imgs = sorted([str(p) for p in Path(folder).iterdir() if p.suffix.lower() in IMG_EXTS])
        if not self.folder_imgs:
            QMessageBox.warning(self, 'Warning', 'No images found in folder.')
            return
        self.folder_idx = 0
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self.stl.setText('Person: 0 | No Person: 0 | Errors: 0')
        self._log(f'=== Folder mode: {len(self.folder_imgs)} images ===')
        self._set_run('folder')
        self._stimer = QTimer(self)
        self._stimer.timeout.connect(self._folder_tick)
        self._stimer.start(600)
        self._folder_tick()

    def _folder_tick(self):
        if self.folder_idx >= len(self.folder_imgs):
            self._stop()
            self._log(f'=== Done! Person: {self.stats["person"]}, No Person: {self.stats["no_person"]}, Errors: {self.stats["err"]} ===')
            return
        p = self.folder_imgs[self.folder_idx]
        self.folder_idx += 1
        data = self._load(p)
        if data:
            self._write(data)
            self._log(f'[{self.folder_idx}/{len(self.folder_imgs)}] {Path(p).name}')
        else:
            self.stats['err'] += 1

    def _start_loop(self):
        if not self._check(): return
        p = self.ilbl.text()
        if not p or p == 'No image selected':
            QMessageBox.warning(self, 'Warning', 'Select an image first.')
            return
        data = self._load(p)
        if not data: return
        self.loop_data = data
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self.stl.setText('Person: 0 | No Person: 0 | Errors: 0')
        self._log(f'=== Loop mode: {Path(p).name} ===')
        self._set_run('loop')
        self._stimer = QTimer(self)
        self._stimer.timeout.connect(self._loop_tick)
        self._stimer.start(600)
        self._loop_tick()

    def _loop_tick(self):
        if self.loop_data:
            self._write(self.loop_data)

    def _start_cam(self):
        if not self._check(): return
        try:
            import cv2, numpy as np
        except ImportError:
            QMessageBox.critical(self, 'Error', 'Camera needs: pip install opencv-python')
            return
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            QMessageBox.critical(self, 'Error', 'Cannot open camera.')
            return
        self.stats = {'person': 0, 'no_person': 0, 'err': 0}
        self.stl.setText('Person: 0 | No Person: 0 | Errors: 0')
        self._log('=== Camera mode ===')
        self._set_run('camera')
        self._stimer = QTimer(self)
        self._stimer.timeout.connect(self._cam_tick)
        self._stimer.start(250)

    def _cam_tick(self):
        import cv2, numpy as np
        ret, frame = self._cap.read()
        if not ret: return
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (IMG_W, IMG_H))
        d = img.astype(np.int16) - 128
        self._write(d.astype(np.int8).tobytes())

    def _stop(self):
        if self._stimer:
            self._stimer.stop()
            self._stimer = None
        if self._cap:
            try:
                self._cap.release()
                import cv2; cv2.destroyAllWindows()
            except: pass
            self._cap = None
        self._mode = None
        self._set_run(None)

    def _set_run(self, mode):
        running = mode is not None
        self._mode = mode
        self.fbtn.setEnabled(not running)
        self.cambtn.setEnabled(not running)
        self.loopbtn.setEnabled(not running)
        self.sendbtn.setEnabled(not running)
        self.stopbtn.setEnabled(running)
        self.stopbtn.setText(f'Stop [{mode}]' if mode else 'Stop')

    def _load(self, path):
        try:
            return preprocess(path)
        except Exception as e:
            self._log(f'Load error: {e}', err=True)
            self.stats['err'] += 1
            return None

    def _write(self, data):
        if self.sp:
            try:
                self.sp.write(packet(data))
                self.sp.flush()
            except SerialException as e:
                self._log(f'Write error: {e}', err=True)

    def _log(self, s, err=False):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self.log.moveCursor(QTextCursor.End)
        self.log.setTextColor(Qt.red if err else Qt.black)
        self.log.insertPlainText(f'[{ts}] {s}\n')
        self.log.setTextColor(Qt.black)
        self.log.ensureCursorVisible()

    def closeEvent(self, e):
        self._stop()
        if self.conn:
            self._rt.stop()
            if self.sp:
                try: self.sp.close()
                except: pass
        e.accept()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
