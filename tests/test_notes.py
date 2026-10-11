import os
import tempfile
import unittest
from pathlib import Path

from notes import (
    encode_registry,
    list_prefix_files,
    parse_registry,
    read_registry_export,
    registry_diff_text,
    setup_note,
    setup_program_names,
    sidecar_paths,
    write_if_absent,
)


BEFORE = """Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\Software\\Existing]
"Keep"="yes"
"Path"="C:\\\\old"

[HKEY_LOCAL_MACHINE\\Software\\SkipMe]
"Nope"=dword:00000001

"""

AFTER = """Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\Software\\Existing]
"keep"="yes"
"Path"="C:\\\\new"
"Added"="1"

[HKEY_LOCAL_MACHINE\\Software\\NewGame]
"InstallPath"="C:\\\\Games\\\\Example"
"Blob"=hex:01,02,\\
  03,04

[HKEY_CURRENT_USER\\Software\\Wine]
"Version"="10"

[HKEY_LOCAL_MACHINE\\System\\Setup]
"Added"="nope"

"""


class RegistryDiffTests(unittest.TestCase):
    def test_added_software_value_is_kept_and_baseline_value_is_omitted(self):
        text = registry_diff_text(parse_registry(BEFORE), parse_registry(AFTER))
        self.assertTrue(text.startswith("Windows Registry Editor Version 5.00"))
        self.assertIn("[HKEY_LOCAL_MACHINE\\Software\\NewGame]", text)
        self.assertIn('"Added"="1"', text)
        self.assertIn('"Blob"=hex:01,02,\\\n  03,04', text)
        self.assertIn("[HKEY_CURRENT_USER\\Software\\Wine]", text)
        self.assertNotIn('"Keep"=', text)
        self.assertNotIn('"keep"=', text)
        self.assertNotIn('"Path"=', text)
        self.assertNotIn("SkipMe", text)
        self.assertNotIn("System", text)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "note.reg"
            path.write_bytes(encode_registry(text))
            self.assertTrue(path.read_bytes().startswith(b"\xff\xfe"))
            decoded = read_registry_export(path)
        self.assertIn("NewGame", decoded)

    def test_unchanged_registry_is_still_a_reg_file(self):
        snapshot = parse_registry(BEFORE)
        text = registry_diff_text(snapshot, snapshot)
        self.assertTrue(text.startswith("Windows Registry Editor Version 5.00"))
        self.assertIn("; No Software values were added.", text)
        self.assertNotIn("[HKEY_", text)


class ProgramListTests(unittest.TestCase):
    def test_new_setup_program_outside_the_game_folder_is_listed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prefix = root / "prefix"
            windows = prefix / "drive_c" / "windows"
            windows.mkdir(parents=True)
            (windows / "notepad.exe").write_bytes(b"MZ")
            before = list_prefix_files(prefix)
            redist = prefix / "drive_c" / "redist"
            redist.mkdir()
            (redist / "vcredist_x86.exe").write_bytes(b"MZ")
            (redist / "setup.msi").write_bytes(b"MZ")
            (redist / "readme.txt").write_bytes(b"hi")
            game = prefix / "drive_c" / "Games" / "Example"
            game.mkdir(parents=True)
            (game / "game.exe").write_bytes(b"MZ")
            (game / "helper.bat").write_bytes(b"echo hi")
            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.exe").write_bytes(b"MZ")
            (prefix / "linked").symlink_to(outside, target_is_directory=True)
            (redist / "link.exe").symlink_to(redist / "vcredist_x86.exe")
            after = list_prefix_files(prefix)
            names = setup_program_names(before, after, "drive_c/Games/Example")
            text = setup_note(names)
        self.assertIn("vcredist_x86.exe", names)
        self.assertIn("setup.msi", names)
        self.assertNotIn("game.exe", names)
        self.assertNotIn("helper.bat", names)
        self.assertNotIn("notepad.exe", names)
        self.assertNotIn("secret.exe", names)
        self.assertNotIn("link.exe", names)
        self.assertNotIn("readme.txt", text)
        self.assertIn("note for a person", text)
        self.assertIn("not a winetricks script", text)
        self.assertIn("original install", text)

    def test_setup_note_says_when_nothing_was_found(self):
        text = setup_note([])
        self.assertIn("No extra setup programs were found", text)
        self.assertIn("not a winetricks script", text)


class SidecarWriteTests(unittest.TestCase):
    def test_paths_use_the_archive_stem(self):
        reg, setup = sidecar_paths(Path("/tmp/out/Title (v1.2) (W_P) (2004).zip"))
        self.assertEqual(reg.name, "Title (v1.2) (W_P) (2004).reg")
        self.assertEqual(setup.name, "Title (v1.2) (W_P) (2004).setup.txt")

    def test_existing_file_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "Title (W_P).reg"
            path.write_bytes(b"keep")
            os.chmod(path, 0o644)
            self.assertFalse(write_if_absent(path, b"new"))
            self.assertEqual(path.read_bytes(), b"keep")
            fresh = Path(raw) / "Title (W_P).setup.txt"
            self.assertTrue(write_if_absent(fresh, b"note"))
            self.assertEqual(fresh.read_bytes(), b"note")


if __name__ == "__main__":
    unittest.main()
