from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from ..recognition import LayoutProfile


class CalibrationPreview(QLabel):
    def __init__(self, pixmap: QPixmap, profile: LayoutProfile, parent=None):
        super().__init__(parent)
        self.source = pixmap
        self.profile = profile
        self.setMinimumSize(760, 420)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        scaled = self.source.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x_offset = (self.width() - scaled.width()) // 2
        y_offset = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x_offset, y_offset, scaled)
        scale_x = scaled.width() / self.profile.resolution[0]
        scale_y = scaled.height() / self.profile.resolution[1]
        colors = ["#5ec8ff", "#61d38e", "#efb74b", "#c58cff", "#ef7188"]
        for index, region in enumerate(self.profile.regions):
            x1, y1, x2, y2 = region.rect
            painter.setPen(QPen(colors[index % len(colors)], 2))
            painter.drawRect(
                round(x_offset + x1 * scale_x),
                round(y_offset + y1 * scale_y),
                round((x2 - x1) * scale_x),
                round((y2 - y1) * scale_y),
            )
            painter.drawText(
                round(x_offset + x1 * scale_x + 4),
                round(y_offset + y1 * scale_y + 16),
                region.name,
            )


class CalibrationDialog(QDialog):
    def __init__(self, image, profile: LayoutProfile, parent=None):
        super().__init__(parent)
        self.target_profile = profile
        self.profile = deepcopy(profile)
        self.setWindowTitle("1080p界面校准确认")
        self.resize(860, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "请确认彩色框大致覆盖口袋、背包、安全箱、武器栏和敌方物资区。"
                "V1使用固定1080p布局；若偏差明显，请在截图后使用人工录入。"
            )
        )
        rgb = image[:, :, ::-1].copy()
        qimage = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        self.preview = CalibrationPreview(QPixmap.fromImage(qimage), self.profile)
        layout.addWidget(self.preview)

        editor = QGroupBox("区域坐标（左、上、右、下）")
        grid = QGridLayout(editor)
        for column, title in enumerate(["区域", "左", "上", "右", "下"]):
            grid.addWidget(QLabel(title), 0, column)
        for row, region in enumerate(self.profile.regions, start=1):
            grid.addWidget(QLabel(region.name), row, 0)
            for coordinate_index, value in enumerate(region.rect):
                spin = QSpinBox()
                spin.setRange(
                    0,
                    self.profile.resolution[
                        0 if coordinate_index in {0, 2} else 1
                    ],
                )
                spin.setValue(value)
                spin.valueChanged.connect(
                    lambda changed,
                    region_index=row - 1,
                    index=coordinate_index: self._set_coordinate(
                        region_index,
                        index,
                        changed,
                    )
                )
                grid.addWidget(spin, row, coordinate_index + 1)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_coordinate(
        self,
        region_index: int,
        coordinate_index: int,
        value: int,
    ) -> None:
        region = self.profile.regions[region_index]
        rect = list(region.rect)
        rect[coordinate_index] = value
        region.rect = tuple(rect)
        self.preview.update()

    def accept(self) -> None:
        invalid = [
            region.name
            for region in self.profile.regions
            if region.rect[0] >= region.rect[2]
            or region.rect[1] >= region.rect[3]
        ]
        if invalid:
            QMessageBox.warning(
                self,
                "区域无效",
                "以下区域的右/下边界必须大于左/上边界："
                + "、".join(invalid),
            )
            return
        self.target_profile.resolution = self.profile.resolution
        self.target_profile.cell_size_range = self.profile.cell_size_range
        self.target_profile.regions = deepcopy(self.profile.regions)
        super().accept()
