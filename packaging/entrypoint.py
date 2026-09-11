import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from delta_loot_assistant.app import run


def smoke_test() -> int:
    # Never touch the real database or make network requests during packaging checks.
    with tempfile.TemporaryDirectory(prefix="delta-loot-smoke-") as data_root:
        with (
            patch.dict(os.environ, {"LOCALAPPDATA": data_root}),
            patch(
                "socket.socket.connect", side_effect=RuntimeError("Network disabled in smoke test")
            ),
        ):
            return _isolated_smoke_test()


def _isolated_smoke_test() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from delta_loot_assistant.controller import AssistantController
    from delta_loot_assistant.models import SolveStatus
    from delta_loot_assistant.recognition import RapidOcrEngine
    from delta_loot_assistant.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = AssistantController()
    window = MainWindow(controller)
    controller.load_demo_state()
    result = controller.optimizer.solve(controller.state)
    RapidOcrEngine().recognize(np.full((100, 320, 3), 255, dtype=np.uint8))
    app.processEvents()
    window.hide()
    return 0 if result.status == SolveStatus.OPTIMAL else 1


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        try:
            status = smoke_test()
            report = {
                "exit_code": status,
                "checks": ["isolated_data", "ui", "optimizer", "local_ocr"],
            }
        except Exception as exc:
            status = 1
            report = {"exit_code": status, "error": f"{type(exc).__name__}: {exc}"}
        if "--smoke-report" in sys.argv:
            output = Path(sys.argv[sys.argv.index("--smoke-report") + 1])
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(status)
    raise SystemExit(run())
