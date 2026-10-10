#!/usr/bin/env bash
# Build a GameVault Archive Preparer AppImage.
# Wine is not included. libarchive.so.13 is copied in for ISO and 7z reading.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VERSION="${VERSION:-${GITHUB_REF_NAME:-}}"
if [[ -z "$VERSION" ]]; then
    VERSION="$("$PYTHON" - <<'PY'
import pathlib
import re

text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
if not match:
    raise SystemExit("version is missing from pyproject.toml")
print(match.group(1))
PY
)"
fi
VERSION="${VERSION#v}"

ARCH="$(uname -m)"
BIN_NAME="gvprep"
DESKTOP_ID="gamevault-preparer"
BUILD_VENV="$ROOT/packaging/.venv"
APPDIR="$ROOT/packaging/AppDir"
CACHE="$ROOT/packaging/.cache"
WORK="$ROOT/packaging/build"
DIST="$ROOT/dist"

rm -rf "$APPDIR" "$WORK"
mkdir -p \
    "$APPDIR/usr/bin" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
    "$DIST" \
    "$CACHE"

if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    "$PYTHON" -m venv "$BUILD_VENV"
fi
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT/requirements.txt" "pyinstaller>=6.11"

# Run outside the project root so this repo's packaging.py does not shadow
# the third-party packaging package PyInstaller imports.
mkdir -p "$WORK"
(
    cd "$WORK"
    python -m PyInstaller \
        --noconfirm \
        --clean \
        --windowed \
        --name "$BIN_NAME" \
        --distpath "$WORK/dist" \
        --workpath "$WORK/work" \
        --specpath "$WORK" \
        --paths "$ROOT" \
        --collect-submodules libarchive \
        --hidden-import libarchive \
        --hidden-import libarchive.ffi \
        "$ROOT/app.py"
)

rm -rf "$APPDIR/usr/bin/$BIN_NAME"
cp -a "$WORK/dist/$BIN_NAME" "$APPDIR/usr/bin/$BIN_NAME"
ln -s "$BIN_NAME/$BIN_NAME" "$APPDIR/usr/bin/$DESKTOP_ID"

find_library() {
    local soname="$1"
    local source
    source="$(ldconfig -p | awk -v name="$soname" '$1 == name && !found { print $NF; found = 1 }')"
    if [[ -z "$source" || ! -e "$source" ]]; then
        echo "Could not find ${soname}. Install the matching system library." >&2
        exit 1
    fi
    printf '%s\n' "$source"
}

# Keep libarchive beside the Qt and Python libraries. A second copy in another
# directory loads a second libcrypto and crashes. Missing pieces are copied
# into the same directory PyInstaller already uses.
INTERNAL="$APPDIR/usr/bin/$BIN_NAME/_internal"
declare -A BUNDLED_LIBS=()
bundle_dependencies() {
    local binary="$1"
    local line soname source dest
    while IFS= read -r line; do
        [[ "$line" == *" => "* ]] || continue
        soname="${line##*$'\t'}"
        soname="${soname%% => *}"
        case "$soname" in
            linux-vdso.so.1|ld-linux*.so.*|libc.so.6|libm.so.6|libdl.so.2|libpthread.so.0|librt.so.1|libresolv.so.2|libutil.so.1)
                continue
                ;;
        esac
        [[ -n "${BUNDLED_LIBS[$soname]:-}" ]] && continue
        dest="$INTERNAL/$soname"
        if [[ -e "$dest" ]]; then
            BUNDLED_LIBS[$soname]=1
            continue
        fi
        source="${line#* => }"
        source="${source%% *}"
        if [[ ! -e "$source" ]]; then
            echo "Could not find ${soname}, needed by ${binary}." >&2
            exit 1
        fi
        cp -L "$source" "$dest"
        chmod 0755 "$dest"
        BUNDLED_LIBS[$soname]=1
        bundle_dependencies "$source"
    done < <(ldd "$binary")
}

if [[ ! -e "$INTERNAL/libarchive.so.13" ]]; then
    cp -L "$(find_library libarchive.so.13)" "$INTERNAL/libarchive.so.13"
    chmod 0755 "$INTERNAL/libarchive.so.13"
fi
BUNDLED_LIBS["libarchive.so.13"]=1
bundle_dependencies "$INTERNAL/libarchive.so.13"

# Qt's xcb plugin asks for this at startup. Shipping it avoids a host package.
if [[ ! -e "$INTERNAL/libxcb-cursor.so.0" ]] && ldconfig -p | awk '$1 == "libxcb-cursor.so.0" { found = 1 } END { exit !found }'; then
    cp -L "$(find_library libxcb-cursor.so.0)" "$INTERNAL/libxcb-cursor.so.0"
fi

install -m 0644 "$ROOT/packaging/${DESKTOP_ID}.desktop" "$APPDIR/${DESKTOP_ID}.desktop"
install -m 0644 "$ROOT/packaging/${DESKTOP_ID}.desktop" "$APPDIR/usr/share/applications/${DESKTOP_ID}.desktop"
install -m 0644 "$ROOT/packaging/${DESKTOP_ID}.png" "$APPDIR/${DESKTOP_ID}.png"
install -m 0644 "$ROOT/packaging/${DESKTOP_ID}.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/${DESKTOP_ID}.png"
install -m 0755 "$ROOT/packaging/AppRun" "$APPDIR/AppRun"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APPDIR/${DESKTOP_ID}.desktop"
fi

TOOL="$CACHE/appimagetool-${ARCH}.AppImage"
if [[ ! -x "$TOOL" ]]; then
    curl -fL --retry 3 -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "$TOOL"
fi

LIBARCHIVE="$INTERNAL/libarchive.so.13" GVPREP_CHECK_LIBARCHIVE=1 \
    "$APPDIR/usr/bin/$BIN_NAME/$BIN_NAME"

OUTPUT="$DIST/GameVault_Archive_Preparer-${VERSION}-${ARCH}.AppImage"
rm -f "$OUTPUT"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" VERSION="$VERSION" \
    "$TOOL" "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"
echo "Wrote $OUTPUT"
