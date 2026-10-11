"""Copying, archive reading, and path checks.

The only program this module runs is 7-Zip, for UDF and hybrid disc images.
It never runs files from an archive or the original installer.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from wine import host_tool_environment

MARKER = ".gamevault-preparer"
BROAD_GAME_ROOTS = {
    "windows",
    "program files",
    "program files (x86)",
}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update(f"COM{i}" for i in range(1, 10))
_RESERVED.update(f"LPT{i}" for i in range(1, 10))


class FileError(Exception):
    """A filesystem problem the user can act on."""


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    relative: str
    overwrite: bool


@dataclass(frozen=True)
class CopyPlan:
    files: tuple[PlannedFile, ...]
    directories: tuple[str, ...]

    @property
    def overwrites(self) -> list[str]:
        return [item.relative for item in self.files if item.overwrite]

    @property
    def new_files(self) -> list[str]:
        return [item.relative for item in self.files if not item.overwrite]


@dataclass(frozen=True)
class StageResult:
    workspace: Path
    media: Path
    prefix: Path
    fixes: Path
    installer: Path | None
    source: Path


def is_reserved_name(name: str) -> bool:
    stem = name.split(".", 1)[0]
    return stem.upper() in _RESERVED


def paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def explain_os_error(exc: OSError, step: str, path: Path | None = None) -> FileError:
    if exc.errno == errno.ENOSPC:
        message = f"{step} failed because the disk is full."
    elif exc.errno in (errno.EACCES, errno.EPERM):
        message = f"{step} failed because permission was denied."
    else:
        message = f"{step} failed: {exc.strerror or exc}."
    if path is not None:
        message += f" Path: {path}"
    return FileError(message)


def safe_archive_path(name: str) -> str:
    """Return a relative archive path, or raise if the entry is unsafe."""
    if not isinstance(name, str):
        raise FileError("An archive entry name is not text.")
    if "\x00" in name:
        raise FileError("An archive entry name contains a null character.")
    if name.startswith("\\\\") or name.startswith("//"):
        raise FileError(f"An archive entry has a UNC path: {name}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise FileError(f"An archive entry has an absolute path: {name}")
    head = normalized.split("/", 1)[0]
    if len(head) >= 2 and head[0].isalpha() and head[1] == ":":
        raise FileError(f"An archive entry has a drive path: {name}")
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise FileError(f"An archive entry contains '..': {name}")
        if part.endswith(" ") or part.endswith("."):
            raise FileError(f"An archive entry ends with a space or a dot: {name}")
        if any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part):
            raise FileError(f"An archive entry has an invalid character: {name}")
        if is_reserved_name(part):
            raise FileError(f"An archive entry uses a reserved name: {name}")
        parts.append(part)
    return "/".join(parts)


def assert_distinct_names(relative_paths: list[str]) -> None:
    seen: dict[str, str] = {}
    for relative in relative_paths:
        pieces: list[str] = []
        for part in relative.split("/"):
            if not part:
                continue
            pieces.append(part)
            current = "/".join(pieces)
            previous = seen.get(current.casefold())
            if previous is not None and previous != current:
                raise FileError(
                    f"Ambiguous case-only names '{previous}' and '{current}'. "
                    "Windows treats those as the same path."
                )
            seen[current.casefold()] = current


def _reject_special(path: Path, st: os.stat_result) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise FileError(f"Refusing to use a link: {path}")
    if not stat.S_ISREG(st.st_mode) and not stat.S_ISDIR(st.st_mode):
        raise FileError(f"Refusing to use a special file: {path}")


def _walk_no_follow(root: Path):
    """Yield (relative posix, path, is_dir) for a real directory tree."""
    root = root.resolve()
    if not root.is_dir():
        raise FileError(f"Not a directory: {root}")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in list(dirnames):
            path = base / name
            relative = path.relative_to(root).as_posix()
            try:
                st = path.lstat()
            except OSError as exc:
                raise explain_os_error(exc, "Reading the folder", path) from exc
            _reject_special(path, st)
            if not stat.S_ISDIR(st.st_mode):
                raise FileError(f"Refusing to use a special folder entry: {relative}")
            yield relative, path, True
        for name in filenames:
            path = base / name
            relative = path.relative_to(root).as_posix()
            try:
                st = path.lstat()
            except OSError as exc:
                raise explain_os_error(exc, "Reading the file", path) from exc
            _reject_special(path, st)
            if not stat.S_ISREG(st.st_mode):
                raise FileError(f"Refusing to use a special file: {relative}")
            yield relative, path, False


def _destination_index(dest_root: Path) -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for relative, path, is_dir in _walk_no_follow(dest_root):
        kind = "dir" if is_dir else "file"
        key = relative.casefold()
        previous = index.get(key)
        if previous is not None and previous[0] != relative:
            raise FileError(
                f"The destination already has ambiguous case-only names "
                f"'{previous[0]}' and '{relative}'."
            )
        index[key] = (relative, kind)
    return index


def _check_against_destination(relative: str, kind: str, index: dict[str, tuple[str, str]]) -> bool:
    """Return True when a file would overwrite an existing file."""
    pieces: list[str] = []
    parts = [part for part in relative.split("/") if part]
    for pos, part in enumerate(parts):
        pieces.append(part)
        current = "/".join(pieces)
        existing = index.get(current.casefold())
        last = pos == len(parts) - 1
        if existing is None:
            continue
        exact, existing_kind = existing
        if exact != current:
            raise FileError(
                f"Ambiguous case-only names '{exact}' and '{current}'. "
                "Windows treats those as the same path."
            )
        if last and kind == "file" and existing_kind == "file":
            return True
        if last and kind == "dir" and existing_kind == "dir":
            return False
        if last and existing_kind != kind:
            existing_label = "folder" if existing_kind == "dir" else "file"
            wanted_label = "folder" if kind == "dir" else "file"
            raise FileError(
                f"A {existing_label} is already in the way of a {wanted_label}: {current}"
            )
        if not last and existing_kind != "dir":
            raise FileError(f"A file is already in the way of a folder: {current}")
    return False


def plan_tree(source_root: Path, dest_root: Path, folder_name: str | None = None) -> CopyPlan:
    source_root = source_root.resolve()
    dest_root = dest_root.resolve()
    if not source_root.is_dir():
        raise FileError(f"Not a directory: {source_root}")
    prefix = ""
    if folder_name is not None:
        prefix = safe_archive_path(folder_name)
        if not prefix or "/" in prefix or prefix != folder_name:
            raise FileError(f"Cannot use this folder name: {folder_name}")
    directories: list[str] = []
    files: list[PlannedFile] = []
    relatives: list[str] = []
    if prefix:
        directories.append(prefix)
        relatives.append(prefix)
    for relative, path, is_dir in _walk_no_follow(source_root):
        dest_rel = f"{prefix}/{relative}" if prefix else relative
        relatives.append(dest_rel)
        if is_dir:
            directories.append(dest_rel)
        else:
            files.append(PlannedFile(path, dest_rel, False))
    assert_distinct_names(relatives)
    index = _destination_index(dest_root)
    for directory in directories:
        _check_against_destination(directory, "dir", index)
    planned: list[PlannedFile] = []
    for item in files:
        overwrite = _check_against_destination(item.relative, "file", index)
        planned.append(PlannedFile(item.source, item.relative, overwrite))
    return CopyPlan(tuple(planned), tuple(directories))


def plan_loose_files(files: list[Path], dest_root: Path) -> CopyPlan:
    if not files:
        raise FileError("Select at least one file to copy.")
    planned_sources: list[tuple[Path, str]] = []
    for raw in files:
        path = raw.resolve()
        try:
            st = path.lstat()
        except OSError as exc:
            raise explain_os_error(exc, "Reading the file", path) from exc
        _reject_special(path, st)
        if not stat.S_ISREG(st.st_mode):
            raise FileError(f"Select a file, not a folder: {path}")
        name = safe_archive_path(path.name)
        if name != path.name or "/" in name:
            raise FileError(f"Cannot copy a file with this name: {path.name}")
        planned_sources.append((path, name))
    assert_distinct_names([name for _, name in planned_sources])
    index = _destination_index(dest_root.resolve())
    planned = []
    for path, name in planned_sources:
        overwrite = _check_against_destination(name, "file", index)
        planned.append(PlannedFile(path, name, overwrite))
    return CopyPlan(tuple(planned), ())


def _mkdir_new(root: Path, relative: str) -> bool:
    current = root.resolve()
    created = False
    for part in [part for part in relative.split("/") if part]:
        current = current / part
        if current.is_symlink():
            raise FileError(f"Refusing to follow a link: {current}")
        if current.exists():
            if not current.is_dir():
                raise FileError(f"A file is already in the way of a folder: {relative}")
            continue
        try:
            current.mkdir()
        except OSError as exc:
            raise explain_os_error(exc, "Creating a folder", current) from exc
        created = True
    return created


def _destination_file(root: Path, relative: str) -> Path:
    parent_rel, _, name = relative.rpartition("/")
    if parent_rel:
        _mkdir_new(root, parent_rel)
    dest = root.resolve() / relative
    lexical = Path(os.path.abspath(dest))
    root_real = root.resolve()
    if lexical != root_real and root_real not in lexical.parents:
        raise FileError(f"A destination escapes the game folder: {relative}")
    if dest.is_symlink():
        raise FileError(f"Refusing to follow a link: {dest}")
    return dest


def _stream_into(src_file, dest: Path) -> None:
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as dest_file:
            shutil.copyfileobj(src_file, dest_file, length=1024 * 1024)
            dest_file.flush()
            os.fsync(dest_file.fileno())
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def copy_regular_file(src: Path, dest: Path, *, overwrite: bool) -> None:
    try:
        src_stat = src.lstat()
    except OSError as exc:
        raise explain_os_error(exc, "Reading the file", src) from exc
    _reject_special(src, src_stat)
    if not stat.S_ISREG(src_stat.st_mode):
        raise FileError(f"Refusing to copy a special file: {src}")
    if dest.is_symlink():
        raise FileError(f"Refusing to follow a link: {dest}")
    if dest.exists() and dest.is_dir():
        raise FileError(f"A folder is already in the way of a file: {dest}")
    if dest.exists() and not overwrite:
        raise FileError(f"Destination already exists: {dest}")

    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    tmp: Path | None = None
    try:
        with os.fdopen(fd, "rb") as source_file:
            if overwrite and dest.exists():
                tmp = dest.parent / f".{dest.name}.{os.getpid()}.gvprep-partial"
                try:
                    _stream_into(source_file, tmp)
                    if dest.is_symlink():
                        raise FileError(f"Refusing to follow a link: {dest}")
                    os.replace(tmp, dest)
                    tmp = None
                except FileError:
                    raise
                except OSError as exc:
                    raise explain_os_error(exc, "Copying", dest) from exc
            else:
                try:
                    _stream_into(source_file, dest)
                except OSError as exc:
                    raise explain_os_error(exc, "Copying", dest) from exc
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def apply_copy_plan(plan: CopyPlan, dest_root: Path, progress=None) -> None:
    dest_root = dest_root.resolve()
    changed = False
    total = len(plan.directories) + len(plan.files)
    done = 0
    try:
        for relative in sorted(plan.directories, key=lambda item: (item.count("/"), item)):
            if _mkdir_new(dest_root, relative):
                changed = True
            done += 1
            _report(progress, done, total, relative)
        for item in plan.files:
            dest = _destination_file(dest_root, item.relative)
            exists = dest.exists()
            if exists and not item.overwrite:
                raise FileError(
                    f"Refusing to overwrite {item.relative} because it was not approved."
                )
            if exists and dest.is_dir():
                raise FileError(f"A folder is already in the way of a file: {item.relative}")
            if item.source.resolve() == dest.resolve():
                raise FileError(
                    f"{item.relative} is the same file as the source. Choose a different file to copy."
                )
            copy_regular_file(item.source, dest, overwrite=bool(item.overwrite and exists))
            changed = True
            done += 1
            _report(progress, done, total, item.relative)
    except Exception as exc:
        if changed:
            raise FileError(
                f"{exc} Copying stopped after this fix had already changed the game folder. "
                "Those partial changes were left in place. Earlier applied fixes were left in place. "
                "This app does not undo changes."
            ) from exc
        raise


def copy_tree_contents(src: Path, dest: Path, progress=None) -> None:
    src = src.resolve()
    dest = dest.resolve()
    if paths_overlap(src, dest):
        raise FileError("Refusing to copy a folder into itself.")
    entries = list(_walk_no_follow(src))
    assert_distinct_names([relative for relative, _, _ in entries])
    total = len(entries) or 1
    for index, (relative, path, is_dir) in enumerate(entries, start=1):
        if is_dir:
            _mkdir_new(dest, relative)
        else:
            target = _destination_file(dest, relative)
            copy_regular_file(path, target, overwrite=False)
        _report(progress, index, total, relative)


def _report(progress, done: int, total: int, message: str) -> None:
    if progress is None:
        return
    percent = 100 if total <= 0 else int(done * 100 / total)
    progress(percent, message)


def _wipe_children(root: Path) -> None:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            if _outside(root, path):
                raise FileError(f"Refusing to clean a path outside staging: {path}")
            path.unlink()
        for name in dirnames:
            path = current / name
            if _outside(root, path):
                raise FileError(f"Refusing to clean a path outside staging: {path}")
            if path.is_symlink():
                path.unlink()
            else:
                path.rmdir()


def _outside(root: Path, path: Path) -> bool:
    lexical = Path(os.path.abspath(path))
    return lexical != root and root not in lexical.parents


def _zip_entries(archive: Path) -> list[tuple[str, zipfile.ZipInfo, bool]]:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise FileError(f"The ZIP archive is corrupt or is not a ZIP file: {archive.name}") from exc
    except OSError as exc:
        raise explain_os_error(exc, "Opening the ZIP archive", archive) from exc
    collected: list[tuple[str, zipfile.ZipInfo, bool]] = []
    try:
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                raise FileError("Encrypted ZIP archives are not supported.")
            relative = safe_archive_path(info.filename)
            if not relative:
                continue
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if file_type == stat.S_IFLNK:
                raise FileError(f"Refusing to extract a link: {info.filename}")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise FileError(f"Refusing to extract a special file: {info.filename}")
            is_dir = info.is_dir()
            collected.append((relative, info, is_dir))
    finally:
        zf.close()
    _assert_entry_set(collected)
    return collected


def _assert_entry_set(entries: list[tuple[str, object, bool]]) -> None:
    kinds: dict[str, str] = {}
    for relative, _, is_dir in entries:
        kind = "dir" if is_dir else "file"
        previous = kinds.get(relative)
        if previous == "file" or (previous is not None and previous != kind):
            if previous == kind:
                raise FileError(f"The archive lists '{relative}' more than once.")
            raise FileError(f"The archive lists '{relative}' as both a file and a folder.")
        if previous == "dir":
            continue
        kinds[relative] = kind
    assert_distinct_names(list(kinds))
    files = {relative for relative, kind in kinds.items() if kind == "file"}
    for relative in files:
        pieces: list[str] = []
        for part in relative.split("/")[:-1]:
            pieces.append(part)
            parent = "/".join(pieces)
            if parent in files:
                raise FileError(f"A file is in the way of a folder inside the archive: {parent}")


def _extract_zip(archive: Path, dest: Path, progress) -> None:
    entries = _zip_entries(archive)
    total = len(entries) or 1
    with zipfile.ZipFile(archive) as zf:
        for index, (relative, info, is_dir) in enumerate(entries, start=1):
            if is_dir:
                _mkdir_new(dest, relative)
            else:
                target = _destination_file(dest, relative)
                if target.exists() or target.is_symlink():
                    raise FileError(f"Refusing to overwrite during extraction: {relative}")
                try:
                    with zf.open(info, "r") as source:
                        in_fd_holder = source
                        out_fd = os.open(
                            target,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o644,
                        )
                        try:
                            with os.fdopen(out_fd, "wb") as dest_file:
                                shutil.copyfileobj(in_fd_holder, dest_file, length=1024 * 1024)
                        except Exception:
                            target.unlink(missing_ok=True)
                            raise
                except FileError:
                    raise
                except OSError as exc:
                    raise explain_os_error(exc, "Extracting", target) from exc
                except zipfile.BadZipFile as exc:
                    raise FileError(f"The ZIP archive is corrupt near {relative}.") from exc
            _report(progress, index, total, relative)


def _libarchive_module():
    try:
        import libarchive
    except ImportError as exc:
        raise FileError(
            "libarchive-c is not installed, so ISO and 7z files cannot be read. "
            "Install the libarchive-c package and the system libarchive library."
        ) from exc
    return libarchive


def _libarchive_manifest(archive: Path) -> list[tuple[str, bool]]:
    libarchive = _libarchive_module()
    manifest: list[tuple[str, bool]] = []
    try:
        with libarchive.file_reader(os.fspath(archive)) as reader:
            for entry in reader:
                raw = entry.pathname or ""
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "surrogateescape")
                relative = safe_archive_path(str(raw))
                if not relative:
                    continue
                if entry.issym or entry.islnk or entry.isdev or entry.isfifo or entry.issock:
                    raise FileError(f"Refusing to extract a link or special file: {raw}")
                if entry.isdir:
                    manifest.append((relative, True))
                elif entry.isreg:
                    manifest.append((relative, False))
                else:
                    raise FileError(f"This archive entry type is not supported: {raw}")
    except FileError:
        raise
    except Exception as exc:
        raise FileError(
            f"Cannot read this archive. It may be corrupt or an unsupported image: {archive.name}. {exc}"
        ) from exc
    _assert_entry_set([(relative, None, is_dir) for relative, is_dir in manifest])
    return manifest


def _extract_libarchive(archive: Path, dest: Path, progress) -> None:
    manifest = _libarchive_manifest(archive)
    if not manifest:
        raise FileError(f"The archive has no files: {archive.name}")
    expected = {relative: is_dir for relative, is_dir in manifest}
    libarchive = _libarchive_module()
    total = len(manifest) or 1
    seen = 0
    try:
        with libarchive.file_reader(os.fspath(archive)) as reader:
            for entry in reader:
                raw = entry.pathname or ""
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "surrogateescape")
                relative = safe_archive_path(str(raw))
                if not relative:
                    continue
                if relative not in expected:
                    raise FileError(f"The archive changed while it was being read: {raw}")
                is_dir = expected[relative]
                if is_dir:
                    if not entry.isdir:
                        raise FileError(f"The archive changed while it was being read: {raw}")
                    _mkdir_new(dest, relative)
                else:
                    if not entry.isreg:
                        raise FileError(f"The archive changed while it was being read: {raw}")
                    target = _destination_file(dest, relative)
                    if target.exists() or target.is_symlink():
                        raise FileError(f"Refusing to overwrite during extraction: {relative}")
                    out_fd = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o644,
                    )
                    try:
                        with os.fdopen(out_fd, "wb") as dest_file:
                            for block in entry.get_blocks():
                                dest_file.write(block)
                            dest_file.flush()
                            os.fsync(dest_file.fileno())
                    except Exception:
                        target.unlink(missing_ok=True)
                        raise
                seen += 1
                _report(progress, seen, total, relative)
    except FileError:
        raise
    except Exception as exc:
        raise FileError(
            f"Cannot read this archive. It may be corrupt or an unsupported image: {archive.name}. {exc}"
        ) from exc
    if seen != len([item for item in manifest]):
        raise FileError("The archive changed while it was being read.")


_UDF_IDENTIFIERS = (b"NSR01", b"NSR02", b"NSR03")
_UNIX_MODE = re.compile(r"^[dlpcb-][rwxstST-]{9}$")


def _image_has_udf(archive: Path) -> bool:
    """True when a UDF volume descriptor is present, including ISO/UDF hybrids."""
    try:
        with archive.open("rb") as handle:
            for sector in range(257):
                handle.seek(sector * 2048 + 1)
                identifier = handle.read(5)
                if len(identifier) < 5:
                    return False
                if identifier in _UDF_IDENTIFIERS:
                    return True
    except OSError as exc:
        raise explain_os_error(exc, "Reading the disc image", archive) from exc
    return False


def _missing_7zip_message() -> str:
    return (
        "This disc image needs 7-Zip (7zz or 7z) to read its UDF filesystem. "
        "Install the 7zip package and make sure that command is on PATH. "
        "The AppImage includes 7-Zip."
    )


def _find_7zip() -> Path:
    override = os.environ.get("GVPREP_7ZIP")
    if override is not None:
        if override == "":
            raise FileError(_missing_7zip_message())
        chosen = Path(override)
        if chosen.is_file() and os.access(chosen, os.X_OK):
            return chosen
        raise FileError(f"7-Zip was not found at {override}. {_missing_7zip_message()}")
    search: list[Path] = []
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        search.append(Path(bundled))
    search.append(Path(sys.executable).resolve().parent / "_internal")
    for directory in search:
        for name in ("7zz", "7z"):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    for name in ("7zz", "7z"):
        found = shutil.which(name)
        if found:
            return Path(found)
    raise FileError(_missing_7zip_message())


def _run_7zip(program: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [os.fspath(program), *args],
            env=host_tool_environment(),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise FileError(f"Could not start 7-Zip ({program}). {exc}") from exc


def _tool_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    parts = [
        completed.stdout.decode("utf-8", "replace").strip(),
        completed.stderr.decode("utf-8", "replace").strip(),
    ]
    text = "\n".join(part for part in parts if part)
    if len(text) > 700:
        return text[-700:]
    return text


def _unix_type(attributes: str) -> str | None:
    for token in attributes.split():
        if _UNIX_MODE.match(token):
            return token[0]
    return None


def _listed_kind(fields: dict[str, str], raw_path: str) -> str:
    unix = _unix_type(fields.get("Attributes", ""))
    if unix == "l":
        return "link"
    if unix in {"p", "c", "b", "s"}:
        return "special"
    folder = fields.get("Folder")
    if unix == "d" or folder == "+":
        return "dir"
    if unix == "-" or folder == "-":
        return "file"
    if fields.get("Attributes", "").startswith("D"):
        return "dir"
    raise FileError(f"This archive entry type is not supported: {raw_path}")


def _entries_from_7zip_listing(text: str) -> list[tuple[str, bool, int | None]]:
    entries: list[tuple[str, bool, int | None]] = []
    fields: dict[str, str] = {}

    def flush() -> None:
        if "Path" not in fields:
            fields.clear()
            return
        raw = fields["Path"]
        relative = safe_archive_path(raw)
        if not relative:
            fields.clear()
            return
        if fields.get("Encrypted") == "+":
            raise FileError(f"Encrypted archive entries are not supported: {raw}")
        kind = _listed_kind(fields, raw)
        if kind in {"link", "special"}:
            raise FileError(f"Refusing to extract a link or special file: {raw}")
        size: int | None = None
        if kind == "file" and fields.get("Size", "").isdigit():
            size = int(fields["Size"])
        entries.append((relative, kind == "dir", size))
        fields.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        fields[key] = value
    flush()
    _assert_entry_set([(relative, None, is_dir) for relative, is_dir, _size in entries])
    return entries


def _audit_7zip_tree(dest: Path, entries: list[tuple[str, bool, int | None]]) -> None:
    expected = {relative: (is_dir, size) for relative, is_dir, size in entries}
    for relative, is_dir, _size in entries:
        if is_dir:
            continue
        parent_parts: list[str] = []
        for part in relative.split("/")[:-1]:
            parent_parts.append(part)
            parent = "/".join(parent_parts)
            expected.setdefault(parent, (True, None))
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        current = Path(dirpath)
        for name in dirnames + filenames:
            path = current / name
            relative = path.relative_to(dest).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise FileError(f"Refusing to keep a link extracted from the disc: {relative}")
            if stat.S_ISDIR(info.st_mode):
                is_dir = True
            elif stat.S_ISREG(info.st_mode):
                is_dir = False
            else:
                raise FileError(f"Refusing to keep a special file extracted from the disc: {relative}")
            if relative not in expected or expected[relative][0] != is_dir:
                raise FileError(f"7-Zip wrote an unexpected entry: {relative}")
            size = expected[relative][1]
            if not is_dir and size is not None and info.st_size != size:
                raise FileError(
                    f"Extracted file size does not match the disc listing: {relative}"
                )
            found.add(relative)
    missing = [relative for relative in expected if relative not in found]
    if missing:
        raise FileError(f"7-Zip did not extract every listed file. Missing: {missing[0]}")


def _extract_with_7zip(archive: Path, dest: Path, progress) -> None:
    program = _find_7zip()
    _report(progress, 0, 1, "Reading the disc with 7-Zip")
    listed = _run_7zip(program, ["l", "-slt", "-ba", os.fspath(archive)])
    if listed.returncode not in (0, 1):
        detail = _tool_text(listed)
        raise FileError(f"7-Zip could not read {archive.name}. {detail}".rstrip())
    entries = _entries_from_7zip_listing(listed.stdout.decode("utf-8", "replace"))
    if not any(not is_dir for _relative, is_dir, _size in entries):
        raise FileError(f"The archive has no files: {archive.name}")
    _report(progress, 0, 1, "Extracting the disc with 7-Zip")
    extracted = _run_7zip(program, ["x", "-y", f"-o{dest}", os.fspath(archive)])
    if extracted.returncode not in (0, 1):
        detail = _tool_text(extracted)
        raise FileError(f"7-Zip could not extract {archive.name}. {detail}".rstrip())
    _audit_7zip_tree(dest, entries)
    _report(progress, 1, 1, "Disc extracted")


def _extract_iso(archive: Path, dest: Path, progress) -> None:
    """Read UDF and hybrid images with 7-Zip. Keep ISO9660 on libarchive."""
    if _image_has_udf(archive):
        _extract_with_7zip(archive, dest, progress)
        return
    try:
        manifest = _libarchive_manifest(archive)
    except FileError as exc:
        if not str(exc).startswith("Cannot read this archive"):
            raise
        try:
            _extract_with_7zip(archive, dest, progress)
        except FileError as fallback_exc:
            raise FileError(f"{exc} {fallback_exc}") from fallback_exc
        return
    if not any(not is_dir for _relative, is_dir in manifest):
        _extract_with_7zip(archive, dest, progress)
        return
    _extract_libarchive(archive, dest, progress)


def extract_archive(archive: Path, dest: Path, *, kind: str, progress=None) -> None:
    archive = archive.resolve()
    dest = dest.resolve()
    if not archive.is_file() or archive.is_symlink():
        raise FileError(f"Select a real archive file: {archive}")
    if paths_overlap(archive, dest):
        raise FileError("Refusing to extract an archive into itself.")
    if any(dest.iterdir()):
        raise FileError(f"The extraction folder is not empty: {dest}")
    try:
        if kind == "zip":
            _extract_zip(archive, dest, progress)
        elif kind == "libarchive":
            _extract_libarchive(archive, dest, progress)
        elif kind == "iso":
            _extract_iso(archive, dest, progress)
        else:
            raise FileError(f"Unsupported archive type: {kind}")
    except Exception:
        try:
            _wipe_children(dest)
        except Exception as cleanup_exc:
            raise FileError(
                f"Extraction failed and the staging folder could not be cleaned: {cleanup_exc}"
            ) from cleanup_exc
        raise


def archive_kind_for_fix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return "zip"
    if suffix == ".7z":
        return "libarchive"
    raise FileError("Choose a .zip or .7z fix archive.")


def create_workspace(scratch: Path) -> Path:
    scratch = Path(scratch).expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="gvprep-", dir=scratch)).resolve()
    (path / MARKER).write_text(str(path) + "\n", encoding="utf-8")
    (path / "media").mkdir()
    (path / "prefix").mkdir()
    (path / "fixes").mkdir()
    return path


def delete_workspace(workspace: Path) -> None:
    workspace = Path(workspace).resolve()
    marker = workspace / MARKER
    try:
        marker_stat = marker.lstat()
    except OSError as exc:
        raise FileError(
            f"Refusing to delete {workspace}. It has no workspace marker from this app."
        ) from exc
    if stat.S_ISLNK(marker_stat.st_mode) or not stat.S_ISREG(marker_stat.st_mode):
        raise FileError(f"Refusing to delete {workspace}. Its marker is not a regular file.")
    recorded = marker.read_text(encoding="utf-8").strip()
    if recorded != str(workspace):
        raise FileError(
            f"Refusing to delete {workspace}. The marker points at a different folder."
        )
    root_dev = workspace.lstat().st_dev
    for dirpath, dirnames, filenames in os.walk(workspace, topdown=False, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            _unlink_owned(workspace, path, root_dev)
        for name in dirnames:
            path = current / name
            if _outside(workspace, path):
                raise FileError(f"Refusing to delete a path outside the workspace: {path}")
            if path.is_symlink():
                path.unlink()
                continue
            if path.lstat().st_dev != root_dev:
                raise FileError(f"Refusing to delete across a different filesystem: {path}")
            path.rmdir()
    workspace.rmdir()


def _unlink_owned(workspace: Path, path: Path, root_dev: int) -> None:
    if _outside(workspace, path):
        raise FileError(f"Refusing to delete a path outside the workspace: {path}")
    st = path.lstat()
    if not stat.S_ISLNK(st.st_mode) and st.st_dev != root_dev:
        raise FileError(f"Refusing to delete across a different filesystem: {path}")
    path.unlink()


def remove_owned_directory(path: Path, parent: Path) -> None:
    path = path.resolve()
    parent = parent.resolve()
    if path == parent or parent not in path.parents:
        raise FileError(f"Refusing to remove a folder outside {parent}.")
    if path.is_symlink():
        path.unlink()
        return
    _wipe_children(path)
    path.rmdir()


def validate_output_location(output: Path, workspace: Path | None, source: Path) -> Path:
    output = Path(output).expanduser().resolve(strict=False)
    source = source.resolve()
    if output == source or output.is_relative_to(source):
        raise FileError("The output folder is inside the source. Choose another output folder.")
    if workspace is not None:
        workspace = workspace.resolve()
        if (
            output == workspace
            or output.is_relative_to(workspace)
            or workspace.is_relative_to(output)
        ):
            raise FileError("The output folder overlaps the workspace. Choose another output folder.")
    return output


def stage_media(
    kind: str,
    source: Path,
    scratch: Path,
    output: Path,
    progress=None,
    media_root: Path | None = None,
) -> StageResult:
    if kind not in {"exe", "directory", "iso"}:
        raise FileError(f"Unknown source type: {kind}")
    source = Path(source).expanduser().resolve()
    scratch = Path(scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    scratch = scratch.resolve()
    copied_root = source if kind == "directory" else (media_root.resolve() if media_root else source.parent)
    if kind == "iso":
        copied_root = source
        if not source.is_file() or source.is_symlink():
            raise FileError("Select an ISO file.")
    elif kind == "exe":
        if media_root is None:
            raise FileError("Choose the folder that contains the installer and its companion files.")
        copied_root = media_root.expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise FileError("Select an installer EXE.")
        try:
            source.relative_to(copied_root)
        except ValueError as exc:
            raise FileError("The installer is outside the selected media folder.") from exc
    else:
        copied_root = source
        if not source.is_dir() or source.is_symlink():
            raise FileError("Select an installer folder.")
    if paths_overlap(copied_root if kind != "iso" else source.parent, scratch) or paths_overlap(source, scratch):
        raise FileError("Scratch storage and the source overlap. Choose a scratch folder outside the source.")
    validate_output_location(output, None, copied_root if kind != "iso" else source.parent)
    workspace = create_workspace(scratch)
    try:
        validate_output_location(output, workspace, copied_root if kind != "iso" else source.parent)
        media = workspace / "media"
        if kind == "iso":
            _report(progress, 0, 1, "Extracting the ISO")
            extract_archive(source, media, kind="iso", progress=progress)
        else:
            copy_tree_contents(copied_root, media, progress)
        installer = None
        if kind == "exe":
            installer = media / source.relative_to(copied_root)
            if not installer.is_file() or installer.is_symlink():
                raise FileError("The installer was not found inside the staged media.")
        protected = copied_root if kind != "iso" else source.parent
        return StageResult(workspace, media, workspace / "prefix", workspace / "fixes", installer, protected)
    except Exception as exc:
        try:
            delete_workspace(workspace)
        except Exception as cleanup_exc:
            raise FileError(
                f"{exc}\nThe incomplete workspace was left at {workspace} because cleanup failed: {cleanup_exc}"
            ) from exc
        raise


def real_exe(path: Path) -> Path:
    """Return a real .exe file. Links are rejected."""
    path = Path(path).expanduser()
    if path.is_symlink():
        raise FileError("Select a real .exe file, not a link.")
    if not path.is_file():
        raise FileError("Select a real .exe file.")
    if path.suffix.lower() != ".exe":
        raise FileError("Select a .exe file.")
    return path.resolve()


def exe_inside(root: Path, selected: Path) -> Path:
    """Return a real .exe that lives inside root."""
    chosen = real_exe(selected)
    try:
        chosen.relative_to(root.resolve())
    except ValueError as exc:
        raise FileError("Select an EXE inside the unpacked ZIP.") from exc
    return chosen


def installer_inside(media: Path, selected: Path) -> Path:
    media = media.resolve()
    selected = Path(selected).expanduser()
    if selected.is_symlink():
        raise FileError("The installer is a link. Select the real file.")
    if not selected.is_file():
        raise FileError("Select an installer file.")
    selected = selected.resolve()
    try:
        selected.relative_to(media)
    except ValueError as exc:
        raise FileError("Select an installer inside the staged media folder.") from exc
    return selected


def validate_game_folder(selected: Path, prefix: Path) -> Path:
    prefix_real = prefix.resolve()
    drive = prefix / "drive_c"
    if not drive.exists() or not drive.is_dir():
        raise FileError("This prefix has no drive_c yet. Finish creating the prefix first.")
    drive = drive.resolve()
    if selected.is_symlink():
        raise FileError("The selected folder is a link.")
    if not selected.is_dir():
        raise FileError("Select the game's folder.")
    game = selected.resolve()
    if drive != game and drive not in game.parents:
        raise FileError("Select a folder inside this session's drive_c.")
    if game in {prefix_real, drive}:
        raise FileError("That folder is too broad. Select the game's own directory, not the prefix or drive_c.")
    relative = game.relative_to(drive)
    if len(relative.parts) == 1 and relative.parts[0].casefold() in BROAD_GAME_ROOTS:
        raise FileError(
            "That folder is too broad. Select the game inside Windows or Program Files, not the whole directory."
        )
    current = drive
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise FileError(f"The path goes through a link: {current}")
    if not any(game.iterdir()):
        raise FileError("The selected folder is empty.")
    return game


def format_listing(root: Path, limit: int = 200) -> str:
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in list(dirnames):
            path = base / name
            text = path.relative_to(root).as_posix() + "/"
            if path.is_symlink():
                text += "  [link]"
                dirnames.remove(name)
            lines.append(text)
            if len(lines) >= limit:
                return "\n".join(lines[:limit]) + "\n..."
        for name in filenames:
            path = base / name
            text = path.relative_to(root).as_posix()
            if path.is_symlink():
                text += "  [link]"
            lines.append(text)
            if len(lines) >= limit:
                return "\n".join(lines[:limit]) + "\n..."
    return "\n".join(lines) if lines else "(empty)"


def child_directories(root: Path) -> list[str]:
    names = ["."]
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in list(dirnames):
            path = Path(dirpath) / name
            if path.is_symlink():
                dirnames.remove(name)
                continue
            names.append(path.relative_to(root).as_posix())
    return names


def find_patcher_programs(root: Path) -> list[str]:
    """Relative paths of real .exe and .bat files. Links are skipped and not followed."""
    root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise FileError(f"Not a directory: {root}")
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not (base / name).is_symlink())
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in {".exe", ".bat"}:
                continue
            found.append(path.relative_to(root).as_posix())
    return found


def stage_patcher_bundle(executable: Path, dest: Path, progress=None) -> Path:
    executable = executable.resolve()
    if executable.is_symlink() or not executable.is_file():
        raise FileError("Select a real patcher file.")
    if executable.suffix.lower() not in {".exe", ".bat"}:
        raise FileError("Select a .exe or .bat patcher. Copying a file does not run it; use Add patcher to run one.")
    parent = executable.parent
    if parent == Path(parent.anchor):
        raise FileError("Select a patcher inside a normal folder, not the filesystem root.")
    dest = dest.resolve()
    copy_tree_contents(parent, dest, progress)
    staged = dest / executable.relative_to(parent)
    if not staged.is_file() or staged.is_symlink():
        raise FileError("The patcher was not found after staging.")
    return staged
