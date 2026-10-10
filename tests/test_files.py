import ast
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from files import (
    FileError,
    apply_copy_plan,
    copy_regular_file,
    create_workspace,
    delete_workspace,
    extract_archive,
    plan_loose_files,
    plan_tree,
    safe_archive_path,
    stage_media,
    validate_game_folder,
)
from fixes import Fix


class PathTests(unittest.TestCase):
    def test_rejects_escape_and_special_names(self):
        samples = [
            "../outside.txt",
            "foo/../../etc/passwd",
            "/etc/passwd",
            "C:/Windows/notepad.exe",
            "C:\\Windows\\notepad.exe",
            "//server/share/file",
            "\\\\server\\share\\file",
            "folder/CON/file.txt",
            "folder/readme.",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(FileError):
                    safe_archive_path(sample)

    def test_accepts_a_normal_relative_path(self):
        self.assertEqual(safe_archive_path(".\\data\\file.txt"), "data/file.txt")


class ArchiveTests(unittest.TestCase):
    def test_zip_slip_writes_nothing_outside_staging(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside.txt"
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", b"pwned")
                zf.writestr("foo/../../also.txt", b"pwned")
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError):
                extract_archive(archive, dest, kind="zip")
            self.assertFalse(outside.exists())
            self.assertFalse((root / "also.txt").exists())
            self.assertEqual(list(dest.iterdir()), [])

    def test_rejects_symlink_duplicate_and_case_entries(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            link_zip = root / "link.zip"
            with zipfile.ZipFile(link_zip, "w") as zf:
                info = zipfile.ZipInfo("link")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, b"target")
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError):
                extract_archive(link_zip, dest, kind="zip")

            case_zip = root / "case.zip"
            with zipfile.ZipFile(case_zip, "w") as zf:
                zf.writestr("A.txt", b"a")
                zf.writestr("a.txt", b"b")
            with self.assertRaises(FileError):
                extract_archive(case_zip, dest, kind="zip")
            self.assertEqual(list(dest.iterdir()), [])

    def test_7z_keeps_wrapper_folder(self):
        import libarchive

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "hello.txt"
            source.write_text("hi")
            archive = root / "fix.7z"
            with libarchive.file_writer(str(archive), "7zip") as writer:
                writer.add_files(str(source), pathname="Wrapper/hello.txt")
            dest = root / "stage"
            dest.mkdir()
            extract_archive(archive, dest, kind="libarchive")
            self.assertEqual((dest / "Wrapper" / "hello.txt").read_text(), "hi")

    def test_keeps_wrapper_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "fix.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Wrapper/file.txt", b"data")
            dest = root / "stage"
            dest.mkdir()
            extract_archive(archive, dest, kind="zip")
            self.assertEqual((dest / "Wrapper" / "file.txt").read_bytes(), b"data")

    def test_unsupported_image_errors(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "game.iso"
            image.write_bytes(b"this is not a disc image")
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError):
                extract_archive(image, dest, kind="libarchive")
            self.assertEqual(list(dest.iterdir()), [])


class CopyTests(unittest.TestCase):
    def test_copying_an_exe_does_not_run_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker = root / "executed"
            source = root / "patch.exe"
            source.write_text(f"#!/bin/sh\necho ran > {marker}\n")
            source.chmod(0o755)
            dest = root / "out" / "patch.exe"
            dest.parent.mkdir()
            copy_regular_file(source, dest, overwrite=False)
            self.assertFalse(marker.exists())
            self.assertEqual(dest.read_bytes(), source.read_bytes())

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "secret.txt"
            outside.write_text("secret")
            src = root / "media"
            src.mkdir()
            os.symlink(outside, src / "link.txt")
            (src / "ok.txt").write_text("ok")
            dest = root / "staged"
            dest.mkdir()
            with self.assertRaises(FileError):
                from files import copy_tree_contents

                copy_tree_contents(src, dest)
            self.assertFalse((dest / "secret.txt").exists())
            self.assertEqual(outside.read_text(), "secret")

    def test_overwrite_and_case_and_type_conflicts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            (game / "mods").mkdir(parents=True)
            (game / "readme.txt").write_text("old")
            (game / "Data").mkdir()
            incoming = root / "fix"
            incoming.mkdir()
            (incoming / "readme.txt").write_text("new")
            (incoming / "extra.txt").write_text("extra")
            plan = plan_tree(incoming, game)
            self.assertEqual(plan.overwrites, ["readme.txt"])
            self.assertEqual(plan.new_files, ["extra.txt"])
            apply_copy_plan(plan, game)
            self.assertEqual((game / "readme.txt").read_text(), "new")
            self.assertEqual((game / "extra.txt").read_text(), "extra")

            (incoming / "Data").mkdir()
            (incoming / "data").mkdir()
            with self.assertRaises(FileError):
                plan_tree(incoming, game)

            loose_a = root / "A.dll"
            loose_b = root / "a.dll"
            loose_a.write_bytes(b"A")
            loose_b.write_bytes(b"a")
            with self.assertRaises(FileError):
                plan_loose_files([loose_a, loose_b], game)

    def test_folder_prefix_and_partial_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            game.mkdir()
            folder = root / "dxvk"
            (folder / "x64").mkdir(parents=True)
            (folder / "x64" / "a.dll").write_bytes(b"a")
            (folder / "x64" / "b.dll").write_bytes(b"b")
            plan = plan_tree(folder, game, folder_name="dxvk")
            self.assertIn("dxvk/x64/a.dll", [item.relative for item in plan.files])
            (folder / "x64" / "b.dll").unlink()
            with self.assertRaises(FileError) as caught:
                apply_copy_plan(plan, game)
            self.assertIn("partial", str(caught.exception).lower())
            self.assertEqual((game / "dxvk" / "x64" / "a.dll").read_bytes(), b"a")
            self.assertFalse((game / "dxvk" / "x64" / "b.dll").exists())

    def test_modules_do_not_execute_programs(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("files.py", "packaging.py", "fixes.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module.split(".")[0])
            self.assertNotIn("subprocess", modules)


class StageTests(unittest.TestCase):
    def test_exe_and_directory_stage_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            disc = root / "disc"
            (disc / "data").mkdir(parents=True)
            (disc / "setup.exe").write_bytes(b"MZ")
            (disc / "data" / "pack.bin").write_bytes(b"bin")
            before = (disc / "setup.exe").read_bytes()
            scratch = root / "scratch"
            output = root / "out"
            staged = stage_media("exe", disc / "setup.exe", scratch, output, media_root=disc)
            self.assertEqual((staged.media / "setup.exe").read_bytes(), b"MZ")
            self.assertEqual((staged.media / "data" / "pack.bin").read_bytes(), b"bin")
            self.assertEqual(staged.installer, staged.media / "setup.exe")
            self.assertEqual((disc / "setup.exe").read_bytes(), before)

            folder = root / "Installer"
            folder.mkdir()
            (folder / "setup.exe").write_bytes(b"MZ")
            (folder / "extra.txt").write_text("x")
            staged_dir = stage_media("directory", folder, scratch, output)
            self.assertTrue((staged_dir.media / "setup.exe").is_file())
            self.assertFalse((staged_dir.media / "Installer").exists())
            self.assertIsNone(staged_dir.installer)

    def test_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            disc = root / "disc"
            disc.mkdir()
            (disc / "setup.exe").write_bytes(b"MZ")
            with self.assertRaises(FileError):
                stage_media("exe", disc / "setup.exe", disc, root / "out", media_root=disc)
            self.assertEqual(list(disc.iterdir()), [disc / "setup.exe"])

    def test_delete_workspace_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = create_workspace(root / "scratch")
            outside = root / "keep.txt"
            outside.write_text("keep")
            os.symlink(outside, workspace / "media" / "link.txt")
            delete_workspace(workspace)
            self.assertFalse(workspace.exists())
            self.assertEqual(outside.read_text(), "keep")
            with self.assertRaises(FileError):
                delete_workspace(root)

    def test_game_folder_rules(self):
        with tempfile.TemporaryDirectory() as raw:
            prefix = Path(raw) / "prefix"
            game = prefix / "drive_c" / "Games" / "Demo"
            game.mkdir(parents=True)
            (game / "game.exe").write_bytes(b"MZ")
            (prefix / "drive_c" / "Program Files").mkdir()
            (prefix / "drive_c" / "windows").mkdir()
            self.assertEqual(validate_game_folder(game, prefix), game.resolve())
            with self.assertRaises(FileError):
                validate_game_folder(prefix / "drive_c", prefix)
            with self.assertRaises(FileError):
                validate_game_folder(prefix / "drive_c" / "Program Files", prefix)
            with self.assertRaises(FileError):
                validate_game_folder(prefix / "drive_c" / "windows", prefix)
            empty = prefix / "drive_c" / "Games" / "Empty"
            empty.mkdir()
            with self.assertRaises(FileError):
                validate_game_folder(empty, prefix)


class FixOrderTests(unittest.TestCase):
    def test_only_enabled_pending_fixes_are_selected(self):
        fixes = [
            Fix("copy", "one", enabled=True),
            Fix("copy", "two", enabled=False),
            Fix("copy", "three", enabled=True, applied=True),
            Fix("patcher", "four", enabled=True),
        ]
        queue = [index for index, fix in enumerate(fixes) if fix.enabled and not fix.applied]
        self.assertEqual(queue, [0, 3])


if __name__ == "__main__":
    unittest.main()
