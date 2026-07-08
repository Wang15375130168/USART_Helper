"""串口调试助手 - USART Helper

功能:
  1. 串口配置: 串口号、波特率、数据位、停止位、校验位
  2. 实时波形显示: 基于 pyqtgraph 的高性能绘图
  3. 默认6通道测试: 可添加/删除通道, 每通道可配置数据类型、名称、颜色
  4. 多Y轴设计: X轴共用(时间), Y轴独立(幅值)
  5. 独立缩放: 各通道Y轴独立缩放, X轴同步缩放
  6. 游标卡尺: 双游标测量时间差和幅值差

数据协议:
  帧头(0xAA, 1B) + 地址(0xFF, 1B) + ID(0xF1, 1B) + 长度(1B)
  + 数据区(按各通道数据类型解析, 小端序) + 校验(2B)

依赖:
  pip install PyQt5 pyqtgraph pyserial numpy
"""
import os
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QIcon

# 启用高DPI缩放支持 (必须在 QApplication 创建前设置)
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
from main_window import MainWindow


def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def main():
    app = QApplication(sys.argv)

    # 设置全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    icon_path = resource_path(os.path.join("assets", "app_icon.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 设置深色主题样式
    app.setStyleSheet("""
        QMainWindow {
            background-color: #2D2D2D;
        }
        QWidget {
            background-color: #2D2D2D;
            color: #CCCCCC;
        }
        QGroupBox {
            border: 1px solid #555555;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 12px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        QComboBox {
            background-color: #3C3C3C;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 3px 6px;
            color: #CCCCCC;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #3C3C3C;
            color: #CCCCCC;
            selection-background-color: #0078D7;
        }
        QLineEdit, QSpinBox {
            background-color: #3C3C3C;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 3px 6px;
            color: #CCCCCC;
        }
        QPushButton {
            background-color: #3C3C3C;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 4px 10px;
            color: #CCCCCC;
        }
        QPushButton:hover {
            background-color: #4A4A4A;
        }
        QPushButton:pressed {
            background-color: #555555;
        }
        QPushButton:checked {
            background-color: #0078D7;
            border-color: #0078D7;
            color: white;
        }
        QCheckBox {
            color: #CCCCCC;
        }
        QCheckBox::indicator {
            width: 14px;
            height: 14px;
        }
        QTabWidget::pane {
            border: 1px solid #555555;
        }
        QTabBar::tab {
            background-color: #3C3C3C;
            border: 1px solid #555555;
            padding: 4px 12px;
            color: #CCCCCC;
        }
        QTabBar::tab:selected {
            background-color: #2D2D2D;
            border-bottom: 2px solid #0078D7;
        }
        QStatusBar {
            background-color: #252526;
            color: #CCCCCC;
        }
        QSplitter::handle {
            background-color: #555555;
        }
        QScrollBar:vertical {
            background-color: #2D2D2D;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background-color: #555555;
            border-radius: 5px;
            min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollArea {
            border: none;
        }
    """)

    window = MainWindow()
    if not app.windowIcon().isNull():
        window.setWindowIcon(app.windowIcon())
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
