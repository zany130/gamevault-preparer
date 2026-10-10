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
    workdir_kind: str = "game"

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
