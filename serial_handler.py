"""Serial communication layer."""
import threading
import time

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QObject, pyqtSignal


class SerialHandler(QObject):
    data_received = pyqtSignal(bytes)
    connection_changed = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    PARITY_MAP = {
        'None': serial.PARITY_NONE,
        'Odd': serial.PARITY_ODD,
        'Even': serial.PARITY_EVEN,
        'Mark': serial.PARITY_MARK,
        'Space': serial.PARITY_SPACE,
    }

    def __init__(self):
        super().__init__()
        self._serial = None
        self._running = False
        self._thread = None
        self._lock = threading.RLock()

    @staticmethod
    def get_available_ports():
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]

    @staticmethod
    def get_baud_rates():
        return ['9600', '19200', '38400', '57600', '115200',
                '230400', '460800', '921600', '2000000']

    @staticmethod
    def get_data_bits():
        return ['5', '6', '7', '8']

    @staticmethod
    def get_stop_bits():
        return ['1', '1.5', '2']

    @staticmethod
    def get_parity():
        return ['None', 'Odd', 'Even', 'Mark', 'Space']

    @property
    def is_connected(self):
        return self._serial is not None and self._serial.is_open

    def open(self, port, baudrate=2000000, bytesize=8, stopbits=1,
             parity='None', timeout=0.01):
        try:
            if self.is_connected:
                self.close()

            with self._lock:
                self._serial = serial.Serial(
                    port=port,
                    baudrate=int(baudrate),
                    bytesize=int(bytesize),
                    stopbits=float(stopbits),
                    parity=self.PARITY_MAP.get(parity, serial.PARITY_NONE),
                    timeout=timeout,
                )

            self._running = True
            self._thread = threading.Thread(
                target=self._read_loop, daemon=True)
            self._thread.start()
            self.connection_changed.emit(True)
            return True
        except Exception as e:
            self.error_occurred.emit(f"打开串口失败: {e}")
            return False

    def close(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

        self.connection_changed.emit(False)

    def update_settings(self, baudrate=None, bytesize=None, stopbits=None,
                        parity=None):
        """Apply serial settings without closing the port."""
        if not self.is_connected:
            return True

        try:
            with self._lock:
                if baudrate is not None:
                    self._serial.baudrate = int(baudrate)
                if bytesize is not None:
                    self._serial.bytesize = int(bytesize)
                if stopbits is not None:
                    self._serial.stopbits = float(stopbits)
                if parity is not None:
                    self._serial.parity = self.PARITY_MAP.get(
                        parity, serial.PARITY_NONE)
            return True
        except Exception as e:
            self.error_occurred.emit(f"更新串口配置失败: {e}")
            return False

    def send(self, data):
        if not self.is_connected:
            self.error_occurred.emit("串口未连接")
            return False
        try:
            if isinstance(data, str):
                data = data.encode('utf-8')
            with self._lock:
                self._serial.write(data)
            return True
        except Exception as e:
            self.error_occurred.emit(f"发送失败: {e}")
            return False

    def _read_loop(self):
        while self._running:
            try:
                with self._lock:
                    if not self._serial or not self._serial.is_open:
                        break
                    waiting = self._serial.in_waiting
                    data = self._serial.read(waiting) if waiting > 0 else b''

                if data:
                    self.data_received.emit(data)
                else:
                    time.sleep(0.002)
            except serial.SerialException:
                if self._running:
                    self.error_occurred.emit("串口连接断开")
                    self._running = False
                    self.connection_changed.emit(False)
                break
            except Exception:
                time.sleep(0.01)
