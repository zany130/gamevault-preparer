import importlib.util
import os
import re
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from files import FileError, create_workspace
from packaging import ArchiveNameError, build_archive_name, create_game_zip


class PackageTests(unittest.TestCase):
    def test_pyproject_lists_every_application_module(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r"^py-modules = \[(.*?)]", text, re.M | re.S)
        self.assertIsNotNone(match)
        listed = set(re.findall(r'"([^"]+)"', match.group(1)))
        modules = {path.stem for path in root.glob("*.py")}
        self.assertEqual(listed, modules)

    def test_appimage_update_information_is_published(self):
        root = Path(__file__).resolve().parents[1]
        update = (
            "gh-releases-zsync|zany130|gamevault-preparer|latest|"
            "GameVault_Archive_Preparer-*-x86_64.AppImage.zsync"
        )
        script = (root / "packaging" / "build-appimage.sh").read_text(encoding="utf-8")
        workflow = (root / ".github" / "workflows" / "release-appimage.yml").read_text(encoding="utf-8")
        self.assertIn(f'-u "$UPDATE_INFORMATION"', script)
        self.assertIn(update, script)
        self.assertIn("dist/*.AppImage.zsync", workflow)
        self.assertIn("\n            zsync\n", workflow)


def notices_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "packaging" / "collect_bundle_notices.py"
    spec = importlib.util.spec_from_file_location("collect_bundle_notices", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoticeTests(unittest.TestCase):
    def test_ubuntu_snapshot_covers_the_audited_libraries(self):
        root = Path(__file__).resolve().parents[1]
        notices = root / "packaging" / "third-party"
        text = (notices / "NOTICES.md").read_text(encoding="utf-8")
        components = (notices / "system" / "COMPONENTS.txt").read_text(encoding="utf-8")
        self.assertIn("LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only", text)
        self.assertIn("6.12.0", (notices / "qt" / "VERSION").read_text(encoding="utf-8"))
        self.assertIn("23.01+dfsg-11", text)
        self.assertIn("does not apply to the binary", text)
        self.assertNotIn("its RAR unpacker adds the unRAR restriction", text)
        for package in (
            "libgtk-3-0t64",
            "libicu74",
            "libkrb5-3",
            "libgssapi-krb5-2",
            "libxcb-cursor0",
            "libarchive13t64",
            "libssl3t64",
            "7zip",
            "gcc-14-base",
        ):
            copyright_file = notices / "system" / "copyright" / package / "copyright"
            self.assertTrue(copyright_file.is_file(), package)
            self.assertIn(f"system/copyright/{package}/copyright", components)
        for name in ("Apache-2.0", "LGPL-2.1", "LGPL-3", "GPL-2", "GPL-3"):
            self.assertTrue((notices / "system" / "common-licenses" / name).exists(), name)
        self.assertIn("GCC Runtime Library Exception", (notices / "system" / "copyright" / "gcc-14-base" / "copyright").read_text(encoding="utf-8"))
        script = (root / "packaging" / "build-appimage.sh").read_text(encoding="utf-8")
        self.assertIn("collect_bundle_notices.py", script)
        self.assertIn("resolve_7zip", script)
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("its RAR unpacker adds the unRAR restriction", readme)

    def test_collector_copies_system_copyrights_and_keeps_qt_on_its_own_license(self):
        notices = notices_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            internal = root / "internal"
            tree = root / "notices"
            wheel = root / "venv" / "PySide6" / "Qt" / "lib"
            internal.mkdir()
            wheel.mkdir(parents=True)
            tree.mkdir()
            qt = b"\x7fELF" + b"Qt 6.12.0"
            (internal / "libQt6Core.so.6").write_bytes(qt)
            (wheel / "libQt6Core.so.6").write_bytes(qt)
            gtk_src = root / "libs" / "libgtk-3.so.0"
            gtk_src.parent.mkdir()
            gtk = b"\x7fELF-gtk"
            gtk_src.write_bytes(gtk)
            (internal / "libgtk-3.so.0").write_bytes(gtk)
            seven_src = root / "libs" / "7zip" / "7z"
            seven_src.parent.mkdir()
            seven = b"\x7fELF-7z"
            seven_src.write_bytes(seven)
            (internal / "7z").write_bytes(seven)
            std_src = root / "libs" / "libstdc++.so.6"
            std = b"\x7fELF-std"
            std_src.write_bytes(std)
            (internal / "libstdc++.so.6").write_bytes(std)
            gtk_notice = root / "doc" / "libgtk-3-0t64" / "copyright"
            gtk_notice.parent.mkdir(parents=True)
            gtk_notice.write_text("gtk\n", encoding="utf-8")
            seven_notice = root / "doc" / "7zip" / "copyright"
            seven_notice.parent.mkdir(parents=True)
            seven_notice.write_text("7zip\n", encoding="utf-8")
            gcc_notice = root / "doc" / "gcc-14-base" / "copyright"
            gcc_notice.parent.mkdir(parents=True)
            gcc_notice.write_text("gcc exception\n", encoding="utf-8")
            common = root / "common"
            common.mkdir()
            (common / "LGPL-2.1").write_text("lgpl\n", encoding="utf-8")
            owners = {
                str(gtk_src): "libgtk-3-0t64",
                str(seven_src): "7zip",
                str(std_src): "libstdc++6",
            }
            copyrights = {
                "libgtk-3-0t64": gtk_notice,
                "7zip": seven_notice,
                "libstdc++6": gcc_notice,
            }
            rows = notices.collect_notices(
                internal,
                tree,
                {"7z": str(seven_src), "libgtk-3.so.0": str(gtk_src), "libstdc++.so.6": str(std_src)},
                [root / "venv"],
                use_dpkg=True,
                owner_of=lambda path: owners[str(path)],
                copyright_of=lambda package: copyrights[package],
                version_of=lambda package: "tested",
                system_library=lambda name: None,
                common_licenses=common,
            )
            by_file = {row[0]: row for row in rows}
            self.assertIn("qt/LGPL-3.0.txt", by_file["libQt6Core.so.6"][2])
            self.assertEqual(by_file["libgtk-3.so.0"][2], "system/copyright/libgtk-3-0t64/copyright")
            self.assertEqual(by_file["libstdc++.so.6"][2], "system/copyright/gcc-14-base/copyright")
            self.assertEqual(by_file["7z"][2], "system/copyright/7zip/copyright")
            self.assertEqual((tree / "system" / "copyright" / "libgtk-3-0t64" / "copyright").read_text(encoding="utf-8"), "gtk\n")
            self.assertTrue((tree / "system" / "common-licenses" / "LGPL-2.1").is_file())

    def test_collector_rejects_a_wrapper_and_an_unknown_library(self):
        notices = notices_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            internal = root / "internal"
            internal.mkdir()
            (internal / "7z").write_text("#!/bin/sh\nexec /usr/lib/7zip/7z \"$@\"\n", encoding="utf-8")
            with self.assertRaises(notices.NoticeError):
                notices.require_7zip_elf(internal)
            (internal / "7z").write_bytes(b"\x7fELF-7z")
            (internal / "libmystery.so.0").write_bytes(b"\x7fELF-mystery")
            seven_src = root / "real-7z"
            seven_src.write_bytes(b"\x7fELF-7z")
            notice = root / "doc" / "7zip" / "copyright"
            notice.parent.mkdir(parents=True)
            notice.write_text("7zip\n", encoding="utf-8")
            common = root / "common"
            common.mkdir()
            (common / "LGPL-2.1").write_text("lgpl\n", encoding="utf-8")
            with self.assertRaises(notices.NoticeError):
                notices.collect_notices(
                    internal,
                    root / "notices",
                    {"7z": str(seven_src)},
                    [],
                    use_dpkg=True,
                    owner_of=lambda path: "7zip",
                    copyright_of=lambda package: notice,
                    version_of=lambda package: "",
                    system_library=lambda name: None,
                    common_licenses=common,
                )

    def test_build_follows_the_ubuntu_7z_wrapper(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "packaging" / "build-appimage.sh").read_text(encoding="utf-8")
        match = re.search(r"resolve_7zip\(\) \{.*?\n\}", script, re.S)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            elf = directory / "7zip" / "7z"
            elf.parent.mkdir()
            elf.write_bytes(b"\x7fELF")
            wrapper = directory / "bin" / "7z"
            wrapper.parent.mkdir()
            wrapper.write_text(f"#!/bin/sh\nexec {elf} \"$@\"\n", encoding="utf-8")
            completed = subprocess.run(
                ["bash", "-c", "set -euo pipefail\n" + match.group(0) + '\nresolve_7zip "$1"\n', "bash", str(wrapper)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), str(elf))


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
