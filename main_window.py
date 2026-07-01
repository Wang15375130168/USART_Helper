"""串口调试助手主窗口"""
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QComboBox, QPushButton,
    QStatusBar, QSplitter, QTextEdit, QCheckBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QTabWidget, QMessageBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QTextCursor
import time
from datetime import datetime

from serial_handler import SerialHandler
from data_parser import DataParser
from waveform_widget import MultiChannelWaveform
from channel_config import ChannelConfigPanel


class MainWindow(QMainWindow):
    """串口调试助手主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("串口调试助手 - USART Helper")
        self.setMinimumSize(900, 600)
        self.resize(1400, 900)

        # 核心模块
        self._serial = SerialHandler()
        self._parser = DataParser()
        self._channel_data_types = self._parser.data_types
        self._device_frame_period_ms = 1.0

        # 统计信息
        self._rx_count = 0
        self._tx_count = 0
        self._start_time = time.time()
        self._frame_count = 0
        self._parse_error_count = 0
        self._fps = 0
        self._fps_timer_count = 0
        self._rx_text_buffer = []
        self._rx_text_pending_chars = 0
        self._rx_text_max_chars = 200000
        self._latest_channel_values = None
        self._live_ui_dirty = False
        self._last_parse_error_msg = ""
        self._last_parse_error_count_shown = 0
        self._rx_display_budget_per_sec = 4096
        self._rx_display_used = 0
        self._rx_display_dropped = 0
        self._rx_display_window_start = time.time()

        self._setup_ui()
        self._connect_signals()
        self._update_sample_interval()

        self._rx_flush_timer = QTimer(self)
        self._rx_flush_timer.timeout.connect(self._flush_rx_text)
        self._rx_flush_timer.start(80)

        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start(1000)

        self._live_ui_timer = QTimer(self)
        self._live_ui_timer.timeout.connect(self._flush_live_ui)
        self._live_ui_timer.start(100)

        self._update_port_status(False)
        self._refresh_ports()

    def _setup_ui(self):
        """初始化UI"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(2)
        main_layout.setContentsMargins(4, 4, 4, 4)
        self._root_splitter = QSplitter(Qt.Vertical)

        # ─── 顶部: 串口配置区 (紧凑) ─────────────────────
        serial_group = QGroupBox("串口配置")
        serial_group.setStyleSheet(
            "QGroupBox { font-size: 13px; padding-top: 10px; "
            "margin-top: 2px; }"
            "QGroupBox::title { subcontrol-origin: margin; "
            "left: 6px; padding: 0 2px; }")
        serial_layout = QHBoxLayout(serial_group)
        serial_layout.setContentsMargins(4, 8, 4, 4)
        serial_layout.setSpacing(4)

        lbl_style = "QLabel { font-size: 13px; }"
        combo_style = ("QComboBox { font-size: 13px; "
                       "padding: 2px 4px; min-height: 20px; }")

        lbl = QLabel("串口:")
        lbl.setStyleSheet(lbl_style)
        serial_layout.addWidget(lbl)
        self._combo_port = QComboBox()
        self._combo_port.setMinimumWidth(100)
        self._combo_port.setStyleSheet(combo_style)
        serial_layout.addWidget(self._combo_port)

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setStyleSheet(
            "QPushButton { font-size: 13px; padding: 2px 6px; "
            "min-height: 20px; }")
        self._btn_refresh.clicked.connect(self._refresh_ports)
        serial_layout.addWidget(self._btn_refresh)

        for text, attr, items, default in [
            ("波特率:", "_combo_baud",
             SerialHandler.get_baud_rates(), '2000000'),
            ("数据位:", "_combo_databits",
             SerialHandler.get_data_bits(), '8'),
            ("停止位:", "_combo_stopbits",
             SerialHandler.get_stop_bits(), '1'),
            ("校验:", "_combo_parity",
             SerialHandler.get_parity(), 'None'),
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet(lbl_style)
            serial_layout.addWidget(lbl)
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(default)
            combo.setStyleSheet(combo_style)
            combo.setMaximumWidth(90)
            serial_layout.addWidget(combo)
            setattr(self, attr, combo)

        serial_layout.addStretch()

        self._btn_connect = QPushButton("打开串口")
        self._btn_connect.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; "
            "font-weight: bold; padding: 4px 14px; font-size: 13px; "
            "min-height: 20px; }"
            "QPushButton:hover { background-color: #388E3C; }")
        self._btn_connect.clicked.connect(self._toggle_connection)
        serial_layout.addWidget(self._btn_connect)

        self._root_splitter.addWidget(serial_group)

        # ─── 中部 + 底部: 使用垂直分割器 ─────────────────
        # 用户可以拖动分割线调整波形区和数据区的大小
        self._v_splitter = QSplitter(Qt.Vertical)

        # ── 中部: 波形 + 通道配置 (水平分割器) ──
        self._h_splitter = QSplitter(Qt.Horizontal)

        # 左侧: 波形显示
        self._waveform = MultiChannelWaveform(
            max_points=5000, num_channels=10)
        self._h_splitter.addWidget(self._waveform)

        # 右侧: 通道配置 + 协议设置
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._channel_config = ChannelConfigPanel(num_channels=10)
        right_layout.addWidget(self._channel_config)

        # 协议配置
        proto_group = QGroupBox("数据协议")
        proto_layout = QVBoxLayout(proto_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("通道数:"))
        self._spin_channels = QSpinBox()
        self._spin_channels.setRange(1, 10)
        self._spin_channels.setValue(10)
        self._spin_channels.setReadOnly(True)  # 由解析器自动检测
        self._spin_channels.setButtonSymbols(QSpinBox.NoButtons)
        self._spin_channels.setStyleSheet(
            "QSpinBox { background-color: #2D2D2D; }")
        row1.addWidget(self._spin_channels)

        row1.addWidget(QLabel("帧头: 0x"))
        self._edit_header = QLineEdit("AA")
        self._edit_header.setMaximumWidth(50)
        row1.addWidget(self._edit_header)

        row1.addStretch()
        proto_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧周期(ms):"))
        self._spin_frame_period = QDoubleSpinBox()
        self._spin_frame_period.setRange(0.001, 100000.0)
        self._spin_frame_period.setDecimals(3)
        self._spin_frame_period.setSingleStep(0.100)
        self._spin_frame_period.setValue(self._device_frame_period_ms)
        self._spin_frame_period.setMaximumWidth(110)
        self._spin_frame_period.setToolTip(
            "Nominal device frame period used for the waveform X axis")
        row2.addWidget(self._spin_frame_period)
        row2.addStretch()
        proto_layout.addLayout(row2)

        info_label = QLabel(
            "Protocol: AA FF F1 + Len(data bytes) + "
            "Data parsed by channel Type; X axis uses configured period")
        info_label.setStyleSheet("color: #888888; font-size: 12px;")
        proto_layout.addWidget(info_label)
        right_layout.addWidget(proto_group)

        # 缓冲区大小
        buf_group = QGroupBox("显示设置")
        buf_layout = QHBoxLayout(buf_group)
        buf_layout.addWidget(QLabel("缓冲点数:"))
        self._spin_bufsize = QSpinBox()
        self._spin_bufsize.setRange(100, 100000)
        self._spin_bufsize.setValue(5000)
        self._spin_bufsize.setSingleStep(1000)
        self._spin_bufsize.valueChanged.connect(
            self._waveform.set_max_points)
        buf_layout.addWidget(self._spin_bufsize)
        buf_layout.addStretch()
        right_layout.addWidget(buf_group)
        right_layout.addStretch()

        self._h_splitter.addWidget(right_panel)
        # 波形区默认占主导, 但右侧配置区允许按需拉宽。
        self._h_splitter.setStretchFactor(0, 5)
        self._h_splitter.setStretchFactor(1, 1)
        self._h_splitter.setChildrenCollapsible(False)
        right_panel.setMinimumWidth(360)
        right_panel.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._h_splitter.setSizes([1040, 360])

        self._v_splitter.addWidget(self._h_splitter)

        # ── 底部: 数据收发区 ──
        bottom_tabs = QTabWidget()
        # 不再设置 maxHeight, 让用户自由调整

        # 接收区
        rx_widget = QWidget()
        rx_layout = QHBoxLayout(rx_widget)
        self._text_rx = QTextEdit()
        self._text_rx.setReadOnly(True)
        self._text_rx.setFont(QFont("Consolas", 11))
        self._text_rx.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }")
        rx_layout.addWidget(self._text_rx)

        rx_ctrl = QVBoxLayout()
        self._chk_hex_rx = QCheckBox("HEX显示")
        rx_ctrl.addWidget(self._chk_hex_rx)
        self._chk_ascii_rx = QCheckBox("ASCII显示")
        rx_ctrl.addWidget(self._chk_ascii_rx)
        self._chk_timestamp_rx = QCheckBox("时间戳")
        rx_ctrl.addWidget(self._chk_timestamp_rx)
        btn_clear_rx = QPushButton("清除接收")
        btn_clear_rx.clicked.connect(self._clear_rx_display)
        rx_ctrl.addWidget(btn_clear_rx)
        rx_ctrl.addStretch()
        rx_layout.addLayout(rx_ctrl)
        bottom_tabs.addTab(rx_widget, "数据接收")

        # 发送区
        tx_widget = QWidget()
        tx_layout = QHBoxLayout(tx_widget)
        self._text_tx = QTextEdit()
        self._text_tx.setMaximumHeight(80)
        self._text_tx.setFont(QFont("Consolas", 11))
        self._text_tx.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }")
        self._text_tx.setPlaceholderText("输入要发送的数据...")
        tx_layout.addWidget(self._text_tx)

        tx_ctrl = QVBoxLayout()
        self._chk_hex_tx = QCheckBox("HEX发送")
        tx_ctrl.addWidget(self._chk_hex_tx)
        btn_send = QPushButton("发送")
        btn_send.setStyleSheet(
            "QPushButton { background-color: #1565C0; color: white; "
            "font-weight: bold; padding: 4px 12px; }")
        btn_send.clicked.connect(self._send_data)
        tx_ctrl.addWidget(btn_send)
        tx_ctrl.addStretch()
        tx_layout.addLayout(tx_ctrl)
        bottom_tabs.addTab(tx_widget, "数据发送")

        self._v_splitter.addWidget(bottom_tabs)

        self._v_splitter.setStretchFactor(0, 4)
        self._v_splitter.setStretchFactor(1, 1)
        # 设置初始大小: 波形区700px, 数据区120px
        self._v_splitter.setSizes([700, 120])

        self._root_splitter.addWidget(self._v_splitter)
        self._root_splitter.setStretchFactor(0, 0)
        self._root_splitter.setStretchFactor(1, 1)
        self._root_splitter.setChildrenCollapsible(False)
        self._root_splitter.setSizes([58, 820])
        main_layout.addWidget(self._root_splitter)

        # ─── 状态栏 ─────────────────────────────────────
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._lbl_status = QLabel("● 串口已关闭")
        self._lbl_status.setStyleSheet("color: #FF4444;")
        self._statusbar.addWidget(self._lbl_status)

        self._lbl_rx = QLabel("RX: 0 字节")
        self._statusbar.addWidget(self._lbl_rx)

        self._lbl_tx = QLabel("TX: 0 字节")
        self._statusbar.addWidget(self._lbl_tx)

        self._lbl_fps = QLabel("FPS: 0")
        self._statusbar.addWidget(self._lbl_fps)

        self._lbl_frames = QLabel("帧: 0")
        self._statusbar.addWidget(self._lbl_frames)

        self._lbl_ber = QLabel("误码率: 0.000%")
        self._statusbar.addWidget(self._lbl_ber)

    def _connect_signals(self):
        self._serial.data_received.connect(self._on_data_received)
        self._serial.connection_changed.connect(self._on_connection_changed)
        self._serial.error_occurred.connect(self._on_serial_error)
        self._parser.frames_decoded.connect(self._on_frames_decoded)
        self._parser.parse_error.connect(self._on_parse_error)
        self._parser.format_detected.connect(self._on_format_detected)
        self._channel_config.channel_changed.connect(
            self._on_channel_config_changed)
        self._channel_config.channel_selected.connect(
            self._waveform.select_channel)
        self._waveform.buffer_size_changed.connect(
            self._on_buffer_size_changed)
        self._combo_baud.currentTextChanged.connect(
            self._on_serial_settings_changed)
        self._combo_databits.currentTextChanged.connect(
            self._on_serial_settings_changed)
        self._combo_stopbits.currentTextChanged.connect(
            self._on_serial_settings_changed)
        self._combo_parity.currentTextChanged.connect(
            self._on_serial_settings_changed)
        self._spin_frame_period.valueChanged.connect(
            self._on_frame_period_changed)
        self._chk_hex_rx.toggled.connect(self._on_hex_rx_toggled)
        self._chk_ascii_rx.toggled.connect(self._on_ascii_rx_toggled)

    # ─── 串口操作 ─────────────────────────────────────────────

    def _on_hex_rx_toggled(self, checked):
        if checked and self._chk_ascii_rx.isChecked():
            self._chk_ascii_rx.blockSignals(True)
            self._chk_ascii_rx.setChecked(False)
            self._chk_ascii_rx.blockSignals(False)

    def _on_ascii_rx_toggled(self, checked):
        if checked and self._chk_hex_rx.isChecked():
            self._chk_hex_rx.blockSignals(True)
            self._chk_hex_rx.setChecked(False)
            self._chk_hex_rx.blockSignals(False)

    def _refresh_ports(self):
        self._combo_port.clear()
        self._combo_port.addItems(SerialHandler.get_available_ports())

    def _toggle_connection(self):
        if self._serial.is_connected:
            self._serial.close()
        else:
            port = self._combo_port.currentText()
            if not port:
                self._show_error("请选择串口号")
                return
            success = self._serial.open(
                port=port,
                baudrate=self._combo_baud.currentText(),
                bytesize=self._combo_databits.currentText(),
                stopbits=self._combo_stopbits.currentText(),
                parity=self._combo_parity.currentText()
            )
            if not success:
                self._show_error("串口打开失败")

    def _send_data(self):
        text = self._text_tx.toPlainText()
        if not text:
            return
        if self._chk_hex_tx.isChecked():
            try:
                hex_str = text.replace(' ', '').replace(',', '')
                data = bytes.fromhex(hex_str)
            except ValueError:
                self._show_error("无效的HEX数据格式")
                return
        else:
            data = text.encode('utf-8')
        if self._serial.send(data):
            self._tx_count += len(data)
            self._lbl_tx.setText(f"TX: {self._tx_count} 字节")

    # ─── 数据接收 ─────────────────────────────────────────────

    def _on_data_received(self, data: bytes):
        self._rx_count += len(data)
        self._live_ui_dirty = True
        # RX label is refreshed by _flush_live_ui to avoid high-rate UI churn.

        self._queue_rx_data_for_display(data)

        self._parser.feed(data)

    def _queue_rx_data_for_display(self, data: bytes):
        now = time.time()
        if now - self._rx_display_window_start >= 1.0:
            if self._rx_display_dropped:
                self._queue_rx_text(
                    f"[RX display throttled: "
                    f"{self._rx_display_dropped} byte(s) hidden]")
            self._rx_display_window_start = now
            self._rx_display_used = 0
            self._rx_display_dropped = 0

        remaining = self._rx_display_budget_per_sec - self._rx_display_used
        if remaining <= 0:
            self._rx_display_dropped += len(data)
            return

        shown = data[:remaining]
        self._rx_display_used += len(shown)
        self._rx_display_dropped += len(data) - len(shown)
        self._queue_rx_text(self._format_rx_data(shown))

    def _format_rx_data(self, data: bytes):
        if self._chk_hex_rx.isChecked():
            text = ' '.join(f'{b:02X}' for b in data)
        elif self._chk_ascii_rx.isChecked():
            text = ''.join(
                chr(b) if b in (9, 10, 13) or 32 <= b <= 126 else '.'
                for b in data)
        else:
            try:
                text = data.decode('utf-8', errors='replace')
            except Exception:
                text = data.hex()

        if self._chk_timestamp_rx.isChecked():
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            text = f'[{timestamp}] {text}'
        return text

    def _queue_rx_text(self, text):
        if not text:
            return
        self._rx_text_buffer.append(text)
        self._rx_text_pending_chars += len(text)
        if self._rx_text_pending_chars > 20000:
            self._flush_rx_text()

    def _flush_rx_text(self):
        if not self._rx_text_buffer:
            return

        text = '\n'.join(self._rx_text_buffer)
        self._rx_text_buffer.clear()
        self._rx_text_pending_chars = 0

        self._text_rx.moveCursor(QTextCursor.End)
        self._text_rx.insertPlainText(text + '\n')
        self._text_rx.moveCursor(QTextCursor.End)

        doc = self._text_rx.document()
        if doc.characterCount() > self._rx_text_max_chars:
            cursor = self._text_rx.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(
                QTextCursor.Right, QTextCursor.KeepAnchor,
                doc.characterCount() - self._rx_text_max_chars)
            cursor.removeSelectedText()

    def _clear_rx_display(self):
        self._text_rx.clear()
        self._rx_text_buffer.clear()
        self._rx_text_pending_chars = 0
        self._rx_count = 0
        self._frame_count = 0
        self._parse_error_count = 0
        self._latest_channel_values = None
        self._live_ui_dirty = False
        self._last_parse_error_msg = ""
        self._last_parse_error_count_shown = 0
        self._rx_display_used = 0
        self._rx_display_dropped = 0
        self._rx_display_window_start = time.time()
        self._lbl_rx.setText("RX: 0 字节")
        self._lbl_frames.setText("帧: 0")
        self._update_error_rate_label()
        self._channel_config.set_channel_values([])
        self._waveform.clear_data()

    def _on_frame_decoded(self, values):
        self._on_frames_decoded([values])

    def _on_frames_decoded(self, frames):
        if not frames:
            return

        frame_count = len(frames)
        self._frame_count += frame_count
        self._fps_timer_count += frame_count
        self._latest_channel_values = frames[-1]
        self._live_ui_dirty = True
        self._waveform.add_data_points(frames)

    def _on_parse_error(self, msg):
        self._parse_error_count += 1
        self._last_parse_error_msg = msg
        self._live_ui_dirty = True

    def _update_error_rate_label(self):
        total = self._frame_count + self._parse_error_count
        rate = (self._parse_error_count / total * 100.0) if total else 0.0
        self._lbl_ber.setText(
            f"误码率: {rate:.3f}% ({self._parse_error_count}/{total})")

    # ─── 通道配置 ─────────────────────────────────────────────

    def _flush_live_ui(self):
        if not self._live_ui_dirty:
            return

        self._live_ui_dirty = False
        self._lbl_rx.setText(f"RX: {self._rx_count} 字节")
        self._lbl_frames.setText(f"帧: {self._frame_count}")
        self._update_error_rate_label()

        if self._latest_channel_values is not None:
            self._channel_config.set_channel_values(
                self._latest_channel_values)

        if self._parse_error_count != self._last_parse_error_count_shown:
            self._last_parse_error_count_shown = self._parse_error_count
            self._statusbar.showMessage(
                f"解析错误: {self._last_parse_error_msg}", 3000)

    def _on_channel_config_changed(self, ch_idx, config):
        self._waveform.set_channel_name(ch_idx, config['name'])
        self._waveform.set_channel_color(ch_idx, config['color'])
        self._waveform.set_channel_visible(ch_idx, config['visible'])
        self._waveform.set_channel_unit(ch_idx, config.get('unit', ''))
        if config['data_type'] != self._channel_data_types[ch_idx]:
            self._channel_data_types[ch_idx] = config['data_type']
            self._parser.set_channel_data_type(ch_idx, config['data_type'])
            self._update_sample_interval()

    def _on_format_detected(self, num_ch, data_len):
        """解析器自动检测到帧格式"""
        self._spin_channels.setValue(num_ch)
        self._waveform.set_active_channel_count(num_ch)
        self._update_sample_interval()
        self._statusbar.showMessage(
            f"检测到协议: {num_ch}通道, 数据区{data_len}字节", 5000)

    def _on_buffer_size_changed(self, size):
        """波形组件自动调整缓冲点数, 同步到Spinbox"""
        self._spin_bufsize.blockSignals(True)
        self._spin_bufsize.setValue(size)
        self._spin_bufsize.blockSignals(False)

    def _on_serial_settings_changed(self, *_):
        self._update_sample_interval()
        if not self._serial.is_connected:
            return

        ok = self._serial.update_settings(
            baudrate=self._combo_baud.currentText(),
            bytesize=self._combo_databits.currentText(),
            stopbits=self._combo_stopbits.currentText(),
            parity=self._combo_parity.currentText(),
        )
        if ok:
            self._update_port_status(True)
            self._statusbar.showMessage("Serial settings updated", 1500)

    def _on_frame_period_changed(self, value):
        self._device_frame_period_ms = float(value)
        self._update_sample_interval()

    def _update_sample_interval(self):
        """根据波特率和帧大小计算每帧时间间隔, 更新X轴刻度"""
        self._waveform.set_sample_interval_ms(self._device_frame_period_ms)

    # ─── 状态更新 ─────────────────────────────────────────────

    def _on_connection_changed(self, connected):
        self._update_port_status(connected)
        self._combo_port.setEnabled(not connected)
        self._btn_refresh.setEnabled(not connected)
        if connected:
            self._update_sample_interval()
            self._channel_config.set_channel_values([])
            self._waveform.clear_data()

            self._btn_connect.setText("关闭串口")
            self._btn_connect.setStyleSheet(
                "QPushButton { background-color: #C62828; color: white; "
                "font-weight: bold; padding: 6px 16px; font-size: 13px; }"
                "QPushButton:hover { background-color: #D32F2F; }")
        else:
            self._btn_connect.setText("打开串口")
            self._btn_connect.setStyleSheet(
                "QPushButton { background-color: #2E7D32; color: white; "
                "font-weight: bold; padding: 6px 16px; font-size: 13px; }"
                "QPushButton:hover { background-color: #388E3C; }")

    def _update_port_status(self, connected):
        if connected:
            port = self._combo_port.currentText()
            baud = self._combo_baud.currentText()
            self._lbl_status.setText(f"● 已连接 {port} @ {baud}")
            self._lbl_status.setStyleSheet("color: #44FF44;")
        else:
            self._lbl_status.setText("● 串口已关闭")
            self._lbl_status.setStyleSheet("color: #FF4444;")

    def _on_serial_error(self, msg):
        self._statusbar.showMessage(f"串口错误: {msg}", 5000)

    def _update_fps(self):
        self._fps = self._fps_timer_count
        self._fps_timer_count = 0
        self._lbl_fps.setText(f"FPS: {self._fps}")

    def _show_error(self, msg):
        QMessageBox.warning(self, "错误", msg)

    # 波形组件内部已有 _check_layout 定时器兜底跨DPI场景,
    # 主窗口无需额外处理

    def closeEvent(self, event):
        self._serial.close()
        event.accept()
