"""Launch the GameVault Archive Preparer window."""

import os
import sys


def _check_libarchive() -> None:
    """Read a small ZIP through the libarchive build the AppImage ships."""
    import tempfile
    import zipfile
    from pathlib import Path

    from libarchive.ffi import libarchive_path
    import libarchive

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "sample.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("hello.txt", "hello")
        with libarchive.file_reader(os.fspath(archive)) as reader:
            names = [entry.pathname for entry in reader]
    if names != ["hello.txt"]:
        raise SystemExit(f"libarchive read unexpected entries from {libarchive_path}: {names}")
    sys.stderr.write(f"libarchive ok: {libarchive_path}\n")


def main() -> None:
    if os.environ.get("GVPREP_CHECK_LIBARCHIVE") == "1":
        _check_libarchive()
        return

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write("PySide6 is not installed. Install it with: pip install PySide6\n")
        raise SystemExit(1) from None
    from window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("GameVault Archive Preparer")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
