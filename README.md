# GameVault Archive Preparer

GameVault Archive Preparer is a Linux desktop app for people who want a Windows game in GameVault's portable archive format. It installs the game in a fresh Wine prefix, applies the fixes you choose, and zips the installed game folder.

The ZIP is the game folder only. It does not include the Wine prefix, the registry, or the installer, and creating it does not promise that the game will run. You choose the installer, the fixes, and the executable. The app does not look for games on its own.

`(W_P)` is GameVault's Windows-portable type tag.

## Features

- Stage an installer EXE, an installer folder, or an ISO, including UDF and hybrid discs.
- Run the installer in a new Wine prefix and leave the original media where it is.
- Copy fixes into the game folder, or run an EXE or BAT patcher there or from its own folder.
- Optionally run an EXE in the install prefix before packaging.
- Write a GameVault `(W_P)` ZIP of the selected game folder.
- Write two notes beside the ZIP describing registry and file changes outside the game folder.
- Optionally unpack that ZIP into a separate Wine prefix and try to start the game.

## Install

The usual download is the AppImage from a [GitHub release](https://github.com/zany130/gamevault-preparer/releases).

```bash
chmod +x GameVault_Archive_Preparer-*-x86_64.AppImage
./GameVault_Archive_Preparer-*-x86_64.AppImage
```

If FUSE is unavailable, start it with `--appimage-extract-and-run`.

The AppImage contains the app, its Qt libraries, libarchive, and 7-Zip. It does not contain Wine. Install Wine on the computer, or export it from a Distrobox as described below, so `wine`, `wineboot`, `wineserver`, and `winepath` are on `PATH`.

To run from a source checkout instead:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

A source install needs:

- Python 3.11 or newer
- PySide6
- libarchive and the `libarchive-c` Python package, for ISO9660 and 7z files
- 7-Zip (`7zz` or `7z`) on `PATH`, for UDF discs and for hybrid discs whose ISO9660 view is missing files

The app does not download these packages. If a command is missing, it reports that and stops. ZIP files and ordinary copying use the Python standard library.

Scratch files go in `~/.cache/gamevault-preparer`, or `$XDG_CACHE_HOME/gamevault-preparer` when that variable is set. Finished archives go in `~/GameVault`. Change either path in the window before staging.

## Wine and Winetricks

Wine is required. The app looks up `wine`, `wineboot`, `wineserver`, and `winepath` on `PATH`. [Winetricks](https://github.com/Winetricks/winetricks) is optional. The app does not install it. Winetricks downloads libraries only when you ask it to.

On **Verify ZIP**, you can install this set into the verification prefix: `d3dx9`, `d3dx10`, `d3dx11_43`, `d3dcompiler_43`, `d3dcompiler_47`, `xact`, `vcrun2008`, `vcrun2010`, and `vcrun2019`. **Open winetricks** starts the winetricks menu for that same prefix if you want something else.

### Bazzite and other immutable desktops

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

To use Winetricks from this container, install it there and export it the same way before you exit:

```bash
distrobox-export --bin /usr/bin/winetricks --export-path "${HOME}/.local/bin"
```

Log out and back in if a new terminal does not see `~/.local/bin`. Then confirm the four Wine commands are the exported wrappers:

```bash
command -v wine wineboot wineserver winepath
wine --version
```

Each path should be under `~/.local/bin`. Leave the scratch and output folders in your home directory. The window defaults already do that, and that home directory is the one the container can see.

## Use the window

1. Choose an installer EXE, an installer folder, or an ISO. Set **Source folder** to the directory that holds those files, such as `roms/pc`. **Select source** opens there, and the app remembers that folder. The window shows the folder that will be copied. Staging copies or extracts into a new workspace and leaves the original alone.
2. Launch the installer in a new prefix. The installer's default location is fine. Finish the installer, uncheck Launch game if it is offered, then click **Installation finished**.
3. Select the installed game folder inside that prefix's `drive_c`. The prefix, `drive_c` itself, `windows`, `Program Files`, and `Program Files (x86)` are too broad and are rejected.
4. Optionally add copy fixes or EXE/BAT patchers. A patcher can be copied into the game folder and run there, or run from its own folder. **Apply All Fixes** runs the enabled rows in order. New files are copied without another prompt. Overwrites wait for approval. Each patcher waits for a separate **Run** confirmation, then for **Patcher finished**. If a ZIP or 7z contains an EXE or BAT, you can copy the files only, or also run one of those programs from the game folder after the copy. Files copied into the game stay there; delete leftovers from Manual adjustments if they should not be in the ZIP.
5. Optionally open the game folder or the Wine prefix in your file manager and edit files yourself, or click **Run EXE** to start a program in this prefix. The app does not inspect those edits or whether the game runs. Close the program, click **EXE finished**, then click **Continue**. You can continue without opening either folder or running a program.
6. Enter a title and, if you want, a version and a four-digit year. Click **Create ZIP**.

The archive name looks like one of these:

- `Title (W_P).zip`
- `Title (v1.2) (W_P).zip`
- `Title (W_P) (2004).zip`
- `Title (v1.2) (W_P) (2004).zip`

The version is stored with one leading `v`. Brackets, reserved device names, trailing spaces or dots, and years that are not four digits are rejected so you can correct them. The ZIP contains the selected game folder's own files, including hidden files and empty folders, without an extra wrapper directory. An existing ZIP of the same name is left unchanged.

Closing the window asks you to **Keep workspace** or **Delete workspace**. Keep is the default. Delete removes only the workspace this session created, after Wine has stopped.

## Notes beside the ZIP

When the before-and-after snapshots succeed, two files are written beside the ZIP. They use the same name stem, such as `Title (W_P).reg` and `Title (W_P).setup.txt`. They are notes for you to read. The app does not import them, turn them into a Winetricks script, or apply them during **Verify ZIP**.

- `.reg` lists Software values the installer added under `HKEY_LOCAL_MACHINE\Software` and `HKEY_CURRENT_USER\Software`. Game and software registrations stay. Wine's own driver and environment keys are left out. Paths in the file may still point at the original install.
- `.setup.txt` lists each new `.exe`, `.msi`, `.bat`, and `.dll` outside the game folder by its path in the Wine prefix, such as `drive_c/windows/system32/d3dx9_43.dll`.

A snapshot that fails is logged, and that note is left out. An existing note is left unchanged. Fixes and edits made after the second snapshot are not part of the notes.

**Verify ZIP** unpacks the ZIP into a new Wine prefix, asks you to choose the game EXE, and tries to start it. The installation prefix is not reused, and the ZIP is not changed. If Winetricks is installed, you can install the common library set first, or click **Open winetricks** for its menu on that prefix. Close the program and click **Verify finished**. A window opening means the program started under Wine. It is not a promise that the game will run the same way elsewhere.

## Limitations

- You are responsible for the installer, the fixes, and the executable you select, and for deciding whether the result is compatible enough to keep.
- The original installer, folder, or ISO is copied or extracted into the workspace. It is not edited in place.
- A run under Wine, including **Verify ZIP**, does not tell you whether the game will work with Proton. This app does not start Proton.
- The notes are a record of what changed outside the game folder. They are not a restore script.
- The AppImage does not include Wine or Winetricks.

## Build and test

Build an AppImage locally with:

```bash
bash packaging/build-appimage.sh
```

The AppImage and its `.zsync` file are written to `dist/`. The build needs `zsyncmake` from the `zsync` package. It uses the Python on your `PATH`, so a release build on an older system travels to newer ones more reliably than a build made on a very new distribution. The GitHub workflow `.github/workflows/release-appimage.yml` runs the tests and then builds the AppImage when a release is published. You can also run that workflow by hand.

Run the automated tests from a source checkout:

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

Manual check after a change that touches staging, install, fixes, or export:

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

## Updates

From 1.0.1 on, the AppImage carries GitHub Releases update information for Gear Lever and AppImageUpdate:

`gh-releases-zsync|zany130|gamevault-preparer|latest|GameVault_Archive_Preparer-*-x86_64.AppImage.zsync`

Each of those releases also includes the matching `.zsync` file. Version 1.0.0 was published without that information, so install 1.0.1 yourself once. After that, Gear Lever can offer newer releases.

## License

GameVault Archive Preparer is released under the MIT License. See [LICENSE](LICENSE).

The AppImage also includes other programs and libraries under their own licenses. PySide6, shiboken6, and Qt 6.12.0 are `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. The Ubuntu build's libarchive is the 2-clause BSD license. Its 7-Zip package is LGPL-2.1+ with the RAR sources excluded, so the unRAR restriction does not apply to that binary. GTK, ICU, Kerberos, XCB, OpenSSL, and the other bundled system libraries keep the copyrights collected from the Ubuntu packages. Those notices are in [packaging/third-party](packaging/third-party). The build copies them into the AppImage at `usr/share/licenses/gamevault-preparer/`. They do not relicense this project.
