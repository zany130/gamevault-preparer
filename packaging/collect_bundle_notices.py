#!/usr/bin/env python3
"""Check the AppImage's bundled files against their license notices.

The release image is built on Ubuntu. When dpkg is available, this copies
each system library's copyright file into the image and stops if a bundled
file cannot be attributed. A build without dpkg keeps the Ubuntu audit
snapshot already stored under third-party/system/ and records that the
snapshot was not collected from that machine.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
QT_LICENSE = "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"
QT_NOTICE = "qt/LGPL-3.0.txt, qt/GPL-3.0.txt, qt/GPL-2.0.txt"
ICU73_NOTICE = "system/copyright/libicu74/copyright"
PYTHON_NOTICE = "python/LICENSE.txt"
PYINSTALLER_NOTICE = "pyinstaller/COPYING.txt"


class NoticeError(Exception):
    """The bundle is missing a notice required to redistribute it."""


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == ELF_MAGIC
    except OSError:
        return False


def is_script(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def load_origins(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    origins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, origin = line.split("\t", 1)
        origins[name] = origin
    return origins


def index_files(roots: list[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if not (path.name.startswith("lib") or ".so" in path.name):
                continue
            found.setdefault(path.name, []).append(path)
    return found


def _same_file(bundled: Path, origin: Path) -> bool:
    try:
        if bundled.stat().st_size != origin.stat().st_size:
            return False
    except OSError:
        return False
    return hashlib.sha256(bundled.read_bytes()).digest() == hashlib.sha256(origin.read_bytes()).digest()


def matching_origin(
    bundled: Path,
    origins: dict[str, str],
    indexed: dict[str, list[Path]],
    system_library,
) -> Path | None:
    hinted = origins.get(bundled.name)
    if hinted:
        candidate = Path(hinted)
        if candidate.is_file() and _same_file(bundled, candidate):
            return candidate
    matches = [path for path in indexed.get(bundled.name, []) if _same_file(bundled, path)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[0]
    system = system_library(bundled.name)
    if system:
        candidate = Path(system)
        if candidate.is_file() and _same_file(bundled, candidate):
            return candidate
    return None


def notice_for_private_file(bundled: Path, origin: Path) -> tuple[str, str]:
    name = bundled.name
    parts = set(bundled.parts) | set(origin.parts)
    if name.startswith("libicu") and ".so.73" in name:
        return (f"ICU 73 bundled with Qt ({origin})", ICU73_NOTICE)
    if (
        name.startswith(("libQt6", "libpyside6", "libshiboken6"))
        or "PySide6" in parts
        or "shiboken6" in parts
    ):
        return (f"Qt/PySide6/shiboken6 ({origin})", QT_NOTICE)
    return (f"CPython ({origin})", PYTHON_NOTICE)


def dpkg_owner(path: Path) -> str:
    tried = [os.fspath(path)]
    resolved = os.path.realpath(path)
    if resolved not in tried:
        tried.append(resolved)
    last_error = ""
    for candidate in tried:
        result = subprocess.run(
            ["dpkg-query", "-S", candidate],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            owner = result.stdout.splitlines()[0].split(": ", 1)[0]
            return owner.split(":", 1)[0]
        last_error = result.stderr.strip() or result.stdout.strip()
    raise NoticeError(f"dpkg does not own {path}. {last_error}")


def copyright_file(package: str) -> Path:
    doc = Path("/usr/share/doc") / package
    if doc.is_symlink() or doc.is_dir():
        resolved = doc.resolve()
        candidate = resolved / "copyright"
        if candidate.is_file():
            return candidate
    raise NoticeError(
        f"No copyright file for {package}. Expected /usr/share/doc/{package}/copyright."
    )


def package_version(package: str) -> str:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Version}", package],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def ldconfig_library(name: str) -> str | None:
    result = subprocess.run(["ldconfig", "-p"], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name} "):
            return stripped.rsplit(" ", 1)[-1]
    return None


def license_expression(metadata: str) -> str:
    for line in metadata.splitlines():
        if line.startswith("License:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("License-Expression:"):
            return line.split(":", 1)[1].strip()
    raise NoticeError("PySide6 or shiboken6 metadata has no license expression.")


def require_wheel_metadata(site_packages: Path, expected_version: str) -> None:
    patterns = ("pyside6-*.dist-info", "shiboken6-*.dist-info", "pyside6_essentials-*.dist-info")
    found = False
    for pattern in patterns:
        for dist in sorted(site_packages.glob(pattern)):
            metadata = dist / "METADATA"
            if not metadata.is_file():
                continue
            text = metadata.read_text(encoding="utf-8", errors="replace")
            version = ""
            for line in text.splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    break
            if version and version != expected_version:
                raise NoticeError(
                    f"{dist.name} is {version}, but packaging/third-party/qt/VERSION "
                    f"and NOTICES.md describe {expected_version}."
                )
            expression = license_expression(text)
            if expression != QT_LICENSE:
                raise NoticeError(
                    f"{dist.name} license is {expression!r}, not {QT_LICENSE!r}."
                )
            found = True
    if not found:
        raise NoticeError(f"No PySide6 metadata under {site_packages}.")


def require_qt_string(internal: Path, version: str) -> None:
    candidates = (
        internal / "libQt6Core.so.6",
        internal / "PySide6" / "Qt" / "lib" / "libQt6Core.so.6",
    )
    token = f"Qt {version}".encode()
    for candidate in candidates:
        if candidate.is_file() and token in candidate.read_bytes():
            return
    present = [str(path) for path in candidates if path.is_file()]
    raise NoticeError(
        f"libQt6Core does not contain {token.decode()!r}. Found: {present or 'no libQt6Core.so.6'}."
    )


def require_7zip_elf(internal: Path) -> Path:
    found: list[Path] = []
    for name in ("7zz", "7z"):
        candidate = internal / name
        if not candidate.is_file():
            continue
        found.append(candidate)
        if is_script(candidate) or not is_elf(candidate):
            raise NoticeError(
                f"{candidate} is not the 7-Zip executable. Ubuntu's /usr/bin/7z is a "
                "wrapper; copy the ELF it executes (usually /usr/lib/7zip/7z)."
            )
    if not found:
        raise NoticeError("The AppImage does not contain a 7z or 7zz executable.")
    return found[0]


def iter_elfs(internal: Path) -> list[Path]:
    files: list[Path] = []
    for path in internal.rglob("*"):
        if path.is_file() and not path.is_symlink() and is_elf(path):
            files.append(path)
    return files


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def is_interpreter_file(origin: Path, python_prefix: Path | None) -> bool:
    """CPython's own library and standard-library extensions, not every file under /usr."""
    if python_prefix is None or not _under(origin, python_prefix):
        return False
    if origin.name.startswith("libpython"):
        return True
    return any(part.startswith("python3") for part in origin.parts)


def interpreter_search_roots(python_prefix: Path) -> list[Path]:
    roots: list[Path] = []
    for libname in ("lib", "lib64"):
        lib = python_prefix / libname
        if lib.is_dir():
            roots.extend(path for path in lib.glob("python3*") if path.is_dir())
    return roots


def index_libpython(python_prefix: Path, indexed: dict[str, list[Path]]) -> None:
    candidates = list(python_prefix.glob("lib*/libpython*.so*"))
    candidates.extend(python_prefix.glob("libpython*.so*"))
    for path in candidates:
        if path.is_symlink():
            path = path.resolve()
        if path.is_file():
            indexed.setdefault(path.name, []).append(path)


def collect_notices(
    internal: Path,
    notices: Path,
    origins: dict[str, str],
    search_roots: list[Path],
    *,
    use_dpkg: bool,
    owner_of=dpkg_owner,
    copyright_of=copyright_file,
    version_of=package_version,
    system_library=ldconfig_library,
    common_licenses: Path | None = None,
    private_roots: list[Path] | None = None,
    python_prefix: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Return rows of (relative path, package, notice) and copy system copyrights."""
    seven = require_7zip_elf(internal)
    indexed = index_files(search_roots)
    if python_prefix is not None:
        index_libpython(python_prefix, indexed)
    private = search_roots if private_roots is None else private_roots
    system_dir = notices / "system"
    rows: list[tuple[str, str, str]] = []
    copied: set[Path] = set()
    if use_dpkg:
        destination = system_dir / "copyright"
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        licenses = common_licenses or Path("/usr/share/common-licenses")
        if not licenses.is_dir():
            raise NoticeError(f"Missing {licenses}, which the Debian copyright files reference.")
        target = system_dir / "common-licenses"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(licenses, target, symlinks=True)

    def add_system(bundled: Path, origin: Path) -> None:
        if not use_dpkg:
            rows.append((str(bundled.relative_to(internal)), f"system file {origin}", "system/ (Ubuntu audit snapshot)"))
            return
        package = owner_of(origin)
        source = copyright_of(package)
        notice_package = source.parent.name
        dest = system_dir / "copyright" / notice_package / "copyright"
        if source.resolve() not in copied:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            copied.add(source.resolve())
        version = version_of(package)
        label = f"{package} {version}".strip()
        if notice_package != package:
            label = f"{label} (copyright in {notice_package})"
        rows.append((str(bundled.relative_to(internal)), label, f"system/copyright/{notice_package}/copyright"))

    for bundled in iter_elfs(internal):
        if bundled == seven:
            hinted = origins.get(bundled.name)
            if use_dpkg and not hinted:
                raise NoticeError(f"No system origin recorded for {bundled.name}.")
            add_system(bundled, Path(hinted) if hinted else bundled)
            continue
        origin = matching_origin(bundled, origins, indexed, system_library)
        if origin is not None and (
            any(_under(origin, root) for root in private)
            or is_interpreter_file(origin, python_prefix)
        ):
            package, notice = notice_for_private_file(bundled, origin)
            rows.append((str(bundled.relative_to(internal)), package, notice))
            continue
        if origin is not None:
            add_system(bundled, origin)
            continue
        if use_dpkg:
            raise NoticeError(f"No source package for bundled file {bundled.relative_to(internal)}.")
        rows.append((str(bundled.relative_to(internal)), "unattributed", ""))
    return rows


def write_components(notices: Path, rows: list[tuple[str, str, str]], note: str) -> None:
    lines = [note.rstrip(), ""]
    for row in rows:
        lines.append("\t".join(row))
    lines.append("")
    (notices / "system").mkdir(parents=True, exist_ok=True)
    (notices / "system" / "COMPONENTS.txt").write_text("\n".join(lines), encoding="utf-8")


def python_license_path(python_prefix: Path) -> Path | None:
    candidates = [python_prefix / "LICENSE.txt", python_prefix / "lib" / "LICENSE.txt"]
    candidates.extend(python_prefix.glob("lib*/python*/LICENSE.txt"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def copy_python_license(notices: Path, python_prefix: Path) -> None:
    source = python_license_path(python_prefix)
    if source is None:
        raise NoticeError(f"CPython license text was not found under {python_prefix}.")
    destination = notices / "python"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination / "LICENSE.txt")


def site_packages(venv: Path) -> Path:
    matches = sorted(venv.glob("lib/python*/site-packages"))
    if not matches:
        raise NoticeError(f"No site-packages under {venv}.")
    return matches[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal", type=Path, required=True)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--origins", type=Path)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--python-prefix", type=Path, required=True)
    args = parser.parse_args(argv)

    expected = (args.notices / "qt" / "VERSION").read_text(encoding="utf-8").strip()
    require_wheel_metadata(site_packages(args.venv), expected)
    require_qt_string(args.internal, expected)
    copy_python_license(args.notices, args.python_prefix)
    use_dpkg = shutil.which("dpkg-query") is not None
    site = site_packages(args.venv)
    rows = collect_notices(
        args.internal,
        args.notices,
        load_origins(args.origins),
        [site, *interpreter_search_roots(args.python_prefix)],
        use_dpkg=use_dpkg,
        private_roots=[site],
        python_prefix=args.python_prefix,
    )
    if use_dpkg:
        note = (
            "# Components bundled in this AppImage.\n"
            "# Collected from dpkg on the build machine.\n"
            "# Columns: bundled file, package, copyright file under third-party/."
        )
    else:
        note = (
            "# This image was not built on a dpkg system.\n"
            "# system/copyright and the packaged COMPONENTS snapshot describe the\n"
            "# Ubuntu 24.04 audit. They were not collected from this machine.\n"
            "# Columns: bundled file, attribution, copyright file under third-party/."
        )
        (args.notices / "system" / "BUILD.txt").write_text(
            "This AppImage was built on a machine without dpkg. The copyright "
            "files under system/copyright document the Ubuntu 24.04 release "
            "audit and were not collected from the libraries on this machine.\n",
            encoding="utf-8",
        )
    write_components(args.notices, rows, note)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NoticeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
