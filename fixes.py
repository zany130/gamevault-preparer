"""Ordered copy and patcher fixes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from files import CopyPlan, FileError, plan_loose_files, plan_tree


@dataclass
class Fix:
    kind: str
    label: str
    enabled: bool = True
    applied: bool = False
    source_root: Path | None = None
    folder_name: str | None = None
    loose_files: tuple[Path, ...] = ()
    executable: Path | None = None
    place_in_game: bool = False

    def summary(self) -> str:
        if self.applied:
            state = "applied"
        elif self.enabled:
            state = "pending"
        else:
            state = "skipped"
        return f"{self.label} [{state}]"


def plan_for_fix(fix: Fix, game_dir: Path) -> CopyPlan:
    if fix.kind != "copy":
        raise FileError("Patchers do not have a copy preview.")
    if fix.loose_files:
        return plan_loose_files(list(fix.loose_files), game_dir)
    if fix.source_root is None:
        raise FileError("This copy fix has no source folder.")
    return plan_tree(fix.source_root, game_dir, folder_name=fix.folder_name)


def placed_patcher_copy_plan(fix: Fix, game_dir: Path) -> CopyPlan:
    """Copy a staged patcher folder's contents into the game folder."""
    if not fix.place_in_game or fix.source_root is None:
        raise FileError("This patcher is not copied into the game folder.")
    return plan_tree(fix.source_root, game_dir)


def _relative_inside(relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise FileError("The patcher path is not inside the game folder.")
    return relative


def patcher_launch_target(fix: Fix, game_dir: Path) -> tuple[Path, Path]:
    """Return the program to start and the directory it should start in."""
    if fix.executable is None:
        raise FileError("The patcher file is missing.")
    if fix.place_in_game:
        relative = _relative_inside(fix.executable)
        game_resolved = game_dir.resolve()
        candidate = game_resolved / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise FileError(
                "The program is not in the game folder yet. "
                "Apply the copy that places it there before this patcher."
            )
        launched = candidate.resolve()
        if launched != game_resolved and game_resolved not in launched.parents:
            raise FileError("The patcher path is not inside the game folder.")
        return launched, game_resolved
    executable = fix.executable
    if executable.is_symlink() or not executable.is_file():
        raise FileError("The patcher file is missing.")
    return executable.resolve(), executable.resolve().parent


def fixes_for_archive_choice(copy_fix: Fix, program: str | None) -> list[Fix]:
    """Add a run step only when the user picked a program from the archive."""
    if not program:
        return [copy_fix]
    relative = _relative_inside(Path(program))
    if relative.suffix.lower() not in {".exe", ".bat"}:
        raise FileError("Select a .exe or .bat patcher.")
    label = f"Run {relative.as_posix()} from the game folder"
    return [
        copy_fix,
        Fix(
            "patcher",
            label,
            executable=relative,
            place_in_game=True,
        ),
    ]


def preview_text(plan: CopyPlan, limit: int = 40) -> str:
    lines = [
        f"{len(plan.new_files)} new file(s), {len(plan.overwrites)} overwrite(s), "
        f"{len(plan.directories)} folder(s)."
    ]
    for item in plan.files[:limit]:
        suffix = " (overwrite)" if item.overwrite else ""
        lines.append(f"{item.relative}{suffix}")
    extra = len(plan.files) - limit
    if extra > 0:
        lines.append(f"... {extra} more")
    return "\n".join(lines)
