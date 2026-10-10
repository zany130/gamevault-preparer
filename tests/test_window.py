import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from window import MainWindow, file_manager_arguments


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_opens_and_previews_the_archive_name(self):
        window = MainWindow()
        window.title_edit.setText("Halo")
        window.version_edit.setText("1.2")
        window.year_edit.setText("2004")
        self.assertEqual(window.filename_label.text(), "Halo (v1.2) (W_P) (2004).zip")
        self.assertIn("not created yet", window.workspace_label.text())
        window.year_edit.setText("nope")
        self.assertIn("four digits", window.filename_label.text())
        window.close()

    def test_continue_unlocks_export_without_checking_edits(self):
        window = MainWindow()
        window.game_dir = Path("/tmp/My Game")
        window.prefix_dir = Path("/tmp/wine prefix")
        window.wine_idle = True
        window.title_edit.setText("Halo")
        window.refresh_buttons()
        self.assertTrue(window.open_game_btn.isEnabled())
        self.assertTrue(window.open_prefix_btn.isEnabled())
        self.assertTrue(window.continue_btn.isEnabled())
        self.assertFalse(window.export_btn.isEnabled())
        self.assertEqual(file_manager_arguments(window.game_dir), ["/tmp/My Game"])
        self.assertEqual(file_manager_arguments(window.prefix_dir), ["/tmp/wine prefix"])
        window.continue_to_export()
        self.assertTrue(window.export_btn.isEnabled())
        self.assertFalse(window.game_dir.exists())
        window.close()


if __name__ == "__main__":
    unittest.main()
