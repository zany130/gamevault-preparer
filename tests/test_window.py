import os
import tempfile
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
        self.assertTrue(window.run_exe_btn.isEnabled())
        self.assertFalse(window.exe_done_btn.isEnabled())
        self.assertTrue(window.continue_btn.isEnabled())
        self.assertFalse(window.export_btn.isEnabled())
        self.assertFalse(window.verify_btn.isEnabled())
        self.assertFalse(window.winetricks_btn.isEnabled())
        self.assertFalse(window.verify_done_btn.isEnabled())
        window.exported_zip = Path("/tmp/Halo (W_P).zip")
        window.workspace = Path("/tmp/gvprep-workspace")
        window.refresh_buttons()
        self.assertTrue(window.verify_btn.isEnabled())
        self.assertFalse(window.winetricks_btn.isEnabled())
        window.verify_prefix = Path("/tmp/gvprep-verify-prefix")
        window.refresh_buttons()
        self.assertTrue(window.winetricks_btn.isEnabled())
        self.assertFalse(window.verify_done_btn.isEnabled())
        window.verify_prefix = None
        window.workspace = None
        self.assertEqual(file_manager_arguments(window.game_dir), ["/tmp/My Game"])
        self.assertEqual(file_manager_arguments(window.prefix_dir), ["/tmp/wine prefix"])
        window.continue_to_export()
        self.assertTrue(window.export_btn.isEnabled())
        self.assertFalse(window.game_dir.exists())
        window.close()

    def test_game_folder_name_fills_title_until_edited(self):
        window = MainWindow()
        window.game_dir = Path("/tmp/drive_c/Games/My Game")
        window._fill_title_from_game_folder()
        self.assertEqual(window.title_edit.text(), "My Game")
        self.assertEqual(window.filename_label.text(), "My Game (W_P).zip")
        window.game_dir = Path("/tmp/drive_c/Games/Other")
        window._fill_title_from_game_folder()
        self.assertEqual(window.title_edit.text(), "Other")
        window.title_edit.setText("Renamed")
        window.title_edit.textEdited.emit("Renamed")
        window.game_dir = Path("/tmp/drive_c/Games/Third")
        window._fill_title_from_game_folder()
        self.assertEqual(window.title_edit.text(), "Renamed")
        window.close()

    def test_source_picker_starts_in_the_chosen_folder(self):
        window = MainWindow()
        original = window.source_folder_edit.text()
        window.source_folder_edit.setText("")
        self.assertEqual(window._source_picker_start(), str(Path.home()))
        window.source_folder_edit.setText("/tmp/does-not-exist-gvprep")
        self.assertEqual(window._source_picker_start(), str(Path.home()))
        with tempfile.TemporaryDirectory() as raw:
            window.source_folder_edit.setText(raw)
            self.assertEqual(Path(window._source_picker_start()), Path(raw))
        window.source_folder_edit.blockSignals(True)
        window.source_folder_edit.setText(original)
        window.source_folder_edit.blockSignals(False)
        window.close()


if __name__ == "__main__":
    unittest.main()
