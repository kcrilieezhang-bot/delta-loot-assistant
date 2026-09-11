from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

import cv2
import mss
import numpy as np


@dataclass(slots=True)
class CaptureInfo:
    monitor_index: int
    width: int
    height: int
    left: int
    top: int


class ScreenCaptureService:
    def __init__(self, required_resolution: tuple[int, int] = (1920, 1080)):
        self.required_resolution = required_resolution
        self.last_info: CaptureInfo | None = None

    def capture_active_monitor(self) -> np.ndarray:
        with mss.mss() as session:
            monitor_index = self._active_monitor_index(session.monitors)
            monitor = session.monitors[monitor_index]
            width = int(monitor["width"])
            height = int(monitor["height"])
            required_width, required_height = self.required_resolution
            if (width, height) != (required_width, required_height):
                raise ValueError(
                    f"仅支持 {required_width}×{required_height}，"
                    f"当前活动显示器为 {width}×{height}"
                )
            raw = np.asarray(session.grab(monitor))
            image = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
            self.last_info = CaptureInfo(
                monitor_index=monitor_index,
                width=width,
                height=height,
                left=int(monitor["left"]),
                top=int(monitor["top"]),
            )
            return image

    @staticmethod
    def _active_monitor_index(monitors: list[dict[str, int]]) -> int:
        if sys.platform != "win32" or len(monitors) <= 2:
            return 1
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        rect = wintypes.RECT()
        if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            center_x = (rect.left + rect.right) // 2
            center_y = (rect.top + rect.bottom) // 2
            for index, monitor in enumerate(monitors[1:], start=1):
                if (
                    int(monitor["left"]) <= center_x < int(monitor["left"] + monitor["width"])
                    and int(monitor["top"]) <= center_y < int(monitor["top"] + monitor["height"])
                ):
                    return index
        return 1
