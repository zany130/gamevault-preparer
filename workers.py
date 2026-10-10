"""Background file work. The worker never touches widgets."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self._report)
            self.succeeded.emit(True if result is None else result)
        except Exception as exc:
            self.failed.emit(str(exc), traceback.format_exc())

    def _report(self, percent: int, message: str = "") -> None:
        self.progress.emit(max(0, min(100, int(percent))), message)
