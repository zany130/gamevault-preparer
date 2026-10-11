# GameVault Archive Preparer

Linux desktop app that installs a Windows game in a fresh Wine prefix, optionally applies fixes you choose, and zips the installed game folder for GameVault.

A finished ZIP is not a promise that the game will run. It does not include the Wine prefix, registry, or installer. When the before-and-after snapshots succeed, two notes are written beside the ZIP for you to read: a Software registry diff and a list of setup programs found outside the game folder. The app does not import them, and Verify ZIP does not use them. `(W_P)` is GameVault's Windows-portable type tag.

## Prerequisites

Install these yourself. The app does not download packages or scan the system beyond starting a command and reporting when it is missing.

- Python 3.11 or newer
- [Wine](https://www.winehq.org/), with `wine`, `wineboot`, `wineserver`, and `winepath` on `PATH`. On Bazzite and other immutable desktops, use the Distrobox setup below.
- PySide6
- libarchive, plus the `libarchive-c` Python package, for ISO9660 and 7z files
- 7-Zip (`7zz` or `7z`) for UDF discs and for hybrid discs whose ISO9660 view is missing files. The AppImage includes 7-Zip. A source install needs the `7zip` package on `PATH`.
- Optional: [winetricks](https://github.com/Winetricks/winetricks), if you want **Verify ZIP** to install `d3dx9`, `d3dx10`, `d3dx11_43`, `d3dcompiler_43`, `d3dcompiler_47`, `xact`, `vcrun2008`, `vcrun2010`, and `vcrun2019` into the verification prefix. **Open winetricks** starts the winetricks menu for that same prefix. The app does not install winetricks. Libraries are downloaded only when you ask for them, and winetricks does the download.

ZIP files and ordinary copying use the Python standard library.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Scratch files go in `~/.cache/gamevault-preparer` (or `$XDG_CACHE_HOME/gamevault-preparer`). Archives go in `~/GameVault`. Change either path in the window before staging.

## Wine on Bazzite and other immutable desktops

Bazzite does not put Wine on the host `PATH`. Steam's Proton is a separate runtime, and this app does not call it. Create a Distrobox, install Wine there, and export the four commands the app looks up. Distrobox is already installed on Bazzite. The exported commands are wrappers: Wine and its libraries stay in the container, and the installer window still opens on your desktop.

```bash
distrobox create --name gamevault-wine --image fedora:latest
distrobox enter gamevault-wine
```

Inside the container, install Wine, including 32-bit support for older installers, and export the commands into the home directory that the host and the container share:

```bash
sudo dnf install -y wine wine.i686
mkdir -p "${HOME}/.local/bin"
for cmd in wine wineboot wineserver winepath; do
  distrobox-export --bin "/usr/bin/${cmd}" --export-path "${HOME}/.local/bin"
done
exit
```

Log out and back in if a new terminal does not see `~/.local/bin`. Then confirm all four commands are the exported wrappers:

```bash
command -v wine wineboot wineserver winepath
wine --version
```

Each path should be under `~/.local/bin`. Leave the scratch and output folders in your home directory. The window defaults already do that, and that home directory is the one the container can see.

## AppImage

Published GitHub releases include an AppImage. The workflow `.github/workflows/release-appimage.yml` builds it when a release is published. You can also run that workflow by hand; the file is saved as a workflow artifact and, on a release, attached to the release.

```bash
chmod +x GameVault_Archive_Preparer-*-x86_64.AppImage
./GameVault_Archive_Preparer-*-x86_64.AppImage
```

If FUSE is unavailable, start it with `--appimage-extract-and-run`.

The AppImage contains the app, its Qt libraries, libarchive, and 7-Zip. It does not contain Wine. Install Wine on the computer, or export it from a Distrobox as described above, so `wine`, `wineboot`, `wineserver`, and `winepath` are on `PATH`.

Build one locally with:

```bash
bash packaging/build-appimage.sh
```

The file is written to `dist/`. The build uses the Python on your `PATH`, so a release build on an older system travels to newer ones more reliably than a build made on a very new distribution.

## What you do in the window

1. Choose an installer EXE, an installer folder, or an ISO. Set **Source folder** to the directory that holds those files, such as `roms/pc`. **Select source** opens there, and the app remembers that folder. The window shows the folder that will be copied. Staging copies or extracts into a new workspace and leaves the original alone.
2. Launch the installer in a new prefix. The installer's default location is fine. Finish the installer, uncheck Launch game if it is offered, then click **Installation finished**.
3. Select the installed game folder inside that prefix's `drive_c`.
4. Optionally add copy fixes or EXE/BAT patchers. A patcher can be copied into the game folder and run there, or run from its own folder. **Apply All Fixes** runs the enabled rows in order. New files are copied without another prompt. Overwrites wait for approval. Each patcher waits for a separate **Run** confirmation, then for **Patcher finished**. If a ZIP or 7z contains an EXE or BAT, you can copy the files only, or also run one of those programs from the game folder after the copy. Files copied into the game stay there; delete leftovers from Manual adjustments if they should not be in the ZIP.
5. Optionally open the game folder or the Wine prefix in your file manager and edit files yourself, or click **Run EXE** to start a program in this prefix and see if the game starts. The app does not inspect those edits or whether the game runs. Close the program, click **EXE finished**, then click **Continue** when you want to package the game folder. You can continue without opening either folder or running a program.
6. Enter a title and, if you want, a version and a four-digit year. Create the ZIP. The archive is still only the game folder: it does not contain the Wine prefix, the registry, or the installer. If both snapshots for a note succeeded, the ZIP's name is also used for two files beside it. `Title (W_P).reg` lists Software values the installer added. `Title (W_P).setup.txt` lists setup program names found outside the game folder. They are notes for you to read, not a winetricks script, and paths in the registry file may still point at the original install. A snapshot that fails is logged and that note is left out. An existing note is left unchanged. Optionally click **Verify ZIP** to unpack that ZIP into a new Wine prefix, choose the game EXE, and try to start it. Verify ZIP does not read those notes. If winetricks is installed, you can install the common library set first, or click **Open winetricks** to use its menu on that prefix. Close the program and click **Verify finished**. The ZIP is not changed, and a window opening is not a compatibility promise.

Closing the window asks you to **Keep workspace** or **Delete workspace**. Keep is the default. Delete removes only the workspace this session created, after Wine has stopped.

## Names

- `Title (W_P).zip`
- `Title (v1.2) (W_P).zip`
- `Title (W_P) (2004).zip`
- `Title (v1.2) (W_P) (2004).zip`

The same stem is used for the notes beside the ZIP, such as `Title (W_P).reg` and `Title (W_P).setup.txt`. The version is stored with one leading `v`. Brackets, reserved device names, trailing spaces or dots, and years that are not four digits are rejected so you can correct them.

The ZIP contains the selected game folder's own files, including hidden files and empty folders, without an extra wrapper directory.

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

## Manual smoke checklist

1. Stage an EXE that has a companion file. Confirm the original folder is unchanged and the workspace `media` folder has the same relative paths.
2. Launch the installer and confirm the window still responds. The log's `WINEPREFIX` should be the workspace `prefix` folder, not `~/.wine`.
3. After **Installation finished**, select the game directory. `drive_c`, `windows`, and `Program Files` should be rejected.
4. Open the game folder and the Wine prefix from Manual adjustments, change a file if you want, and click **Continue**. The file list in the window should stay as it was. Then create a ZIP and confirm the archive contains the game files, including that edit, and not the prefix or the installer. If the prefix snapshots succeed, `.reg` and `.setup.txt` notes appear beside the ZIP and are not inside the archive. Optionally run an EXE from Manual adjustments, close it, and click **EXE finished** before **Continue**.
5. Repeat with an existing ZIP of the same name and confirm that file is unchanged.
6. Stage an installer folder, then an ISO, and reach a ZIP from each.
7. Add a copy fix that only adds files and confirm **Apply All Fixes** does not ask again. Add one that overwrites, decline it, and confirm older applied fixes are still there.
8. Add an EXE or BAT patcher with a space in its folder name. Choose **Copy into the game folder, then run it there**. Confirm the queue shows which files would be replaced when that matters, stops until you click **Run**, then continues only after **Patcher finished**. Repeat with **Run it from its own folder** and confirm nothing is copied into the game.
9. Add a ZIP that contains an EXE. Choose **Copy the files only** and confirm it is not launched. Add it again, choose the program, and confirm a run row is added after the copy and is not launched until you click **Run**.
10. Close the window once with **Keep workspace** and once, on a disposable run, with **Delete workspace**.
