"""Notes written beside a GameVault ZIP for a person to read.

The ZIP stays the game folder only. These helpers compare two prefix
snapshots and produce a registry file and a list of setup programs and libraries. Nothing
here imports a registry file, launches a program, or builds a winetricks
command. Verify ZIP does not use the results.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

SOFTWARE_HIVES = (
    ("hklm", r"HKEY_LOCAL_MACHINE\Software"),
    ("hkcu", r"HKEY_CURRENT_USER\Software"),
)
SETUP_SUFFIXES = {".exe", ".msi", ".bat", ".dll"}
SETUP_HEADER = (
    "Setup programs and libraries added outside the game folder.\n"
    "Each line is a path relative to the Wine prefix.\n"
    "This is a note for a person. It is not a winetricks script.\n"
    "Paths in the .reg file may still point at the original install.\n"
)
# Wine's own environment and device settings. Other Software keys, including
# unknown keys under Software\Wine, stay in the note.
_WINE_NOISE = (
    "wine",
    "wine\\drivers",
    "wine\\x11 driver",
    "wine\\mac driver",
    "wine\\wayland driver",
    "wine\\direct3d",
    "wine\\directsound",
    "wine\\directinput",
    "wine\\network",
    "wine\\fonts",
    "wine\\explorer",
    "wine\\fileopenassociations",
    "wine\\dlloverrides",
    "wine\\debug",
    "wine\\licenseinformation",
    "wine\\ports",
    "wine\\mshtml",
    "wine\\winedbg",
    "wine\\version",
)
NONE_FOUND = "No extra setup programs or libraries were found outside the game folder.\n"


class _Key:
    def __init__(self, display: str) -> None:
        self.display = display
        self.values: dict[str, str] = {}
        self.order: list[str] = []


class RegistrySnapshot:
    """Software keys in export order, with value names compared case-insensitively."""

    def __init__(self) -> None:
        self._keys: dict[str, _Key] = {}
        self._order: list[str] = []

    def add_key(self, display: str) -> _Key:
        norm = _norm(display)
        key = self._keys.get(norm)
        if key is None:
            key = _Key(display)
            self._keys[norm] = key
            self._order.append(norm)
        return key

    def add_value(self, display_key: str, name: str, raw: str) -> None:
        key = self.add_key(display_key)
        norm = _norm(name)
        if norm not in key.values:
            key.order.append(norm)
        key.values[norm] = raw

    def add_snapshot(self, other: RegistrySnapshot) -> None:
        for norm in other._order:
            key = other._keys[norm]
            dest = self.add_key(key.display)
            for value_norm in key.order:
                if value_norm not in dest.values:
                    dest.order.append(value_norm)
                dest.values[value_norm] = key.values[value_norm]

    def has_key(self, display: str) -> bool:
        return _norm(display) in self._keys

    def value_names(self, display: str) -> set[str]:
        key = self._keys.get(_norm(display))
        if key is None:
            return set()
        return set(key.values)


def list_prefix_files(prefix: Path) -> set[str]:
    """Relative paths of regular files in the prefix. Links are not followed."""
    root = prefix.resolve()
    if not root.is_dir():
        raise OSError(f"{root} is not a directory")
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            found.add(path.resolve().relative_to(root).as_posix())
    return found


def setup_program_names(before: set[str], after: set[str], game_relative: str) -> list[str]:
    """Prefix-relative paths of new setup programs and libraries outside the game folder."""
    added = [
        rel
        for rel in after - before
        if Path(rel).suffix.casefold() in SETUP_SUFFIXES and not _is_under(rel, game_relative)
    ]
    added.sort(key=str.casefold)
    return added


def setup_note(names: list[str]) -> str:
    body = "".join(f"{name}\n" for name in names) if names else NONE_FOUND
    return SETUP_HEADER + "\n" + body


def parse_registry(text: str) -> RegistrySnapshot:
    """Parse a Registry Editor export into the keys and values it contains."""
    snapshot = RegistrySnapshot()
    current: str | None = None
    lines = text.lstrip("\ufeff").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("Windows Registry Editor") or stripped == "REGEDIT4":
            index += 1
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            body = stripped[1:-1]
            current = None if body.startswith("-") or not _is_software_key(body) else body
            if current is not None:
                snapshot.add_key(current)
            index += 1
            continue
        parsed = _parse_value_line(stripped)
        if parsed is None or current is None:
            index += 1
            continue
        name, data = parsed
        block = [line]
        while _line_continues(block[-1]) and index + 1 < len(lines):
            index += 1
            block.append(lines[index])
        if data != "-":
            snapshot.add_value(current, name, "\n".join(block))
        index += 1
    return snapshot


def registry_diff_text(before: RegistrySnapshot, after: RegistrySnapshot) -> str:
    """Registry Editor text for Software values present after and absent before."""
    lines = ["Windows Registry Editor Version 5.00", ""]
    wrote = False
    for norm in after._order:
        key = after._keys[norm]
        if not _is_software_key(key.display) or _is_wine_noise(key.display):
            continue
        prior = before._keys.get(norm)
        chosen: list[str] = []
        for value_norm in key.order:
            if prior is not None and value_norm in prior.values:
                continue
            chosen.append(key.values[value_norm])
        if prior is not None and not chosen:
            continue
        wrote = True
        lines.append(f"[{key.display}]")
        lines.extend(chosen)
        lines.append("")
    if not wrote:
        lines.append("; No Software values were added.")
        lines.append("")
    return "\n".join(lines) + "\n"


def read_registry_export(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig")
    elif b"\x00" in data[:4]:
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8")
    return text.lstrip("\ufeff")


def encode_registry(text: str) -> bytes:
    body = text.replace("\r\n", "\n").replace("\n", "\r\n")
    if not body.endswith("\r\n"):
        body += "\r\n"
    return b"\xff\xfe" + body.encode("utf-16-le")


def sidecar_paths(archive: Path) -> tuple[Path, Path]:
    """The registry note and the setup note that sit beside this ZIP."""
    return archive.with_name(archive.stem + ".reg"), archive.with_name(archive.stem + ".setup.txt")


def write_if_absent(path: Path, data: bytes) -> bool:
    """Write data when path is absent. An existing file is left unchanged."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return True


def prefix_relative(prefix: Path, path: Path) -> str:
    return path.resolve().relative_to(prefix.resolve()).as_posix()


def _norm(value: str) -> str:
    return value.casefold()


def _is_software_key(key: str) -> bool:
    folded = key.casefold()
    return folded.startswith(r"hkey_local_machine\software") or folded.startswith(r"hkey_current_user\software")


def _is_wine_noise(key: str) -> bool:
    folded = key.casefold()
    rest = ""
    for prefix in (r"hkey_local_machine\software", r"hkey_current_user\software"):
        if folded == prefix:
            return False
        if folded.startswith(prefix + "\\"):
            rest = folded[len(prefix) + 1 :]
            break
    else:
        return False
    if rest == "wow6432node":
        return False
    if rest.startswith("wow6432node\\"):
        rest = rest[len("wow6432node\\") :]
    for item in _WINE_NOISE:
        if item == "wine":
            if rest == "wine":
                return True
            continue
        if rest == item or rest.startswith(item + "\\"):
            return True
    return False


def _is_under(relative: str, root: str) -> bool:
    folder = root.strip("/").casefold()
    if not folder:
        return False
    folded = relative.casefold()
    return folded == folder or folded.startswith(folder + "/")


def _parse_value_line(line: str) -> tuple[str, str] | None:
    if line.startswith("@="):
        return "@", line[2:]
    if not line.startswith('"'):
        return None
    chars: list[str] = []
    index = 1
    while index < len(line):
        char = line[index]
        if char == "\\":
            if index + 1 >= len(line):
                return None
            chars.append(line[index + 1])
            index += 2
            continue
        if char == '"':
            if index + 1 >= len(line) or line[index + 1] != "=":
                return None
            return "".join(chars), line[index + 2 :]
        chars.append(char)
        index += 1
    return None


def _line_continues(line: str) -> bool:
    count = 0
    for char in reversed(line.rstrip("\r\n")):
        if char != "\\":
            break
        count += 1
    return count % 2 == 1
