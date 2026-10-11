import ast
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from files import (
    FileError,
    _entries_from_7zip_listing,
    _image_has_udf,
    apply_copy_plan,
    copy_regular_file,
    create_workspace,
    delete_workspace,
    exe_inside,
    _extract_with_7zip,
    extract_archive,
    find_patcher_programs,
    plan_loose_files,
    plan_tree,
    real_exe,
    safe_archive_path,
    stage_media,
    validate_game_folder,
)
from fixes import (
    Fix,
    fixes_for_archive_choice,
    patcher_launch_target,
    placed_patcher_copy_plan,
)


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


class IsoTests(unittest.TestCase):
    def setUp(self):
        self._saved_7zip = os.environ.get("GVPREP_7ZIP")

    def tearDown(self):
        if self._saved_7zip is None:
            os.environ.pop("GVPREP_7ZIP", None)
        else:
            os.environ["GVPREP_7ZIP"] = self._saved_7zip

    def _require_genisoimage(self) -> str:
        tool = shutil_which("genisoimage")
        if tool is None:
            self.skipTest("genisoimage is not installed")
        return tool

    def _make_tree(self, root: Path) -> Path:
        source = root / "src"
        source.mkdir()
        (source / "readme.txt").write_text("hello")
        (source / "setup.exe").write_bytes(b"MZ")
        return source

    def _iso9660(self, root: Path) -> Path:
        source = self._make_tree(root)
        image = root / "game.iso"
        subprocess_run(
            [self._require_genisoimage(), "-o", str(image), "-R", "-J", str(source)],
        )
        return image

    def _udf(self, root: Path) -> Path:
        source = self._make_tree(root)
        image = root / "game.iso"
        subprocess_run(
            [self._require_genisoimage(), "-o", str(image), "-udf", str(source)],
        )
        return image

    def test_iso9660_extracts_without_7zip(self):
        os.environ["GVPREP_7ZIP"] = ""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = self._iso9660(root)
            self.assertFalse(_image_has_udf(image))
            before = image.read_bytes()
            dest = root / "stage"
            dest.mkdir()
            extract_archive(image, dest, kind="iso")
            self.assertEqual((dest / "readme.txt").read_text(), "hello")
            self.assertEqual((dest / "setup.exe").read_bytes(), b"MZ")
            self.assertEqual(image.read_bytes(), before)
            self.assertFalse((dest / "setup.exe").stat().st_mode & 0o111)

    def test_rock_ridge_symlink_is_rejected(self):
        os.environ["GVPREP_7ZIP"] = ""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = self._make_tree(root)
            os.symlink("readme.txt", source / "link.txt")
            image = root / "linked.iso"
            subprocess_run(
                [self._require_genisoimage(), "-o", str(image), "-R", str(source)],
            )
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError) as caught:
                extract_archive(image, dest, kind="iso")
            self.assertIn("link", str(caught.exception).lower())
            self.assertEqual(list(dest.iterdir()), [])

    def test_udf_hybrid_uses_7zip_names_not_the_iso9660_view(self):
        if shutil_which("7z") is None and shutil_which("7zz") is None:
            self.skipTest("7-Zip is not installed")
        os.environ.pop("GVPREP_7ZIP", None)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = self._udf(root)
            self.assertTrue(_image_has_udf(image))
            before = image.stat().st_mtime_ns
            dest = root / "stage"
            dest.mkdir()
            extract_archive(image, dest, kind="iso")
            self.assertEqual((dest / "readme.txt").read_text(), "hello")
            self.assertEqual((dest / "setup.exe").read_bytes(), b"MZ")
            self.assertFalse((dest / "README.TXT").exists())
            self.assertEqual(image.stat().st_mtime_ns, before)

    def test_udf_reports_a_missing_7zip(self):
        os.environ["GVPREP_7ZIP"] = ""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = self._udf(root)
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError) as caught:
                extract_archive(image, dest, kind="iso")
            self.assertIn("7-Zip", str(caught.exception))
            self.assertEqual(list(dest.iterdir()), [])

    def test_failed_7zip_extract_removes_the_workspace(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = self._udf(root)
            before = image.read_bytes()
            tool = root / "failing-7z"
            tool.write_text("#!/bin/sh\necho boom from 7zip >&2\nexit 2\n", encoding="utf-8")
            tool.chmod(0o755)
            os.environ["GVPREP_7ZIP"] = str(tool)
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError) as caught:
                extract_archive(image, dest, kind="iso")
            self.assertIn("boom from 7zip", str(caught.exception))
            self.assertEqual(list(dest.iterdir()), [])
            disc = root / "disc"
            disc.mkdir()
            placed = disc / "game.iso"
            image.replace(placed)
            scratch = root / "scratch"
            output = root / "out"
            with self.assertRaises(FileError) as staged:
                stage_media("iso", placed, scratch, output)
            self.assertIn("boom from 7zip", str(staged.exception))
            self.assertFalse(any(scratch.glob("gvprep-*")))
            self.assertEqual(placed.read_bytes(), before)

    def test_7zip_does_not_write_outside_the_stage(self):
        tool = shutil_which("7zz") or shutil_which("7z")
        if tool is None:
            self.skipTest("7-Zip is not installed")
        os.environ.pop("GVPREP_7ZIP", None)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("original")
            before = sentinel.read_bytes()
            src = root / "src"
            src.mkdir()
            (src / "readme.txt").write_text("hello")
            os.symlink(sentinel, src / "escape")
            os.symlink("/etc/passwd", src / "abs")
            links = root / "links.7z"
            subprocess_run([tool, "a", "-snl", "-y", os.fspath(links), "src"], cwd=root)
            dest = root / "stage"
            dest.mkdir()
            with self.assertRaises(FileError):
                _extract_with_7zip(links, dest, None)
            self.assertEqual(list(dest.iterdir()), [])
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual(list(outside.iterdir()), [sentinel])

            nest = root / "nest"
            nest.mkdir()
            (nest / "outside.txt").write_text("out")
            parent = root / "parent.7z"
            subprocess_run([tool, "a", "-y", os.fspath(parent), "nest/outside.txt"], cwd=root)
            subprocess_run([tool, "rn", os.fspath(parent), "nest/outside.txt", "../outside.txt"])
            with self.assertRaises(FileError):
                _extract_with_7zip(parent, dest, None)
            self.assertEqual(list(dest.iterdir()), [])
            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual(list(outside.iterdir()), [sentinel])

    def test_udf_symlink_does_not_write_outside_the_stage(self):
        tool = shutil_which("7zz") or shutil_which("7z")
        if tool is None:
            self.skipTest("7-Zip is not installed")
        os.environ.pop("GVPREP_7ZIP", None)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("original")
            before = sentinel.read_bytes()
            source = root / "src"
            source.mkdir()
            (source / "readme.txt").write_text("hello")
            os.symlink(sentinel, source / "escape")
            os.symlink("/etc/passwd", source / "abs")
            image = root / "game.iso"
            subprocess_run(
                [self._require_genisoimage(), "-o", str(image), "-udf", "-R", str(source)],
            )
            dest = root / "stage"
            dest.mkdir()
            try:
                extract_archive(image, dest, kind="iso")
            except FileError:
                self.assertEqual(list(dest.iterdir()), [])
            else:
                for path in dest.rglob("*"):
                    self.assertFalse(path.is_symlink(), path)
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual(list(outside.iterdir()), [sentinel])
            self.assertFalse((root / "passwd").exists())

    def test_7zip_listing_rejects_unsafe_entries(self):
        samples = [
            "Path = ../outside.txt\nFolder = -\nSize = 1\n",
            "Path = dir/link.txt\nAttributes = A lrwxrwxrwx\nSize = 4\n",
            "Path = dir/fifo\nAttributes = A prw-r--r--\nSize = 0\n",
            "Path = readme.txt\nFolder = -\nSize = 1\n\nPath = readme.txt\nFolder = -\nSize = 2\n",
        ]
        for listing in samples:
            with self.subTest(listing=listing.splitlines()[0]):
                with self.assertRaises(FileError):
                    _entries_from_7zip_listing(listing)

    def test_udf_marker_in_a_hybrid_header_is_detected(self):
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "hybrid.iso"
            blob = bytearray(22 * 2048)
            blob[16 * 2048 + 1 : 16 * 2048 + 6] = b"CD001"
            blob[20 * 2048 + 1 : 20 * 2048 + 6] = b"NSR02"
            image.write_bytes(blob)
            self.assertTrue(_image_has_udf(image))
            plain = Path(raw) / "plain.iso"
            plain_blob = bytearray(22 * 2048)
            plain_blob[16 * 2048 + 1 : 16 * 2048 + 6] = b"CD001"
            plain.write_bytes(plain_blob)
            self.assertFalse(_image_has_udf(plain))


def shutil_which(name: str) -> str | None:
    return __import__("shutil").which(name)


def subprocess_run(args: list[str], cwd: Path | None = None) -> None:
    completed = __import__("subprocess").run(args, cwd=cwd, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", "replace"))


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
        files_source = (root / "files.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", files_source)
        self.assertNotIn("shell = True", files_source)
        for name in ("packaging.py", "fixes.py"):
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


class RealExeTests(unittest.TestCase):
    def test_accepts_a_real_exe_and_rejects_links_and_other_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            program = root / "Game.exe"
            program.write_bytes(b"MZ")
            self.assertEqual(real_exe(program), program.resolve())
            (root / "notes.txt").write_text("hi")
            with self.assertRaises(FileError):
                real_exe(root / "notes.txt")
            (root / "linked.exe").symlink_to(program)
            with self.assertRaises(FileError):
                real_exe(root / "linked.exe")
            with self.assertRaises(FileError):
                real_exe(root)

    def test_exe_must_stay_inside_the_unpacked_zip(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "game"
            game.mkdir()
            program = game / "Game.exe"
            program.write_bytes(b"MZ")
            outside = root / "other.exe"
            outside.write_bytes(b"MZ")
            self.assertEqual(exe_inside(game, program), program.resolve())
            with self.assertRaises(FileError):
                exe_inside(game, outside)


class PatcherFixTests(unittest.TestCase):
    def test_scan_finds_only_real_executables(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tree = root / "archive"
            nested = tree / "tools"
            nested.mkdir(parents=True)
            (tree / "Patch.exe").write_bytes(b"MZ")
            (tree / "readme.txt").write_text("notes")
            (nested / "setup.BAT").write_text("echo hi")
            (tree / "linked.exe").symlink_to(tree / "Patch.exe")
            outside = root / "hidden"
            outside.mkdir()
            (outside / "secret.exe").write_bytes(b"MZ")
            (tree / "elsewhere").symlink_to(outside)
            self.assertEqual(
                find_patcher_programs(tree),
                ["Patch.exe", "tools/setup.BAT"],
            )

    def test_placed_patcher_is_planned_into_the_game_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            staged = root / "staged"
            staged.mkdir()
            (staged / "Patch.exe").write_bytes(b"MZ")
            (staged / "data.bin").write_bytes(b"bin")
            game = root / "game"
            game.mkdir()
            (game / "data.bin").write_bytes(b"old")
            fix = Fix(
                "patcher",
                "Copy Patch.exe into the game folder, then run it",
                source_root=staged,
                executable=Path("Patch.exe"),
                place_in_game=True,
            )
            plan = placed_patcher_copy_plan(fix, game)
            self.assertEqual(plan.new_files, ["Patch.exe"])
            self.assertEqual(plan.overwrites, ["data.bin"])
            self.assertNotIn("staged", [item.relative for item in plan.files])

    def test_launch_path_is_under_the_game_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            game = root / "My Game"
            (game / "tools").mkdir(parents=True)
            (game / "tools" / "Patch.exe").write_bytes(b"MZ")
            fix = Fix(
                "patcher",
                "Run tools/Patch.exe from the game folder",
                executable=Path("tools/Patch.exe"),
                place_in_game=True,
            )
            executable, cwd = patcher_launch_target(fix, game)
            self.assertEqual(executable, (game / "tools" / "Patch.exe").resolve())
            self.assertEqual(cwd, game.resolve())
            missing = Fix(
                "patcher",
                "Run Missing.exe from the game folder",
                executable=Path("Missing.exe"),
                place_in_game=True,
            )
            with self.assertRaises(FileError) as caught:
                patcher_launch_target(missing, game)
            self.assertIn("not in the game folder yet", str(caught.exception))
            with self.assertRaises(FileError):
                patcher_launch_target(
                    Fix("patcher", "bad", executable=Path("../outside.exe"), place_in_game=True),
                    game,
                )

    def test_own_folder_patcher_stays_outside_the_game(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            folder = root / "patch files"
            folder.mkdir()
            staged = folder / "Patch.exe"
            staged.write_bytes(b"MZ")
            game = root / "game"
            game.mkdir()
            executable, cwd = patcher_launch_target(
                Fix("patcher", "Run Patch.exe from its own folder", executable=staged),
                game,
            )
            self.assertEqual(executable, staged.resolve())
            self.assertEqual(cwd, folder.resolve())
            self.assertFalse((game / "Patch.exe").exists())

    def test_copy_only_does_not_add_a_run_step(self):
        copy = Fix("copy", "Copy contents of fix.zip", source_root=Path("/tmp/staged"))
        self.assertEqual(fixes_for_archive_choice(copy, None), [copy])
        self.assertEqual(fixes_for_archive_choice(copy, ""), [copy])
        rows = fixes_for_archive_choice(copy, "tools/Patch.exe")
        self.assertEqual([row.kind for row in rows], ["copy", "patcher"])
        self.assertTrue(rows[1].place_in_game)
        self.assertIsNone(rows[1].source_root)
        self.assertEqual(rows[1].executable, Path("tools/Patch.exe"))
        self.assertIn("tools/Patch.exe", rows[1].label)


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
