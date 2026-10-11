#!/usr/bin/env bash
# Build a GameVault Archive Preparer AppImage.
# Wine is not included. libarchive.so.13 is copied in for ISO9660 and 7z
# archives. A 7zz or 7z binary is copied in for UDF and hybrid discs.
# appimagetool -u embeds the GitHub Releases update information and, with
# zsyncmake installed, writes the matching .zsync file beside the AppImage.
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
UPDATE_INFORMATION="gh-releases-zsync|zany130|gamevault-preparer|latest|GameVault_Archive_Preparer-*-x86_64.AppImage.zsync"
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
ORIGINS="$WORK/bundle-origins.tsv"
: > "$ORIGINS"
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
        source="${line#* => }"
        source="${source%% *}"
        if [[ -n "$source" && -e "$source" ]]; then
            printf '%s\t%s\n' "$soname" "$source" >> "$ORIGINS"
        fi
        [[ -n "${BUNDLED_LIBS[$soname]:-}" ]] && continue
        dest="$INTERNAL/$soname"
        if [[ -e "$dest" ]]; then
            BUNDLED_LIBS[$soname]=1
            continue
        fi
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

LIBARCHIVE_SOURCE="$(find_library libarchive.so.13)"
printf '%s\t%s\n' "libarchive.so.13" "$LIBARCHIVE_SOURCE" >> "$ORIGINS"
if [[ ! -e "$INTERNAL/libarchive.so.13" ]]; then
    cp -L "$LIBARCHIVE_SOURCE" "$INTERNAL/libarchive.so.13"
    chmod 0755 "$INTERNAL/libarchive.so.13"
fi
BUNDLED_LIBS["libarchive.so.13"]=1
bundle_dependencies "$INTERNAL/libarchive.so.13"

# Ubuntu's /usr/bin/7z is a shell wrapper that execs /usr/lib/7zip/7z. Copy
# that ELF, still named 7z, so the image contains the program the notice
# describes and the wrapper's host path is not required at runtime.
resolve_7zip() {
    local path="$1" target
    if head -c 2 "$path" | grep -q '#!'; then
        target="$(awk '/^exec[[:space:]]+\// { print $2; exit }' "$path")"
        if [[ -z "$target" || ! -e "$target" ]]; then
            echo "7-Zip command ${path} is a wrapper and its target was not found." >&2
            exit 1
        fi
        if head -c 2 "$target" | grep -q '#!'; then
            echo "7-Zip target ${target} is still a script." >&2
            exit 1
        fi
        printf '%s\n' "$target"
        return
    fi
    printf '%s\n' "$path"
}

SEVENZIP=""
for candidate in 7zz 7z; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SEVENZIP="$(command -v "$candidate")"
        break
    fi
done
if [[ -z "$SEVENZIP" ]]; then
    echo "Install 7zip (the 7zz or 7z command) so the AppImage can stage UDF discs." >&2
    exit 1
fi
SEVENZIP_BIN="$(resolve_7zip "$SEVENZIP")"
SEVENZIP_NAME="$(basename "$SEVENZIP")"
cp -L "$SEVENZIP_BIN" "$INTERNAL/$SEVENZIP_NAME"
chmod 0755 "$INTERNAL/$SEVENZIP_NAME"
printf '%s\t%s\n' "$SEVENZIP_NAME" "$SEVENZIP_BIN" >> "$ORIGINS"

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

license_dest="$APPDIR/usr/share/licenses/gamevault-preparer"
mkdir -p "$license_dest"
install -m 0644 "$ROOT/LICENSE" "$license_dest/LICENSE"
cp -a "$ROOT/packaging/third-party/." "$license_dest/third-party/"
python "$ROOT/packaging/collect_bundle_notices.py" \
    --internal "$INTERNAL" \
    --notices "$license_dest/third-party" \
    --origins "$ORIGINS" \
    --venv "$BUILD_VENV" \
    --python-prefix "$(python -c 'import sys; print(sys.base_prefix)')"

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

if ! command -v zsyncmake >/dev/null 2>&1; then
    echo "zsyncmake is required so the AppImage can be updated. Install the zsync package." >&2
    exit 1
fi

OUTPUT="$DIST/GameVault_Archive_Preparer-${VERSION}-${ARCH}.AppImage"
# appimagetool returns to $OWD and writes the .zsync file there under the
# AppImage's base name. Clear OWD so that file is created in this directory.
unset OWD
rm -f "$OUTPUT" "${OUTPUT}.zsync" "$ROOT/$(basename "$OUTPUT").zsync"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" VERSION="$VERSION" \
    "$TOOL" -u "$UPDATE_INFORMATION" "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"

embedded="$("$OUTPUT" --appimage-updateinformation | tr -d '\r')"
if [[ "$embedded" != "$UPDATE_INFORMATION" ]]; then
    echo "The AppImage update information is '${embedded}', expected '${UPDATE_INFORMATION}'." >&2
    exit 1
fi
zsync_written="$ROOT/$(basename "$OUTPUT").zsync"
if [[ ! -s "$zsync_written" ]]; then
    echo "appimagetool did not write ${zsync_written}" >&2
    exit 1
fi
header="$(head -n 8 "$zsync_written")"
case "$header" in
    *"Filename: $(basename "$OUTPUT")"*) ;;
    *)
        echo "The zsync file does not name $(basename "$OUTPUT")." >&2
        exit 1
        ;;
esac
if [[ "$zsync_written" != "${OUTPUT}.zsync" ]]; then
    mv -f "$zsync_written" "${OUTPUT}.zsync"
fi
echo "Wrote $OUTPUT"
echo "Wrote ${OUTPUT}.zsync"
