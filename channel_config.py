"""通道配置面板组件"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QCheckBox, QPushButton,
    QColorDialog, QScrollArea, QGroupBox, QComboBox, QSizePolicy
)
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QColor

DATA_TYPE_OPTIONS = [
    ('int8', 'int8'),
    ('uint8', 'uint8'),
    ('int16', 'int16'),
    ('uint16', 'uint16'),
    ('int32', 'int32'),
    ('uint32', 'uint32'),
    ('int64', 'int64'),
    ('uint64', 'uint64'),
    ('float32', 'float32'),
    ('float64', 'float64'),
]

DEFAULT_DATA_TYPES = [
    'int16', 'int16', 'uint16', 'uint16', 'int32',
    'int32', 'int32', 'int16', 'int16', 'int16',
]

class ChannelConfigPanel(QWidget):
    """多通道配置面板"""

    # 通道配置变化信号 (channel_index, config_dict)
    channel_changed = pyqtSignal(int, dict)
    # 通道被选中信号 (点击通道行时发射)
    channel_selected = pyqtSignal(int)

    def __init__(self, num_channels=10, parent=None):
        super().__init__(parent)
        self._num_channels = num_channels
        self._channel_widgets = []
        self._selected = 0
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("通道配置 (点击选中Y轴)")
        title.setStyleSheet(
            "font-weight: bold; font-size: 15px; padding: 4px;")
        main_layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid = QGridLayout(container)
        grid.setSpacing(6)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 4)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(4, 2)
        grid.setColumnStretch(5, 2)
        grid.setColumnStretch(6, 2)

        # 表头
        headers = ['CH', 'Show', 'Name', 'Color', 'Value', 'Type', 'Unit']
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("font-weight: bold; color: #AAAAAA;")
            grid.addWidget(lbl, 0, col)

        default_colors = [
            '#FF4444', '#44FF44', '#4488FF', '#FFAA00',
            '#FF44FF', '#44FFFF', '#FFFF44', '#AAAAFF',
            '#FF8888', '#88FF88'
        ]

        for i in range(self._num_channels):
            row = i + 1
            widgets = {}

            # 通道按钮 (可点击选中)
            ch_btn = QPushButton(f"CH{i + 1}")
            ch_btn.setCheckable(True)
            ch_btn.setChecked(i == 0)
            ch_btn.setMinimumWidth(60)
            ch_btn.setStyleSheet(self._ch_btn_style(default_colors[i], True))
            ch_btn.clicked.connect(
                lambda checked, idx=i: self._on_channel_clicked(idx))
            grid.addWidget(ch_btn, row, 0)
            widgets['ch_btn'] = ch_btn

            # 显示/隐藏
            cb_show = QCheckBox()
            cb_show.setChecked(True)
            cb_show.stateChanged.connect(
                lambda state, idx=i: self._on_changed(idx))
            grid.addWidget(cb_show, row, 1)
            widgets['visible'] = cb_show

            # 名称
            name_edit = QLineEdit(f"CH{i + 1}")
            name_edit.setMinimumWidth(80)
            name_edit.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Fixed)
            name_edit.textChanged.connect(
                lambda text, idx=i: self._on_changed(idx))
            grid.addWidget(name_edit, row, 2)
            widgets['name'] = name_edit

            # 颜色
            color_btn = QPushButton()
            color_btn.setMinimumSize(60, 24)
            color_btn.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Fixed)
            color_btn.setStyleSheet(
                f"background-color: {default_colors[i]}; "
                f"border: 1px solid #666; border-radius: 3px;")
            color_btn.clicked.connect(
                lambda checked, idx=i: self._pick_color(idx))
            grid.addWidget(color_btn, row, 3)
            widgets['color_btn'] = color_btn
            widgets['color'] = default_colors[i]

            # 数据类型
            type_combo = QComboBox()
            default_type = DEFAULT_DATA_TYPES[i]
            for label, value in DATA_TYPE_OPTIONS:
                type_combo.addItem(label, value)
            type_combo.setCurrentIndex(
                max(0, type_combo.findData(default_type)))
            type_combo.setMinimumWidth(90)
            type_combo.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Fixed)
            type_combo.currentIndexChanged.connect(
                lambda idx_combo, idx=i: self._on_changed(idx))
            grid.addWidget(type_combo, row, 5)
            widgets['data_type'] = type_combo

            # 当前采集值
            value_label = QLabel("--")
            value_label.setMinimumWidth(70)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setStyleSheet(
                "font-family: Consolas, monospace; color: #DDDDDD;")
            grid.addWidget(value_label, row, 4)
            widgets['value'] = value_label

            # 通道单位
            unit_edit = QLineEdit("")
            unit_edit.setPlaceholderText("unit")
            unit_edit.setMinimumWidth(55)
            unit_edit.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Fixed)
            unit_edit.textChanged.connect(
                lambda text, idx=i: self._on_changed(idx))
            grid.addWidget(unit_edit, row, 6)
            widgets['unit'] = unit_edit

            self._channel_widgets.append(widgets)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # 全局按钮
        btn_layout = QHBoxLayout()
        btn_show_all = QPushButton("全部显示")
        btn_show_all.clicked.connect(self._show_all)
        btn_hide_all = QPushButton("全部隐藏")
        btn_hide_all.clicked.connect(self._hide_all)
        btn_reset = QPushButton("重置配置")
        btn_reset.clicked.connect(self._reset_config)
        btn_layout.addWidget(btn_show_all)
        btn_layout.addWidget(btn_hide_all)
        btn_layout.addWidget(btn_reset)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

    @staticmethod
    def _ch_btn_style(color, selected=False):
        """通道按钮样式"""
        if selected:
            return (
                f"QPushButton {{ background-color: {color}; "
                f"color: white; font-weight: bold; "
                f"border: 2px solid white; border-radius: 3px; "
                f"padding: 2px 8px; }}"
            )
        else:
            return (
                f"QPushButton {{ background-color: #3C3C3C; "
                f"color: {color}; font-weight: bold; "
                f"border: 1px solid {color}; border-radius: 3px; "
                f"padding: 2px 8px; }}"
                f"QPushButton:hover {{ background-color: #4A4A4A; }}"
            )

    def _on_channel_clicked(self, ch_idx):
        """点击通道按钮, 选中该通道"""
        self._selected = ch_idx
        # 更新所有按钮的样式
        for i, w in enumerate(self._channel_widgets):
            color = w['color']
            is_sel = (i == ch_idx)
            w['ch_btn'].setChecked(is_sel)
            w['ch_btn'].setStyleSheet(self._ch_btn_style(color, is_sel))
        # 发射选中信号
        self.channel_selected.emit(ch_idx)

    def _pick_color(self, ch_idx):
        widgets = self._channel_widgets[ch_idx]
        current = QColor(widgets['color'])
        color = QColorDialog.getColor(
            current, self, f"选择 CH{ch_idx + 1} 颜色")
        if color.isValid():
            hex_color = color.name()
            widgets['color'] = hex_color
            widgets['color_btn'].setStyleSheet(
                f"background-color: {hex_color}; "
                f"border: 1px solid #666; border-radius: 3px;")
            # 更新通道按钮颜色
            is_sel = (ch_idx == self._selected)
            widgets['ch_btn'].setStyleSheet(
                self._ch_btn_style(hex_color, is_sel))
            self._on_changed(ch_idx)

    def _on_changed(self, ch_idx):
        config = self.get_channel_config(ch_idx)
        self.channel_changed.emit(ch_idx, config)

    def get_channel_config(self, ch_idx):
        if 0 <= ch_idx < len(self._channel_widgets):
            w = self._channel_widgets[ch_idx]
            return {
                'name': w['name'].text(),
                'visible': w['visible'].isChecked(),
                'color': w['color'],
                'data_type': w['data_type'].currentData(),
                'unit': w['unit'].text().strip(),
            }
        return {}

    def set_channel_value(self, ch_idx, value):
        if 0 <= ch_idx < len(self._channel_widgets):
            self._channel_widgets[ch_idx]['value'].setText(
                self._format_value(value))

    def set_channel_values(self, values):
        if not values:
            for i in range(self._num_channels):
                self._channel_widgets[i]['value'].setText("--")
            return
        for i, value in enumerate(values[:self._num_channels]):
            self.set_channel_value(i, value)
        for i in range(len(values), self._num_channels):
            self._channel_widgets[i]['value'].setText("--")

    @staticmethod
    def _format_value(value):
        try:
            return f"{float(value):.6g}"
        except (TypeError, ValueError):
            return "--"

    def get_all_configs(self):
        return [self.get_channel_config(i)
                for i in range(self._num_channels)]

    def _show_all(self):
        for w in self._channel_widgets:
            w['visible'].setChecked(True)

    def _hide_all(self):
        for w in self._channel_widgets:
            w['visible'].setChecked(False)

    def _reset_config(self):
        default_colors = [
            '#FF4444', '#44FF44', '#4488FF', '#FFAA00',
            '#FF44FF', '#44FFFF', '#FFFF44', '#AAAAFF',
            '#FF8888', '#88FF88'
        ]
        for i, w in enumerate(self._channel_widgets):
            w['name'].setText(f"CH{i + 1}")
            w['visible'].setChecked(True)
            w['color'] = default_colors[i]
            w['color_btn'].setStyleSheet(
                f"background-color: {default_colors[i]}; "
                f"border: 1px solid #666; border-radius: 3px;")
            is_sel = (i == self._selected)
            w['ch_btn'].setStyleSheet(
                self._ch_btn_style(default_colors[i], is_sel))
            default_type = DEFAULT_DATA_TYPES[i]
            w['data_type'].setCurrentIndex(
                max(0, w['data_type'].findData(default_type)))
            w['unit'].clear()
            w['value'].setText("--")
            self._on_changed(i)
