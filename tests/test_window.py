import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from notes import list_prefix_files, parse_registry, read_registry_export, sidecar_paths
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

    def test_note_capture_leaves_game_selection_and_export_available(self):
        window = MainWindow()
        window.install_ready = True
        window.install_launched = True
        window.game_dir = Path("/tmp/game")
        window.prefix_dir = Path("/tmp/prefix")
        window.manual_continued = True
        window.wine_idle = True
        window._note_capture = True
        window.title_edit.setText("Halo")
        window.refresh_buttons()
        self.assertTrue(window.game_btn.isEnabled())
        self.assertTrue(window.export_btn.isEnabled())
        self.assertTrue(window.continue_btn.isEnabled())
        self.assertFalse(window.run_exe_btn.isEnabled())
        self.assertFalse(window.apply_btn.isEnabled())
        self.assertTrue(window.stop_btn.isEnabled())
        window._note_capture = False
        window.prefix_dir = None
        window.close()

    def test_ready_notes_are_written_beside_the_zip_without_overwrite(self):
        window = MainWindow()
        before = """Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\Software\\Existing]
"Keep"="yes"

"""
        after = """Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\Software\\Existing]
"Keep"="yes"

[HKEY_LOCAL_MACHINE\\Software\\NewGame]
"Added"="1"

"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prefix = root / "prefix"
            game = prefix / "drive_c" / "Games" / "Example"
            game.mkdir(parents=True)
            (game / "game.exe").write_bytes(b"MZ")
            redist = prefix / "drive_c" / "redist"
            redist.mkdir()
            (redist / "vcredist_x86.exe").write_bytes(b"MZ")
            window.prefix_dir = prefix
            window.game_dir = game.resolve()
            window._files_before = set()
            window._files_after = list_prefix_files(prefix)
            window._reg_before = parse_registry(before)
            window._reg_after = parse_registry(after)
            archive = root / "Example (W_P).zip"
            archive.write_bytes(b"PK")
            summary = window._write_install_notes(archive)
            reg_path, setup_path = sidecar_paths(archive)
            setup_text = setup_path.read_text(encoding="utf-8")
            setup_bytes = setup_path.read_bytes()
            reg_bytes = reg_path.read_bytes()
            reg_text = read_registry_export(reg_path)
            self.assertIn("vcredist_x86.exe", setup_text)
            self.assertNotIn("game.exe", setup_text)
            self.assertIn("NewGame", reg_text)
            self.assertNotIn("Keep", reg_text)
            self.assertIn("Verify ZIP does not use them", summary)
            again = window._write_install_notes(archive)
            self.assertIn("unchanged", again)
            self.assertEqual(reg_path.read_bytes(), reg_bytes)
            self.assertEqual(setup_path.read_bytes(), setup_bytes)
            window._reg_before = None
            window._reg_after = None
            other = root / "Other (W_P).zip"
            other.write_bytes(b"PK")
            skipped = window._write_install_notes(other)
            other_reg, other_setup = sidecar_paths(other)
            self.assertFalse(other_reg.exists())
            self.assertTrue(other_setup.is_file())
            self.assertIn("registry note was not ready", skipped)
            self.assertIn("vcredist_x86.exe", other_setup.read_text(encoding="utf-8"))
        window.prefix_dir = None
        window.game_dir = None
        window.workspace = None
        window.close()


if __name__ == "__main__":
    unittest.main()
