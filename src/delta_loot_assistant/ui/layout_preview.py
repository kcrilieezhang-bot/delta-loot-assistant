from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..catalog import Catalog
from ..models import PlanResult


class LayoutPreview(QWidget):
    COLORS = [
        QColor("#49a6ff"),
        QColor("#63d297"),
        QColor("#f0b84b"),
        QColor("#c488ff"),
        QColor("#ee7186"),
    ]

    def __init__(self, catalog: Catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.plan: PlanResult | None = None
        self.setMinimumHeight(230)

    def set_plan(self, plan: PlanResult | None) -> None:
        self.plan = plan
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#10151d"))
        if not self.plan or not self.plan.placements:
            painter.setPen(QColor("#8893a2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待计算最终布局")
            return

        container_ids = [
            container_id
            for container_id in self.catalog.containers
            if any(
                placement.container_id == container_id
                for placement in self.plan.placements
            )
        ]
        if not container_ids:
            painter.setPen(QColor("#8893a2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "物品均放入装备栏")
            return

        margin = 10
        available_width = self.width() - margin * (len(container_ids) + 1)
        panel_width = max(130, available_width // len(container_ids))
        for panel_index, container_id in enumerate(container_ids):
            container = self.catalog.containers[container_id]
            x0 = margin + panel_index * (panel_width + margin)
            y0 = 28
            cell = min(
                panel_width / max(1, container.width),
                (self.height() - y0 - 12) / max(1, container.height),
            )
            painter.setPen(QColor("#cbd5e1"))
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(x0, 18, container.name)
            for y in range(container.height):
                for x in range(container.width):
                    rect = QRectF(x0 + x * cell, y0 + y * cell, cell, cell)
                    painter.setPen(QPen(QColor("#3b4655"), 1))
                    painter.setBrush(
                        QColor("#252d39")
                        if (x, y) not in container.blocked_cells
                        else QColor("#090c11")
                    )
                    painter.drawRect(rect)
            for placement_index, placement in enumerate(self.plan.placements):
                if placement.container_id != container_id:
                    continue
                rect = QRectF(
                    x0 + placement.x * cell + 1,
                    y0 + placement.y * cell + 1,
                    max(1, placement.width * cell - 2),
                    max(1, placement.height * cell - 2),
                )
                color = self.COLORS[placement_index % len(self.COLORS)]
                painter.setPen(QPen(color.lighter(140), 1.5))
                painter.setBrush(color.darker(165))
                painter.drawRoundedRect(rect, 3, 3)
                painter.setPen(QColor("#ffffff"))
                painter.setFont(QFont("Microsoft YaHei", max(6, int(cell / 5))))
                painter.drawText(
                    rect.adjusted(3, 2, -3, -2),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    placement.instance_id[:5],
                )
