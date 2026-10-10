"""Wine prefix commands. This module does not start processes or touch os.environ."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REQUIRED_TOOLS = ("wine", "wineboot", "wineserver", "winepath")

# Characters that stay dangerous inside a quoted cmd.exe command.
_CMD_META = set('&|<>^%!"\r\n()')


class WineError(Exception):
    """A Wine setup or command-line problem the user can act on."""


def missing_tools() -> list[str]:
    return [name for name in REQUIRED_TOOLS if shutil.which(name) is None]


def require_tools() -> None:
    missing = missing_tools()
    if missing:
        raise WineError(
            "Required Wine programs are missing: "
            + ", ".join(missing)
            + ". Install Wine so that wine, wineboot, wineserver, and winepath "
            "are on PATH. This app does not install or bundle Wine."
        )


# A packaged build may point these at bundled libraries. Host programs such as
# Wine and xdg-open must keep using the system libraries.
_BUNDLED_ENV = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "LIBARCHIVE",
)


def host_tool_environment(prefix: Path | None = None) -> dict[str, str]:
    """Copy the process environment for a host program such as Wine or xdg-open.

    The global process environment is left unchanged. Library paths added for a
    bundled copy of this app are removed so the host program is not redirected
    into that bundle.
    """
    env = dict(os.environ)
    for key in _BUNDLED_ENV:
        env.pop(key, None)
    if prefix is not None:
        env["WINEPREFIX"] = os.fspath(Path(prefix))
    return env


def prefixed_environment(prefix: Path) -> dict[str, str]:
    """Return a host environment with WINEPREFIX set for this session."""
    return host_tool_environment(prefix)


def quote_cmd_path(windows_path: str) -> str:
    """Quote a Windows path for cmd.exe, or reject it.

    BAT files have to go through the Windows command parser. Only a quoted
    path is accepted. Metacharacters are rejected instead of escaped.
    """
    if not isinstance(windows_path, str) or windows_path == "":
        raise WineError("The Windows path for the batch file is empty.")
    if windows_path != windows_path.strip() or windows_path.endswith(" ") or windows_path.endswith("."):
        raise WineError("The Windows path has surrounding whitespace or a trailing dot.")
    if any(ord(ch) < 32 or ch in _CMD_META for ch in windows_path):
        raise WineError(
            "The batch path contains command characters (& | < > ^ % ! quotes, "
            "parentheses, or control characters). Rename the patcher so the path "
            "is plain, then try again."
        )
    return '"' + windows_path + '"'


def bat_command_arguments(windows_path: str) -> list[str]:
    """Arguments for Wine's cmd.exe that run one quoted batch path.

    ``/s`` makes cmd strip the surrounding quotes in a defined way. The path
    itself is one argument; nothing here is concatenated for a host shell.
    """
    return ["cmd.exe", "/s", "/c", quote_cmd_path(windows_path)]


def parse_winepath_output(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]
    candidates = [line for line in lines if ":\\" in line or line.startswith("\\\\")]
    if not candidates:
        raise WineError("winepath did not return a Windows path.")
    return candidates[-1]
