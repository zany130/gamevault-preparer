"""The single window: stage, install, fix, and export."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from files import (
    FileError,
    archive_kind_for_fix,
    child_directories,
    extract_archive,
    format_listing,
    installer_inside,
    remove_owned_directory,
    stage_media,
    stage_patcher_bundle,
    validate_game_folder,
    delete_workspace,
    apply_copy_plan,
)
from fixes import Fix, plan_for_fix, preview_text
from packaging import ArchiveNameError, build_archive_name, create_game_zip
from wine import (
    WineError,
    bat_command_arguments,
    parse_winepath_output,
    host_tool_environment,
    prefixed_environment,
    require_tools,
)
from workers import FunctionWorker


def default_scratch() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "gamevault-preparer"


def default_output() -> Path:
    return Path.home() / "GameVault"


def saved_source_folder() -> str:
    value = QSettings("gamevault-preparer", "gamevault-preparer").value("sourceFolder", "")
    return str(value or "")


def file_manager_arguments(directory: Path) -> list[str]:
    """One path argument for xdg-open, with no shell command string."""
    return [os.fspath(directory)]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GameVault Archive Preparer")
        self.resize(920, 860)

        self.workspace: Path | None = None
        self.media_dir: Path | None = None
        self.prefix_dir: Path | None = None
        self.fixes_dir: Path | None = None
        self.protected_source: Path | None = None
        self.installer: Path | None = None
        self.game_dir: Path | None = None
        self.source_path: Path | None = None
        self.media_root: Path | None = None
        self.fixes: list[Fix] = []

        self.busy = False
        self.mode: str | None = None
        self.wine_idle = True
        self.prefix_initialized = False
        self.install_launched = False
        self.install_ready = False
        self.finish_requested = False
        self._stopped = False
        self._stop_settled = False
        self._killing = False
        self._start_failed = False
        self._name_error = "Enter a title."
        self._title_custom = False
        self.manual_continued = False
        self._fix_queue: list[int] = []
        self._fix_pos = 0
        self._fix_queue_active = False
        self._idle_kind = ""
        self._patcher_cwd: Path | None = None
        self._program = ""

        self.worker: FunctionWorker | None = None
        self._workers: list[FunctionWorker] = []
        self._cleanup_dir: Path | None = None
        self._captured = ""
        self._fix_seq = 0
        self._archive_queue: list[Path] = []
        self._file_managers: list[QProcess] = []
        self.process: QProcess | None = None
        self.wait_process: QProcess | None = None
        self.killer: QProcess | None = None
        self._process_done = None

        self._build_ui()
        self.refresh_name()
        self.refresh_buttons()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        self.workspace_label = QLabel("Workspace: not created yet")
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.workspace_label.setWordWrap(True)
        self.status_label = QLabel("Choose an installer EXE, an installer folder, or an ISO.")
        self.status_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        outer.addWidget(self.workspace_label)
        outer.addWidget(self.status_label)
        outer.addWidget(self.progress)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.addWidget(self._build_source())
        form_layout.addWidget(self._build_install())
        form_layout.addWidget(self._build_game())
        form_layout.addWidget(self._build_fixes())
        form_layout.addWidget(self._build_manual())
        form_layout.addWidget(self._build_export())
        form_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        outer.addWidget(scroll, 1)

        self.log_box = QGroupBox("Log")
        self.log_box.setCheckable(True)
        self.log_box.setChecked(True)
        log_layout = QVBoxLayout(self.log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_view.setPlaceholderText("Details from Wine and file operations appear here.")
        log_layout.addWidget(self.log_view)
        self.log_box.toggled.connect(self.log_view.setVisible)
        outer.addWidget(self.log_box)

    def _build_source(self) -> QGroupBox:
        box = QGroupBox("Source")
        layout = QVBoxLayout(box)

        scratch_row = QHBoxLayout()
        self.scratch_edit = QLineEdit(str(default_scratch()))
        self.scratch_btn = QPushButton("Browse…")
        self.scratch_btn.clicked.connect(lambda: self._browse_dir(self.scratch_edit, "Scratch storage"))
        scratch_row.addWidget(self.scratch_edit, 1)
        scratch_row.addWidget(self.scratch_btn)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(default_output()))
        self.output_btn = QPushButton("Browse…")
        self.output_btn.clicked.connect(lambda: self._browse_dir(self.output_edit, "Output folder"))
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_btn)

        source_folder_row = QHBoxLayout()
        self.source_folder_edit = QLineEdit(saved_source_folder())
        self.source_folder_edit.setPlaceholderText("Optional. Select source starts in this folder.")
        self.source_folder_edit.editingFinished.connect(self._save_source_folder)
        self.source_folder_btn = QPushButton("Browse…")
        self.source_folder_btn.clicked.connect(self._browse_source_folder)
        source_folder_row.addWidget(self.source_folder_edit, 1)
        source_folder_row.addWidget(self.source_folder_btn)

        form = QFormLayout()
        form.addRow("Source folder", source_folder_row)
        form.addRow("Scratch storage", scratch_row)
        form.addRow("Output folder", output_row)
        layout.addLayout(form)

        radios = QHBoxLayout()
        self.exe_radio = QRadioButton("Installer EXE")
        self.dir_radio = QRadioButton("Installer folder")
        self.iso_radio = QRadioButton("ISO")
        self.exe_radio.setChecked(True)
        self.source_radios = QButtonGroup(self)
        for index, radio in enumerate((self.exe_radio, self.dir_radio, self.iso_radio)):
            self.source_radios.addButton(radio, index)
            radios.addWidget(radio)
        radios.addStretch(1)
        self.source_radios.idClicked.connect(self._source_kind_changed)
        layout.addLayout(radios)

        self.source_label = QLabel("Nothing selected yet.")
        self.source_label.setWordWrap(True)
        self.media_label = QLabel("The folder to copy will appear here before anything is copied.")
        self.media_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        layout.addWidget(self.media_label)

        row = QHBoxLayout()
        self.choose_btn = QPushButton("Select source")
        self.media_btn = QPushButton("Change folder to copy")
        self.stage_btn = QPushButton("Stage media")
        self.installer_btn = QPushButton("Select installer")
        self.choose_btn.clicked.connect(self.choose_source)
        self.media_btn.clicked.connect(self.change_media_folder)
        self.stage_btn.clicked.connect(self.stage)
        self.installer_btn.clicked.connect(self.select_installer)
        for button in (self.choose_btn, self.media_btn, self.stage_btn, self.installer_btn):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_install(self) -> QGroupBox:
        box = QGroupBox("Install")
        layout = QVBoxLayout(box)
        layout.addWidget(QLabel("Suggested install location: C:\\Games\\MyGame"))
        note = QLabel(
            "Finish the installer, uncheck Launch game if offered, then return here. "
            "The prefix keeps this install separate from your normal Wine setup. "
            "It is not a security sandbox, so run only installers and fixes you trust."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.installer_label = QLabel("Installer: not selected")
        self.installer_label.setWordWrap(True)
        self.installer_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.installer_label)
        row = QHBoxLayout()
        self.launch_btn = QPushButton("Initialize prefix and launch installer")
        self.finished_btn = QPushButton("Installation finished")
        self.stop_btn = QPushButton("Stop this session's Wine processes")
        self.launch_btn.clicked.connect(self.launch_installer)
        self.finished_btn.clicked.connect(self.installation_finished)
        self.stop_btn.clicked.connect(self.stop_wine)
        for button in (self.launch_btn, self.finished_btn, self.stop_btn):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_game(self) -> QGroupBox:
        box = QGroupBox("Game folder")
        layout = QVBoxLayout(box)
        self.game_btn = QPushButton("Select game folder")
        self.game_btn.clicked.connect(self.select_game)
        self.game_label = QLabel("No game folder selected.")
        self.game_label.setWordWrap(True)
        self.game_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.listing = QPlainTextEdit()
        self.listing.setReadOnly(True)
        self.listing.setPlaceholderText("A short listing of the selected folder appears here.")
        self.listing.setFixedHeight(120)
        layout.addWidget(self.game_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.game_label)
        layout.addWidget(self.listing)
        return box

    def _build_fixes(self) -> QGroupBox:
        box = QGroupBox("Fixes")
        layout = QVBoxLayout(box)
        hint = QLabel(
            "Copy fixes can add or replace files. A patcher runs only after you confirm it, "
            "because it cannot show an overwrite preview. Skip any row you do not want."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.fix_list = QListWidget()
        self.fix_list.setMinimumHeight(120)
        self.fix_list.itemChanged.connect(self._fix_toggled)
        layout.addWidget(self.fix_list)
        row = QHBoxLayout()
        self.add_copy_btn = QPushButton("Add files or archive")
        self.add_patcher_btn = QPushButton("Add patcher")
        self.remove_fix_btn = QPushButton("Remove")
        self.up_btn = QPushButton("Move up")
        self.down_btn = QPushButton("Move down")
        self.apply_btn = QPushButton("Apply All Fixes")
        self.patcher_done_btn = QPushButton("Patcher finished")
        self.add_copy_btn.clicked.connect(self.add_copy_fix)
        self.add_patcher_btn.clicked.connect(self.add_patcher)
        self.remove_fix_btn.clicked.connect(self.remove_fix)
        self.up_btn.clicked.connect(lambda: self.move_fix(-1))
        self.down_btn.clicked.connect(lambda: self.move_fix(1))
        self.apply_btn.clicked.connect(self.apply_fixes)
        self.patcher_done_btn.clicked.connect(self.patcher_finished)
        for button in (
            self.add_copy_btn,
            self.add_patcher_btn,
            self.remove_fix_btn,
            self.up_btn,
            self.down_btn,
            self.apply_btn,
            self.patcher_done_btn,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_manual(self) -> QGroupBox:
        box = QGroupBox("Manual adjustments")
        layout = QVBoxLayout(box)
        note = QLabel(
            "Optional. After fixes, open the game folder or the Wine prefix and edit files yourself. "
            "This step does not check what you change. Click Continue when you want to package the game folder."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.open_game_btn = QPushButton("Open game folder")
        self.open_prefix_btn = QPushButton("Open Wine prefix")
        self.continue_btn = QPushButton("Continue")
        self.open_game_btn.clicked.connect(self.open_game_folder)
        self.open_prefix_btn.clicked.connect(self.open_wine_prefix)
        self.continue_btn.clicked.connect(self.continue_to_export)
        for button in (self.open_game_btn, self.open_prefix_btn, self.continue_btn):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_export(self) -> QGroupBox:
        box = QGroupBox("Export")
        layout = QVBoxLayout(box)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.version_edit = QLineEdit()
        self.year_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Game title")
        self.version_edit.setPlaceholderText("optional, such as 1.2")
        self.year_edit.setPlaceholderText("optional, such as 2004")
        form.addRow("Title", self.title_edit)
        form.addRow("Version", self.version_edit)
        form.addRow("Year", self.year_edit)
        layout.addLayout(form)
        self.filename_label = QLabel("")
        self.filename_label.setWordWrap(True)
        self.filename_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.filename_label)
        note = QLabel(
            "(W_P) is GameVault's Windows-portable type tag. "
            "Creating the ZIP does not test whether the game runs."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.export_btn = QPushButton("Create ZIP")
        self.export_btn.clicked.connect(self.export_zip)
        layout.addWidget(self.export_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.title_edit.textEdited.connect(self._mark_title_custom)
        self.title_edit.textChanged.connect(self.refresh_name)
        self.version_edit.textChanged.connect(self.refresh_name)
        self.year_edit.textChanged.connect(self.refresh_name)
        return box

    def source_kind(self) -> str:
        if self.dir_radio.isChecked():
            return "directory"
        if self.iso_radio.isChecked():
            return "iso"
        return "exe"

    def _source_kind_changed(self) -> None:
        self.source_path = None
        self.media_root = None
        self.source_label.setText("Nothing selected yet.")
        self.media_label.setText("The folder to copy will appear here before anything is copied.")
        self.refresh_buttons()

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.log(message)

    def show_error(self, message: str) -> None:
        self.log(message)
        first = message.splitlines()[0] if message else "Something went wrong."
        self.status_label.setText(first)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("GameVault Archive Preparer")
        box.setText(message)
        box.setInformativeText("Details are in the log.")
        box.exec()

    def refresh_name(self) -> None:
        try:
            name = build_archive_name(
                self.title_edit.text(),
                self.version_edit.text(),
                self.year_edit.text(),
            )
        except ArchiveNameError as exc:
            self._name_error = str(exc)
            self.filename_label.setText(str(exc))
        else:
            self._name_error = ""
            self.filename_label.setText(name)
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        if not hasattr(self, "stage_btn"):
            return
        locked = self.busy or self.mode is not None or bool(self._archive_queue)
        staged = self.workspace is not None
        self.scratch_edit.setEnabled(not locked and not staged)
        self.scratch_btn.setEnabled(not locked and not staged)
        self.output_edit.setEnabled(not locked)
        self.output_btn.setEnabled(not locked)
        self.source_folder_edit.setEnabled(not locked)
        self.source_folder_btn.setEnabled(not locked)
        for radio in (self.exe_radio, self.dir_radio, self.iso_radio):
            radio.setEnabled(not locked and not staged)
        self.choose_btn.setEnabled(not locked and not staged)
        self.media_btn.setEnabled(
            not locked and not staged and self.source_kind() == "exe" and self.source_path is not None
        )
        ready_to_stage = self.source_path is not None and (
            self.source_kind() != "exe" or self.media_root is not None
        )
        self.stage_btn.setEnabled(not locked and not staged and ready_to_stage)
        self.installer_btn.setEnabled(not locked and staged and not self.install_launched)
        self.launch_btn.setEnabled(
            not locked and staged and self.installer is not None and not self.install_ready
        )
        self.launch_btn.setText(
            "Launch installer" if self.prefix_initialized else "Initialize prefix and launch installer"
        )
        self.finished_btn.setEnabled(self.mode == "install-ui")
        self.patcher_done_btn.setEnabled(self.mode == "patcher-ui")
        self.stop_btn.setEnabled(
            self.prefix_dir is not None and not self.wine_idle and not self._killing
        )
        game_ready = self.install_ready and self.wine_idle and not locked
        self.game_btn.setEnabled(game_ready)
        fixes_ready = self.game_dir is not None and self.wine_idle and not locked
        for button in (self.add_copy_btn, self.add_patcher_btn, self.apply_btn):
            button.setEnabled(fixes_ready)
        row = self.fix_list.currentRow()
        pending = (
            fixes_ready
            and 0 <= row < len(self.fixes)
            and not self.fixes[row].applied
        )
        self.remove_fix_btn.setEnabled(pending)
        self.up_btn.setEnabled(pending and row > 0 and not self.fixes[row - 1].applied)
        self.down_btn.setEnabled(
            pending and row + 1 < len(self.fixes) and not self.fixes[row + 1].applied
        )
        self.open_game_btn.setEnabled(fixes_ready)
        self.open_prefix_btn.setEnabled(fixes_ready and self.prefix_dir is not None)
        self.continue_btn.setEnabled(fixes_ready)
        self.export_btn.setEnabled(fixes_ready and self.manual_continued and self._name_error == "")

    def _browse_dir(self, edit: QLineEdit, title: str) -> None:
        current = edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, title, current)
        if chosen:
            edit.setText(chosen)

    def _browse_source_folder(self) -> None:
        self._browse_dir(self.source_folder_edit, "Source folder")
        self._save_source_folder()

    def _save_source_folder(self) -> None:
        QSettings("gamevault-preparer", "gamevault-preparer").setValue(
            "sourceFolder",
            self.source_folder_edit.text().strip(),
        )

    def _source_picker_start(self) -> str:
        text = self.source_folder_edit.text().strip()
        if text:
            folder = Path(text).expanduser()
            if folder.is_dir():
                return str(folder)
        return str(Path.home())

    def choose_source(self) -> None:
        kind = self.source_kind()
        start = self._source_picker_start()
        if kind == "exe":
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "Select the installer",
                start,
                "Windows executables (*.exe *.EXE);;All files (*)",
            )
            if not chosen:
                return
            self.source_path = Path(chosen)
            self.media_root = self.source_path.parent
            self.source_label.setText(f"Installer: {self.source_path}")
            self.media_label.setText(f"Folder to copy: {self.media_root}")
        elif kind == "directory":
            chosen = QFileDialog.getExistingDirectory(self, "Select the installer folder", start)
            if not chosen:
                return
            self.source_path = Path(chosen)
            self.media_root = self.source_path
            self.source_label.setText(f"Installer folder: {self.source_path}")
            self.media_label.setText(f"Folder to copy: {self.source_path}")
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "Select the ISO",
                start,
                "Disc images (*.iso *.ISO);;All files (*)",
            )
            if not chosen:
                return
            self.source_path = Path(chosen)
            self.media_root = None
            self.source_label.setText(f"ISO: {self.source_path}")
            self.media_label.setText(
                f"This ISO will be extracted into the workspace. Original file: {self.source_path}"
            )
        self.refresh_buttons()

    def change_media_folder(self) -> None:
        if self.source_path is None:
            return
        start = str(self.media_root or self.source_path.parent)
        chosen = QFileDialog.getExistingDirectory(self, "Folder to copy", start)
        if not chosen:
            return
        self.media_root = Path(chosen)
        self.media_label.setText(f"Folder to copy: {self.media_root}")
        self.refresh_buttons()

    def stage(self) -> None:
        if self.source_path is None:
            return
        scratch = Path(self.scratch_edit.text().strip())
        output = Path(self.output_edit.text().strip())
        if not str(scratch) or not str(output):
            self.show_error("Choose both a scratch folder and an output folder.")
            return
        kind = self.source_kind()
        source = self.source_path
        media_root = self.media_root
        self.set_status(f"Staging {self.media_label.text()}")

        def work(progress):
            return stage_media(kind, source, scratch, output, progress, media_root=media_root)

        self._start_worker(work, self._stage_done)

    def _stage_done(self, result) -> None:
        self.workspace = result.workspace
        self.media_dir = result.media
        self.prefix_dir = result.prefix
        self.fixes_dir = result.fixes
        self.protected_source = result.source
        self.installer = result.installer
        self.workspace_label.setText(f"Workspace: {self.workspace}")
        self.log(f"WINEPREFIX will be {self.prefix_dir}")
        self.log("Original media was copied or extracted. The source files were not modified.")
        if self.installer is not None:
            self.installer_label.setText(f"Installer: {self.installer}")
            self.set_status("Media is staged. Initialize the prefix and launch the installer when you are ready.")
        else:
            self.set_status("Media is staged. Select the installer EXE inside the staged folder.")
            self.select_installer()
        self.refresh_buttons()

    def select_installer(self) -> None:
        if self.media_dir is None:
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select the installer inside the staged media",
            str(self.media_dir),
            "Windows executables (*.exe *.EXE);;All files (*)",
        )
        if not chosen:
            self.set_status("Select the installer EXE inside the staged media.")
            return
        try:
            self.installer = installer_inside(self.media_dir, Path(chosen))
        except FileError as exc:
            self.show_error(str(exc))
            return
        self.installer_label.setText(f"Installer: {self.installer}")
        self.set_status("Installer selected. Initialize the prefix and launch it when you are ready.")
        self.refresh_buttons()

    def _wine_env(self) -> QProcessEnvironment:
        qenv = self._qenv(prefixed_environment(self.prefix_dir))
        qenv.insert("WINEPREFIX", str(self.prefix_dir))
        return qenv

    def _run_prefix_command(self, program: str, args: list[str], cwd: Path | None, on_done, mode: str) -> None:
        try:
            require_tools()
        except WineError as exc:
            self.show_error(str(exc))
            self.busy = False
            self.mode = None
            self.refresh_buttons()
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.show_error("A Wine program from this session is already running.")
            return
        self.busy = True
        self.mode = mode
        self.wine_idle = False
        self._stopped = False
        self._stop_settled = False
        self._start_failed = False
        self._captured = ""
        self._process_done = on_done
        self._program = program
        self.refresh_buttons()
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments([str(arg) for arg in args])
        proc.setProcessEnvironment(self._wine_env())
        if cwd is not None:
            proc.setWorkingDirectory(str(cwd))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read_process)
        proc.errorOccurred.connect(self._process_error)
        proc.finished.connect(self._process_finished)
        self.process = proc
        self.log("Starting " + program + " " + " ".join(str(arg) for arg in args))
        self.log(f"WINEPREFIX={self.prefix_dir}")
        proc.start()

    def _read_process(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self._captured += data
        for line in data.splitlines():
            if line.strip():
                self.log(line)

    def _process_error(self, error) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._start_failed:
            return
        self._start_failed = True
        self.busy = False
        self.mode = None
        self._fix_queue_active = False
        if not self.prefix_initialized:
            self.wine_idle = True
        self.show_error(
            f"Could not start {self._program}. Install Wine so wine, wineboot, wineserver, "
            "and winepath are on PATH."
        )
        self.refresh_buttons()

    def _process_finished(self, code: int, status) -> None:
        self._read_process()
        if self._start_failed:
            return
        if self._stopped:
            self.busy = False
            self._settle_after_stop()
            self.refresh_buttons()
            return
        normal = status == QProcess.ExitStatus.NormalExit
        callback = self._process_done
        self._process_done = None
        self.busy = False
        if callback is not None:
            callback(int(code), bool(normal))
        self.refresh_buttons()

    def launch_installer(self) -> None:
        if self.installer is None or self.prefix_dir is None:
            return
        self._stopped = False
        self._stop_settled = False
        if not self.prefix_initialized:
            self.set_status("Creating a new Wine prefix.")
            self._run_prefix_command("wineboot", ["-u"], None, self._wineboot_finished, "waiting-processes")
        else:
            self._launch_installer_process()

    def _wineboot_finished(self, code: int, normal: bool) -> None:
        if self._stopped:
            self._settle_after_stop()
            return
        if code != 0 or not normal:
            self.mode = None
            self.busy = False
            self.wine_idle = True
            self.show_error(f"wineboot failed with exit code {code}. See the log for the Wine output.")
            self.refresh_buttons()
            return
        self.prefix_initialized = True
        self.wine_idle = False
        self._launch_installer_process()

    def _launch_installer_process(self) -> None:
        self.install_launched = True
        self.set_status("Finish the installer, uncheck Launch game if offered, then return here.")
        self._run_prefix_command(
            "wine",
            [str(self.installer)],
            self.installer.parent,
            self._installer_exited,
            "install-ui",
        )

    def _installer_exited(self, code: int, _normal: bool) -> None:
        self.log(f"The installer process exited with code {code}.")
        if self._stopped:
            self._settle_after_stop()
            return
        if self.install_ready or self._process_running(self.wait_process):
            return
        self.busy = False
        self.mode = "install-ui"
        self.wine_idle = False
        self.set_status("Finish the installer, uncheck Launch game if offered, then return here.")
        self.refresh_buttons()

    def installation_finished(self) -> None:
        if self.mode != "install-ui" or self.prefix_dir is None:
            return
        self.finish_requested = True
        self._start_wine_wait("install")

    def patcher_finished(self) -> None:
        if self.mode != "patcher-ui":
            return
        self._start_wine_wait("patcher")

    def _start_wine_wait(self, kind: str) -> None:
        try:
            require_tools()
        except WineError as exc:
            self.show_error(str(exc))
            return
        self._idle_kind = kind
        self.mode = "waiting-processes"
        self.busy = True
        self.refresh_buttons()
        proc = QProcess(self)
        proc.setProgram("wineserver")
        proc.setArguments(["-w"])
        proc.setProcessEnvironment(self._wine_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._read_extra(proc))
        proc.finished.connect(self._wait_finished)
        self.wait_process = proc
        self.log("Waiting for Wine processes in this prefix to exit (wineserver -w).")
        self.log(f"WINEPREFIX={self.prefix_dir}")
        proc.start()

    def _read_extra(self, proc: QProcess) -> None:
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.log(line)

    def _wait_finished(self, code: int, status) -> None:
        self._read_extra(self.wait_process) if self.wait_process is not None else None
        self.busy = False
        if self._stopped:
            self._settle_after_stop()
            self.refresh_buttons()
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            self.mode = "install-ui" if self._idle_kind == "install" else "patcher-ui"
            self.show_error(
                f"wineserver -w exited with code {code}. "
                "Stop this session's Wine processes if something is still running."
            )
            self.refresh_buttons()
            return
        self.wine_idle = True
        self.mode = None
        if self._idle_kind == "install":
            self.install_ready = True
            self.set_status("Wine is idle. Select the installed game folder.")
        else:
            self._complete_current_fix()
        self.refresh_buttons()

    def stop_wine(self) -> None:
        if self.prefix_dir is None or self.wine_idle or self._killing:
            return
        answer = QMessageBox.question(
            self,
            "Stop Wine",
            "Stop all Wine processes in this session's prefix?\n\nOther Wine prefixes are not affected.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            require_tools()
        except WineError as exc:
            self.show_error(str(exc))
            return
        self._stopped = True
        self._killing = True
        self.refresh_buttons()
        proc = QProcess(self)
        proc.setProgram("wineserver")
        proc.setArguments(["-k"])
        proc.setProcessEnvironment(self._wine_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._read_extra(proc))
        proc.finished.connect(self._kill_finished)
        self.killer = proc
        self.log("Stopping Wine processes for this prefix (wineserver -k).")
        self.log(f"WINEPREFIX={self.prefix_dir}")
        proc.start()

    def _kill_finished(self, code: int, _status) -> None:
        self._killing = False
        if self.killer is not None:
            self._read_extra(self.killer)
        self.log(f"wineserver -k exited with code {code}.")
        self._settle_after_stop()
        self.refresh_buttons()

    def _settle_after_stop(self) -> None:
        if self._stop_settled:
            return
        if self._process_running(self.process) or self._process_running(self.wait_process) or self._killing:
            self.set_status("Waiting for this session's Wine processes to exit.")
            return
        self._stop_settled = True
        self.wine_idle = True
        self.busy = False
        queue = self._fix_queue_active
        self._fix_queue_active = False
        self.mode = None
        if queue:
            self.set_status(
                "Wine processes for this prefix were stopped. The patcher may have changed the prefix. "
                "Later fixes were not started. Earlier applied fixes remain."
            )
        elif self.finish_requested and self.install_launched:
            self.install_ready = True
            self.set_status(
                "Wine processes for this prefix were stopped. Select the game folder if installation finished."
            )
        else:
            self.set_status("Wine processes for this prefix were stopped.")

    def _process_running(self, proc: QProcess | None) -> bool:
        return proc is not None and proc.state() != QProcess.ProcessState.NotRunning

    def select_game(self) -> None:
        if self.prefix_dir is None:
            return
        drive = self.prefix_dir / "drive_c"
        start = str(drive if drive.is_dir() else self.prefix_dir)
        chosen = QFileDialog.getExistingDirectory(self, "Select the installed game folder", start)
        if not chosen:
            return
        try:
            self.game_dir = validate_game_folder(Path(chosen), self.prefix_dir)
        except FileError as exc:
            self.show_error(str(exc))
            return
        self.game_label.setText(str(self.game_dir))
        self.listing.setPlainText(format_listing(self.game_dir))
        self._fill_title_from_game_folder()
        self.manual_continued = False
        self.set_status("Game folder selected. Add fixes if you want, then continue to packaging.")
        self.refresh_buttons()

    def _mark_title_custom(self, _text: str = "") -> None:
        self._title_custom = True

    def _fill_title_from_game_folder(self) -> None:
        if self._title_custom or self.game_dir is None:
            return
        self.title_edit.setText(self.game_dir.name)

    def _reload_fixes(self, select: int | None = None) -> None:
        self.fix_list.blockSignals(True)
        self.fix_list.clear()
        for fix in self.fixes:
            item = QListWidgetItem(fix.summary())
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not fix.applied:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            if not fix.applied:
                item.setCheckState(
                    Qt.CheckState.Checked if fix.enabled else Qt.CheckState.Unchecked
                )
            self.fix_list.addItem(item)
        self.fix_list.blockSignals(False)
        if select is not None and 0 <= select < len(self.fixes):
            self.fix_list.setCurrentRow(select)
        self.refresh_buttons()

    def _fix_toggled(self, item: QListWidgetItem) -> None:
        row = self.fix_list.row(item)
        if row < 0 or self.fixes[row].applied:
            return
        self.fixes[row].enabled = item.checkState() == Qt.CheckState.Checked
        self.fix_list.blockSignals(True)
        item.setText(self.fixes[row].summary())
        self.fix_list.blockSignals(False)

    def _append_fix(self, fix: Fix) -> None:
        self.fixes.append(fix)
        self._reload_fixes(len(self.fixes) - 1)
        self.set_status(f"Added fix: {fix.label}")

    def remove_fix(self) -> None:
        row = self.fix_list.currentRow()
        if row < 0 or self.fixes[row].applied:
            self.set_status("Applied fixes stay in the list. Remove only a pending fix.")
            return
        del self.fixes[row]
        self._reload_fixes(min(row, len(self.fixes) - 1))

    def move_fix(self, delta: int) -> None:
        row = self.fix_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self.fixes)):
            return
        if self.fixes[row].applied or self.fixes[target].applied:
            self.set_status("Applied fixes stay in place. Only pending fixes can move.")
            return
        self.fixes[row], self.fixes[target] = self.fixes[target], self.fixes[row]
        self._reload_fixes(target)

    def add_copy_fix(self) -> None:
        if self.game_dir is None or self.fixes_dir is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Add a copy fix")
        box.setText("What should be copied into the game folder?")
        archive_btn = box.addButton("ZIP or 7z archives", QMessageBox.ButtonRole.AcceptRole)
        files_btn = box.addButton("Loose files", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("Loose folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == archive_btn:
            self._pick_archive_fix()
        elif clicked == files_btn:
            self._pick_loose_files()
        elif clicked == folder_btn:
            self._pick_loose_folder()

    def _pick_archive_fix(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(
            self,
            "Select ZIP or 7z fixes",
            str(Path.home()),
            "Fix archives (*.zip *.7z *.ZIP *.7Z);;All files (*)",
        )
        if not chosen:
            return
        archives: list[Path] = []
        for name in chosen:
            archive = Path(name)
            try:
                archive_kind_for_fix(archive)
            except FileError as exc:
                self.show_error(str(exc))
                return
            archives.append(archive)
        self._archive_queue = archives
        self._start_next_archive()

    def _start_next_archive(self) -> None:
        if not self._archive_queue or self.fixes_dir is None:
            self._archive_queue.clear()
            self.refresh_buttons()
            return
        archive = self._archive_queue.pop(0)
        try:
            kind = archive_kind_for_fix(archive)
        except FileError as exc:
            self._archive_queue.clear()
            self.show_error(str(exc))
            return
        staging = self._new_fix_dir("archive")
        waiting = len(self._archive_queue)
        status = f"Extracting {archive.name} into staging."
        if waiting:
            status += f" {waiting} more still queued."
        self.set_status(status)

        def work(progress):
            extract_archive(archive, staging, kind=kind, progress=progress)
            return staging

        self._start_worker(
            work,
            lambda path, current=archive: self._choose_archive_contents(current, path),
            cleanup=staging,
        )

    def _choose_archive_contents(self, archive: Path, staging: Path) -> None:
        options = [(name, "(archive root)" if name == "." else name) for name in child_directories(staging)]
        dialog = ChoiceDialog(
            "Choose the files to copy",
            "Choose the folder whose contents should be copied into the game. "
            "A wrapper folder stays in place until you select the folder inside it.",
            options,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._discard_staging(staging)
            self.set_status(f"{archive.name} was not added.")
            self._start_next_archive()
            return
        chosen = dialog.selected
        source = staging if chosen == "." else staging / chosen
        label = f"Copy contents of {archive.name}" + ("" if chosen == "." else f" from {chosen}")
        self._preview_and_add_copy(label, source, None, (), staging)

    def _pick_loose_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Select files to copy", str(Path.home()))
        if not chosen:
            return
        paths = [Path(path) for path in chosen]
        game = self.game_dir

        def work(progress):
            progress(50, "Checking files")
            plan = plan_for_fix(
                Fix("copy", "files", loose_files=tuple(paths)),
                game,
            )
            progress(100, "Ready")
            return plan

        self._start_worker(work, lambda plan: self._confirm_loose_files(paths, plan))

    def _confirm_loose_files(self, paths: list[Path], plan) -> None:
        names = ", ".join(path.name for path in paths[:3])
        if len(paths) > 3:
            names += f" (+{len(paths) - 3})"
        dialog = TextDialog(
            "Copy files to the game root",
            preview_text(plan),
            "Add fix",
            "Cancel",
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._append_fix(
            Fix(
                "copy",
                f"Copy file(s) to the game root: {names}",
                loose_files=tuple(paths),
            )
        )

    def _pick_loose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select a folder to copy", str(Path.home()))
        if not chosen:
            return
        folder = Path(chosen)
        game = self.game_dir

        def work(progress):
            progress(20, "Reading the folder")
            contents = plan_for_fix(Fix("copy", "contents", source_root=folder), game)
            progress(70, "Reading the folder")
            wrapped = plan_for_fix(
                Fix("copy", "folder", source_root=folder, folder_name=folder.name),
                game,
            )
            progress(100, "Ready")
            return contents, wrapped

        self._start_worker(work, lambda plans: self._confirm_folder(folder, plans))

    def _confirm_folder(self, folder: Path, plans) -> None:
        contents, wrapped = plans
        dialog = FolderModeDialog(folder.name, preview_text(wrapped), preview_text(contents), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.copy_folder:
            self._append_fix(
                Fix(
                    "copy",
                    f"Copy folder {folder.name} into the game",
                    source_root=folder,
                    folder_name=folder.name,
                )
            )
        else:
            self._append_fix(
                Fix(
                    "copy",
                    f"Copy contents of {folder.name} into the game",
                    source_root=folder,
                )
            )

    def _preview_and_add_copy(self, label, source, folder_name, loose_files, staging: Path | None) -> None:
        game = self.game_dir
        fix = Fix(
            "copy",
            label,
            source_root=source,
            folder_name=folder_name,
            loose_files=tuple(loose_files),
        )

        def work(progress):
            progress(40, "Checking files")
            plan = plan_for_fix(fix, game)
            progress(100, "Ready")
            return plan

        def done(plan):
            dialog = TextDialog("Files that will be copied", preview_text(plan), "Add fix", "Cancel", self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                if staging is not None:
                    self._discard_staging(staging)
                self.set_status("The copy fix was not added.")
                self._start_next_archive()
                return
            self._append_fix(fix)
            self._start_next_archive()

        self._start_worker(work, done, cleanup=staging)

    def add_patcher(self) -> None:
        if self.fixes_dir is None:
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select the patcher to run",
            str(Path.home()),
            "Patchers (*.exe *.bat *.EXE *.BAT);;All files (*)",
        )
        if not chosen:
            return
        executable = Path(chosen)
        if executable.suffix.lower() not in {".exe", ".bat"}:
            self.show_error("Select a .exe or .bat patcher.")
            return
        dialog = PatcherDialog(executable.parent, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        staging = self._new_fix_dir("patcher")
        workdir = dialog.workdir
        self.set_status(f"Staging files next to {executable.name}.")

        def work(progress):
            return stage_patcher_bundle(executable, staging, progress)

        def done(staged: Path):
            where = "game folder" if workdir == "game" else "patcher folder"
            self._append_fix(
                Fix(
                    "patcher",
                    f"Run patcher {staged.name} (working directory: {where})",
                    executable=staged,
                    workdir_kind=workdir,
                )
            )

        self._start_worker(work, done, cleanup=staging)

    def _new_fix_dir(self, prefix: str) -> Path:
        assert self.fixes_dir is not None
        self._fix_seq += 1
        path = self.fixes_dir / f"{prefix}-{self._fix_seq}"
        path.mkdir()
        return path

    def _discard_staging(self, staging: Path) -> None:
        if self.fixes_dir is None:
            return
        try:
            remove_owned_directory(staging, self.fixes_dir)
        except FileError as exc:
            self.log(str(exc))

    def apply_fixes(self) -> None:
        if self.game_dir is None or not self.wine_idle:
            return
        self._fix_queue = [
            index for index, fix in enumerate(self.fixes) if fix.enabled and not fix.applied
        ]
        self._fix_pos = 0
        if not self._fix_queue:
            self.set_status("No enabled fixes to apply. You can create the ZIP as the game folder is now.")
            return
        self._fix_queue_active = True
        self._run_next_fix()

    def _run_next_fix(self) -> None:
        if not self._fix_queue_active:
            return
        if self._fix_pos >= len(self._fix_queue):
            self._fix_queue_active = False
            self.busy = False
            self.mode = None
            self.set_status(
                "Finished applying the enabled fixes. "
                "You can edit the game folder or prefix, then click Continue."
            )
            self.refresh_buttons()
            return
        fix = self.fixes[self._fix_queue[self._fix_pos]]
        if fix.kind == "patcher":
            self._confirm_patcher(fix)
        else:
            self._preview_fix(fix)

    def _preview_fix(self, fix: Fix) -> None:
        game = self.game_dir

        def work(progress):
            progress(30, fix.label)
            return plan_for_fix(fix, game)

        self._start_worker(work, lambda plan: self._after_preview(fix, plan))

    def _after_preview(self, fix: Fix, plan) -> None:
        if not self._fix_queue_active:
            return
        if plan.overwrites:
            shown = "\n".join(plan.overwrites[:40])
            extra = len(plan.overwrites) - 40
            if extra > 0:
                shown += f"\n... and {extra} more"
            dialog = TextDialog(
                "Overwrite files",
                f"This fix overwrites {len(plan.overwrites)} file(s):\n\n{shown}\n\n"
                "Overwrite them? Stop leaves this fix unchanged.",
                "Overwrite",
                "Stop",
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._fix_queue_active = False
                self.set_status(
                    "Stopped before a fix that would overwrite files. Earlier applied fixes remain."
                )
                self.refresh_buttons()
                return
        else:
            self.log(f"{fix.label}: copying {len(plan.new_files)} new file(s).")
        game = self.game_dir

        def work(progress):
            apply_copy_plan(plan, game, progress)

        self._start_worker(work, lambda _result: self._complete_current_fix())

    def _confirm_patcher(self, fix: Fix) -> None:
        dialog = TextDialog(
            "Run patcher",
            f"{fix.label}\n\n"
            "This patcher cannot show an overwrite preview and may modify the Wine prefix. "
            "Apply All Fixes does not run it until you confirm it here.\n\n"
            "Run it now? Stop leaves this fix unstarted.",
            "Run",
            "Stop",
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._fix_queue_active = False
            self.set_status("Stopped before running a patcher. Earlier applied fixes remain.")
            self.refresh_buttons()
            return
        self._launch_patcher(fix)

    def _launch_patcher(self, fix: Fix) -> None:
        if fix.executable is None or self.game_dir is None:
            self._fail_queue("The patcher file is missing.")
            return
        cwd = self.game_dir if fix.workdir_kind == "game" else fix.executable.parent
        self._patcher_cwd = cwd
        self._stopped = False
        self._stop_settled = False
        if fix.executable.suffix.lower() == ".bat":
            self.set_status("Converting the batch path with winepath.")
            self._run_prefix_command(
                "winepath",
                ["-w", str(fix.executable)],
                cwd,
                self._winepath_finished,
                "waiting-processes",
            )
        else:
            self.set_status("Finish the patcher, then click Patcher finished.")
            self._run_prefix_command(
                "wine",
                [str(fix.executable)],
                cwd,
                self._patcher_exited,
                "patcher-ui",
            )

    def _winepath_finished(self, code: int, normal: bool) -> None:
        captured = self._captured
        if self._stopped:
            self._settle_after_stop()
            return
        if code != 0 or not normal:
            self._fail_queue(f"winepath failed with exit code {code}.")
            return
        try:
            parsed = parse_winepath_output(captured)
            args = bat_command_arguments(parsed)
        except WineError as exc:
            self._fail_queue(str(exc))
            return
        self.log(f"Batch path: {parsed}")
        self.set_status("Finish the patcher, then click Patcher finished.")
        self._run_prefix_command("wine", args, self._patcher_cwd, self._patcher_exited, "patcher-ui")

    def _patcher_exited(self, code: int, _normal: bool) -> None:
        self.log(f"The patcher process exited with code {code}.")
        if self._stopped:
            self._settle_after_stop()
            return
        if self.wine_idle or self._process_running(self.wait_process) or not self._fix_queue_active:
            return
        self.busy = False
        self.mode = "patcher-ui"
        self.wine_idle = False
        self.set_status("Finish the patcher, then click Patcher finished.")
        self.refresh_buttons()

    def _complete_current_fix(self) -> None:
        if not self._fix_queue_active:
            return
        fix = self.fixes[self._fix_queue[self._fix_pos]]
        fix.applied = True
        fix.enabled = True
        self._reload_fixes(self._fix_queue[self._fix_pos])
        self.log(f"Applied: {fix.label}")
        self._fix_pos += 1
        self.wine_idle = True
        self.mode = None
        self.busy = False
        self._run_next_fix()

    def _fail_queue(self, message: str) -> None:
        self._fix_queue_active = False
        self.busy = False
        self.mode = None
        if self.prefix_initialized:
            self.wine_idle = False
            message += " Stop this session's Wine processes if they are still running."
        else:
            self.wine_idle = True
        self.show_error(message)
        self.refresh_buttons()

    def open_game_folder(self) -> None:
        self._open_in_file_manager(self.game_dir, "game folder")

    def open_wine_prefix(self) -> None:
        self._open_in_file_manager(self.prefix_dir, "Wine prefix")

    def _open_in_file_manager(self, path: Path | None, label: str) -> None:
        if path is None:
            return
        program = shutil.which("xdg-open")
        if not program:
            self.show_error(
                f"xdg-open is not available, so the {label} was not opened. "
                f"Open this folder in your file manager: {path}"
            )
            return
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(file_manager_arguments(path))
        proc.setProcessEnvironment(self._qenv(host_tool_environment()))
        proc.setWorkingDirectory(str(path if path.is_dir() else path.parent))
        self._file_managers.append(proc)
        proc.finished.connect(lambda _code=0, _status=None, running=proc: self._drop_file_manager(running))
        proc.start()
        if not proc.waitForStarted(5000):
            self._drop_file_manager(proc)
            self.show_error(
                f"The file manager did not open the {label}. Open this folder yourself: {path}"
            )
            return
        self.log(f"Opened the {label}: {path}")

    def _drop_file_manager(self, proc: QProcess) -> None:
        if proc in self._file_managers:
            self._file_managers.remove(proc)

    def _qenv(self, env: dict[str, str]) -> QProcessEnvironment:
        qenv = QProcessEnvironment()
        for key, value in env.items():
            if not key or "=" in key:
                continue
            qenv.insert(key, value)
        return qenv

    def continue_to_export(self) -> None:
        if self.game_dir is None or not self.wine_idle or self.busy or self.mode is not None:
            return
        self.manual_continued = True
        self.set_status(
            "Ready to create the ZIP. The game folder will be packaged as it is, including any manual edits."
        )
        self.refresh_buttons()

    def export_zip(self) -> None:
        if not self.manual_continued:
            self.show_error("Click Continue in Manual adjustments before creating the ZIP.")
            return
        if self.game_dir is None or self.workspace is None or self.protected_source is None:
            return
        if not self.wine_idle:
            self.show_error("Wait until this session's Wine processes have stopped before creating the ZIP.")
            return
        try:
            filename = build_archive_name(
                self.title_edit.text(),
                self.version_edit.text(),
                self.year_edit.text(),
            )
        except ArchiveNameError as exc:
            self.show_error(str(exc))
            return
        output = Path(self.output_edit.text().strip())
        game = self.game_dir
        workspace = self.workspace
        source = self.protected_source
        self.set_status(f"Creating {filename}")

        def work(progress):
            return create_game_zip(
                game,
                output,
                filename,
                workspace=workspace,
                source=source,
                progress=progress,
            )

        def done(path: Path):
            self.progress.setValue(100)
            self.set_status("Archive created. Gameplay compatibility has not been tested.")
            QMessageBox.information(
                self,
                "Archive created",
                "Archive created. Gameplay compatibility has not been tested.\n\n" + str(path),
            )

        self._start_worker(work, done)

    def _start_worker(self, fn, on_ok, cleanup: Path | None = None) -> None:
        self.busy = True
        self.mode = "working"
        self._cleanup_dir = cleanup
        self.refresh_buttons()
        worker = FunctionWorker(fn, self)
        self._workers.append(worker)
        self.worker = worker

        def ok(result):
            self.busy = False
            self.mode = None
            self._cleanup_dir = None
            try:
                on_ok(result)
            except Exception as exc:
                self._fix_queue_active = False
                if cleanup is not None and self.fixes_dir is not None:
                    self._discard_staging(cleanup)
                self.show_error(str(exc))
            self.refresh_buttons()

        def fail(message: str, trace: str):
            self.busy = False
            self.mode = None
            self._fix_queue_active = False
            self._cleanup_dir = None
            waiting = len(self._archive_queue)
            self._archive_queue.clear()
            if cleanup is not None:
                self._discard_staging(cleanup)
            self.log(trace)
            if waiting:
                message += f"\n\nThe remaining {waiting} archive(s) were not extracted."
            self.show_error(message)
            self.refresh_buttons()

        def finished() -> None:
            if worker in self._workers:
                self._workers.remove(worker)

        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(ok)
        worker.failed.connect(fail)
        worker.finished.connect(finished)
        worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        if message:
            self.status_label.setText(message)

    def closeEvent(self, event) -> None:
        running = (
            self.busy
            or self.mode is not None
            or self._process_running(self.process)
            or self._process_running(self.wait_process)
            or self._killing
        )
        if running:
            QMessageBox.warning(
                self,
                "Work in progress",
                "Wait for the current step to finish, or stop this session's Wine processes, before closing.",
            )
            event.ignore()
            return
        if self.workspace is None:
            event.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle("Workspace")
        box.setText("The installation workspace is still on disk.")
        box.setInformativeText(str(self.workspace))
        keep = box.addButton("Keep workspace", QMessageBox.ButtonRole.AcceptRole)
        delete = box.addButton("Delete workspace", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() == delete:
            if not self.wine_idle:
                QMessageBox.warning(
                    self,
                    "Wine is running",
                    "Stop this session's Wine processes before deleting the workspace.",
                )
                event.ignore()
                return
            confirm = QMessageBox.question(
                self,
                "Delete workspace",
                f"Delete this workspace?\n\n{self.workspace}\n\n"
                "This removes the Wine prefix and staged files created by this app. "
                "Original media and exported ZIPs are not deleted.",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            try:
                delete_workspace(self.workspace)
            except Exception as exc:
                self.show_error(str(exc))
                event.ignore()
                return
        event.accept()


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, accept_label: str, reject_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        layout.addWidget(view)
        buttons = QDialogButtonBox()
        buttons.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(reject_label, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ChoiceDialog(QDialog):
    def __init__(self, title: str, intro: str, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.selected = options[0][0] if options else "."
        layout = QVBoxLayout(self)
        label = QLabel(intro)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.group = QButtonGroup(self)
        for index, (value, caption) in enumerate(options):
            radio = QRadioButton(caption)
            radio.setProperty("value", value)
            if index == 0:
                radio.setChecked(True)
            self.group.addButton(radio)
            layout.addWidget(radio)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        button = self.group.checkedButton()
        if button is not None:
            self.selected = button.property("value")
        self.accept()


class FolderModeDialog(QDialog):
    def __init__(self, folder_name: str, folder_preview: str, contents_preview: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How should this folder be copied?")
        self.resize(640, 420)
        self.copy_folder = True
        self._folder_preview = folder_preview
        self._contents_preview = contents_preview
        layout = QVBoxLayout(self)
        intro = QLabel(f"Show the paths that will land in the game folder for '{folder_name}'.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.folder_radio = QRadioButton(f"Copy this folder ({folder_name}/…)")
        self.contents_radio = QRadioButton("Copy its contents")
        self.folder_radio.setChecked(True)
        layout.addWidget(self.folder_radio)
        layout.addWidget(self.contents_radio)
        self.preview = QPlainTextEdit(folder_preview)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.folder_radio.toggled.connect(self._update)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update(self) -> None:
        self.preview.setPlainText(
            self._folder_preview if self.folder_radio.isChecked() else self._contents_preview
        )

    def _accept(self) -> None:
        self.copy_folder = self.folder_radio.isChecked()
        self.accept()


class PatcherDialog(QDialog):
    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a patcher")
        self.workdir = "game"
        layout = QVBoxLayout(self)
        text = QLabel(
            "This copies the patcher's folder, including companion files, into the workspace:\n"
            f"{folder}\n\n"
            "Choose the working directory. You will be asked again before the patcher runs."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.game_radio = QRadioButton("Working directory: game folder")
        self.patcher_radio = QRadioButton("Working directory: patcher folder")
        self.game_radio.setChecked(True)
        layout.addWidget(self.game_radio)
        layout.addWidget(self.patcher_radio)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add patcher")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.workdir = "game" if self.game_radio.isChecked() else "patcher"
        self.accept()
