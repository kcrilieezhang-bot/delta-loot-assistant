from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .controller import AssistantController
from .hotkeys import GlobalHotkeyFilter
from .ui.main_window import APP_STYLE, MainWindow


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("三角洲最高收益理包助手")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_STYLE)

    controller = AssistantController()
    window = MainWindow(controller)
    hotkeys = GlobalHotkeyFilter()
    app.installNativeEventFilter(hotkeys)
    hotkeys.capture_requested.connect(window.capture_from_hotkey)
    hotkeys.solve_requested.connect(window.solve_from_hotkey)
    hotkeys.registration_failed.connect(controller.error_message.emit)
    hotkeys.register()
    app.aboutToQuit.connect(hotkeys.unregister)

    window.show()
    window.raise_()
    exit_code = app.exec()
    hotkeys.unregister()
    return exit_code
