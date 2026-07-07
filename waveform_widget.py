"""多通道波形显示组件

核心设计:
  - 共享X轴(时间), 各通道独立Y轴
  - 各通道Y轴独立缩放, 互不影响
  - X轴缩放/平移时所有通道同步
  - 只显示当前选中通道的Y轴, 其他通道曲线可见但无Y轴
  - 游标卡尺功能(双竖线测量)
  - 使用可增长缓存和视野抽样实现高性能实时刷新
  - 左键框选放大: 按住左键拖拽矩形, 松开放大到选区
"""
import math

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets


DEFAULT_CHANNEL_COLORS = [
    '#FF4444', '#44FF44', '#4488FF', '#FFAA00',
    '#FF44FF', '#44FFFF', '#FFFF44', '#AAAAFF',
    '#FF8888', '#88FF88'
]


def default_channel_color(ch_idx):
    return DEFAULT_CHANNEL_COLORS[ch_idx % len(DEFAULT_CHANNEL_COLORS)]


class TimeAxisItem(pg.AxisItem):
    """自适应时间单位的X轴 (µs / ms / s / min / h)"""

    _UNITS = [
        (3600000, 'h'),
        (60000, 'min'),
        (1000, 's'),
        (1, 'ms'),
        (0.001, 'µs'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(False)
        self._last_unit = None
        self._label_style = {'color': '#CCCCCC', 'font-size': '10pt'}

    @classmethod
    def best_unit(cls, span_ms):
        """根据当前可见时间跨度选择最易读的单位。"""
        span_ms = abs(span_ms)
        if span_ms >= 2 * 3600000:
            return 3600000, 'h'
        if span_ms >= 2 * 60000:
            return 60000, 'min'
        if span_ms >= 2 * 1000:
            return 1000, 's'
        if span_ms >= 1:
            return 1, 'ms'
        return 0.001, 'µs'

    def tickStrings(self, values, scale, spacing):
        if len(values) == 0:
            return []

        view = self.linkedView()
        if view is not None:
            x_min, x_max = view.viewRange()[0]
            span_ms = x_max - x_min
        else:
            span_ms = max(values) - min(values)
            if span_ms <= 0:
                span_ms = spacing

        if self._last_unit != 'compound':
            self._last_unit = 'compound'
            self.setLabel(text='时间', units='', **self._label_style)
        return [self.format_time_ms(v) for v in values]

        factor, unit = self.best_unit(span_ms)
        # 单位变化时更新标签 (仅在变化时触发, 避免无限重绘)
        if unit != self._last_unit:
            self._last_unit = unit
            self.setLabel(text='时间', units=unit, **self._label_style)
        return [f'{v / factor:.4g}' for v in values]

    @classmethod
    def format_time_ms(cls, value_ms, max_parts=2):
        sign = '-' if value_ms < 0 else ''
        total_us = int(round(abs(float(value_ms)) * 1000.0))
        if total_us == 0:
            return '0'

        units = [
            (3600_000_000, 'h'),
            (60_000_000, 'min'),
            (1_000_000, 's'),
            (1000, 'ms'),
            (1, 'us'),
        ]
        parts = []
        remaining = total_us
        for unit_us, suffix in units:
            value, remaining = divmod(remaining, unit_us)
            if value:
                parts.append(f'{value}{suffix}')
                if len(parts) >= max_parts:
                    break
        return sign + ''.join(parts)


class ZoomViewBox(pg.ViewBox):
    """支持左键框选放大的 ViewBox"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseMode(self.PanMode)
        self.setMenuEnabled(False)  # 禁用右键菜单, 左键用于框选放大
        self._zooming = False
        self._zooming = False
        self._zoom_start = None
        self._rubber_band = None
        self._glw_ref = None
        self._click_callback = None
        self._zoom_exclusion_lines = []
        self._zoom_line_hit_px = 8

    def set_click_callback(self, callback):
        self._click_callback = callback

    def set_zoom_exclusion_lines(self, lines):
        self._zoom_exclusion_lines = list(lines or [])

    def setGLW(self, glw):
        """绑定 GraphicsLayoutWidget, 用于绘制矩形选框"""
        self._glw_ref = glw
        self._rubber_band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Rectangle, glw)
        self._rubber_band.setStyleSheet(
            "QRubberBand { border: 2px solid #0078D7; "
            "background: rgba(0, 120, 215, 40); }")
        glw.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截鼠标事件, 实现左键框选放大"""
        if self._rubber_band is None:
            return False

        etype = event.type()

        if etype == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton:
                pos = event.pos()
                if self._is_on_zoom_exclusion_line(pos):
                    return False
                scene_pos = self._viewport_to_scene(pos)
                vb_rect = self.sceneBoundingRect()
                if scene_pos is not None and vb_rect.contains(scene_pos):
                    self._zooming = True
                    self._zoom_start = pos
                    self._rubber_band.setGeometry(
                        QtCore.QRect(pos, QtCore.QSize()))
                    self._rubber_band.show()
                    return True

        elif etype == QtCore.QEvent.MouseMove:
            if self._zooming and self._zoom_start is not None:
                rect = QtCore.QRect(self._zoom_start, event.pos()).normalized()
                self._rubber_band.setGeometry(rect)
                return True

        elif etype == QtCore.QEvent.MouseButtonRelease:
            if self._zooming and event.button() == QtCore.Qt.LeftButton:
                self._zooming = False
                self._rubber_band.hide()
                if self._is_click(event.pos()):
                    self._handle_click(event.pos())
                else:
                    self._apply_zoom(event.pos())
                return True

        return False

    def _is_click(self, end_pos):
        if self._zoom_start is None:
            return False
        delta = end_pos - self._zoom_start
        return abs(delta.x()) < 5 and abs(delta.y()) < 5

    def _handle_click(self, pos):
        if self._click_callback is None:
            return
        scene_pos = self._viewport_to_scene(pos)
        if scene_pos is None or not self.sceneBoundingRect().contains(scene_pos):
            return
        self._click_callback(scene_pos, pos)

    def _viewport_to_scene(self, pos):
        if self._glw_ref is None:
            return None
        return self._glw_ref.mapToScene(pos)

    def _is_on_zoom_exclusion_line(self, pos):
        if not self._zoom_exclusion_lines or self._glw_ref is None:
            return False

        scene_pos = self._viewport_to_scene(pos)
        if scene_pos is None:
            return False

        for item in self.scene().items(scene_pos):
            current = item
            while current is not None:
                if current in self._zoom_exclusion_lines:
                    return True
                current = current.parentItem()

        data_pos = self.mapSceneToView(scene_pos)
        left_pos = QtCore.QPoint(
            pos.x() - self._zoom_line_hit_px, pos.y())
        right_pos = QtCore.QPoint(
            pos.x() + self._zoom_line_hit_px, pos.y())
        left_scene = self._viewport_to_scene(left_pos)
        right_scene = self._viewport_to_scene(right_pos)
        if left_scene is None or right_scene is None:
            return False

        left_x = self.mapSceneToView(left_scene).x()
        right_x = self.mapSceneToView(right_scene).x()
        tolerance = abs(right_x - left_x) * 0.5

        for line in self._zoom_exclusion_lines:
            if line is None:
                continue
            if hasattr(line, 'isVisible') and not line.isVisible():
                continue
            try:
                if abs(float(line.value()) - data_pos.x()) <= tolerance:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _apply_zoom(self, end_pos):
        """将矩形选区映射到数据坐标并放大"""
        if self._zoom_start is None:
            return

        start = self._zoom_start
        x1 = min(start.x(), end_pos.x())
        x2 = max(start.x(), end_pos.x())
        y1 = min(start.y(), end_pos.y())
        y2 = max(start.y(), end_pos.y())

        # 忽略过小的选区 (防止误触)
        if abs(x2 - x1) < 5 or abs(y2 - y1) < 5:
            return

        # 像素坐标 → 场景坐标 → 数据坐标
        glw = self._glw_ref
        if glw is None:
            return

        scene_start = glw.mapToScene(QtCore.QPoint(x1, y1))
        scene_end = glw.mapToScene(QtCore.QPoint(x2, y2))

        # 场景坐标映射到 ViewBox 数据坐标
        data_start = self.mapSceneToView(scene_start)
        data_end = self.mapSceneToView(scene_end)

        x_min = min(data_start.x(), data_end.x())
        x_max = max(data_start.x(), data_end.x())
        y_min = min(data_start.y(), data_end.y())
        y_max = max(data_start.y(), data_end.y())

        # 设置新的视图范围
        self.setRange(
            QtCore.QRectF(x_min, y_min, x_max - x_min, y_max - y_min),
            padding=0)


class CursorStatsDialog(QtWidgets.QDialog):
    """Floating cursor measurement table."""

    lock_changed = QtCore.pyqtSignal(bool)
    floating_values_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("游标卡尺数据")
        self.resize(720, 360)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.setStyleSheet("""
            QDialog {
                background-color: #252526;
                color: #DCDCDC;
            }
            QLabel {
                color: #E6E6E6;
            }
            QCheckBox {
                color: #E6E6E6;
                spacing: 8px;
            }
            QTableWidget {
                background-color: #1E1E1E;
                color: #E6E6E6;
                gridline-color: #3C3C3C;
                border: 1px solid #3C3C3C;
                selection-background-color: #264F78;
                selection-color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #333333;
                color: #E6E6E6;
                border: 0;
                border-right: 1px solid #454545;
                border-bottom: 1px solid #454545;
                padding: 5px 8px;
                font-weight: bold;
            }
            QTableCornerButton::section {
                background-color: #333333;
                border: 0;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        top = QtWidgets.QGridLayout()
        self._lbl_t1 = QtWidgets.QLabel("C1: --")
        self._lbl_t2 = QtWidgets.QLabel("C2: --")
        self._lbl_dt = QtWidgets.QLabel("ΔT: --")
        for lbl in (self._lbl_t1, self._lbl_t2, self._lbl_dt):
            lbl.setStyleSheet("font-weight: bold;")
        top.addWidget(self._lbl_t1, 0, 0)
        top.addWidget(self._lbl_t2, 0, 1)
        top.addWidget(self._lbl_dt, 0, 2)
        self._chk_lock = QtWidgets.QCheckBox("锁定 C1-C2 间距")
        self._chk_lock.toggled.connect(self.lock_changed)
        top.addWidget(self._chk_lock, 1, 0, 1, 3)
        self._chk_floating_values = QtWidgets.QCheckBox("显示悬浮读数")
        self._chk_floating_values.setChecked(True)
        self._chk_floating_values.toggled.connect(
            self.floating_values_changed)
        top.addWidget(self._chk_floating_values, 2, 0, 1, 3)
        layout.addLayout(top)

        self._table = QtWidgets.QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "通道", "C1值", "C2值", "最小值", "最大值", "平均值"
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignCenter)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

    def set_measurements(self, t1_text, t2_text, dt_text, rows):
        self._lbl_t1.setText(f"C1: {t1_text}")
        self._lbl_t2.setText(f"C2: {t2_text}")
        self._lbl_dt.setText(f"ΔT: {dt_text}")

        self._table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                row['name'], row['c1'], row['c2'],
                row['min'], row['max'], row['avg']
            ]
            for col_idx, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if col_idx == 0:
                    item.setForeground(QtGui.QBrush(QtGui.QColor(row['color'])))
                else:
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#E6E6E6")))
                item.setBackground(QtGui.QBrush(QtGui.QColor(
                    "#252526" if row_idx % 2 else "#1E1E1E")))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self._table.setItem(row_idx, col_idx, item)

    def set_lock_checked(self, checked):
        self._chk_lock.blockSignals(True)
        self._chk_lock.setChecked(bool(checked))
        self._chk_lock.blockSignals(False)

    def set_floating_values_checked(self, checked):
        self._chk_floating_values.blockSignals(True)
        self._chk_floating_values.setChecked(bool(checked))
        self._chk_floating_values.blockSignals(False)


class MultiChannelWaveform(QtWidgets.QWidget):
    """多通道波形显示组件"""

    cursor_changed = QtCore.pyqtSignal(int, list)
    selected_channel_changed = QtCore.pyqtSignal(int)
    buffer_size_changed = QtCore.pyqtSignal(int)  # 缓冲点数变化

    def __init__(self, max_points=5000, num_channels=6, parent=None):
        super().__init__(parent)
        self._max_points = max_points
        self._num_channels = num_channels
        self._active_channel_count = num_channels
        self._paused = False
        self._selected_channel = 0
        self._sample_interval_ms = 1.0  # 每帧间隔(ms), 默认1ms

        self._buffers = [[] for _ in range(num_channels)]
        self._time_counter = 0

        self._channel_configs = [
            self._default_channel_config(i) for i in range(num_channels)]

        self._main_view_box = None
        self._channel_view_boxes = []
        self._curve_view_boxes = []
        self._channel_y_ranges = [None for _ in range(num_channels)]
        self._curves = []
        self._cursor1 = None
        self._cursor2 = None
        self._cursor_dialog = None
        self._cursor_value_labels = [None, None]
        self._cursor_floating_values_enabled = True
        self._cursor_anchor_ratios = [0.4, 0.6]
        self._cursor_syncing = False
        self._cursor_lock_enabled = False
        self._cursor_pair_delta = 0.0
        self._trigger_marker_item = None
        self._trigger_marker_label = None
        self._trigger_marker_sample_index = None
        self._trigger_marker_channel = None
        self._trigger_marker_view_box = None
        self._dirty = False
        self._needs_initial_range = True
        self._auto_x_range_update = False
        self._follow_latest_x = True
        self._channel_click_hit_px = 16.0

        self._setup_ui()

        # 定时刷新波形 (33ms ≈ 30fps, 避免每帧都重绘)
        self._refresh_timer = QtCore.QTimer()
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(33)

    @staticmethod
    def _default_channel_config(ch_idx):
        return {
            'name': f'CH{ch_idx + 1}',
            'color': default_channel_color(ch_idx),
            'visible': True,
            'unit': '',
        }

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ─── 工具栏 (紧凑) ───────────────────────────────
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(2, 2, 2, 2)
        toolbar.setSpacing(4)

        btn_style = ("QPushButton { padding: 1px 6px; font-size: 12px; "
                     "min-height: 20px; }")
        self._btn_pause = QtWidgets.QPushButton("暂停")
        self._btn_pause.setCheckable(True)
        self._btn_pause.setStyleSheet(btn_style)
        self._btn_pause.toggled.connect(self._on_pause_toggled)
        self._btn_clear = QtWidgets.QPushButton("清除")
        self._btn_clear.setStyleSheet(btn_style)
        self._btn_clear.clicked.connect(self.clear_data)
        self._btn_autorange = QtWidgets.QPushButton("自动范围")
        self._btn_autorange.setStyleSheet(btn_style)
        self._btn_autorange.clicked.connect(self._auto_range_all)
        self._btn_cursor = QtWidgets.QPushButton("游标卡尺")
        self._btn_cursor.setCheckable(True)
        self._btn_cursor.setStyleSheet(btn_style)
        self._btn_cursor.toggled.connect(self._on_cursor_toggled)
        self._btn_test = QtWidgets.QPushButton("测试数据")
        self._btn_test.setStyleSheet(btn_style)
        self._btn_test.clicked.connect(self._generate_test_data)

        self._combo_y_ch = QtWidgets.QComboBox()

        for i in range(self._num_channels):
            self._combo_y_ch.addItem(f"CH{i + 1}", i)
        self._combo_y_ch.currentIndexChanged.connect(
            self._on_y_channel_changed)
        self._combo_y_ch.setMaximumWidth(100)
        self._combo_y_ch.setStyleSheet(
            "QComboBox { padding: 1px 4px; font-size: 12px; "
            "min-height: 20px; }")
        self._lbl_sample_rate = QLabel_Y("")

        toolbar.addWidget(self._btn_pause)
        toolbar.addWidget(self._btn_clear)
        toolbar.addWidget(self._btn_autorange)
        toolbar.addWidget(self._btn_cursor)
        toolbar.addWidget(self._btn_test)
        toolbar.addSpacing(10)
        toolbar.addWidget(QLabel_Y("Y轴:"))
        toolbar.addWidget(self._combo_y_ch)
        toolbar.addSpacing(10)
        toolbar.addWidget(self._lbl_sample_rate)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ─── 绘图区 ───────────────────────────────────────
        self._glw = None

        # 主 PlotItem: 使用 ZoomViewBox 支持右键框选放大
        self._time_axis = TimeAxisItem(orientation='bottom')
        self._time_axis = TimeAxisItem(orientation='bottom')
        self._plot_item = pg.PlotItem(
            viewBox=ZoomViewBox(),
            axisItems={'bottom': self._time_axis})
        self._glw = pg.PlotWidget(plotItem=self._plot_item)
        self._glw.setBackground('#1E1E1E')
        self._plot_item.setDefaultPadding(0.01)  # 默认0.05, 缩小边距
        self._plot_item.showGrid(x=True, y=True, alpha=0.3)
        self._plot_item.enableAutoRange('xy', False)

        # 缩小轴标签字号
        axis_font = pg.QtGui.QFont()
        axis_font.setPointSize(10)
        axis_pen = pg.mkPen('#808080', width=1)
        hidden_axis_pen = pg.mkPen((0, 0, 0, 0))
        for side in ('left', 'bottom', 'right', 'top'):
            ax = self._plot_item.getAxis(side)
            ax.setStyle(tickFont=axis_font)
            ax.setLabel(color='#CCCCCC', **{'font-size': '10pt'})
        # 设置左侧和底部轴的画笔, 确保轴线可见并在原点相交
        left_axis = self._plot_item.getAxis('left')
        bottom_axis = self._plot_item.getAxis('bottom')
        left_axis.setPen(hidden_axis_pen)
        left_axis.setWidth(96)
        left_axis.setStyle(tickTextOffset=2, tickTextWidth=52)
        bottom_axis.setPen(hidden_axis_pen)
        bottom_axis.setHeight(46)
        bottom_axis.setStyle(tickTextOffset=8)
        top_axis = self._plot_item.getAxis('top')
        right_axis = self._plot_item.getAxis('right')
        top_axis.setPen(axis_pen)
        right_axis.setPen(axis_pen)
        top_axis.setStyle(showValues=False)
        right_axis.setStyle(showValues=False)
        top_axis.setHeight(0)
        right_axis.setWidth(0)
        top_axis.setVisible(False)
        right_axis.setVisible(False)

        # 关键: 设置 ViewBox 和 PlotItem 内部布局边距为 0
        # 使网格线延伸到轴线处, Y轴和X轴在原点相交
        vb = self._plot_item.getViewBox()
        self._main_view_box = vb
        vb.setDefaultPadding(0)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.border = axis_pen
        vb.setLimits(xMin=0)
        # 限制X轴最小值为0, 防止缩放时出现负数
        vb.setLimits(xMin=0)

        # 监听X轴范围变化, 自动调整缓冲点数
        vb.sigXRangeChanged.connect(self._on_x_range_changed)
        vb.sigYRangeChanged.connect(self._on_main_y_range_changed)
        vb.sigResized.connect(self._sync_channel_view_boxes)

        # 消除 PlotItem 内部布局边距
        pl = self._plot_item.layout
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        vb.setGLW(self._glw)
        vb.set_click_callback(self._select_channel_from_scene_click)
        self._right_axis = self._plot_item.getAxis('right')

        # 绑定 GLW 到 ViewBox, 启用框选放大
        vb.setGLW(self._glw)

        # 右侧Y轴
        self._right_axis = self._plot_item.getAxis('right')
        self._right_axis.setMaximumWidth(0)
        self._right_axis.setVisible(False)

        self._init_channels()

        # 游标信息标签 (紧凑, 可折叠)
        self._info_label = QtWidgets.QLabel("")
        self._info_label.setStyleSheet(
            "QLabel { background-color: #2D2D2D; color: #CCCCCC; "
            "padding: 2px 4px; font-family: Consolas, monospace; "
            "font-size: 12px; }"
        )
        self._info_label.setWordWrap(True)
        self._info_label.setMaximumHeight(40)
        self._info_label.setAlignment(
            QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        layout.addWidget(self._glw, stretch=1)
        layout.addWidget(self._info_label)

        # 初始CH1
        self._update_axis_display()

        # 记录 ViewBox 的场景坐标, 用于检测布局是否过期
        main_vb = self._plot_item.getViewBox()
        self._last_vb_rect = main_vb.sceneBoundingRect()
        self._last_device_pixel_ratio = self.devicePixelRatioF()
        self._last_vb_pixel_size = None
        self._last_axis_density = None
        self._relayout_pending = False

        # 定时器: 每200ms检查一次布局是否需要刷新 (跨DPI屏幕兜底)
        self._layout_check_timer = QtCore.QTimer(self)
        self._layout_check_timer.timeout.connect(self._check_layout)
        self._layout_check_timer.start(1000)
        self._schedule_relayout()

    def resizeEvent(self, event):
        """尺寸变化时"""
        super().resizeEvent(event)
        self._schedule_relayout()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_window_screen_signal()
        self._schedule_relayout()

    def event(self, event):
        result = super().event(event)
        relayout_events = [
            getattr(QtCore.QEvent, name, None)
            for name in ('WindowStateChange', 'PolishRequest', 'ShowToParent')
        ]
        if event.type() in [e for e in relayout_events if e is not None]:
            self._schedule_relayout()
        screen_change = getattr(QtCore.QEvent, 'ScreenChangeInternal', None)
        if screen_change is not None and event.type() == screen_change:
            self._schedule_relayout()
        return result

    def _legacy_check_layout_unused(self):
        """定期检查 ViewBox 布局是否与 GLW 尺寸匹配

        跨DPI屏幕拖拽时, Qt 可能不发 resizeEvent, 导致
        pyqtgraph 内部布局卡在旧尺寸。检测到不匹配时强制刷新。
        """
        glw = self._glw
        if glw is None:
            return

        glw_w = glw.width()
        glw_h = glw.height()
        if glw_w <= 0 or glw_h <= 0:
            return

        vb = self._plot_item.getViewBox()
        cur_rect = vb.sceneBoundingRect()

        # 理论值: ViewBox 宽 ≈ GLW宽 - 左轴(60) - PlotItem边距(10)
        #         ViewBox 高 ≈ GLW高 - X轴(30) - PlotItem边距(10)
        # 正常误差在 5px 以内; 超过 50px 认为布局过期
        if (abs(cur_rect.width() - (glw_w - 70)) > 50 or
                abs(cur_rect.height() - (glw_h - 40)) > 50):
            self._force_dpi_relayout()

    def _legacy_force_dpi_relayout_unused(self):
        """强制 pyqtgraph 重新执行完整的 resizeEvent 布局链"""
        glw = self._glw
        if glw is None:
            return
        cur = glw.size()
        w, h = cur.width(), cur.height()
        if w <= 0 or h <= 0:
            return
        glw.resize(w + 1, h + 1)
        glw.resize(w, h)
        glw.scene().update()

    def _check_layout(self):
        vb = self._plot_item.getViewBox()
        cur_rect = vb.sceneBoundingRect()
        dpr = self._effective_device_pixel_ratio()
        if (cur_rect != self._last_vb_rect or
                dpr != self._last_device_pixel_ratio):
            self._last_vb_rect = cur_rect
            self._last_device_pixel_ratio = dpr
            self._schedule_relayout()

    def _schedule_relayout(self):
        if getattr(self, '_relayout_pending', False):
            return
        self._relayout_pending = True
        QtCore.QTimer.singleShot(0, self._force_dpi_relayout)
        QtCore.QTimer.singleShot(80, self._force_dpi_relayout)
        QtCore.QTimer.singleShot(220, self._force_dpi_relayout)

    def _force_dpi_relayout(self):
        glw = self._glw
        if glw is None:
            self._relayout_pending = False
            return

        size = glw.size()
        w, h = size.width(), size.height()
        if w <= 1 or h <= 1:
            self._relayout_pending = False
            return

        self._plot_item.layout.invalidate()
        self._plot_item.layout.activate()

        glw.resize(w - 1, h - 1)
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        glw.resize(w, h)

        glw.updateGeometry()
        self._plot_item.layout.invalidate()
        self._plot_item.layout.activate()
        self._plot_item.updateGeometry()
        self._sync_channel_view_boxes()
        self._refresh_axis_density()
        self._invalidate_axis_cache()
        self._adapt_ranges_to_viewbox_size()
        self._last_vb_rect = self._main_view_box.sceneBoundingRect()
        self._last_device_pixel_ratio = self._effective_device_pixel_ratio()
        glw.scene().update()
        glw.viewport().update()
        self._relayout_pending = False

    def _effective_device_pixel_ratio(self):
        ratios = [self.devicePixelRatioF()]
        if self._glw is not None:
            ratios.append(self._glw.devicePixelRatioF())
            window = self._glw.window().windowHandle()
            if window is not None and window.screen() is not None:
                ratios.append(window.screen().devicePixelRatio())
        return max(float(r) for r in ratios if r)

    def _refresh_axis_density(self):
        if self._main_view_box is None:
            return

        rect = self._main_view_box.sceneBoundingRect()
        if rect.width() <= 1 or rect.height() <= 1:
            return

        dpr = self._effective_device_pixel_ratio()
        width_px = float(rect.width()) * dpr
        height_px = float(rect.height()) * dpr

        x_density = min(3.0, max(1.0, width_px / 900.0))
        y_density = min(2.4, max(1.0, height_px / 520.0))
        densities = (round(x_density, 2), round(y_density, 2))
        if densities == self._last_axis_density:
            return

        self._last_axis_density = densities
        self._plot_item.getAxis('bottom').setTickDensity(x_density)
        self._plot_item.getAxis('left').setTickDensity(y_density)

    def _invalidate_axis_cache(self):
        for side in ('left', 'bottom', 'right', 'top'):
            axis = self._plot_item.getAxis(side)
            axis.picture = None
            axis.updateGeometry()
            axis.update()
        self._time_axis.picture = None
        self._time_axis.update()

    def _adapt_ranges_to_viewbox_size(self):
        rect = self._main_view_box.sceneBoundingRect()
        width = float(rect.width())
        height = float(rect.height())
        if width <= 1 or height <= 1:
            return

        old_size = self._last_vb_pixel_size
        self._last_vb_pixel_size = (width, height)
        if old_size is None:
            return

        old_width, old_height = old_size
        if old_width <= 1 or old_height <= 1:
            return

        x_ratio = width / old_width
        y_ratio = height / old_height
        if abs(x_ratio - 1.0) < 0.02 and abs(y_ratio - 1.0) < 0.02:
            return

        x_range, y_range = self._main_view_box.viewRange()
        self._scale_x_range(x_range, x_ratio)
        self._scale_y_range(self._selected_channel, y_range, y_ratio)

        for ch in range(self._num_channels):
            if ch == self._selected_channel:
                continue
            y_saved = self._channel_y_ranges[ch]
            if y_saved is not None:
                self._scale_y_range(ch, y_saved, y_ratio)

    def _scale_x_range(self, x_range, ratio):
        x_min, x_max = x_range
        span = x_max - x_min
        if span <= 0:
            return
        new_span = span * ratio
        self._main_view_box.setXRange(x_min, x_min + new_span, padding=0)

    def _scale_y_range(self, ch, y_range, ratio):
        y_min, y_max = y_range
        span = y_max - y_min
        if span <= 0:
            return
        new_span = span * ratio
        new_range = (y_max - new_span, y_max)
        self._channel_y_ranges[ch] = new_range
        vb = (self._main_view_box if ch == self._selected_channel
              else self._channel_view_boxes[ch])
        vb.setYRange(new_range[0], new_range[1], padding=0)

    def _connect_window_screen_signal(self):
        window = self.window().windowHandle() if self.window() else None
        if window is None or getattr(self, '_screen_signal_window', None) is window:
            return
        old_window = getattr(self, '_screen_signal_window', None)
        if old_window is not None:
            try:
                old_window.screenChanged.disconnect(self._on_screen_changed)
            except (TypeError, RuntimeError):
                pass
        window.screenChanged.connect(self._on_screen_changed)
        self._screen_signal_window = window
        self._connect_screen_detail_signals(window.screen())

    def _on_screen_changed(self, screen):
        self._connect_screen_detail_signals(screen)
        self._schedule_relayout()

    def _connect_screen_detail_signals(self, screen):
        if screen is None or getattr(self, '_screen_signal_screen', None) is screen:
            return

        old_screen = getattr(self, '_screen_signal_screen', None)
        if old_screen is not None:
            for signal_name in (
                    'logicalDotsPerInchChanged',
                    'physicalDotsPerInchChanged',
                    'geometryChanged'):
                signal = getattr(old_screen, signal_name, None)
                if signal is not None:
                    try:
                        signal.disconnect(self._on_screen_metric_changed)
                    except (TypeError, RuntimeError):
                        pass

        for signal_name in (
                'logicalDotsPerInchChanged',
                'physicalDotsPerInchChanged',
                'geometryChanged'):
            signal = getattr(screen, signal_name, None)
            if signal is not None:
                signal.connect(self._on_screen_metric_changed)

        self._screen_signal_screen = screen

    def _on_screen_metric_changed(self, *args):
        self._schedule_relayout()

    def _init_channels(self):
        """初始化独立Y轴通道。

        当前选中的通道放在主 ViewBox 中接收鼠标缩放/平移；其他通道放在
        与主绘图区重叠的独立 ViewBox 中，并只同步X轴。
        """
        for i in range(self._num_channels):
            self._add_channel_plot(i)

        self._sync_channel_view_boxes()

    def _add_channel_plot(self, ch_idx):
        main_vb = self._main_view_box
        scene = self._plot_item.scene()
        cfg = self._channel_configs[ch_idx]
        pen = pg.mkPen(cfg['color'], width=1.5)
        curve = pg.PlotDataItem(pen=pen, antialias=False)

        ch_vb = pg.ViewBox(enableMenu=False)
        ch_vb.setDefaultPadding(0)
        ch_vb.setContentsMargins(0, 0, 0, 0)
        ch_vb.setMouseEnabled(x=False, y=False)
        ch_vb.setLimits(xMin=0)
        ch_vb.setXLink(main_vb)
        ch_vb.setZValue(-10)
        ch_vb.sigYRangeChanged.connect(
            lambda viewbox, range_info, ch=ch_idx:
            self._on_channel_y_range_changed(ch, range_info))
        scene.addItem(ch_vb)

        owner_vb = main_vb if ch_idx == self._selected_channel else ch_vb
        owner_vb.addItem(curve)

        self._channel_view_boxes.append(ch_vb)
        self._curve_view_boxes.append(owner_vb)
        self._curves.append(curve)

    def _remove_channel_plot(self, ch_idx):
        if ch_idx >= len(self._curves):
            return

        curve = self._curves[ch_idx]
        owner_vb = self._curve_view_boxes[ch_idx]
        try:
            owner_vb.removeItem(curve)
        except (TypeError, RuntimeError):
            pass

        ch_vb = self._channel_view_boxes[ch_idx]
        try:
            self._plot_item.scene().removeItem(ch_vb)
        except (TypeError, RuntimeError):
            pass

    def _sync_channel_view_boxes(self, *args):
        """让所有叠加 ViewBox 与主绘图区完全重合。"""
        if self._main_view_box is None:
            return
        rect = self._main_view_box.sceneBoundingRect()
        for vb in self._channel_view_boxes:
            vb.setGeometry(rect)
            vb.linkedViewChanged(self._main_view_box, vb.XAxis)

    def _select_channel_from_scene_click(self, scene_pos, viewport_pos=None):
        if self._main_view_box is None:
            return
        if not self._main_view_box.sceneBoundingRect().contains(scene_pos):
            return

        x_ms = self._main_view_box.mapSceneToView(scene_pos).x()
        x_tolerance = self._click_x_tolerance_ms(viewport_pos)
        best_ch = None
        best_distance = float('inf')

        for ch in range(self._active_channel_count):
            if not self._channel_configs[ch]['visible']:
                continue
            distance = self._curve_click_distance_px(
                ch, scene_pos, x_ms, x_tolerance)
            if distance is not None and distance < best_distance:
                best_ch = ch
                best_distance = distance

        if best_ch is not None and best_distance <= self._channel_click_hit_px:
            self.select_channel(best_ch)

    def _click_x_tolerance_ms(self, viewport_pos):
        pixel_radius = 8
        if viewport_pos is not None and self._glw is not None:
            y = viewport_pos.y()
            left_scene = self._glw.mapToScene(
                QtCore.QPoint(viewport_pos.x() - pixel_radius, y))
            right_scene = self._glw.mapToScene(
                QtCore.QPoint(viewport_pos.x() + pixel_radius, y))
            left_x = self._main_view_box.mapSceneToView(left_scene).x()
            right_x = self._main_view_box.mapSceneToView(right_scene).x()
            return max(abs(right_x - left_x) * 0.5,
                       self._sample_interval_ms)

        x_min, x_max = self._main_view_box.viewRange()[0]
        rect = self._main_view_box.sceneBoundingRect()
        width = max(float(rect.width()), 1.0)
        return max(abs(x_max - x_min) / width * pixel_radius,
                   self._sample_interval_ms)

    def _curve_click_distance_px(self, ch, scene_pos, x_ms, x_tolerance):
        x_data, y_data = self._curves[ch].getData()
        if x_data is None or y_data is None:
            return None

        x_arr = np.asarray(x_data, dtype=np.float64)
        y_arr = np.asarray(y_data, dtype=np.float64)
        count = min(len(x_arr), len(y_arr))
        if count <= 0:
            return None
        if len(x_arr) != count:
            x_arr = x_arr[:count]
        if len(y_arr) != count:
            y_arr = y_arr[:count]

        left = int(np.searchsorted(
            x_arr, x_ms - x_tolerance, side='left'))
        right = int(np.searchsorted(
            x_arr, x_ms + x_tolerance, side='right'))
        start = max(0, left - 1)
        end = min(count - 1, right)
        if start > end:
            nearest = int(np.argmin(np.abs(x_arr - x_ms)))
            start = max(0, nearest - 1)
            end = min(count - 1, nearest + 1)

        owner_vb = self._curve_view_boxes[ch]
        best = float('inf')
        for idx in range(start, end + 1):
            point = owner_vb.mapViewToScene(
                QtCore.QPointF(float(x_arr[idx]), float(y_arr[idx])))
            best = min(best, self._point_distance_px(scene_pos, point))
            if idx < end:
                next_point = owner_vb.mapViewToScene(
                    QtCore.QPointF(float(x_arr[idx + 1]),
                                   float(y_arr[idx + 1])))
                best = min(best, self._point_to_segment_distance_px(
                    scene_pos, point, next_point))
        return best

    @staticmethod
    def _point_distance_px(a, b):
        return math.hypot(a.x() - b.x(), a.y() - b.y())

    @staticmethod
    def _point_to_segment_distance_px(point, start, end):
        px, py = point.x(), point.y()
        x1, y1 = start.x(), start.y()
        x2, y2 = end.x(), end.y()
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq <= 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        nearest_x = x1 + t * dx
        nearest_y = y1 + t * dy
        return math.hypot(px - nearest_x, py - nearest_y)

    def _move_curve_to_view_box(self, ch, target_vb):
        curve = self._curves[ch]
        current_vb = self._curve_view_boxes[ch]
        if current_vb is target_vb:
            return
        current_vb.removeItem(curve)
        target_vb.addItem(curve)
        self._curve_view_boxes[ch] = target_vb

    def _apply_channel_y_range(self, ch, target_vb=None):
        y_range = self._channel_y_ranges[ch]
        if y_range is None:
            return
        vb = target_vb or self._curve_view_boxes[ch]
        vb.setYRange(y_range[0], y_range[1], padding=0)

    # ─── Y轴显示切换 ─────────────────────────────────────────

    def _on_y_channel_changed(self, combo_index):
        ch = self._combo_y_ch.itemData(combo_index)
        if ch is not None:
            self.select_channel(ch)

    def select_channel(self, ch):
        """选中某个通道, 显示其Y轴"""
        if ch < 0 or ch >= self._num_channels:
            return
        old_ch = self._selected_channel
        if old_ch == ch:
            self._update_axis_display()
            return

        if self._main_view_box is not None and 0 <= old_ch < self._num_channels:
            self._channel_y_ranges[old_ch] = tuple(
                self._main_view_box.viewRange()[1])
            self._move_curve_to_view_box(
                old_ch, self._channel_view_boxes[old_ch])
            self._apply_channel_y_range(
                old_ch, self._channel_view_boxes[old_ch])

        self._selected_channel = ch
        if self._main_view_box is not None:
            self._move_curve_to_view_box(ch, self._main_view_box)
            self._apply_channel_y_range(ch, self._main_view_box)

        self._update_axis_display()
        self.selected_channel_changed.emit(ch)

    def _update_axis_display(self):
        """更新Y轴标签颜色, 必要时自动范围到选中通道"""
        ch = self._selected_channel
        cfg = self._channel_configs[ch]
        color = cfg['color']
        name = cfg['name']
        unit = cfg.get('unit', '')
        axis_name = f"{name} ({unit})" if unit else name
        left_axis = self._plot_item.getAxis('left')

        left_axis.setLabel(axis_name, color=color)
        left_axis.setTextPen(pg.mkPen(color))
        left_axis.setVisible(True)
        self._right_axis.setVisible(False)
        self._right_axis.setMaximumWidth(0)

        if self._channel_y_ranges[ch] is None:
            self._auto_range_selected()

        # 同步下拉框 (不触发信号)
        self._combo_y_ch.blockSignals(True)
        self._combo_y_ch.setCurrentIndex(ch)
        self._combo_y_ch.blockSignals(False)
        self._update_trigger_marker_position()

    # ─── 数据输入 ─────────────────────────────────────────────

    def add_data_point(self, values):
        self.add_data_points([values])
        return
        """只积累数据, 不立即重绘"""
        if self._paused:
            return
        self._time_counter += 1
        active_count = min(len(values), self._num_channels)
        self.set_active_channel_count(active_count)
        for i in range(active_count):
            value = values[i]
            self._buffers[i].append(value)
            self._expand_y_range_for_value(i, value)
        self._dirty = True

    def add_data_points(self, frames):
        """Append decoded frames in batches; drawing still happens on timer."""
        if self._paused or not frames:
            return

        self._ensure_buffer_capacity(self._time_counter + len(frames))
        active_count = min(max(len(values) for values in frames),
                           self._num_channels)
        self.set_active_channel_count(active_count)

        batch_min = [float('inf')] * active_count
        batch_max = [float('-inf')] * active_count

        for values in frames:
            self._time_counter += 1
            for i, value in enumerate(values[:active_count]):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = float('nan')
                self._buffers[i].append(value)
                if not math.isfinite(value):
                    continue
                if value < batch_min[i]:
                    batch_min[i] = value
                if value > batch_max[i]:
                    batch_max[i] = value

        for i in range(active_count):
            if batch_min[i] != float('inf'):
                self._expand_y_range_for_minmax(
                    i, batch_min[i], batch_max[i])
        self._dirty = True

    def _ensure_buffer_capacity(self, needed_points):
        if needed_points <= self._max_points:
            return

        grow_step = max(1000, self._max_points // 2)
        new_max = max(needed_points, self._max_points + grow_step)
        new_max = int(math.ceil(new_max / 1000.0) * 1000)
        self.set_max_points(new_max)
        self.buffer_size_changed.emit(new_max)

    def _expand_y_range_for_minmax(self, ch, value_min, value_max):
        y_range = self._channel_y_ranges[ch]
        if y_range is None:
            return

        y_min, y_max = y_range
        if y_min <= value_min and value_max <= y_max:
            return

        span = max(y_max - y_min, 1.0)
        margin = max(span * 0.05, 1.0)
        new_min = min(y_min, value_min - margin)
        new_max = max(y_max, value_max + margin)
        self._channel_y_ranges[ch] = (new_min, new_max)

        vb = (self._main_view_box if ch == self._selected_channel
              else self._channel_view_boxes[ch])
        vb.setYRange(new_min, new_max, padding=0)

    def _expand_y_range_for_value(self, ch, value):
        y_range = self._channel_y_ranges[ch]
        if y_range is None:
            return

        y_min, y_max = y_range
        if y_min <= value <= y_max:
            return

        span = max(y_max - y_min, 1.0)
        margin = max(span * 0.05, 1.0)
        new_min = min(y_min, value - margin)
        new_max = max(y_max, value + margin)
        self._channel_y_ranges[ch] = (new_min, new_max)

        vb = (self._main_view_box if ch == self._selected_channel
              else self._channel_view_boxes[ch])
        vb.setYRange(new_min, new_max, padding=0)

    def _on_refresh_tick(self):
        """定时刷新: 仅在有新数据时重绘"""
        if not self._dirty:
            return
        self._dirty = False
        self._refresh_curves()

    def _refresh_curves(self):
        has_data = any(
            i < self._active_channel_count and
            self._channel_configs[i]['visible'] and
            len(self._buffers[i]) > 0
            for i in range(self._num_channels))
        if has_data and not self._paused and self._follow_latest_x:
            self._set_latest_x_range()

        for i in range(self._num_channels):
            if i >= self._active_channel_count:
                self._curves[i].setData([], [])
                continue
            if not self._channel_configs[i]['visible']:
                self._curves[i].setData([], [])
                continue
            buf = self._buffers[i]
            if len(buf) == 0:
                self._curves[i].setData([], [])
                continue
            x, y = self._curve_data_for_buffer(buf)
            self._curves[i].setData(x, y)

        if has_data and self._needs_initial_range:
            self._auto_range_all_channels()
            self._needs_initial_range = False
        if has_data:
            self._update_cursor_info()
            self._update_trigger_marker_position()

    def _set_initial_x_range(self):
        self._set_latest_x_range()

    def _set_latest_x_range(self):
        if self._time_counter <= 1:
            return
        x_max = (self._time_counter - 1) * self._sample_interval_ms
        x_min = max(0, x_max - (self._max_points - 1) * self._sample_interval_ms)
        self._auto_x_range_update = True
        try:
            self._main_view_box.setXRange(x_min, x_max, padding=0)
        finally:
            self._auto_x_range_update = False

    def _buffer_start_sample_index(self, buf):
        return max(0, self._time_counter - len(buf))

    def _curve_data_for_buffer(self, buf):
        start_index, end_index = self._visible_sample_bounds(buf)
        if end_index < start_index:
            empty = np.array([], dtype=np.float64)
            return empty, empty

        count = end_index - start_index + 1
        buf_start = self._buffer_start_sample_index(buf)
        offset = start_index - buf_start
        budget = self._curve_point_budget()

        if count <= budget:
            y = np.asarray(buf[offset:offset + count], dtype=np.float64)
            x = (np.arange(start_index, start_index + count,
                           dtype=np.float64) * self._sample_interval_ms)
            return x, y

        step = max(1, int(math.ceil(count / float(budget))))
        indices = list(range(start_index, end_index + 1, step))
        if indices[-1] != end_index:
            indices.append(end_index)

        y = np.asarray([buf[i - buf_start] for i in indices],
                       dtype=np.float64)
        x = (np.asarray(indices, dtype=np.float64) *
             self._sample_interval_ms)
        return x, y

    def _visible_sample_bounds(self, buf):
        buf_start = self._buffer_start_sample_index(buf)
        buf_end = buf_start + len(buf) - 1
        if self._needs_initial_range or self._main_view_box is None:
            return buf_start, buf_end

        x_min, x_max = self._main_view_box.viewRange()[0]
        if x_max <= x_min or self._sample_interval_ms <= 0:
            return buf_start, buf_end

        view_start = int(math.floor(x_min / self._sample_interval_ms)) - 1
        view_end = int(math.ceil(x_max / self._sample_interval_ms)) + 1
        return max(buf_start, view_start), min(buf_end, view_end)

    def _curve_point_budget(self):
        width = 1000.0
        if self._main_view_box is not None:
            rect = self._main_view_box.sceneBoundingRect()
            if rect.width() > 1:
                width = float(rect.width()) * self._effective_device_pixel_ratio()
        return max(2000, min(20000, int(width * 4)))

    def _on_x_range_changed(self, viewbox, range_info):
        """X轴范围变化时, 自动调整缓冲点数"""
        x_min, x_max = range_info
        visible_range_ms = x_max - x_min
        if visible_range_ms <= 0 or self._sample_interval_ms <= 0:
            return
        if not self._auto_x_range_update:
            samples_visible = visible_range_ms / self._sample_interval_ms + 1.0
            required = max(1, int(np.ceil(samples_visible - 1e-9)))
            if required > self._max_points:
                self.set_max_points(required)
                self.buffer_size_changed.emit(required)
            self._dirty = True
        self._apply_cursor_anchor_ratios(x_min, x_max)

    def _on_main_y_range_changed(self, viewbox, range_info):
        if 0 <= self._selected_channel < len(self._channel_y_ranges):
            self._channel_y_ranges[self._selected_channel] = tuple(range_info)
        self._update_cursor_info()
        self._update_trigger_marker_position()

    def _on_channel_y_range_changed(self, ch, range_info):
        if 0 <= ch < len(self._channel_y_ranges):
            self._channel_y_ranges[ch] = tuple(range_info)
        self._update_trigger_marker_position()

    @staticmethod
    def _data_offset_for_screen_pixels(view_box, dx_px=0.0, dy_up_px=0.0):
        if view_box is None:
            return 0.0, 0.0
        rect = view_box.sceneBoundingRect()
        width = max(float(rect.width()), 1.0)
        height = max(float(rect.height()), 1.0)
        x_range, y_range = view_box.viewRange()
        x_span = max(float(x_range[1] - x_range[0]), 1e-12)
        y_span = max(float(y_range[1] - y_range[0]), 1e-12)
        return float(dx_px) * x_span / width, float(dy_up_px) * y_span / height

    # ─── 通道配置 ─────────────────────────────────────────────

    def _update_cursor_anchor_ratios(self):
        if not self._cursor1 or not self._cursor2 or self._main_view_box is None:
            return

        x_min, x_max = self._main_view_box.viewRange()[0]
        span = x_max - x_min
        if span <= 0:
            return

        self._cursor_anchor_ratios = [
            (self._cursor1.value() - x_min) / span,
            (self._cursor2.value() - x_min) / span,
        ]

    def _apply_cursor_anchor_ratios(self, x_min=None, x_max=None):
        if not self._cursor1 or not self._cursor2:
            return

        if x_min is None or x_max is None:
            x_min, x_max = self._main_view_box.viewRange()[0]

        span = x_max - x_min
        if span <= 0:
            return

        self._cursor_syncing = True
        try:
            self._cursor1.setValue(
                x_min + self._cursor_anchor_ratios[0] * span)
            self._cursor2.setValue(
                x_min + self._cursor_anchor_ratios[1] * span)
        finally:
            self._cursor_syncing = False
        self._update_cursor_pair_delta()
        self._update_cursor_info()

    def set_channel_name(self, ch, name):
        if 0 <= ch < self._num_channels:
            self._channel_configs[ch]['name'] = name
            self._combo_y_ch.setItemText(ch, name)
            if ch == self._selected_channel:
                self._update_axis_display()

    def set_channel_color(self, ch, color):
        if 0 <= ch < self._num_channels:
            self._channel_configs[ch]['color'] = color
            self._curves[ch].setPen(pg.mkPen(color, width=1.5))
            if ch == self._selected_channel:
                self._update_axis_display()

    def set_channel_visible(self, ch, visible):
        if 0 <= ch < self._num_channels:
            self._channel_configs[ch]['visible'] = visible
            self._dirty = True
            self._update_cursor_info()

    def channel_count(self):
        return self._num_channels

    def set_channel_count(self, count):
        count = max(1, int(count))
        if count == self._num_channels:
            return

        old_count = self._num_channels
        if count > old_count:
            for ch_idx in range(old_count, count):
                self._channel_configs.append(
                    self._default_channel_config(ch_idx))
                self._buffers.append([])
                self._channel_y_ranges.append(None)
                self._combo_y_ch.addItem(f"CH{ch_idx + 1}", ch_idx)
                self._add_channel_plot(ch_idx)
            self._num_channels = count
            self._active_channel_count = count
        else:
            for ch_idx in range(old_count - 1, count - 1, -1):
                self._remove_channel_plot(ch_idx)
                del self._curves[ch_idx]
                del self._curve_view_boxes[ch_idx]
                del self._channel_view_boxes[ch_idx]
                del self._channel_configs[ch_idx]
                del self._buffers[ch_idx]
                del self._channel_y_ranges[ch_idx]
                self._combo_y_ch.removeItem(ch_idx)
            self._num_channels = count
            self._active_channel_count = min(self._active_channel_count, count)

            if self._selected_channel >= count:
                self._selected_channel = count - 1
                if self._main_view_box is not None:
                    self._move_curve_to_view_box(
                        self._selected_channel, self._main_view_box)
                    self._apply_channel_y_range(
                        self._selected_channel, self._main_view_box)
                self.selected_channel_changed.emit(self._selected_channel)

        self._sync_channel_view_boxes()
        self._update_axis_display()
        self._dirty = True
        self._update_cursor_info()

    def set_active_channel_count(self, count):
        count = max(0, min(int(count), self._num_channels))
        if count == self._active_channel_count:
            return

        old_count = self._active_channel_count
        self._active_channel_count = count

        if count < old_count:
            for ch in range(count, self._num_channels):
                self._buffers[ch].clear()
                self._channel_y_ranges[ch] = None
                self._curves[ch].setData([], [])
        self._dirty = True

    def set_channel_unit(self, ch, unit):
        if 0 <= ch < self._num_channels:
            self._channel_configs[ch]['unit'] = unit.strip()
            self._update_cursor_info()
            if ch == self._selected_channel:
                self._update_axis_display()

    def load_sample_data(self, frames, sample_interval_ms=None,
                         channel_configs=None, trigger_index=None,
                         trigger_channel=None, follow_latest=True):
        """Replace current waveform data with imported samples."""
        if channel_configs:
            self.set_channel_count(len(channel_configs))
            for ch, config in enumerate(channel_configs):
                self.set_channel_name(ch, config.get('name', f'CH{ch + 1}'))
                self.set_channel_color(
                    ch, config.get('color', default_channel_color(ch)))
                self.set_channel_visible(ch, config.get('visible', True))
                self.set_channel_unit(ch, config.get('unit', ''))
        elif frames:
            self.set_channel_count(max(len(values) for values in frames))

        if sample_interval_ms is not None:
            self.set_sample_interval_ms(sample_interval_ms)

        self.clear_data()
        self.set_follow_latest_enabled(True)
        if frames:
            self.add_data_points(frames)
            self._on_refresh_tick()
            self._set_full_data_x_range()
            self._auto_range_all_channels()
        self.set_follow_latest_enabled(follow_latest)
        if trigger_index is not None:
            self.set_trigger_marker_sample_index(trigger_index, trigger_channel)

    def _set_full_data_x_range(self):
        if self._main_view_box is None or self._time_counter <= 1:
            return
        x_min = 0.0
        x_max = (self._time_counter - 1) * self._sample_interval_ms
        self._auto_x_range_update = True
        try:
            self._main_view_box.setXRange(x_min, x_max, padding=0)
        finally:
            self._auto_x_range_update = False

    def get_channel_config(self, ch):
        if 0 <= ch < self._num_channels:
            return dict(self._channel_configs[ch])
        return None

    def get_sample_data(self):
        """Return buffered waveform data aligned by global sample index."""
        channel_count = min(self._active_channel_count, self._num_channels)
        channels = list(range(channel_count))
        populated = [
            ch for ch in channels
            if ch < len(self._buffers) and len(self._buffers[ch]) > 0
        ]
        if not populated:
            return {
                'sample_interval_ms': self._sample_interval_ms,
                'channel_configs': [
                    dict(self._channel_configs[ch]) for ch in channels],
                'rows': [],
            }

        starts = {
            ch: self._buffer_start_sample_index(self._buffers[ch])
            for ch in populated
        }
        first_sample = min(starts.values())
        last_sample = max(
            starts[ch] + len(self._buffers[ch]) - 1 for ch in populated)

        rows = []
        for sample_index in range(first_sample, last_sample + 1):
            values = []
            has_value = False
            for ch in channels:
                buf = self._buffers[ch]
                if not buf:
                    values.append(None)
                    continue
                start = self._buffer_start_sample_index(buf)
                offset = sample_index - start
                if 0 <= offset < len(buf):
                    values.append(buf[offset])
                    has_value = True
                else:
                    values.append(None)
            if has_value:
                rows.append((sample_index, values))

        return {
            'sample_interval_ms': self._sample_interval_ms,
            'channel_configs': [
                dict(self._channel_configs[ch]) for ch in channels],
            'rows': rows,
        }

    # ─── 游标卡尺 ─────────────────────────────────────────────

    def _on_cursor_toggled(self, checked):
        if checked:
            self._create_cursors()
        else:
            self._remove_cursors()

    def _create_cursors(self):
        vb = self._plot_item.getViewBox()
        x_range = vb.viewRange()[0]
        mid = (x_range[0] + x_range[1]) / 2
        span = (x_range[1] - x_range[0]) * 0.1

        self._cursor1 = pg.InfiniteLine(
            pos=mid - span, angle=90, movable=True,
            pen=pg.mkPen('#FF0000', width=2,
                         style=QtCore.Qt.DashLine),
            label='C1',
            labelOpts={'color': '#FF0000', 'movable': True}
        )
        self._cursor2 = pg.InfiniteLine(
            pos=mid + span, angle=90, movable=True,
            pen=pg.mkPen('#00FF00', width=2,
                         style=QtCore.Qt.DashLine),
            label='C2',
            labelOpts={'color': '#00FF00', 'movable': True}
        )

        self._plot_item.addItem(self._cursor1)
        self._plot_item.addItem(self._cursor2)
        self._create_cursor_value_labels()
        vb = self._plot_item.getViewBox()
        if hasattr(vb, 'set_zoom_exclusion_lines'):
            vb.set_zoom_exclusion_lines([self._cursor1, self._cursor2])
        self._update_cursor_anchor_ratios()
        self._update_cursor_pair_delta()
        self._cursor1.sigPositionChanged.connect(
            lambda *args: self._on_cursor_position_changed(0))
        self._cursor2.sigPositionChanged.connect(
            lambda *args: self._on_cursor_position_changed(1))
        self._show_cursor_dialog()
        self._update_cursor_info()

    def _remove_cursors(self):
        vb = self._plot_item.getViewBox()
        if hasattr(vb, 'set_zoom_exclusion_lines'):
            vb.set_zoom_exclusion_lines([])
        if self._cursor1:
            self._plot_item.removeItem(self._cursor1)
            self._cursor1 = None
        if self._cursor2:
            self._plot_item.removeItem(self._cursor2)
            self._cursor2 = None
        self._remove_cursor_value_labels()
        if self._cursor_dialog:
            self._cursor_dialog.hide()
        self._info_label.setText("")

    def _show_cursor_dialog(self):
        if self._cursor_dialog is None:
            self._cursor_dialog = CursorStatsDialog(self.window())
            self._cursor_dialog.lock_changed.connect(
                self._on_cursor_lock_changed)
            self._cursor_dialog.floating_values_changed.connect(
                self._on_cursor_floating_values_changed)
        self._cursor_dialog.set_lock_checked(self._cursor_lock_enabled)
        self._cursor_dialog.set_floating_values_checked(
            self._cursor_floating_values_enabled)
        self._cursor_dialog.show()
        self._cursor_dialog.raise_()
        self._cursor_dialog.activateWindow()

    def _create_cursor_value_labels(self):
        self._remove_cursor_value_labels()
        for idx, title in enumerate(('C1', 'C2')):
            label = pg.TextItem(
                html=f"<b>{title}</b>",
                anchor=(0, 0),
                border=pg.mkPen('#888888'),
                fill=pg.mkBrush(30, 30, 30, 220))
            label.setZValue(1000)
            self._plot_item.addItem(label)
            self._cursor_value_labels[idx] = label
        self._set_cursor_value_labels_visible(
            self._cursor_floating_values_enabled)

    def _remove_cursor_value_labels(self):
        for idx, label in enumerate(self._cursor_value_labels):
            if label is not None:
                try:
                    self._plot_item.removeItem(label)
                except (TypeError, RuntimeError):
                    pass
            self._cursor_value_labels[idx] = None

    def _set_cursor_value_labels_visible(self, visible):
        for label in self._cursor_value_labels:
            if label is not None:
                label.setVisible(bool(visible))

    def _on_cursor_floating_values_changed(self, checked):
        self._cursor_floating_values_enabled = bool(checked)
        self._set_cursor_value_labels_visible(checked)
        self._update_cursor_info()

    def _on_cursor_lock_changed(self, checked):
        self._cursor_lock_enabled = bool(checked)
        self._update_cursor_pair_delta()

    def _update_cursor_pair_delta(self):
        if self._cursor1 and self._cursor2:
            self._cursor_pair_delta = (
                self._cursor2.value() - self._cursor1.value())

    def _on_cursor_position_changed(self, moved_cursor=None):
        if self._cursor_syncing:
            self._update_cursor_info()
            return

        if self._cursor_lock_enabled and moved_cursor in (0, 1):
            self._cursor_syncing = True
            try:
                if moved_cursor == 0 and self._cursor1 and self._cursor2:
                    self._cursor2.setValue(
                        self._cursor1.value() + self._cursor_pair_delta)
                elif moved_cursor == 1 and self._cursor1 and self._cursor2:
                    self._cursor1.setValue(
                        self._cursor2.value() - self._cursor_pair_delta)
            finally:
                self._cursor_syncing = False

        if not self._cursor_lock_enabled:
            self._update_cursor_pair_delta()
        self._update_cursor_anchor_ratios()
        self._update_cursor_info()

    def _update_cursor_info(self):
        if not self._cursor1 or not self._cursor2:
            return

        t1 = self._cursor1.value()
        t2 = self._cursor2.value()
        dt = abs(t2 - t1)
        # 时间值转换为全局样本索引
        sample_idx1 = int(t1 / self._sample_interval_ms)
        sample_idx2 = int(t2 / self._sample_interval_ms)

        # 动态选择与X轴一致的单位
        max_t = max(abs(t1), abs(t2), 1)
        factor, unit = 1, 'ms'
        for f, u in TimeAxisItem._UNITS:
            if max_t >= f:
                factor, unit = f, u
                break

        def _fmt(ms):
            return TimeAxisItem.format_time_ms(ms)
        unit = ''

        info_parts = [
            f"<b>游标卡尺</b> | ΔT = {_fmt(dt)}{unit} | "
            f"C1@{_fmt(t1)}{unit}  C2@{_fmt(t2)}{unit}"
        ]

        dialog_rows = []

        for i in range(self._num_channels):
            if not self._channel_configs[i]['visible']:
                continue
            buf = self._buffers[i]
            if len(buf) == 0:
                continue

            buf_start = self._buffer_start_sample_index(buf)
            idx1 = sample_idx1 - buf_start
            idx2 = sample_idx2 - buf_start
            v1 = self._get_value_at(buf, idx1)
            v2 = self._get_value_at(buf, idx2)
            dv = ((v2 - v1) if v1 is not None and v2 is not None
                  else None)

            name = self._channel_configs[i]['name']
            color = self._channel_configs[i]['color']
            value_unit = self._channel_configs[i].get('unit', '')
            marker = " ◀" if i == self._selected_channel else ""
            suffix = value_unit if value_unit else ""
            interval_min, interval_max, interval_avg = (
                self._get_interval_stats(buf, idx1, idx2))
            dialog_rows.append({
                'name': name,
                'color': color,
                'c1': self._format_measurement(v1, suffix),
                'c2': self._format_measurement(v2, suffix),
                'min': self._format_measurement(interval_min, suffix),
                'max': self._format_measurement(interval_max, suffix),
                'avg': self._format_measurement(interval_avg, suffix),
            })

            if v1 is not None and v2 is not None:
                info_parts.append(
                    f"<font color='{color}'>{name}{marker}</font>: "
                    f"C1={v1:.1f}{suffix}  C2={v2:.1f}{suffix}  "
                    f"ΔY={dv:.1f}{suffix}"
                )
            else:
                info_parts.append(
                    f"<font color='{color}'>{name}{marker}</font>: --")

        self._info_label.setText(
            " &nbsp;|&nbsp; ".join(info_parts))
        if self._cursor_dialog is not None:
            self._cursor_dialog.set_measurements(
                f"{_fmt(t1)}{unit}",
                f"{_fmt(t2)}{unit}",
                f"{_fmt(dt)}{unit}",
                dialog_rows)
        self._update_cursor_value_labels(t1, t2, dialog_rows)

    def _update_cursor_value_labels(self, t1, t2, rows):
        if not self._cursor_floating_values_enabled:
            self._set_cursor_value_labels_visible(False)
            return
        if not self._cursor1 or not self._cursor2:
            return

        values = [
            ('C1', t1, 'c1', self._cursor_value_labels[0], (0, 0)),
            ('C2', t2, 'c2', self._cursor_value_labels[1], (1, 0)),
        ]
        y_pos = self._cursor_value_label_y()
        for title, t_value, key, label, anchor in values:
            if label is None:
                continue
            label.setHtml(self._cursor_value_label_html(title, t_value, rows, key))
            if hasattr(label, 'setAnchor'):
                label.setAnchor(anchor)
            label.setPos(t_value, y_pos)
            label.setVisible(True)

    def _cursor_value_label_y(self):
        if self._main_view_box is None:
            return 0.0
        y_min, y_max = self._main_view_box.viewRange()[1]
        _, top_margin = self._data_offset_for_screen_pixels(
            self._main_view_box, dy_up_px=14.0)
        return max(y_min, y_max - top_margin)

    def _cursor_value_label_html(self, title, t_value, rows, value_key):
        lines = [
            "<div style='font-family:Consolas; font-size:11pt; "
            "color:#E6E6E6;'>"
            f"<b>{title}</b> @ {TimeAxisItem.format_time_ms(t_value)}"
        ]
        for row in rows:
            lines.append(
                f"<br><span style='color:{row['color']}'>{row['name']}</span>: "
                f"{row[value_key]}")
        lines.append("</div>")
        return ''.join(lines)

    @staticmethod
    def _format_measurement(value, suffix=""):
        if value is None:
            return "--"
        try:
            return f"{float(value):.6g}{suffix}"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _get_interval_stats(buf, idx1, idx2):
        if len(buf) == 0:
            return None, None, None

        start = max(0, min(idx1, idx2))
        end = min(len(buf) - 1, max(idx1, idx2))
        if start > end:
            return None, None, None

        values = [float(buf[i]) for i in range(start, end + 1)]
        if not values:
            return None, None, None
        return min(values), max(values), sum(values) / len(values)

    @staticmethod
    def _get_value_at(buf, index):
        if index < 0 or index >= len(buf):
            return None
        return buf[index]

    # ─── 控制方法 ─────────────────────────────────────────────

    def _on_pause_toggled(self, paused):
        self._paused = paused
        self._btn_pause.setText("继续" if paused else "暂停")

    def clear_data(self):
        self.clear_trigger_marker()
        for buf in self._buffers:
            buf.clear()
        self._time_counter = 0
        self._channel_y_ranges = [None for _ in range(self._num_channels)]
        self._needs_initial_range = True
        self._last_vb_pixel_size = None
        for curve in self._curves:
            curve.setData([], [])
        self._update_cursor_info()

    def _auto_range_selected(self):
        """自动范围: Y轴只适配选中通道的数据"""
        self._auto_range_channel(self._selected_channel)

    def _auto_range_channel(self, ch):
        """按单个通道数据设置该通道自己的Y范围。"""
        buf = self._buffers[ch]
        if len(buf) == 0:
            return
        y = np.fromiter(buf, dtype=np.float64)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return
        y_min, y_max = float(np.min(y)), float(np.max(y))
        margin = max((y_max - y_min) * 0.05, 1.0)
        self._channel_y_ranges[ch] = (y_min - margin, y_max + margin)

        vb = (self._main_view_box if ch == self._selected_channel
              else self._channel_view_boxes[ch])
        vb.setYRange(y_min - margin, y_max + margin, padding=0)

    def _auto_range_all_channels(self):
        for ch in range(self._num_channels):
            self._auto_range_channel(ch)

    def _auto_range_all(self):
        """自动范围: 适配选中通道的Y范围"""
        self._auto_range_selected()

    def _generate_test_data(self):
        """生成测试波形数据, 用于验证波形显示是否正常"""
        import math
        n = 200
        self.set_active_channel_count(self._num_channels)
        for i in range(self._num_channels):
            self._buffers[i].clear()
        self._time_counter = 0
        for t in range(n):
            for ch in range(self._num_channels):
                scale = 125 if ch == 0 else (20000 if ch == 1 else 100)
                val = scale * math.sin(2 * math.pi * (t / n) * (ch + 1)
                                       + ch * 0.5) + ch * 50
                self._buffers[ch].append(val)
            self._time_counter += 1
        self._refresh_curves()
        self._auto_range_all_channels()

    def set_max_points(self, n):
        self._max_points = max(1, int(n))
        for buf in self._buffers:
            overflow = len(buf) - self._max_points
            if overflow > 0:
                del buf[:overflow]
        self._dirty = True

    def set_follow_latest_enabled(self, enabled):
        self._follow_latest_x = bool(enabled)
        self._dirty = True

    def _trigger_marker_channel_index(self):
        if self._trigger_marker_channel is None:
            return self._selected_channel
        if 0 <= self._trigger_marker_channel < self._num_channels:
            return self._trigger_marker_channel
        return self._selected_channel

    def _trigger_marker_target_view_box(self):
        channel = self._trigger_marker_channel_index()
        if 0 <= channel < len(self._curve_view_boxes):
            return self._curve_view_boxes[channel]
        return self._main_view_box

    def _move_trigger_marker_to_view_box(self):
        if self._trigger_marker_item is None:
            return
        target_view_box = self._trigger_marker_target_view_box()
        if target_view_box is None:
            return
        if self._trigger_marker_view_box is target_view_box:
            return

        items = (self._trigger_marker_item, self._trigger_marker_label)
        if self._trigger_marker_view_box is not None:
            for item in items:
                if item is None:
                    continue
                try:
                    self._trigger_marker_view_box.removeItem(item)
                except (TypeError, RuntimeError, ValueError):
                    pass
        else:
            for item in items:
                if item is None:
                    continue
                try:
                    self._plot_item.removeItem(item)
                except (TypeError, RuntimeError, ValueError):
                    pass

        for item in items:
            if item is None:
                continue
            try:
                target_view_box.addItem(item)
            except (TypeError, RuntimeError, ValueError):
                pass
        self._trigger_marker_view_box = target_view_box

    def _trigger_marker_y_value(self):
        channel = self._trigger_marker_channel_index()
        if not (0 <= channel < len(self._buffers)):
            return None
        sample_index = self._trigger_marker_sample_index
        if sample_index is None:
            return None
        buffer_start = self._buffer_start_sample_index(self._buffers[channel])
        offset = sample_index - buffer_start
        if not (0 <= offset < len(self._buffers[channel])):
            return None
        value = self._buffers[channel][offset]
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return value

    def set_trigger_marker_sample_index(self, sample_index, channel=None):
        self.clear_trigger_marker()
        try:
            sample_index = int(sample_index)
        except (TypeError, ValueError):
            return
        if sample_index < 0:
            return

        self._trigger_marker_sample_index = sample_index
        try:
            channel = int(channel) if channel is not None else None
        except (TypeError, ValueError):
            channel = None
        self._trigger_marker_channel = (
            channel if channel is not None and
            0 <= channel < self._num_channels else None)
        x_pos = sample_index * self._sample_interval_ms
        marker_path = QtGui.QPainterPath()
        marker_path.moveTo(0, 0)
        marker_path.lineTo(-7, -14)
        marker_path.lineTo(7, -14)
        marker_path.closeSubpath()
        self._trigger_marker_item = QtWidgets.QGraphicsPathItem(marker_path)
        self._trigger_marker_item.setFlag(
            QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self._trigger_marker_item.setPen(
            pg.mkPen('#FFD166', width=1.2))
        self._trigger_marker_item.setBrush(pg.mkBrush('#FFD166'))
        self._trigger_marker_item.setPos(x_pos, 0.0)
        self._trigger_marker_item.setZValue(1001)

        self._trigger_marker_label = pg.TextItem(
            html="<span style='color:#FFD166; font-weight:bold;'>触发点</span>",
            anchor=(0, 0.5),
            fill=pg.mkBrush(30, 30, 30, 180))
        self._trigger_marker_label.setZValue(1001)
        self._move_trigger_marker_to_view_box()
        self._update_trigger_marker_position()

    def clear_trigger_marker(self):
        for item_name in ('_trigger_marker_item', '_trigger_marker_label'):
            item = getattr(self, item_name)
            if item is not None:
                try:
                    if self._trigger_marker_view_box is not None:
                        self._trigger_marker_view_box.removeItem(item)
                    else:
                        self._plot_item.removeItem(item)
                except (TypeError, RuntimeError, ValueError):
                    pass
                setattr(self, item_name, None)
        self._trigger_marker_sample_index = None
        self._trigger_marker_channel = None
        self._trigger_marker_view_box = None

    def _update_trigger_marker_position(self):
        if self._trigger_marker_sample_index is None:
            return
        x_pos = self._trigger_marker_sample_index * self._sample_interval_ms
        self._move_trigger_marker_to_view_box()
        view_box = self._trigger_marker_view_box
        if view_box is None:
            return
        x_min, x_max = view_box.viewRange()[0]
        y_min, y_max = view_box.viewRange()[1]
        span = max(y_max - y_min, 1.0)
        y_pos = self._trigger_marker_y_value()
        if y_pos is None:
            y_pos = y_max - span * 0.04
        label_dx, _ = self._data_offset_for_screen_pixels(
            view_box, dx_px=14.0)
        _, label_dy = self._data_offset_for_screen_pixels(
            view_box, dy_up_px=8.0)
        edge_dx, _ = self._data_offset_for_screen_pixels(
            view_box, dx_px=90.0)
        label_x = x_pos + label_dx
        label_anchor = (0, 0.5)
        if label_x > x_max - edge_dx:
            label_x = x_pos - label_dx
            label_anchor = (1, 0.5)
        label_y = y_pos + label_dy
        if self._trigger_marker_item is not None:
            self._trigger_marker_item.setPos(x_pos, y_pos)
        if self._trigger_marker_label is not None:
            if hasattr(self._trigger_marker_label, 'setAnchor'):
                self._trigger_marker_label.setAnchor(label_anchor)
            self._trigger_marker_label.setPos(label_x, label_y)

    def set_sample_interval_ms(self, interval_ms):
        """设置每帧数据的时间间隔(ms), 用于将样本索引转换为时间"""
        self._sample_interval_ms = max(0.001, interval_ms)
        self._update_sample_rate_label()
        self._time_axis.picture = None
        self._time_axis.update()
        self._update_trigger_marker_position()

    def _update_sample_rate_label(self):
        if not hasattr(self, '_lbl_sample_rate'):
            return
        sample_rate_hz = 1000.0 / self._sample_interval_ms
        if sample_rate_hz >= 1_000_000:
            text = f"采样: {sample_rate_hz / 1_000_000:.3g} MHz"
        elif sample_rate_hz >= 1000:
            text = f"采样: {sample_rate_hz / 1000:.3g} kHz"
        else:
            text = f"采样: {sample_rate_hz:.3g} Hz"
        self._lbl_sample_rate.setText(text)


class QLabel_Y(QtWidgets.QLabel):
    def __init__(self, text):
        super().__init__(text)
        self.setStyleSheet("color: #AAAAAA; font-size: 13px;")
