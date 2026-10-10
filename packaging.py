"""GameVault archive names and ZIP export."""

from __future__ import annotations

import os
import stat
import zipfile
from datetime import datetime
from pathlib import Path

from files import FileError, explain_os_error, validate_output_location

_FORBIDDEN = set('<>:"/\\|?*()')
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update(f"COM{i}" for i in range(1, 10))
_RESERVED.update(f"LPT{i}" for i in range(1, 10))


class ArchiveNameError(Exception):
    """The user needs to change a name field."""


def _reserved(value: str) -> bool:
    return value.split(".", 1)[0].upper() in _RESERVED


def _validate_field(value: str, label: str) -> None:
    if any(ord(ch) < 32 or ch in _FORBIDDEN for ch in value):
        raise ArchiveNameError(
            f"{label} contains a character that cannot be used in the archive name."
        )
    if value.endswith(" ") or value.endswith("."):
        raise ArchiveNameError(f"{label} cannot end with a space or a dot.")
    if _reserved(value):
        raise ArchiveNameError(f"{label} uses a reserved file name.")


def _optional(value: str, label: str) -> str | None:
    if value == "":
        return None
    if value != value.strip():
        raise ArchiveNameError(f"{label} cannot start or end with spaces.")
    _validate_field(value, label)
    return value


def build_archive_name(title: str, version: str = "", year: str = "") -> str:
    """Build ``Title (v1.2) (W_P) (2004).zip``, omitting empty optional fields."""
    if title == "":
        raise ArchiveNameError("Enter a title.")
    if title != title.strip():
        raise ArchiveNameError("Title cannot start or end with spaces.")
    _validate_field(title, "Title")

    parts = [title]
    version_text = _optional(version, "Version")
    if version_text is not None:
        if version_text[0] in {"v", "V"}:
            version_text = version_text[1:]
        if version_text == "":
            raise ArchiveNameError("Enter the version numbers after the v, or leave version empty.")
        _validate_field(version_text, "Version")
        parts.append(f"(v{version_text})")
    parts.append("(W_P)")

    if year == "":
        year_text = None
    elif len(year) != 4 or not year.isdigit():
        raise ArchiveNameError("Year must be four digits, such as 2004.")
    else:
        year_text = year
    if year_text is not None:
        parts.append(f"({year_text})")

    filename = " ".join(parts) + ".zip"
    stem = filename[:-4]
    if stem.endswith(" ") or stem.endswith(".") or _reserved(stem):
        raise ArchiveNameError("That title produces a file name Windows will not accept.")
    return filename


def _zip_date(timestamp: float) -> tuple[int, int, int, int, int, int]:
    try:
        when = datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        when = datetime(1980, 1, 1)
    if when.year < 1980:
        when = datetime(1980, 1, 1)
    return (when.year, when.month, when.day, when.hour, when.minute, when.second)


def _collect(game_dir: Path) -> list[tuple[str, Path | None]]:
    entries: list[tuple[str, Path | None]] = []
    for dirpath, dirnames, filenames in os.walk(game_dir, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        for name in list(dirnames):
            path = base / name
            relative = path.relative_to(game_dir).as_posix()
            try:
                st = path.lstat()
            except OSError as exc:
                raise explain_os_error(exc, "Reading the game folder", path) from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FileError(f"Refusing to package a link or special folder: {relative}")
            entries.append((relative + "/", path))
        for name in filenames:
            path = base / name
            relative = path.relative_to(game_dir).as_posix()
            try:
                st = path.lstat()
            except OSError as exc:
                raise explain_os_error(exc, "Reading the game folder", path) from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise FileError(f"Refusing to package a link or special file: {relative}")
            entries.append((relative, path))
    return entries


def create_game_zip(
    game_dir: Path,
    output_dir: Path,
    filename: str,
    *,
    workspace: Path,
    source: Path,
    progress=None,
) -> Path:
    if Path(filename).name != filename or not filename.endswith(".zip"):
        raise ArchiveNameError("The archive name is not a plain .zip file name.")
    output = validate_output_location(output_dir, workspace, source)
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise explain_os_error(exc, "Creating the output folder", output) from exc
    output = validate_output_location(output, workspace, source)
    game_dir = game_dir.resolve()
    if not any(game_dir.iterdir()):
        raise FileError("The selected game folder is empty.")

    entries = _collect(game_dir)
    final = output / filename
    partial = output / f"{filename}.partial"
    if final.exists() or final.is_symlink():
        raise FileError(
            f"{final.name} already exists in the output folder. Choose a different title or folder. "
            "The existing file was left unchanged."
        )
    if partial.exists() or partial.is_symlink():
        raise FileError(
            f"A partial archive is already at {partial.name}. Remove it or choose a different title."
        )

    total = len(entries) or 1
    fd = -1
    linked = False
    try:
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "wb") as raw:
            fd = -1
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for index, (relative, path) in enumerate(entries, start=1):
                    info = zipfile.ZipInfo(relative)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    if relative.endswith("/"):
                        info.date_time = _zip_date(path.lstat().st_mtime if path else 0)
                        archive.writestr(info, b"")
                    else:
                        info.date_time = _zip_date(path.lstat().st_mtime)
                        src_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(src_fd, "rb") as src, archive.open(info, "w") as dest:
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                dest.write(chunk)
                    if progress is not None:
                        progress(int(index * 100 / total), relative)
            raw.flush()
            os.fsync(raw.fileno())
        try:
            os.link(partial, final)
        except FileExistsError as exc:
            raise FileError(
                f"A file named {final.name} appeared while the archive was being written. "
                "The existing file was left unchanged."
            ) from exc
        linked = True
        partial.unlink(missing_ok=True)
    except Exception as exc:
        if fd >= 0:
            os.close(fd)
        if not linked:
            partial.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise explain_os_error(exc, "Creating the archive") from exc
        raise
    return final
