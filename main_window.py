"""串口调试助手主窗口"""
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QComboBox, QPushButton,
    QStatusBar, QSplitter, QTextEdit, QCheckBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QTabWidget, QMessageBox, QSizePolicy,
    QDialog, QFileDialog, QApplication, QProgressDialog, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, QStandardPaths
from PyQt5.QtGui import QFont, QTextCursor
import csv
import math
import os
import re
import time
from collections import deque
from datetime import datetime

from serial_handler import SerialHandler
from data_parser import DataParser
from waveform_widget import MultiChannelWaveform
from channel_config import (
    ChannelConfigPanel, DEFAULT_CHANNEL_COUNT, MAX_CHANNEL_COUNT
)


class MainWindow(QMainWindow):
    """串口调试助手主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("串口调试助手 - USART Helper")
        self.setMinimumSize(900, 600)
        self.resize(1400, 900)

        # 核心模块
        self._serial = SerialHandler()
        self._parser = DataParser(num_channels=DEFAULT_CHANNEL_COUNT)
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
        self._trigger_armed = False
        self._trigger_capturing = False
        self._trigger_pre_buffer = deque()
        self._trigger_capture_frames = []
        self._trigger_prev_value = None
        self._trigger_total_samples = 0
        self._trigger_pre_samples = 0
        self._trigger_sample_index = None
        self._trigger_sample_channel = None
        self._trigger_wait_dialog = None
        self._trigger_wait_title_label = None
        self._trigger_wait_detail_label = None
        self._trigger_wait_progress = None
        self._save_dir = (
            QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            or os.getcwd()
        )
        self._save_sequence_by_date = {}
        self._pending_save_date = None
        self._pending_save_sequence = None
        self._waveform_import_mode = False

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

        tool_btn_style = (
            "QPushButton { font-size: 13px; padding: 2px 8px; "
            "min-height: 20px; }")
        self._btn_save_waveform = QPushButton("保存波形")
        self._btn_save_waveform.setToolTip(
            "保存当前波形；触发采样完成时优先保存触发波形")
        self._btn_save_waveform.setStyleSheet(tool_btn_style)
        serial_layout.addWidget(self._btn_save_waveform)

        self._btn_import_waveform = QPushButton("导入波形")
        self._btn_import_waveform.setToolTip("导入已保存的CSV波形数据")
        self._btn_import_waveform.setStyleSheet(tool_btn_style)
        serial_layout.addWidget(self._btn_import_waveform)

        self._btn_toggle_channel_panel = QPushButton("隐藏配置")
        self._btn_toggle_channel_panel.setToolTip("隐藏或显示右侧通道配置列")
        self._btn_toggle_channel_panel.setStyleSheet(tool_btn_style)
        serial_layout.addWidget(self._btn_toggle_channel_panel)

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
            max_points=5000, num_channels=DEFAULT_CHANNEL_COUNT)
        self._h_splitter.addWidget(self._waveform)

        # 右侧: 通道配置 + 协议设置
        self._right_panel = QWidget()
        self._channel_panel_last_width = 420
        self._channel_panel_visible = True
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self._right_content = QSplitter(Qt.Vertical)
        self._right_content.setChildrenCollapsible(False)
        right_layout.addWidget(self._right_content, 1)

        self._channel_config = ChannelConfigPanel(
            num_channels=DEFAULT_CHANNEL_COUNT,
            max_channels=MAX_CHANNEL_COUNT)
        self._right_content.addWidget(self._channel_config)

        # 协议配置
        proto_group = QGroupBox("数据协议")
        proto_layout = QVBoxLayout(proto_group)
        proto_layout.setAlignment(Qt.AlignTop)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("通道数:"))
        self._spin_channels = QSpinBox()
        self._spin_channels.setRange(1, MAX_CHANNEL_COUNT)
        self._spin_channels.setValue(DEFAULT_CHANNEL_COUNT)
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
        self._make_group_collapsible(proto_group)
        self._right_content.addWidget(proto_group)

        # 缓冲区大小
        buf_layout = QHBoxLayout()
        buf_layout.addWidget(QLabel("缓冲点数:"))
        self._spin_bufsize = QSpinBox()
        self._spin_bufsize.setRange(100, 1000000)
        self._spin_bufsize.setValue(5000)
        self._spin_bufsize.setSingleStep(1000)
        self._spin_bufsize.valueChanged.connect(
            self._waveform.set_max_points)
        buf_layout.addWidget(self._spin_bufsize)
        buf_layout.addStretch()
        proto_layout.addLayout(buf_layout)

        trigger_group = QGroupBox("触发采样")
        trigger_layout = QVBoxLayout(trigger_group)
        trigger_layout.setSpacing(4)
        trigger_layout.setAlignment(Qt.AlignTop)

        trigger_top = QHBoxLayout()
        self._chk_trigger_enable = QCheckBox("启用触发")
        trigger_top.addWidget(self._chk_trigger_enable)
        self._btn_trigger_arm = QPushButton("重新触发")
        self._btn_trigger_arm.setEnabled(False)
        trigger_top.addWidget(self._btn_trigger_arm)
        trigger_top.addStretch()
        trigger_layout.addLayout(trigger_top)

        trigger_row1 = QHBoxLayout()
        trigger_row1.addWidget(QLabel("触发源:"))
        self._combo_trigger_channel = QComboBox()
        self._combo_trigger_channel.setMaximumWidth(110)
        trigger_row1.addWidget(self._combo_trigger_channel)
        trigger_row1.addWidget(QLabel("模式:"))
        self._combo_trigger_mode = QComboBox()
        self._combo_trigger_mode.addItem("上升沿", "rising")
        self._combo_trigger_mode.addItem("下降沿", "falling")
        self._combo_trigger_mode.addItem("电平", "level")
        self._combo_trigger_mode.setMaximumWidth(90)
        trigger_row1.addWidget(self._combo_trigger_mode)
        trigger_row1.addStretch()
        trigger_layout.addLayout(trigger_row1)

        trigger_row2 = QHBoxLayout()
        trigger_row2.addWidget(QLabel("阈值:"))
        self._spin_trigger_threshold = QDoubleSpinBox()
        self._spin_trigger_threshold.setRange(-1e12, 1e12)
        self._spin_trigger_threshold.setDecimals(3)
        self._spin_trigger_threshold.setSingleStep(1.0)
        self._spin_trigger_threshold.setMaximumWidth(120)
        trigger_row2.addWidget(self._spin_trigger_threshold)
        trigger_row2.addWidget(QLabel("预触发:"))
        self._spin_trigger_pre_percent = QSpinBox()
        self._spin_trigger_pre_percent.setRange(0, 100)
        self._spin_trigger_pre_percent.setValue(20)
        self._spin_trigger_pre_percent.setSuffix("%")
        self._spin_trigger_pre_percent.setMaximumWidth(80)
        trigger_row2.addWidget(self._spin_trigger_pre_percent)
        trigger_row2.addStretch()
        trigger_layout.addLayout(trigger_row2)

        trigger_row3 = QHBoxLayout()
        trigger_row3.addWidget(QLabel("长度:"))
        self._spin_trigger_length = QDoubleSpinBox()
        self._spin_trigger_length.setRange(0.001, 3600000.0)
        self._spin_trigger_length.setDecimals(3)
        self._spin_trigger_length.setSingleStep(0.1)
        self._spin_trigger_length.setValue(2.0)
        self._spin_trigger_length.setMaximumWidth(110)
        trigger_row3.addWidget(self._spin_trigger_length)
        self._combo_trigger_length_unit = QComboBox()
        self._combo_trigger_length_unit.addItem("us", 0.001)
        self._combo_trigger_length_unit.addItem("ms", 1.0)
        self._combo_trigger_length_unit.addItem("s", 1000.0)
        self._combo_trigger_length_unit.addItem("min", 60000.0)
        self._combo_trigger_length_unit.setCurrentText("s")
        self._combo_trigger_length_unit.setMaximumWidth(70)
        trigger_row3.addWidget(self._combo_trigger_length_unit)
        self._lbl_trigger_points = QLabel("点数: --")
        trigger_row3.addWidget(self._lbl_trigger_points)
        trigger_row3.addStretch()
        trigger_layout.addLayout(trigger_row3)

        self._lbl_trigger_state = QLabel("触发: 关闭")
        self._lbl_trigger_state.setStyleSheet("color: #888888; font-size: 12px;")
        trigger_layout.addWidget(self._lbl_trigger_state)
        self._make_group_collapsible(trigger_group)
        self._right_content.addWidget(trigger_group)
        self._right_content.setSizes([520, 190, 230])
        self._refresh_trigger_channel_combo()

        self._h_splitter.addWidget(self._right_panel)
        # 波形区默认占主导, 但右侧配置区允许按需拉宽。
        self._h_splitter.setStretchFactor(0, 5)
        self._h_splitter.setStretchFactor(1, 1)
        self._h_splitter.setChildrenCollapsible(False)
        self._right_panel.setMinimumWidth(420)
        self._right_panel.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._h_splitter.setCollapsible(0, False)
        self._h_splitter.setCollapsible(1, True)
        self._h_splitter.setSizes([980, 420])

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
        self._chk_hex_rx.setChecked(True)
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
        self._chk_hex_tx.setChecked(True)
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

    def _make_group_collapsible(self, group):
        group.setCheckable(True)
        group.setChecked(True)
        group.toggled.connect(
            lambda checked, g=group: self._set_group_expanded(g, checked))
        self._set_group_expanded(group, True)

    def _set_group_expanded(self, group, expanded):
        layout = group.layout()
        if layout is not None:
            self._set_layout_visible(layout, expanded)
        if expanded:
            group.setMaximumHeight(16777215)
        else:
            collapsed_height = group.fontMetrics().height() + 24
            group.setMaximumHeight(collapsed_height)
        group.updateGeometry()

    def _set_layout_visible(self, layout, visible):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(visible)
            child_layout = item.layout()
            if child_layout is not None:
                self._set_layout_visible(child_layout, visible)

    def _connect_signals(self):
        self._serial.data_received.connect(self._on_data_received)
        self._serial.connection_changed.connect(self._on_connection_changed)
        self._serial.error_occurred.connect(self._on_serial_error)
        self._parser.frames_decoded.connect(self._on_frames_decoded)
        self._parser.parse_error.connect(self._on_parse_error)
        self._parser.format_detected.connect(self._on_format_detected)
        self._channel_config.channel_changed.connect(
            self._on_channel_config_changed)
        self._channel_config.channel_count_changed.connect(
            self._on_channel_count_changed)
        self._channel_config.channel_selected.connect(
            self._waveform.select_channel)
        self._channel_config.y_link_channels_changed.connect(
            self._waveform.set_y_link_channels)
        self._waveform.set_y_link_channels(
            self._channel_config.y_link_channels())
        self._waveform.buffer_size_changed.connect(
            self._on_buffer_size_changed)
        self._waveform._btn_clear.clicked.connect(
            self._exit_waveform_import_mode)
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
        self._chk_trigger_enable.toggled.connect(
            self._on_trigger_enable_toggled)
        self._btn_trigger_arm.clicked.connect(self._arm_trigger_capture)
        self._combo_trigger_channel.currentIndexChanged.connect(
            self._reset_trigger_after_setting_change)
        self._combo_trigger_mode.currentIndexChanged.connect(
            self._reset_trigger_after_setting_change)
        self._spin_trigger_threshold.valueChanged.connect(
            self._reset_trigger_after_setting_change)
        self._spin_trigger_pre_percent.valueChanged.connect(
            self._on_trigger_sample_setting_changed)
        self._spin_trigger_length.valueChanged.connect(
            self._on_trigger_sample_setting_changed)
        self._combo_trigger_length_unit.currentIndexChanged.connect(
            self._on_trigger_sample_setting_changed)
        self._btn_save_waveform.clicked.connect(self._save_current_waveform)
        self._btn_import_waveform.clicked.connect(self._import_waveform_csv)
        self._btn_toggle_channel_panel.clicked.connect(
            self._toggle_channel_panel)

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

    # ─── 触发采样 ─────────────────────────────────────────────

    def _refresh_trigger_channel_combo(self, active_count=None):
        if not hasattr(self, '_combo_trigger_channel'):
            return

        current = self._combo_trigger_channel.currentData()
        if current is None:
            current = 0
        if active_count is None:
            active_count = (
                self._spin_channels.value()
                if hasattr(self, '_spin_channels')
                else DEFAULT_CHANNEL_COUNT)
        configured_count = (
            self._channel_config.channel_count()
            if hasattr(self, '_channel_config') else MAX_CHANNEL_COUNT)
        active_count = max(1, min(int(active_count), configured_count))

        self._combo_trigger_channel.blockSignals(True)
        self._combo_trigger_channel.clear()
        for ch in range(active_count):
            cfg = self._channel_config.get_channel_config(ch)
            name = cfg['name'] if cfg else f"CH{ch + 1}"
            self._combo_trigger_channel.addItem(
                f"CH{ch + 1}: {name}", ch)
        index = self._combo_trigger_channel.findData(current)
        self._combo_trigger_channel.setCurrentIndex(max(0, index))
        self._combo_trigger_channel.blockSignals(False)

    def _on_trigger_enable_toggled(self, checked):
        self._btn_trigger_arm.setEnabled(checked)
        if checked:
            self._arm_trigger_capture()
        else:
            self._reset_trigger_capture_state()
            self._waveform.set_follow_latest_enabled(True)
            self._lbl_trigger_state.setText("触发: 关闭")

    def _reset_trigger_after_setting_change(self, *_):
        if self._chk_trigger_enable.isChecked():
            self._reset_trigger_capture_state()
            self._waveform.set_follow_latest_enabled(False)
            self._lbl_trigger_state.setText("触发: 参数已修改，点击重新触发")
            self._statusbar.showMessage(
                "触发参数已修改，请点击重新触发开始采样", 2500)

    def _on_trigger_sample_setting_changed(self, *_):
        self._update_trigger_sample_count_label()
        self._reset_trigger_after_setting_change()

    def _trigger_source_channel(self):
        ch = self._combo_trigger_channel.currentData()
        if ch is None:
            return 0
        return int(ch)

    def _trigger_mode(self):
        mode = self._combo_trigger_mode.currentData()
        return mode or "rising"

    def _trigger_length_ms(self):
        factor = self._combo_trigger_length_unit.currentData()
        if factor is None:
            factor = 1000.0
        return max(0.001, self._spin_trigger_length.value() * float(factor))

    def _trigger_sample_counts(self):
        interval_ms = max(float(self._device_frame_period_ms), 0.001)
        total = max(1, int(math.ceil(
            self._trigger_length_ms() / interval_ms - 1e-12)))
        pre_percent = self._spin_trigger_pre_percent.value() / 100.0
        pre = int(round(total * pre_percent))
        pre = max(0, min(pre, max(0, total - 1)))
        return total, pre

    def _update_trigger_sample_count_label(self, *_):
        if not hasattr(self, '_lbl_trigger_points'):
            return
        total, pre = self._trigger_sample_counts()
        self._lbl_trigger_points.setText(f"点数: {total} 预:{pre}")

    def _reset_trigger_capture_state(self):
        self._trigger_armed = False
        self._trigger_capturing = False
        self._trigger_pre_buffer = deque()
        self._trigger_capture_frames = []
        self._trigger_prev_value = None
        self._trigger_total_samples = 0
        self._trigger_pre_samples = 0
        self._trigger_sample_index = None
        self._trigger_sample_channel = None
        self._hide_trigger_wait_dialog()
        if hasattr(self, '_waveform'):
            self._waveform.clear_trigger_marker()

    def _show_trigger_wait_dialog(self, title="触发采样中",
                                  detail_text="正在等待触发条件...",
                                  progress_value=None,
                                  progress_total=None):
        if self._trigger_wait_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("触发采样")
            dialog.setModal(False)
            dialog.setMinimumWidth(320)
            layout = QVBoxLayout(dialog)
            label = QLabel("触发采样中")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-size: 18px; font-weight: bold;")
            detail = QLabel("正在等待触发条件...")
            detail.setAlignment(Qt.AlignCenter)
            detail.setWordWrap(True)
            progress = QProgressBar()
            progress.setTextVisible(True)
            progress.setAlignment(Qt.AlignCenter)
            progress.setMinimumWidth(260)
            progress.setMaximumWidth(300)
            progress.setStyleSheet("QProgressBar { text-align: center; }")
            layout.addWidget(label)
            layout.addWidget(detail)
            layout.addWidget(progress, 0, Qt.AlignHCenter)
            self._trigger_wait_dialog = dialog
            self._trigger_wait_title_label = label
            self._trigger_wait_detail_label = detail
            self._trigger_wait_progress = progress

        if self._trigger_wait_title_label is not None:
            self._trigger_wait_title_label.setText(title)
        if self._trigger_wait_detail_label is not None:
            self._trigger_wait_detail_label.setText(detail_text)
        if self._trigger_wait_progress is not None:
            if progress_value is None or progress_total is None:
                self._trigger_wait_progress.setRange(0, 0)
                self._trigger_wait_progress.setFormat("等待触发")
            else:
                progress_total = max(1, int(progress_total))
                progress_value = max(0, min(int(progress_value), progress_total))
                self._trigger_wait_progress.setRange(0, progress_total)
                self._trigger_wait_progress.setValue(progress_value)
                percent = progress_value / progress_total * 100.0
                self._trigger_wait_progress.setFormat(
                    f"{progress_value}/{progress_total} ({percent:.1f}%)")
        was_visible = self._trigger_wait_dialog.isVisible()
        self._trigger_wait_dialog.show()
        if not was_visible:
            self._center_trigger_wait_dialog()
        self._trigger_wait_dialog.raise_()

    def _hide_trigger_wait_dialog(self):
        if self._trigger_wait_dialog is not None:
            self._trigger_wait_dialog.hide()

    def _center_trigger_wait_dialog(self):
        if self._trigger_wait_dialog is None:
            return
        self._trigger_wait_dialog.adjustSize()
        parent_rect = self.frameGeometry()
        dialog_rect = self._trigger_wait_dialog.frameGeometry()
        dialog_rect.moveCenter(parent_rect.center())
        self._trigger_wait_dialog.move(dialog_rect.topLeft())

    def _update_trigger_capture_progress_dialog(self, force=False):
        captured = min(len(self._trigger_capture_frames),
                       self._trigger_total_samples)
        total = max(1, self._trigger_total_samples)
        refresh_step = max(1, total // 100)
        if (not force and captured not in (0, 1, total) and
                captured % refresh_step != 0):
            return

        text = f"触发采集中: 捕获中 {captured}/{total}"
        self._lbl_trigger_state.setText(text)
        self._show_trigger_wait_dialog(
            title="触发采集中",
            detail_text=text,
            progress_value=captured,
            progress_total=total)
        QApplication.processEvents()

    def _arm_trigger_capture(self, *_):
        if not self._chk_trigger_enable.isChecked():
            return

        self._waveform_import_mode = False
        total, pre = self._trigger_sample_counts()
        self._trigger_total_samples = total
        self._trigger_pre_samples = pre
        self._trigger_pre_buffer = deque(maxlen=pre)
        self._trigger_capture_frames = []
        self._trigger_prev_value = None
        self._trigger_sample_index = None
        self._trigger_sample_channel = None
        self._trigger_armed = True
        self._trigger_capturing = False

        if total > self._spin_bufsize.value():
            self._waveform.set_max_points(total)
            self._on_buffer_size_changed(total)
        self._waveform.set_follow_latest_enabled(False)
        self._waveform.clear_data()

        ch = self._trigger_source_channel()
        mode_text = self._combo_trigger_mode.currentText()
        threshold = self._spin_trigger_threshold.value()
        self._lbl_trigger_state.setText(
            f"触发采集中: 等待 CH{ch + 1} {mode_text} 阈值 {threshold:.6g}")
        self._statusbar.showMessage("触发采集中: 已布防", 1500)
        self._show_trigger_wait_dialog(detail_text=self._lbl_trigger_state.text())

    def _process_trigger_frames(self, frames):
        if not self._chk_trigger_enable.isChecked():
            self._waveform.add_data_points(frames)
            return

        if not self._trigger_armed and not self._trigger_capturing:
            return

        source_ch = self._trigger_source_channel()
        for frame in frames:
            if self._trigger_capturing:
                if self._append_trigger_capture_frame(frame):
                    break
                continue

            if not self._trigger_armed or source_ch >= len(frame):
                continue

            value = float(frame[source_ch])
            if self._trigger_condition_met(self._trigger_prev_value, value):
                self._trigger_capturing = True
                self._trigger_armed = False
                self._trigger_capture_frames = list(self._trigger_pre_buffer)
                self._trigger_sample_index = len(self._trigger_capture_frames)
                self._trigger_sample_channel = source_ch
                self._trigger_capture_frames.append(frame)
                self._trigger_pre_buffer.clear()
                self._trigger_prev_value = value
                self._update_trigger_capture_progress_dialog(force=True)
                if self._trigger_capture_complete():
                    break
            else:
                self._trigger_pre_buffer.append(frame)
                self._trigger_prev_value = value

        if self._trigger_capturing:
            self._update_trigger_capture_progress_dialog(force=True)

    def _append_trigger_capture_frame(self, frame):
        self._trigger_capture_frames.append(frame)
        self._update_trigger_capture_progress_dialog()
        return self._trigger_capture_complete()

    def _trigger_capture_complete(self):
        if len(self._trigger_capture_frames) < self._trigger_total_samples:
            return False

        captured = self._trigger_capture_frames[:self._trigger_total_samples]
        self._trigger_capturing = False
        self._trigger_armed = False
        self._trigger_pre_buffer.clear()
        self._trigger_capture_frames = captured

        self._hide_trigger_wait_dialog()
        self._load_trigger_waveform_with_progress(captured)
        if self._trigger_sample_index is not None:
            trigger_index = min(self._trigger_sample_index, len(captured) - 1)
            self._waveform.set_trigger_marker_sample_index(
                trigger_index, self._trigger_sample_channel)
        self._waveform.set_follow_latest_enabled(False)

        self._lbl_trigger_state.setText(
            f"触发: 完成 {len(captured)} 点")
        self._statusbar.showMessage(
            f"触发采样完成: {len(captured)} 点", 3000)
        return True

    def _load_trigger_waveform_with_progress(self, frames):
        total = len(frames)
        progress = QProgressDialog("正在加载触发波形...", "", 0,
                                   max(total, 1), self)
        progress.setWindowTitle("加载波形")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        QApplication.processEvents()

        try:
            self._waveform.set_follow_latest_enabled(True)
            self._waveform.clear_data()
            if total <= 0:
                return

            chunk_size = max(1000, min(50000, int(math.ceil(total / 100.0))))
            for start in range(0, total, chunk_size):
                end = min(start + chunk_size, total)
                self._waveform.add_data_points(
                    frames[start:end], ignore_pause=True)
                progress.setLabelText(f"正在加载触发波形... {end}/{total}")
                progress.setValue(end)
                QApplication.processEvents()

            self._waveform._on_refresh_tick()
        finally:
            progress.setValue(max(total, 1))
            progress.close()

    def _trigger_condition_met(self, previous, current):
        threshold = self._spin_trigger_threshold.value()
        mode = self._trigger_mode()

        if mode == "level":
            return current >= threshold
        if previous is None:
            if mode == "falling":
                return current <= threshold
            return current >= threshold
        if mode == "falling":
            return previous > threshold >= current
        return previous < threshold <= current

    # ─── 波形保存 ─────────────────────────────────────────────

    def _save_current_waveform(self):
        if self._trigger_capture_ready():
            self._save_trigger_waveform()
        else:
            self._save_live_waveform()

    def _trigger_capture_ready(self):
        return (
            bool(self._trigger_capture_frames) and
            not self._trigger_capturing and
            not self._trigger_armed
        )

    def _exit_waveform_import_mode(self):
        self._waveform_import_mode = False

    def _save_live_waveform(self):
        data = self._waveform.get_sample_data()
        rows = data.get('rows', [])
        if not rows:
            self._show_error("没有可保存的连续采样波形")
            return

        interval_ms = data.get('sample_interval_ms',
                               self._device_frame_period_ms)
        channel_configs = self._export_channel_configs(
            len(data.get('channel_configs', [])),
            data.get('channel_configs', []))
        export_rows = [
            (sample_index, sample_index * interval_ms, values)
            for sample_index, values in rows
        ]
        self._save_waveform_csv(
            "连续采样", export_rows, channel_configs, interval_ms)

    def _save_trigger_waveform(self):
        if self._trigger_capturing:
            self._show_error("触发采样尚未完成")
            return
        if not self._trigger_capture_frames:
            self._show_error("没有可保存的触发采样波形")
            return

        channel_count = min(
            self._channel_config.channel_count(),
            max(len(frame) for frame in self._trigger_capture_frames))
        configs = [
            self._channel_config.get_channel_config(ch)
            for ch in range(channel_count)
        ]
        interval_ms = max(float(self._device_frame_period_ms), 0.001)
        trigger_index = (
            self._trigger_sample_index
            if self._trigger_sample_index is not None else 0)
        trigger_channel = (
            self._trigger_sample_channel
            if self._trigger_sample_channel is not None else
            self._trigger_source_channel())

        export_rows = []
        for sample_index, frame in enumerate(self._trigger_capture_frames):
            values = [
                frame[ch] if ch < len(frame) else None
                for ch in range(channel_count)
            ]
            time_ms = (sample_index - trigger_index) * interval_ms
            export_rows.append((sample_index, time_ms, values))

        self._save_waveform_csv(
            "触发采样", export_rows, configs, interval_ms,
            trigger_index=trigger_index,
            trigger_channel=trigger_channel)

    def _save_waveform_csv(self, sample_type, rows, channel_configs,
                           interval_ms, trigger_index=None,
                           trigger_channel=None):
        path = self._ask_waveform_save_path()
        if not path:
            return

        try:
            self._write_waveform_csv(
                path, sample_type, rows, channel_configs,
                interval_ms, trigger_index, trigger_channel)
        except OSError as exc:
            self._show_error(f"保存失败: {exc}")
            return

        self._save_dir = os.path.dirname(path) or self._save_dir
        self._record_waveform_save(path)
        self._statusbar.showMessage(f"波形已保存: {path}", 5000)

    def _ask_waveform_save_path(self):
        default_path = self._next_default_save_path()
        path, _ = QFileDialog.getSaveFileName(
            self, "保存波形数据", default_path,
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return ""
        root, ext = os.path.splitext(path)
        if not ext:
            path = root + ".csv"
        return path

    def _next_default_save_path(self):
        directory = self._save_dir if os.path.isdir(self._save_dir) else os.getcwd()
        date_text = datetime.now().strftime("%Y%m%d")
        file_seq = self._next_daily_save_sequence(directory, date_text)
        runtime_seq = self._save_sequence_by_date.get(date_text, 0) + 1
        seq = max(file_seq, runtime_seq)
        self._pending_save_date = date_text
        self._pending_save_sequence = seq
        return os.path.join(directory, f"Data{seq}_{date_text}.csv")

    def _record_waveform_save(self, path):
        date_text = datetime.now().strftime("%Y%m%d")
        seq = self._pending_save_sequence or 0

        file_seq = self._sequence_from_save_name(path, date_text)
        if file_seq is not None:
            seq = max(seq, file_seq)

        if self._pending_save_date and self._pending_save_date != date_text:
            date_text = self._pending_save_date
        self._save_sequence_by_date[date_text] = max(
            self._save_sequence_by_date.get(date_text, 0), seq)
        self._pending_save_date = None
        self._pending_save_sequence = None

    @staticmethod
    def _next_daily_save_sequence(directory, date_text):
        pattern = re.compile(
            rf"^Data(\d+)_{re.escape(date_text)}(?:\D.*)?\.csv$",
            re.IGNORECASE)
        max_seq = 0
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        for name in names:
            match = pattern.match(name)
            if not match:
                continue
            max_seq = max(max_seq, int(match.group(1)))
        return max_seq + 1

    @staticmethod
    def _sequence_from_save_name(path, date_text):
        pattern = re.compile(
            rf"^Data(\d+)_{re.escape(date_text)}(?:\D.*)?\.csv$",
            re.IGNORECASE)
        match = pattern.match(os.path.basename(path))
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _channel_csv_header(ch_idx, config):
        name = config.get('name') or f"CH{ch_idx + 1}"
        unit = config.get('unit') or ""
        if unit:
            return f"CH{ch_idx + 1}:{name}({unit})"
        return f"CH{ch_idx + 1}:{name}"

    def _export_channel_configs(self, channel_count, fallback_configs=None):
        fallback_configs = fallback_configs or []
        configs = []
        for ch_idx in range(channel_count):
            fallback = (
                fallback_configs[ch_idx]
                if ch_idx < len(fallback_configs) else {})
            config = dict(fallback)
            panel_config = self._channel_config.get_channel_config(ch_idx)
            if panel_config:
                config.update(panel_config)
            config.setdefault('name', f"CH{ch_idx + 1}")
            config.setdefault('unit', '')
            config.setdefault('visible', True)
            config.setdefault('color', '#FF4444')
            config.setdefault('data_type', 'int16')
            configs.append(config)
        return configs

    @staticmethod
    def _metadata_bool_text(value):
        return "1" if bool(value) else "0"

    def _write_channel_config_metadata(self, writer, channel_configs):
        writer.writerow(["ChannelConfigVersion", "1"])
        writer.writerow(["ChannelCount", len(channel_configs)])
        for ch_idx, config in enumerate(channel_configs, start=1):
            prefix = f"Channel{ch_idx}"
            writer.writerow([
                f"{prefix}Name", config.get('name') or f"CH{ch_idx}"])
            writer.writerow([f"{prefix}Unit", config.get('unit') or ""])
            writer.writerow([f"{prefix}Color", config.get('color') or ""])
            writer.writerow([
                f"{prefix}Visible",
                self._metadata_bool_text(config.get('visible', True))])
            writer.writerow([
                f"{prefix}DataType",
                config.get('data_type') or "int16"])

    def _write_waveform_csv(self, path, sample_type, rows, channel_configs,
                            interval_ms, trigger_index=None,
                            trigger_channel=None):
        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        headers = ["Sample", "Time_ms"]
        headers.extend(
            self._channel_csv_header(ch_idx, config)
            for ch_idx, config in enumerate(channel_configs))

        with open(path, "w", newline="", encoding="utf-8-sig") as file_obj:
            writer = csv.writer(file_obj)
            writer.writerow(["Type", sample_type])
            writer.writerow(["SavedAt", saved_at])
            writer.writerow(["SampleIntervalMs", f"{interval_ms:.12g}"])
            self._write_channel_config_metadata(writer, channel_configs)
            if trigger_index is not None:
                writer.writerow(["TriggerSampleIndex", trigger_index])
            if trigger_channel is not None:
                writer.writerow(["TriggerChannelIndex", int(trigger_channel)])
            writer.writerow([])
            writer.writerow(headers)
            for sample_index, time_ms, values in rows:
                row = [sample_index, f"{time_ms:.12g}"]
                for value in values[:len(channel_configs)]:
                    if value is None:
                        row.append("")
                    else:
                        row.append(f"{float(value):.12g}")
                writer.writerow(row)

    def _import_waveform_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入波形数据", self._save_dir,
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return

        try:
            imported = self._read_waveform_csv(path)
        except (OSError, ValueError) as exc:
            self._show_error(f"导入失败: {exc}")
            return

        frames = imported['frames']
        channel_configs = imported['channel_configs']
        if not frames or not channel_configs:
            self._show_error("导入文件中没有有效波形数据")
            return

        if self._chk_trigger_enable.isChecked():
            self._chk_trigger_enable.setChecked(False)
        else:
            self._reset_trigger_capture_state()

        channel_count = len(channel_configs)
        self._channel_config.set_channel_count(channel_count)
        for ch_idx, config in enumerate(channel_configs):
            self._channel_config.set_channel_config(ch_idx, config)

        self._device_frame_period_ms = imported['sample_interval_ms']
        self._spin_frame_period.blockSignals(True)
        self._spin_frame_period.setValue(self._device_frame_period_ms)
        self._spin_frame_period.blockSignals(False)
        self._sync_parser_channel_types()
        self._waveform.load_sample_data(
            frames,
            sample_interval_ms=imported['sample_interval_ms'],
            channel_configs=channel_configs,
            trigger_index=imported.get('trigger_index'),
            trigger_channel=imported.get('trigger_channel'),
            follow_latest=False)
        self._waveform.set_active_channel_count(channel_count)
        self._waveform_import_mode = True
        self._latest_channel_values = frames[-1]
        self._channel_config.set_channel_values(frames[-1])
        self._save_dir = os.path.dirname(path) or self._save_dir
        self._lbl_trigger_state.setText("触发: 导入数据"
                                        if imported.get('trigger_index') is not None
                                        else "触发: 关闭")
        self._statusbar.showMessage(f"波形已导入: {path}", 5000)

    def _read_waveform_csv(self, path):
        metadata = {}
        headers = None
        data_rows = []

        with open(path, "r", newline="", encoding="utf-8-sig") as file_obj:
            reader = csv.reader(file_obj)
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                first = row[0].strip()
                if headers is None and first == "Sample":
                    headers = row
                    continue
                if headers is None:
                    if len(row) >= 2:
                        metadata[first] = row[1].strip()
                    continue
                data_rows.append(row)

        if headers is None or len(headers) < 3:
            raise ValueError("未找到 Sample/Time_ms 数据表头")

        channel_headers = headers[2:]
        channel_configs = [
            self._parse_import_channel_header(idx, header)
            for idx, header in enumerate(channel_headers)
        ]
        self._apply_import_channel_metadata(channel_configs, metadata)

        frames = []
        times_ms = []
        for row in data_rows:
            if len(row) < 2:
                continue
            try:
                times_ms.append(float(row[1]))
            except (TypeError, ValueError):
                times_ms.append(float(len(times_ms)))

            values = []
            for cell in row[2:2 + len(channel_configs)]:
                cell = cell.strip()
                if not cell:
                    values.append(float('nan'))
                    continue
                try:
                    values.append(float(cell))
                except ValueError:
                    values.append(float('nan'))
            if len(values) < len(channel_configs):
                values.extend([float('nan')] *
                              (len(channel_configs) - len(values)))
            frames.append(values)

        interval_ms = self._import_sample_interval(metadata, times_ms)
        trigger_index = None
        if metadata.get("TriggerSampleIndex"):
            try:
                trigger_index = int(float(metadata["TriggerSampleIndex"]))
            except ValueError:
                trigger_index = None
        trigger_channel = None
        if metadata.get("TriggerChannelIndex"):
            try:
                trigger_channel = int(float(metadata["TriggerChannelIndex"]))
            except ValueError:
                trigger_channel = None
        if trigger_channel is not None:
            trigger_channel = max(
                0, min(trigger_channel, len(channel_configs) - 1))

        return {
            'frames': frames,
            'channel_configs': channel_configs,
            'sample_interval_ms': interval_ms,
            'trigger_index': trigger_index,
            'trigger_channel': trigger_channel,
        }

    def _parse_import_channel_header(self, ch_idx, header):
        current = self._channel_config.get_channel_config(ch_idx)
        config = dict(current) if current else {}
        config.setdefault('name', f"CH{ch_idx + 1}")
        config.setdefault('unit', '')
        config.setdefault('visible', True)
        config.setdefault('data_type', 'int16')
        config.setdefault('color', self._waveform.get_channel_config(
            ch_idx).get('color') if self._waveform.get_channel_config(ch_idx)
            else '#FF4444')

        text = (header or "").strip()
        match = re.match(r"^CH\d+\s*:\s*(.*)$", text)
        if match:
            text = match.group(1).strip()
        unit_match = re.match(r"^(.*)\(([^()]*)\)$", text)
        if unit_match:
            config['name'] = unit_match.group(1).strip() or config['name']
            config['unit'] = unit_match.group(2).strip()
        elif text:
            config['name'] = text
        return config

    @staticmethod
    def _parse_metadata_bool(value, default=True):
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on", "visible", "show"):
            return True
        if text in ("0", "false", "no", "off", "hidden", "hide"):
            return False
        return default

    def _apply_import_channel_metadata(self, channel_configs, metadata):
        for ch_idx, config in enumerate(channel_configs):
            prefix = f"Channel{ch_idx + 1}"

            name_key = f"{prefix}Name"
            if name_key in metadata and metadata[name_key]:
                config['name'] = metadata[name_key]

            unit_key = f"{prefix}Unit"
            if unit_key in metadata:
                config['unit'] = metadata[unit_key]

            color_key = f"{prefix}Color"
            color = metadata.get(color_key, "").strip()
            if re.match(r"^#[0-9A-Fa-f]{6}$", color):
                config['color'] = color

            visible_key = f"{prefix}Visible"
            if visible_key in metadata:
                config['visible'] = self._parse_metadata_bool(
                    metadata[visible_key], config.get('visible', True))

            data_type_key = f"{prefix}DataType"
            data_type = metadata.get(data_type_key, "").strip()
            if data_type:
                config['data_type'] = data_type

    def _import_sample_interval(self, metadata, times_ms):
        value = metadata.get("SampleIntervalMs")
        if value:
            try:
                return max(0.001, float(value))
            except ValueError:
                pass
        if len(times_ms) >= 2:
            diffs = [
                times_ms[i + 1] - times_ms[i]
                for i in range(len(times_ms) - 1)
                if times_ms[i + 1] != times_ms[i]
            ]
            if diffs:
                return max(0.001, abs(diffs[0]))
        return max(float(self._device_frame_period_ms), 0.001)

    def _toggle_channel_panel(self):
        visible = not self._channel_panel_visible
        self._set_channel_panel_visible(visible)

    def _set_channel_panel_visible(self, visible):
        self._channel_panel_visible = bool(visible)
        sizes = self._h_splitter.sizes()
        if not visible:
            if len(sizes) > 1 and sizes[1] > 0:
                self._channel_panel_last_width = sizes[1]
            self._right_panel.setVisible(False)
            if sizes:
                self._h_splitter.setSizes([sum(sizes), 0])
            self._btn_toggle_channel_panel.setText("显示配置")
            return

        self._right_panel.setVisible(True)
        total = sum(sizes) if sizes else self.width()
        panel_width = max(420, self._channel_panel_last_width)
        wave_width = max(400, total - panel_width)
        self._h_splitter.setSizes([wave_width, panel_width])
        self._btn_toggle_channel_panel.setText("隐藏配置")

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
        self._waveform_import_mode = False
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
        if self._chk_trigger_enable.isChecked():
            self._arm_trigger_capture()
        else:
            self._reset_trigger_capture_state()

    def _on_frame_decoded(self, values):
        self._on_frames_decoded([values])

    def _on_frames_decoded(self, frames):
        if not frames:
            return

        frame_count = len(frames)
        self._frame_count += frame_count
        self._fps_timer_count += frame_count
        if self._waveform_import_mode:
            self._live_ui_dirty = True
            return
        self._latest_channel_values = frames[-1]
        self._live_ui_dirty = True
        self._process_trigger_frames(frames)

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
        if ch_idx >= self._waveform.channel_count():
            self._waveform.set_channel_count(ch_idx + 1)
        self._waveform.set_channel_name(ch_idx, config['name'])
        self._waveform.set_channel_color(ch_idx, config['color'])
        self._waveform.set_channel_visible(ch_idx, config['visible'])
        self._waveform.set_channel_unit(ch_idx, config.get('unit', ''))
        self._refresh_trigger_channel_combo()
        if ch_idx >= len(self._channel_data_types):
            self._sync_parser_channel_types()
            self._update_sample_interval()
            return

        if config['data_type'] != self._channel_data_types[ch_idx]:
            self._channel_data_types[ch_idx] = config['data_type']
            self._parser.set_channel_data_type(ch_idx, config['data_type'])
            self._update_sample_interval()

    def _on_channel_count_changed(self, count):
        self._spin_channels.blockSignals(True)
        self._spin_channels.setValue(count)
        self._spin_channels.blockSignals(False)

        self._waveform.set_channel_count(count)
        self._waveform.set_active_channel_count(count)
        for ch_idx, config in enumerate(self._channel_config.get_all_configs()):
            self._waveform.set_channel_name(ch_idx, config['name'])
            self._waveform.set_channel_color(ch_idx, config['color'])
            self._waveform.set_channel_visible(ch_idx, config['visible'])
            self._waveform.set_channel_unit(ch_idx, config.get('unit', ''))
        self._waveform.set_y_link_channels(
            self._channel_config.y_link_channels())

        self._sync_parser_channel_types()
        self._refresh_trigger_channel_combo(count)
        self._latest_channel_values = None
        self._channel_config.set_channel_values([])
        self._update_sample_interval()

    def _sync_parser_channel_types(self):
        configs = self._channel_config.get_all_configs()
        data_types = [cfg.get('data_type', 'int16') for cfg in configs]
        self._parser.set_channel_data_types(data_types)
        self._channel_data_types = self._parser.data_types

    def _on_format_detected(self, num_ch, data_len):
        """解析器自动检测到帧格式"""
        self._spin_channels.setValue(num_ch)
        self._waveform.set_active_channel_count(num_ch)
        self._refresh_trigger_channel_combo(num_ch)
        self._update_sample_interval()
        self._statusbar.showMessage(
            f"检测到协议: {num_ch}通道, 数据区{data_len}字节", 5000)

    def _on_buffer_size_changed(self, size):
        """波形组件自动调整缓冲点数, 同步到Spinbox"""
        if size > self._spin_bufsize.maximum():
            self._spin_bufsize.setMaximum(size)
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
        self._reset_trigger_after_setting_change()

    def _update_sample_interval(self):
        """根据波特率和帧大小计算每帧时间间隔, 更新X轴刻度"""
        self._waveform.set_sample_interval_ms(self._device_frame_period_ms)
        self._update_trigger_sample_count_label()

    # ─── 状态更新 ─────────────────────────────────────────────

    def _on_connection_changed(self, connected):
        self._update_port_status(connected)
        self._combo_port.setEnabled(not connected)
        self._btn_refresh.setEnabled(not connected)
        if connected:
            self._waveform_import_mode = False
            self._update_sample_interval()
            self._channel_config.set_channel_values([])
            self._waveform.clear_data()
            if self._chk_trigger_enable.isChecked():
                self._arm_trigger_capture()
            else:
                self._reset_trigger_capture_state()
                self._waveform.set_follow_latest_enabled(True)

            self._btn_connect.setText("关闭串口")
            self._btn_connect.setStyleSheet(
                "QPushButton { background-color: #C62828; color: white; "
                "font-weight: bold; padding: 6px 16px; font-size: 13px; }"
                "QPushButton:hover { background-color: #D32F2F; }")
        else:
            self._waveform.set_follow_latest_enabled(False)
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
