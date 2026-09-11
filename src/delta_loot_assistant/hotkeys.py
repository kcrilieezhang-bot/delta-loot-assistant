from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F8 = 0x77
VK_F9 = 0x78


class GlobalHotkeyFilter(QObject, QAbstractNativeEventFilter):
    capture_requested = Signal()
    solve_requested = Signal()
    registration_failed = Signal(str)

    CAPTURE_ID = 0xD8
    SOLVE_ID = 0xD9

    def __init__(self) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self._registered: list[int] = []

    def register(self) -> bool:
        if sys.platform != "win32":
            self.registration_failed.emit("全局快捷键仅在Windows可用")
            return False
        user32 = ctypes.windll.user32
        pairs = [(self.CAPTURE_ID, VK_F8), (self.SOLVE_ID, VK_F9)]
        for identifier, virtual_key in pairs:
            if not user32.RegisterHotKey(None, identifier, MOD_NOREPEAT, virtual_key):
                self.unregister()
                self.registration_failed.emit(
                    "F8或F9已被其他程序占用，可使用侧边窗按钮操作"
                )
                return False
            self._registered.append(identifier)
        return True

    def unregister(self) -> None:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            for identifier in self._registered:
                user32.UnregisterHotKey(None, identifier)
        self._registered.clear()

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0
        if msg.message != WM_HOTKEY:
            return False, 0
        if msg.wParam == self.CAPTURE_ID:
            self.capture_requested.emit()
            return True, 0
        if msg.wParam == self.SOLVE_ID:
            self.solve_requested.emit()
            return True, 0
        return False, 0

