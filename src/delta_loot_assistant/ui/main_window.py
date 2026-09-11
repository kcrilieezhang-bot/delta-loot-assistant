from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..controller import AssistantController
from ..models import InventoryState, Origin, PlanResult, SolveStatus
from .calibration import CalibrationDialog
from .layout_preview import LayoutPreview


class ValueCard(QFrame):
    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("valueCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        title_label = QLabel(title)
        title_label.setObjectName("valueCardTitle")
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {color};"
        )
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: int | None, prefix: str = "") -> None:
        self.value_label.setText("—" if value is None else f"{prefix}{value:,}")


class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, value: int, *, display: str | None = None):
        super().__init__(display if display is not None else f"{value:,}")
        self.setData(Qt.ItemDataRole.UserRole, value)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        own_value = self.data(Qt.ItemDataRole.UserRole)
        other_value = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(own_value, int | float) and isinstance(other_value, int | float):
            return own_value < other_value
        return super().__lt__(other)


class MainWindow(QMainWindow):
    def __init__(self, controller: AssistantController):
        super().__init__()
        self.controller = controller
        self.settings = QSettings("DeltaLootAssistant", "DeltaLootAssistant")
        self._updating_table = False
        self.setWindowTitle("三角洲最高收益理包助手")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.resize(520, 900)
        self.setMinimumWidth(460)
        self._build_ui()
        self._connect()
        self._render_state(None)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(10)

        heading_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("最高收益理包")
        title.setObjectName("appTitle")
        subtitle = QLabel("F8追加截图 · F9计算 · 只读提示")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading_row.addLayout(title_box)
        heading_row.addStretch()
        self.capture_badge = QLabel("未扫描")
        self.capture_badge.setObjectName("badge")
        heading_row.addWidget(self.capture_badge)
        root.addLayout(heading_row)

        cards = QGridLayout()
        self.current_card = ValueCard("已识别参考值", "#9eb4cc")
        self.target_card = ValueCard("最优总价值", "#66d9a0")
        self.gain_card = ValueCard("净增益", "#f1bd59")
        cards.addWidget(self.current_card, 0, 0)
        cards.addWidget(self.target_card, 0, 1)
        cards.addWidget(self.gain_card, 0, 2)
        root.addLayout(cards)

        controls = QHBoxLayout()
        self.capture_button = QPushButton("F8 截图")
        self.capture_button.setObjectName("primaryButton")
        self.solve_button = QPushButton("F9 完成并计算")
        self.demo_button = QPushButton("演示数据")
        self.reset_button = QPushButton("清空")
        self.import_image_button = QPushButton("导入截图")
        controls.addWidget(self.capture_button)
        controls.addWidget(self.solve_button)
        controls.addWidget(self.demo_button)
        controls.addWidget(self.reset_button)
        root.addLayout(controls)
        root.addWidget(self.import_image_button)

        self.notice = QLabel("打开理包界面后按F8。检测到滚动区时可继续补拍。")
        self.notice.setWordWrap(True)
        self.notice.setObjectName("notice")
        root.addWidget(self.notice)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_items_tab(), "识别物品")
        self.tabs.addTab(self._build_plan_tab(), "最高收益方案")
        self.tabs.addTab(self._build_data_tab(), "价格与设置")
        root.addWidget(self.tabs, 1)

        self.setCentralWidget(central)
        status = QStatusBar()
        self.setStatusBar(status)
        status.showMessage("就绪")

        hide_action = QAction(self)
        hide_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        hide_action.triggered.connect(self.hide)
        self.addAction(hide_action)

    def _build_items_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        add_row = QHBoxLayout()
        self.add_item_combo = QComboBox()
        for definition in sorted(
            self.controller.catalog.items.values(), key=lambda item: item.name
        ):
            self.add_item_combo.addItem(definition.name, definition.id)
        self.add_origin_combo = QComboBox()
        self.add_origin_combo.addItem("敌方物资", Origin.LOOT.value)
        self.add_origin_combo.addItem("自身携带", Origin.CARRIED.value)
        self.add_item_button = QPushButton("手工添加")
        add_row.addWidget(self.add_item_combo, 1)
        add_row.addWidget(self.add_origin_combo)
        add_row.addWidget(self.add_item_button)
        layout.addLayout(add_row)

        self.lowest_value_label = QLabel("完成截图识别后显示优先替换物品")
        self.lowest_value_label.setWordWrap(True)
        self.lowest_value_label.setObjectName("lowestValue")
        layout.addWidget(self.lowest_value_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.item_table = QTableWidget(0, 12)
        self.item_table.setHorizontalHeaderLabels(
            [
                "锁定",
                "物品",
                "来源",
                "数量",
                "耐久%",
                "单价",
                "参考总价",
                "占格",
                "单格价值",
                "匹配分",
                "装配到",
                "删除",
            ]
        )
        self.item_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.item_table.verticalHeader().setVisible(False)
        self.item_table.setSortingEnabled(True)
        header = self.item_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.item_table)

        unresolved_panel = QWidget()
        unresolved_layout = QVBoxLayout(unresolved_panel)
        unresolved_layout.setContentsMargins(0, 4, 0, 0)
        unresolved_layout.addWidget(QLabel("待确认识别"))
        self.unresolved_list = QListWidget()
        unresolved_layout.addWidget(self.unresolved_list)
        self.crop_preview = QLabel("选择待核对项查看物品裁剪")
        self.crop_preview.setMinimumHeight(90)
        self.crop_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        unresolved_layout.addWidget(self.crop_preview)
        resolve_row = QHBoxLayout()
        self.resolve_combo = QComboBox()
        self.resolve_combo.setEditable(True)
        self.add_item_combo.setEditable(True)
        for definition in sorted(
            self.controller.catalog.items.values(), key=lambda item: item.name
        ):
            self.resolve_combo.addItem(definition.name, definition.id)
        self.resolve_button = QPushButton("将选中未知项确认成此物品")
        resolve_row.addWidget(self.resolve_combo, 1)
        resolve_row.addWidget(self.resolve_button)
        unresolved_layout.addLayout(resolve_row)
        splitter.addWidget(unresolved_panel)
        splitter.setSizes([480, 170])
        layout.addWidget(splitter)
        return widget

    def _build_plan_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.plan_headline = QLabel("等待计算")
        self.plan_headline.setObjectName("planHeadline")
        self.plan_meta = QLabel("")
        self.plan_meta.setObjectName("muted")
        layout.addWidget(self.plan_headline)
        layout.addWidget(self.plan_meta)
        self.layout_preview = LayoutPreview(self.controller.catalog)
        layout.addWidget(self.layout_preview)
        layout.addWidget(QLabel("操作步骤"))
        self.action_list = QListWidget()
        layout.addWidget(self.action_list, 1)
        layout.addWidget(QLabel("警告"))
        self.warning_list = QListWidget()
        self.warning_list.setMaximumHeight(140)
        layout.addWidget(self.warning_list)
        return widget

    def _build_data_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.price_pack_label = QLabel()
        self.price_pack_label.setWordWrap(True)
        form.addRow("当前价格包", self.price_pack_label)
        form.addRow("支持设置", QLabel("1920×1080 · 简体中文 · UI缩放100%"))
        form.addRow("网络访问", QLabel("仅手动日更时访问一次；24小时内禁止重复"))
        self.orzice_token_input = QLineEdit()
        self.orzice_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.orzice_token_input.setPlaceholderText(
            "已安全保存" if self.controller.has_orzice_token() else "输入专属 Token"
        )
        form.addRow("数据帝 Token", self.orzice_token_input)
        layout.addLayout(form)
        button_row = QHBoxLayout()
        self.import_price_button = QPushButton("导入价格包")
        self.rollback_price_button = QPushButton("回滚上一版")
        self.calibrate_button = QPushButton("查看校准框")
        button_row.addWidget(self.import_price_button)
        button_row.addWidget(self.rollback_price_button)
        button_row.addWidget(self.calibrate_button)
        layout.addLayout(button_row)
        online_row = QHBoxLayout()
        self.save_orzice_token_button = QPushButton("安全保存 Token")
        self.sync_orzice_button = QPushButton("手动日更一次")
        online_row.addWidget(self.save_orzice_token_button)
        online_row.addWidget(self.sync_orzice_button)
        layout.addLayout(online_row)
        self.contribute_checkbox = QCheckBox("贡献识别样本（只保存确认后的裁剪，不保存整张截图）")
        self.contribute_checkbox.setChecked(
            self.settings.value("contribute_samples", False, bool)
        )
        layout.addWidget(self.contribute_checkbox)
        policy = QLabel(
            "安全边界：本程序不会注入游戏、读取进程内存、抓包或控制鼠标键盘。"
            "数据帝接口只使用用户付费获得的 Token 手动生成本地快照，不会后台轮询。"
        )
        policy.setWordWrap(True)
        policy.setObjectName("notice")
        layout.addWidget(policy)
        layout.addStretch()
        return widget

    def _connect(self) -> None:
        self.capture_button.clicked.connect(self._capture_clicked)
        self.import_image_button.clicked.connect(self._import_image)
        self.solve_button.clicked.connect(self.controller.solve)
        self.demo_button.clicked.connect(self.controller.load_demo_state)
        self.reset_button.clicked.connect(self.controller.reset)
        self.add_item_button.clicked.connect(self._add_manual_item)
        self.resolve_button.clicked.connect(self._resolve_unknown)
        self.unresolved_list.currentItemChanged.connect(self._select_candidate)
        self.item_table.itemChanged.connect(self._item_changed)
        self.item_table.cellDoubleClicked.connect(self._cell_double_clicked)
        self.import_price_button.clicked.connect(self._import_price)
        self.rollback_price_button.clicked.connect(self.controller.rollback_price_pack)
        self.save_orzice_token_button.clicked.connect(self._save_orzice_token)
        self.sync_orzice_button.clicked.connect(self.controller.sync_orzice_snapshot)
        self.calibrate_button.clicked.connect(self._show_calibration)
        self.contribute_checkbox.toggled.connect(
            lambda checked: self.settings.setValue("contribute_samples", checked)
        )
        self.controller.state_changed.connect(self._render_state)
        self.controller.catalog_changed.connect(self._refresh_catalog_choices)
        self.controller.plan_ready.connect(self._render_plan)
        self.controller.capture_ready.connect(self._capture_received)
        self.controller.status_message.connect(self._show_status)
        self.controller.error_message.connect(self._show_error)

    def _capture_clicked(self) -> None:
        self.hide()
        QTimer.singleShot(150, self.controller.capture)

    def _import_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择1920×1080理包截图", "", "截图 (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return
        try:
            encoded = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("无法读取该图片")
            self.controller.add_image(image)
        except Exception as exc:
            self._show_error(str(exc))

    def _select_candidate(self, current, previous) -> None:
        if current is None or self.controller.state is None:
            return
        recognition_id = current.data(Qt.ItemDataRole.UserRole)
        entry = next(
            (row for row in self.controller.state.unresolved
             if row.recognition_id == recognition_id), None
        )
        if entry and entry.candidate_definition_ids:
            index = self.resolve_combo.findData(entry.candidate_definition_ids[0])
            if index >= 0:
                self.resolve_combo.setCurrentIndex(index)
        if entry:
            crop = entry.metadata.get("_crop")
            if isinstance(crop, np.ndarray) and crop.size:
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                preview = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                                 QImage.Format.Format_RGB888).copy()
                self.crop_preview.setPixmap(QPixmap.fromImage(preview).scaled(
                    240, 110, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))

    def capture_from_hotkey(self) -> None:
        self._capture_clicked()

    def solve_from_hotkey(self) -> None:
        self.show()
        self.raise_()
        self.controller.solve()

    def _capture_received(self, image) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        if not self.settings.value("calibration_confirmed", False, bool):
            dialog = CalibrationDialog(image, self.controller.recognizer.layout, self)
            if dialog.exec():
                self.controller.save_calibration()
                self.settings.setValue("calibration_confirmed", True)

    def _show_calibration(self) -> None:
        if self.controller.last_image is None:
            QMessageBox.information(self, "校准", "请先按F8截取一张1080p理包画面。")
            return
        dialog = CalibrationDialog(
            self.controller.last_image, self.controller.recognizer.layout, self
        )
        if dialog.exec():
            self.controller.save_calibration()

    def _render_state(self, state: InventoryState | None) -> None:
        self._updating_table = True
        self.item_table.setSortingEnabled(False)
        self.item_table.setRowCount(0)
        self.unresolved_list.clear()
        if state is None:
            self.capture_badge.setText("未扫描")
            self.current_card.set_value(None)
            self.lowest_value_label.setText("完成截图识别后显示优先替换物品")
            self._refresh_price_pack()
            self.item_table.setSortingEnabled(True)
            self._updating_table = False
            return
        self.capture_badge.setText(
            f"{len(state.capture_ids)}张 · {len(state.items)}件"
        )
        self.current_card.set_value(self.controller.valuation.state_current_value(state))
        item_index = {item.instance_id: item for item in state.items}
        lowest = self.controller.value_ranker.lowest_replaceable(state)
        if state.unresolved:
            self.lowest_value_label.setText(
                f"还有 {len(state.unresolved)} 件物品未确认，暂不能可靠判断最低价值物品。"
            )
        elif lowest is None:
            self.lowest_value_label.setText(
                "尚不能给出替换建议：请完成扫描并核对缺价物品、数量与整枪总价。"
            )
        else:
            self.lowest_value_label.setText(
                f"优先替换：{lowest.name} · 总价值 {lowest.total_value:,} · "
                f"占 {lowest.occupied_cells} 格 · 单格 {lowest.value_per_cell:,}"
            )
        for row, item in enumerate(state.items):
            self.item_table.insertRow(row)
            locked = QTableWidgetItem()
            locked.setFlags(
                (locked.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            locked.setCheckState(
                Qt.CheckState.Checked if item.locked else Qt.CheckState.Unchecked
            )
            locked.setData(Qt.ItemDataRole.UserRole, item.instance_id)
            self.item_table.setItem(row, 0, locked)

            definition = self.controller.catalog.items.get(item.definition_id)
            name = QTableWidgetItem(
                definition.name if definition else item.definition_id
            )
            name.setData(Qt.ItemDataRole.UserRole, item.instance_id)
            self.item_table.setItem(row, 1, name)
            self.item_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    "自身" if item.origin == Origin.CARRIED else "敌方"
                ),
            )
            self.item_table.setItem(row, 3, QTableWidgetItem(str(item.quantity)))
            self.item_table.setItem(
                row, 4, QTableWidgetItem(str(round(item.durability * 100)))
            )
            entry = self.controller.price_provider.unit_price(item.definition_id)
            price = item.manual_unit_price
            if price is None and entry:
                price = entry.unit_price
            metrics = self.controller.value_ranker.value_for_item(item, item_index)
            self.item_table.setItem(
                row,
                5,
                NumericTableWidgetItem(
                    price if price is not None else 2**63 - 1,
                    display=str(price) if price is not None else "未定价",
                ),
            )
            self.item_table.setItem(row, 6, NumericTableWidgetItem(
                metrics.total_value if metrics.price_available else 2**63 - 1,
                display=f"{metrics.total_value:,}" if metrics.price_available else "未定价",
            ))
            self.item_table.setItem(row, 7, NumericTableWidgetItem(metrics.occupied_cells))
            value_per_cell = NumericTableWidgetItem(
                metrics.value_per_cell if metrics.price_available else 2**63 - 1,
                display=(
                    f"{metrics.value_per_cell:,}"
                    if metrics.price_available
                    else "未定价"
                ),
            )
            if (
                lowest is not None
                and item.instance_id == lowest.instance_id
                and not state.unresolved
            ):
                value_per_cell.setForeground(QColor("#f06f82"))
            self.item_table.setItem(row, 8, value_per_cell)
            confidence = QTableWidgetItem(f"{item.confidence:.0%}")
            if item.confidence < 0.65:
                confidence.setForeground(QColor("#f06f82"))
            elif item.confidence < 0.9:
                confidence.setForeground(QColor("#efbd58"))
            self.item_table.setItem(row, 9, confidence)
            parent = item_index.get(item.installed_on or "")
            parent_text = ""
            if parent:
                parent_definition = self.controller.catalog.items[parent.definition_id]
                parent_text = f"{parent_definition.name}#{parent.instance_id[:6]}"
            installation = QTableWidgetItem(parent_text)
            installation.setData(Qt.ItemDataRole.UserRole, item.instance_id)
            self.item_table.setItem(row, 10, installation)
            delete = QTableWidgetItem("双击删除")
            delete.setData(Qt.ItemDataRole.UserRole, item.instance_id)
            delete.setFlags(delete.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.item_table.setItem(row, 11, delete)

        for unresolved in state.unresolved:
            candidates = " / ".join(
                self.controller.catalog.items[key].name
                for key in unresolved.candidate_definition_ids
                if key in self.controller.catalog.items
            )
            item = QListWidgetItem(
                f"{unresolved.metadata.get('ocr_text', '')} → {candidates}\n"
                f"{unresolved.reason} · 匹配分 {unresolved.confidence:.0%}"
            )
            item.setData(Qt.ItemDataRole.UserRole, unresolved.recognition_id)
            self.unresolved_list.addItem(item)
        self.notice.setText(
            f"已识别 {len(state.items)} 件，待确认 {len(state.unresolved)} 件。"
            + (" 请继续补拍滚动区域。" if not state.complete_scan else "")
        )
        self._refresh_price_pack()
        self.item_table.setSortingEnabled(True)
        self.item_table.sortItems(8, Qt.SortOrder.AscendingOrder)
        self._updating_table = False

    def _render_plan(self, plan: PlanResult) -> None:
        self.current_card.set_value(plan.current_value)
        self.target_card.set_value(plan.target_value)
        self.gain_card.set_value(plan.net_gain, "+" if plan.net_gain >= 0 else "")
        self.plan_headline.setText(plan.headline)
        color = {
            SolveStatus.OPTIMAL: "#63d297",
            SolveStatus.FEASIBLE: "#efbd58",
            SolveStatus.BLOCKED: "#efbd58",
            SolveStatus.INFEASIBLE: "#ef7188",
            SolveStatus.ERROR: "#ef7188",
        }.get(plan.status, "#cbd5e1")
        self.plan_headline.setStyleSheet(
            f"font-size: 22px; font-weight: 700; color: {color};"
        )
        self.plan_meta.setText(
            f"安全箱保护价值 {plan.protected_value:,} · "
            f"{len(plan.selected_instance_ids)}件 · {plan.solve_seconds:.3f}秒"
        )
        self.action_list.clear()
        for step in plan.actions:
            self.action_list.addItem(
                f"{step.index}. {step.instruction}"
                + (f"  ({step.value_delta:+,})" if step.value_delta else "")
            )
        self.warning_list.clear()
        for warning in plan.warnings:
            self.warning_list.addItem(warning)
        self.layout_preview.set_plan(plan)
        self.tabs.setCurrentIndex(1)

    def _item_changed(self, table_item: QTableWidgetItem) -> None:
        if self._updating_table:
            return
        row = table_item.row()
        id_item = self.item_table.item(row, 1)
        if id_item is None:
            return
        instance_id = id_item.data(Qt.ItemDataRole.UserRole)
        if table_item.column() == 0:
            self.controller.update_item(
                instance_id,
                locked=table_item.checkState() == Qt.CheckState.Checked,
            )
            if (
                table_item.checkState() == Qt.CheckState.Checked
                and self.contribute_checkbox.isChecked()
            ):
                self.controller.save_confirmed_sample(instance_id)
        elif table_item.column() == 1:
            definition = self.controller.catalog.find_item(table_item.text())
            if definition:
                self.controller.update_item(
                    instance_id, definition_id=definition.id
                )
                if self.contribute_checkbox.isChecked():
                    self.controller.save_confirmed_sample(instance_id)
            else:
                self._show_error("物品名称不在本地物品库中")
                self._render_state(self.controller.state)
        elif table_item.column() == 3:
            try:
                self.controller.update_item(
                    instance_id, quantity=int(table_item.text())
                )
            except ValueError:
                self._show_error("数量必须是整数")
        elif table_item.column() == 4:
            try:
                self.controller.update_item(
                    instance_id, durability=float(table_item.text()) / 100
                )
            except ValueError:
                self._show_error("耐久必须是0到100的数字")
        elif table_item.column() == 5:
            try:
                self.controller.update_item(
                    instance_id, manual_unit_price=int(table_item.text())
                )
            except ValueError:
                self._show_error("单价必须是整数")
        elif table_item.column() == 10:
            try:
                target = table_item.text().split("#")[-1]
                self.controller.set_item_installation(instance_id, target)
            except ValueError as exc:
                self._show_error(str(exc))
                self._render_state(self.controller.state)

    def _cell_double_clicked(self, row: int, column: int) -> None:
        if column != 11:
            return
        id_item = self.item_table.item(row, 1)
        if id_item is None:
            return
        self.controller.remove_item(id_item.data(Qt.ItemDataRole.UserRole))

    def _add_manual_item(self) -> None:
        definition_id = self.add_item_combo.currentData()
        origin = Origin(self.add_origin_combo.currentData())
        self.controller.add_manual_item(definition_id, origin=origin)

    def _resolve_unknown(self) -> None:
        selected = self.unresolved_list.currentItem()
        if selected is None:
            self._show_error("请先选择一个待确认项")
            return
        recognition_id = selected.data(Qt.ItemDataRole.UserRole)
        definition_id = self.resolve_combo.currentData()
        item = self.controller.resolve_unknown(recognition_id, definition_id)
        if self.contribute_checkbox.isChecked():
            self.controller.save_confirmed_sample(item.instance_id)

    def _import_price(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入价格包",
            str(Path.home()),
            "价格包 (*.json *.csv)",
        )
        if path:
            self.controller.import_price_pack(path)
            self._refresh_price_pack()

    def _save_orzice_token(self) -> None:
        token = self.orzice_token_input.text().strip()
        if not token:
            self._show_error("请输入 Token")
            return
        self.controller.save_orzice_token(token)
        self.orzice_token_input.clear()
        self.orzice_token_input.setPlaceholderText("已安全保存")

    def _refresh_catalog_choices(self) -> None:
        definitions = sorted(
            self.controller.catalog.items.values(), key=lambda item: item.name
        )
        for combo in (self.add_item_combo, self.resolve_combo):
            current_id = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for definition in definitions:
                combo.addItem(definition.name, definition.id)
            if current_id is not None:
                index = combo.findData(current_id)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _refresh_price_pack(self) -> None:
        pack = self.controller.price_provider.current_pack()
        if not pack:
            self.price_pack_label.setText("未导入")
            return
        warning = self.controller.valuation.stale_warning(
            self.controller.catalog.season
        )
        text = (
            f"{pack.season} / {pack.region} / {pack.generated_at.isoformat()}"
            f"\n来源：{pack.source}"
        )
        if warning:
            text += f"\n⚠ {warning}"
        self.price_pack_label.setText(text)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.notice.setText(message)

    def _show_error(self, message: str) -> None:
        self.show()
        self.raise_()
        self.statusBar().showMessage(message, 10000)
        QMessageBox.warning(self, "理包助手", message)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


APP_STYLE = """
QWidget {
    background: #111720;
    color: #d9e2ec;
    font-family: "Microsoft YaHei";
    font-size: 12px;
}
QMainWindow { background: #111720; }
#appTitle { font-size: 24px; font-weight: 700; color: #f4f7fb; }
#muted, #valueCardTitle { color: #8897a8; }
#badge {
    background: #243244; color: #8fc6ff; border-radius: 10px;
    padding: 4px 9px; font-weight: 600;
}
#valueCard {
    background: #19222d; border: 1px solid #2b3848; border-radius: 8px;
}
#notice {
    background: #182433; border-left: 3px solid #4ca8ff;
    border-radius: 4px; padding: 8px; color: #b9c9db;
}
#planHeadline { font-size: 22px; font-weight: 700; }
#lowestValue {
    background: #2a1f24; border: 1px solid #6d3946; border-radius: 5px;
    color: #ff9caf; padding: 8px; font-weight: 700;
}
QPushButton {
    background: #253346; border: 1px solid #34475f; border-radius: 5px;
    padding: 7px 10px;
}
QPushButton:hover { background: #2e4057; }
#primaryButton {
    background: #1f6fb2; border-color: #338bd0; color: white; font-weight: 700;
}
QTabWidget::pane { border: 1px solid #2a3747; border-radius: 5px; }
QTabBar::tab { background: #18212c; padding: 8px 12px; }
QTabBar::tab:selected { background: #26364a; color: #7dc4ff; }
QTableWidget, QListWidget, QComboBox {
    background: #0d1219; border: 1px solid #2a3747; gridline-color: #273444;
}
QHeaderView::section {
    background: #202c3a; color: #aebdd0; border: 0; padding: 6px;
}
QLineEdit, QSpinBox {
    background: #0d1219; border: 1px solid #34475f; padding: 5px;
}
QStatusBar { color: #8ea2b8; }
"""
