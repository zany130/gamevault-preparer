import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from files import FileError, create_workspace
from packaging import ArchiveNameError, build_archive_name, create_game_zip


class NameTests(unittest.TestCase):
    def test_title_only(self):
        self.assertEqual(build_archive_name("Title"), "Title (W_P).zip")

    def test_version_and_year_order(self):
        self.assertEqual(
            build_archive_name("Title", "1.2", "2004"),
            "Title (v1.2) (W_P) (2004).zip",
        )

    def test_version_prefix_is_added_once(self):
        self.assertEqual(build_archive_name("Title", "v1.2"), "Title (v1.2) (W_P).zip")
        self.assertEqual(build_archive_name("Title", "V1.2"), "Title (v1.2) (W_P).zip")

    def test_omits_empty_optional_fields(self):
        self.assertEqual(build_archive_name("Title", "", "2004"), "Title (W_P) (2004).zip")
        self.assertEqual(build_archive_name("Title", "1.2", ""), "Title (v1.2) (W_P).zip")

    def test_rejects_brackets_reserved_names_and_bad_years(self):
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title (Demo)")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("CON")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title.")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title ")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title", year="04")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title", year="20045")
        with self.assertRaises(ArchiveNameError):
            build_archive_name("Title", version="1.2 ")


class ZipTests(unittest.TestCase):
    def test_zip_contains_only_the_game_folder_contents(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "My Game"
            (game / "empty").mkdir(parents=True)
            (game / "sub").mkdir()
            (game / "sub" / "file.txt").write_bytes(b"hello")
            (game / ".hidden").write_bytes(b"secret")
            workspace = create_workspace(root / "scratch")
            source = root / "source"
            source.mkdir()
            output = root / "out"
            path = create_game_zip(
                game,
                output,
                "Title (W_P).zip",
                workspace=workspace,
                source=source,
            )
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                self.assertIn(".hidden", names)
                self.assertIn("empty/", names)
                self.assertIn("sub/file.txt", names)
                self.assertNotIn("My Game/sub/file.txt", names)
                self.assertEqual(archive.read(".hidden"), b"secret")
                self.assertEqual(archive.read("sub/file.txt"), b"hello")
                info = archive.getinfo("sub/file.txt")
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)

    def test_existing_output_is_left_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            game.mkdir()
            (game / "a.txt").write_text("game")
            workspace = create_workspace(root / "scratch")
            output = root / "out"
            output.mkdir()
            existing = output / "Title (W_P).zip"
            existing.write_bytes(b"keep-me")
            with self.assertRaises(FileError):
                create_game_zip(
                    game,
                    output,
                    "Title (W_P).zip",
                    workspace=workspace,
                    source=root / "source",
                )
            self.assertEqual(existing.read_bytes(), b"keep-me")
            self.assertFalse((output / "Title (W_P).zip.partial").exists())

    def test_symlink_is_rejected_and_partial_is_removed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            game.mkdir()
            (game / "ok.txt").write_text("ok")
            outside = root / "outside.txt"
            outside.write_text("secret")
            os.symlink(outside, game / "link.txt")
            workspace = create_workspace(root / "scratch")
            output = root / "out"
            with self.assertRaises(FileError):
                create_game_zip(
                    game,
                    output,
                    "Title (W_P).zip",
                    workspace=workspace,
                    source=root / "source",
                )
            self.assertFalse(output.exists() and any(output.glob("*.zip*")))
            self.assertEqual(outside.read_text(), "secret")

    def test_unreadable_file_removes_partial(self):
        if os.geteuid() == 0:
            self.skipTest("root can read mode 000 files")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            game.mkdir()
            blocked = game / "blocked.txt"
            blocked.write_text("nope")
            os.chmod(blocked, 0)
            workspace = create_workspace(root / "scratch")
            output = root / "out"
            try:
                with self.assertRaises(FileError):
                    create_game_zip(
                        game,
                        output,
                        "Title (W_P).zip",
                        workspace=workspace,
                        source=root / "source",
                    )
            finally:
                os.chmod(blocked, stat.S_IRUSR | stat.S_IWUSR)
            self.assertFalse(list(output.glob("*")) if output.exists() else [])


if __name__ == "__main__":
    unittest.main()
