"""Protocol/state regression checks; no hardware or camera access."""
import csv
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class FakeSerial:
    def __init__(self):
        self.rx = bytearray()
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, size):
        result = bytes(self.rx[:size])
        del self.rx[:size]
        return result

    def reset_input_buffer(self):
        self.rx.clear()

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def close(self):
        self.closed = True


class WorkbenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(main.STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.png'
        main.Image.new('RGB', (211, 95), (255, 128, 0)).save(self.path)
        with patch.object(main.list_ports, 'comports', return_value=[]):
            self.window = main.MainWindow()
        self.serial = FakeSerial()
        self.window.sp = self.serial
        self.window.conn = True
        self.window._select_image(str(self.path))
        self.window._rt.stop()

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def response(self):
        self.serial.rx.extend(b'\x70\x90\x00\x00\xcc\xdd')
        self.window._read()

    def test_real_payload_and_fragmented_response(self):
        self.window._send_one()
        frame = self.serial.writes[0]
        self.assertEqual(len(frame), 19204)
        self.assertEqual(frame[:5], b'\xaa\xbb\x7f\x00\x80')
        self.assertEqual(frame[-2:], b'\x00\x00')
        self.window._send_one()
        self.assertEqual(len(self.serial.writes), 1, 'Do not overwrite in-flight image')
        self.serial.rx.extend(b'noise\x70\x90\x00')
        self.window._read()
        self.assertFalse(self.window.history)
        self.serial.rx.extend(b'\x00\xcc\xdd')
        self.window._read()
        self.assertEqual(self.window.history[0][2:6], ['test.png', 16, -16, 'PERSON'])
        self.assertIsNone(self.window.pending)
        self.assertEqual(self.window.table.rowCount(), 1)

    def test_batch_waits_for_response(self):
        self.window.folder_imgs = [self.path, self.path]
        self.window._start_mode('folder')
        self.window._stimer.stop()
        self.window._tick()
        self.assertEqual(len(self.serial.writes), 1)
        self.response()
        self.window._tick()
        self.assertEqual(len(self.serial.writes), 2)
        self.response()
        self.window._tick()
        self.assertIsNone(self.window._mode)
        self.assertEqual(len(self.window.history), 2)

    def test_timeout_stops_stream(self):
        self.window._start_loop()
        self.window._stimer.stop()
        self.window.pending['started'] = time.monotonic() - 11
        self.window._read()
        self.assertIsNone(self.window.pending)
        self.assertIsNone(self.window._mode)
        self.assertEqual(self.window.stats['err'], 1)
        self.assertEqual(self.window.history, [])

    def test_csv_export_keeps_real_scores(self):
        self.window._send_one()
        self.response()
        output = Path(self.temp.name) / '结果.csv'
        with patch.object(main.QFileDialog, 'getSaveFileName', return_value=(str(output), 'CSV')):
            self.window._export()
        with output.open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.reader(source))
        self.assertEqual(rows[0], main.CSV_FIELDS)
        self.assertEqual(rows[1][2:6], ['test.png', '16', '-16', 'PERSON'])
        self.assertGreater(float(rows[1][-1]), 0)

    def test_layout_and_disconnect(self):
        self.window.show()
        for width, height in [(1040, 760), (900, 700)]:
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertLess(self.window.prev.geometry().bottom(), self.window.ilbl.geometry().top())
            for widget in (self.window.prev, self.window.rlbl, self.window.table):
                self.assertTrue(widget.parentWidget().rect().contains(widget.geometry()))
        self.window._disconnect()
        self.assertTrue(self.serial.closed)
        self.assertFalse(self.window.sendbtn.isEnabled())
        self.window._stop_record()  # Closing without ever recording must be harmless.


if __name__ == '__main__':
    unittest.main()
