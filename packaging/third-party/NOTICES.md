# Third-party notices

This file is not the license for GameVault Archive Preparer. The application source is under the MIT License in `LICENSE` at the repository root. The components below keep their own licenses. Nothing here changes the MIT license of this project.

The AppImage build copies this directory to `usr/share/licenses/gamevault-preparer/third-party/` inside the image. Qt and the bundled 7-Zip executable are separate files, so an extracted AppImage can have those files replaced.

These notices were checked against `GameVault_Archive_Preparer-1.0.0-x86_64.AppImage`, the image built on GitHub Actions `ubuntu-24.04`. The file list is `system/COMPONENTS.txt`. On a machine with dpkg, `packaging/collect_bundle_notices.py` replaces that list and `system/copyright/` with the copyright files of the libraries that build actually copied, and the build stops if a bundled system file has no copyright. A build without dpkg does not pretend to have repeated that check.

## PySide6, shiboken6, and Qt

The audited image contains PySide6 6.12.0, shiboken6 6.12.0, and Qt 6.12.0. `libQt6Core.so.6` in that image identifies itself as `Qt 6.12.0`. Their shared license expression is `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. The same expression is in the wheel metadata for `PySide6`, `PySide6_Essentials`, `PySide6_Addons`, and `shiboken6`. The expected version is `qt/VERSION`. The build refuses to package a different version or a different license expression until this notice is updated.

The copies in the image are unmodified shared libraries (`libQt6*.so.6`, `libpyside6.abi3.so.6.12`, and `libshiboken6.abi3.so.6.12`), including a second copy under `PySide6/Qt/lib/`. The Qt plugins under `PySide6/Qt/plugins/`, including the GTK 3 platform theme, are part of the same wheel and the same license. Qt WebEngine is not in the image. The license texts are `qt/LGPL-3.0.txt`, `qt/GPL-3.0.txt`, and `qt/GPL-2.0.txt`. The wheels do not ship those texts.

Corresponding source for that version is published by the Qt Project:

- https://download.qt.io/official_releases/qt/6.12/6.12.0/single/qt-everywhere-src-6.12.0.tar.xz
- https://pypi.org/project/PySide6/6.12.0/#files
- https://pypi.org/project/shiboken6/6.12.0/#files
- https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0

## Replacing an LGPL library

Qt, PySide6, shiboken6, 7-Zip, and the other LGPL libraries in the image are separate shared objects, not copied into the executable. Extract the AppImage with `--appimage-extract`. The files are under `squashfs-root/usr/bin/gvprep/_internal/`. Qt also has a copy under `squashfs-root/usr/bin/gvprep/_internal/PySide6/Qt/lib/`. Replace a library with an interface-compatible build, then run `squashfs-root/AppRun`. The image is not signed, and running the extracted copy does not require a key.

On a dpkg build, `system/COMPONENTS.txt` records each system library's binary package, its copyright file, and the Ubuntu source package version with a Launchpad page for that version. That page is the corresponding source for the binary that was copied. The image does not contain those source archives.

## ICU

The Qt 6.12.0 wheel bundles ICU 73.2. `u_getVersion` on `libicuuc.so.73` returns 73.2.0.0. The libraries are `libicudata.so.73`, `libicui18n.so.73`, and `libicuuc.so.73`, including the copy under `PySide6/Qt/lib/`. They are not Debian packages. The wheel does not include a license file. The data library carries Unicode's copyright notice and points at https://www.unicode.org/copyright.html.

The license that applies to this ICU 73.2 copy is `icu/LICENSE-73.2.txt`, the `icu4c/LICENSE` file from the ICU 73.2 release. It is the Unicode, Inc. License Agreement - Data Files and Software, copyright 1991-2023 Unicode, Inc., followed by the third-party notices in that file. The Debian MIT wording in the ICU 74 copyright is a different text and is not this notice.

Source for this version:

- https://github.com/unicode-org/icu/releases/tag/release-73-2
- https://github.com/unicode-org/icu/releases/download/release-73-2/icu4c-73_2-src.tgz

The Ubuntu build also bundles the system ICU 74 libraries `libicudata.so.74` and `libicuuc.so.74` from `libicu74`. Their notice is `system/copyright/libicu74/copyright`. Its `Files: *` section is the Debian MIT wording for ICU 74. Its `debian/*` section is GPL-3 and covers Debian packaging files, which are not the shared libraries. ICU 74's corresponding source is the Ubuntu source package named for `libicu74` in `system/COMPONENTS.txt`.

## 7-Zip

The Ubuntu image is built with the `7zip` package, version 23.01+dfsg-11 in the audit. Debian excludes `CPP/7zip/Compress/Rar*` and states that the RAR code is under the non-free unRAR restriction, so that restriction does not apply to the binary this package builds. The package license is LGPL-2.1+, plus the public-domain and BSD-3-clause portions named in `system/copyright/7zip/copyright`. The LGPL-2.1 text those copyright files point at is `system/common-licenses/LGPL-2.1`.

`7zip/License.txt` and `7zip/copying.txt` are upstream 7-Zip 26.03 texts from a different build. They describe the unRAR restriction. They are not the license of the Ubuntu binary.

Ubuntu's `/usr/bin/7z` is a shell script that executes `/usr/lib/7zip/7z`. The published 1.0.0 image copied that script, so it does not contain the 7-Zip executable. The AppImage build follows the wrapper and copies the ELF, still named `7z` or `7zz`, and stops if the bundled file is still a script.

Corresponding source for the Ubuntu binary is the Ubuntu `7zip` source package for the version named in `system/COMPONENTS.txt`. For the audited package that page is https://launchpad.net/ubuntu/+source/7zip/23.01+dfsg-11. That source excludes the RAR code. The image does not contain that source archive.

## libarchive

`libarchive.so.13` in the audited image is Ubuntu `libarchive13t64` 3.7.2-2ubuntu0.9. Its notice is `system/copyright/libarchive13t64/copyright` (2-clause BSD, with the per-file terms in that file). `libarchive/COPYING` is an upstream text from libarchive 3.8.7 and is not the notice for this library.

`libarchive-c` 5.3, the Python binding, is CC0-1.0. See `libarchive-c/LICENSE.md`.

## Python

`libpython3.12.so.1.0` in the published 1.0.0 image is CPython 3.12.15 from GitHub Actions `actions/setup-python` (`/opt/hostedtoolcache/Python/3.12.15/x64`). It is not Ubuntu's `libpython3.12t64` package. The Python Software Foundation license is `python/LICENSE.txt`. The build replaces that file in the image with `LICENSE.txt` from the CPython prefix it froze. When that library instead comes from an Ubuntu package, its row in `system/COMPONENTS.txt` points at the Debian copyright and the Ubuntu source package.

## PyInstaller

PyInstaller 6.22.3 produces the executable. PyInstaller is GPL-2.0-or-later. Its bootloader exception allows the unmodified bootloader to be combined with this program and distributed under the MIT License. The exception does not cover changes to the bootloader. The text is `pyinstaller/COPYING.txt`.

PyInstaller uses altgraph, which is MIT. See `altgraph/LICENSE`.

## OpenSSL

The audited image bundles Ubuntu `libssl3t64` 3.0.13-0ubuntu3.16 as `libssl.so.3` and `libcrypto.so.3`. The library license is Apache-2.0. The Debian copyright file points at `/usr/share/common-licenses/Apache-2.0`; that text is `system/common-licenses/Apache-2.0`. The copyright file itself is `system/copyright/libssl3t64/copyright`. A Perl template under `external/perl/` is Artistic or GPL-1+; that file is not `libssl.so.3`. The Ubuntu package does not ship a separate Apache `NOTICE` file. `openssl/LICENSE.txt` is an Apache-2.0 text kept beside the Debian notice.

## GTK, Kerberos, XCB, and the other system libraries

The audited image bundles the shared libraries listed in `system/COMPONENTS.txt`, including:

- GTK 3 (`libgtk-3.so.0`, `libgdk-3.so.0`) from `libgtk-3-0t64` 3.24.41-4ubuntu1.3. The library copyright says LGPL-2+ and LGPL-2.1+ and Expat. GPL-3+ in that file applies to `debian/tests/run-with-display`, which is not the shared library. The copyright file also names other per-file terms.
- MIT Kerberos (`libkrb5.so.3`, `libgssapi_krb5.so.2`, `libk5crypto.so.3`, `libkrb5support.so.0`) from the 1.20.1 packages. The notice, including its redistribution conditions, is `system/copyright/libkrb5-3/copyright` and the matching files for the other Kerberos libraries. `libkeyutils.so.1` and `libcom_err.so.2` have their own copyright files.
- XCB, including `libxcb-cursor.so.0` from `libxcb-cursor0`. Its license is MIT/X, in `system/copyright/libxcb-cursor0/copyright`. The other `libxcb` and `libX` libraries have their own files in the same directory.
- `libstdc++.so.6` and `libgcc_s.so.1` from GCC 14.2.0. Their packages do not contain a copyright file; Debian links their documentation to `gcc-14-base`. That file includes the GCC Runtime Library Exception and is `system/copyright/gcc-14-base/copyright`.

Every other system library in the image is mapped the same way. Debian copyright files that name a file in `/usr/share/common-licenses/` are satisfied by the copy under `system/common-licenses/`.

## What this audit does not settle

- The source locations above are public archives for the named versions. The image does not include those source archives. Nothing here is a legal opinion, and counsel has not reviewed this distribution.
- The GTK copyright file lists extra per-file licenses, including GPL-3+ for a Debian test script. This audit did not prove that `libgtk-3.so.0` contains none of the GPL-only source files named there.
- The dpkg collection during a build records the package versions on that machine. Those versions can differ from the noble-updates debs used for the checked-in `system/copyright/` snapshot. The published 1.0.0 image is unchanged and does not contain this notice directory.
