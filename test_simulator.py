"""串口数据模拟器 - 用于测试波形显示功能

直接启动主窗口并模拟10通道正弦波数据，无需真实串口设备。
运行方式: python test_simulator.py
"""
import sys
import math
import struct
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from main_window import MainWindow
from data_parser import DATA_TYPE_DEFS, DEFAULT_SEGMENT_TYPES


class DataSimulator:
    """数据模拟器 - 生成10通道测试数据"""

    def __init__(self, main_window):
        self._win = main_window
        self._running = False
        self._counter = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._generate_data)

    def start(self, interval_ms=20):
        """启动模拟, interval_ms 为数据发送间隔(毫秒)"""
        self._running = True
        self._timer.start(interval_ms)
        print(f"模拟器已启动, 间隔 {interval_ms}ms (50Hz)")

    def stop(self):
        self._running = False
        self._timer.stop()

    def _generate_data(self):
        """生成模拟数据"""
        if not self._running:
            return

        self._counter += 1
        t = self._counter * 0.05

        # 10个通道: 不同频率和波形
        values = [
            100 * math.sin(t * 1.0),                       # CH1: 低频正弦
            80 * math.sin(t * 2.5),                        # CH2: 中频正弦
            60 * math.sin(t * 5.0 + 0.5),                  # CH3: 高频正弦
            50 * math.sin(t * 1.5) * math.cos(t * 0.3),   # CH4: 调幅波
            120 * (1 if math.sin(t * 3.0) > 0 else -1),   # CH5: 方波
            40 * (t % 2 - 1),                              # CH6: 锯齿波
            70 * abs(math.sin(t * 2.0)),                   # CH7: 全波整流
            90 * math.sin(t * 0.8) + 20 * math.sin(t * 4.0),  # CH8: 叠加
            30 * math.sin(t * 6.0),                        # CH9: 高频小信号
            150 * math.cos(t * 0.6),                       # CH10: 低频余弦
        ]

        # 构造协议帧并喂入解析器
        frame = self._build_frame(values)
        self._win._parser.feed(frame)

    @staticmethod
    def _build_frame(values):
        """构建协议帧"""
        payload = bytearray()
        # Match the firmware test frame shown by the device:
        # CH1/CH2 int16, CH3/CH4 uint16, CH5-CH7 int32 = 20 data bytes.
        for v, data_type in zip(values[:7], DEFAULT_SEGMENT_TYPES[:7]):
            size, fmt = DATA_TYPE_DEFS[data_type]
            if data_type.startswith('float'):
                val = float(v)
            elif data_type.startswith('u'):
                bits = size * 8
                val = max(0, min((1 << bits) - 1, int(abs(v))))
            else:
                bits = size * 8
                low = -(1 << (bits - 1))
                high = (1 << (bits - 1)) - 1
                val = max(low, min(high, int(v)))
            payload.extend(struct.pack(fmt, val))

        frame = bytearray([0xAA, 0xFF, 0xF1, len(payload)])
        frame.extend(payload)
        sc1 = 0x00
        sc2 = 0x00
        for byte in frame:
            sc1 = (sc1 + byte) & 0xFF
            sc2 = (sc2 + sc1) & 0xFF
        frame.extend(bytes([sc1, sc2]))
        return bytes(frame)


def main():
    from PyQt5.QtCore import Qt
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)

    # 导入样式 (复用 main.py 中的设置)
    from PyQt5.QtGui import QFont
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # 深色主题
    app.setStyleSheet("""
        QMainWindow { background-color: #2D2D2D; }
        QWidget { background-color: #2D2D2D; color: #CCCCCC; }
        QGroupBox {
            border: 1px solid #555555; border-radius: 4px;
            margin-top: 8px; padding-top: 12px; font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 10px; padding: 0 4px;
        }
        QComboBox {
            background-color: #3C3C3C; border: 1px solid #555555;
            border-radius: 3px; padding: 3px 6px; color: #CCCCCC;
        }
        QComboBox QAbstractItemView {
            background-color: #3C3C3C; color: #CCCCCC;
            selection-background-color: #0078D7;
        }
        QLineEdit, QSpinBox {
            background-color: #3C3C3C; border: 1px solid #555555;
            border-radius: 3px; padding: 3px 6px; color: #CCCCCC;
        }
        QPushButton {
            background-color: #3C3C3C; border: 1px solid #555555;
            border-radius: 3px; padding: 4px 10px; color: #CCCCCC;
        }
        QPushButton:hover { background-color: #4A4A4A; }
        QPushButton:checked {
            background-color: #0078D7; border-color: #0078D7; color: white;
        }
        QTabWidget::pane { border: 1px solid #555555; }
        QTabBar::tab {
            background-color: #3C3C3C; border: 1px solid #555555;
            padding: 4px 12px; color: #CCCCCC;
        }
        QTabBar::tab:selected {
            background-color: #2D2D2D; border-bottom: 2px solid #0078D7;
        }
        QStatusBar { background-color: #252526; color: #CCCCCC; }
        QSplitter::handle { background-color: #555555; }
        QScrollBar:vertical {
            background-color: #2D2D2D; width: 10px;
        }
        QScrollBar::handle:vertical {
            background-color: #555555; border-radius: 5px; min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollArea { border: none; }
    """)

    window = MainWindow()
    window.setWindowTitle("串口调试助手 - 模拟测试模式")
    window.show()

    # 启动数据模拟器 (50Hz = 每20ms一帧)
    simulator = DataSimulator(window)
    simulator.start(interval_ms=20)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
