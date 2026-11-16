#!/usr/bin/env bash
# roachHub macOS 打包：roachHub.app → roachHub.dmg
#
#   ./packaging/build_macos.sh                 # 普通构建（ad-hoc 签名）
#   CODESIGN_IDENTITY="Developer ID Application: XXX (TEAMID)" ./packaging/build_macos.sh
#   NOTARY_PROFILE=roachhub-notary ./packaging/build_macos.sh   # 签 + 公证 + 装订
#   ./packaging/build_macos.sh --skip-install  # 不联网装 PyInstaller
#   ./packaging/build_macos.sh --arch universal2   # 同时支持 Intel / Apple Silicon
#
# **必须在 macOS 上跑**：PyInstaller 不能交叉编译，Windows 上生成的 exe 和应用
# 包是两种完全不同的产物（mach-o vs PE）。
#
# 为什么还要 ad-hoc 签名：arm64 机器上未签名的二进制会被内核直接拒绝执行
# （"killed: 9"），连"右键打开"都救不回来。ad-hoc（`-`）不做身份验证，只是让
# 系统能校验完整性 —— 用户第一次打开仍需右键 → 打开，或 `xattr -cr`。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAME="roachHub"
DIST="$ROOT/dist/macos"
WORK="$ROOT/build/macos"
SPEC="$ROOT/packaging/roachHub.spec"
APP="$DIST/$NAME.app"
DMG="$DIST/$NAME.dmg"
ICNS="$ROOT/assets/icons/roachhub.icns"
ICNS_SRC="$ROOT/assets/icons/roachhub_512.png"

SKIP_INSTALL=0
ARCH="${ARCH:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-install) SKIP_INSTALL=1 ;;
        --arch) ARCH="${2:-}"; shift ;;
        --arch=*) ARCH="${1#--arch=}" ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$ARCH" in
    ""|arm64|x86_64|universal2) ;;
    *) echo "未知架构: $ARCH（可选 arm64 / x86_64 / universal2）" >&2; exit 2 ;;
esac

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
die()  { printf '\033[31m错误: %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "只能在 macOS 上构建（PyInstaller 不能交叉编译）"

# --------------------------------------------------------------- 1. 解释器
step "定位 Python"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    if [ -x "$ROOT/.venv/bin/python" ]; then
        PY="$ROOT/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PY="$(command -v python3)"
        echo "    未找到 .venv，改用系统解释器: $PY"
    else
        die "找不到 python3。先执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    fi
fi

"$PY" -c 'import sys, PySide6; print("    Python %s | PySide6 %s" % (sys.version.split()[0], PySide6.__version__))' \
    || die "这个解释器里没有 PySide6（打包出来的会是空壳）"

# ------------------------------------------------------------ 2. PyInstaller
if [ "$SKIP_INSTALL" -eq 0 ]; then
    step "安装/更新 PyInstaller"
    "$PY" -m pip install --upgrade --disable-pip-version-check 'pyinstaller>=6.10' \
        || die "安装 PyInstaller 失败"
fi

"$PY" -c 'import PyInstaller; print("    PyInstaller", PyInstaller.__version__)' \
    || die "PyInstaller 不可用"

# ----------------------------------------------------------------- 3. 图标
# .icns 是 macOS 独有的容器格式（多分辨率 + 元数据），.ico 喂不进去。
step "准备 .icns 图标"

make_icns() {
    local src="$1" out="$2"
    [ -f "$src" ] || die "缺少图标源文件: $src（用 tools/make_icon.py 生成 roachhub_512.png）"

    local iconset="$WORK/$NAME.iconset"
    rm -rf "$iconset"
    mkdir -p "$iconset"

    local s
    for s in 16 32 128 256 512; do
        sips -z "$s" "$s" "$src" --out "$iconset/icon_${s}x${s}.png" >/dev/null
        sips -z "$((s * 2))" "$((s * 2))" "$src" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
    done

    iconutil -c icns "$iconset" -o "$out" || die "iconutil 失败（iconset 目录结构不对？）"
    rm -rf "$iconset"
}

mkdir -p "$WORK"
if [ ! -f "$ICNS" ] || [ "$ICNS_SRC" -nt "$ICNS" ]; then
    make_icns "$ICNS_SRC" "$ICNS"
    echo "    已生成 $ICNS"
else
    echo "    复用已有的 $ICNS"
fi

# ----------------------------------------------------------------- 4. 构建
step "PyInstaller 构建 -> $DIST"

rm -rf "$DIST" "$WORK/pyinstaller"
mkdir -p "$DIST"

# --target-arch 只在 macOS 上有效；universal2 要求所有依赖都是 universal2
# （PySide6 的 macOS wheel 就是 universal2，纯 Python 的 tomli 无所谓）。
ARCH_ARGS=()
if [ -n "$ARCH" ]; then
    ARCH_ARGS=(--target-arch "$ARCH")
    echo "    目标架构: $ARCH"
fi

"$PY" -m PyInstaller "$SPEC" --noconfirm ${ARCH_ARGS[@]+"${ARCH_ARGS[@]}"} \
    --distpath "$DIST" --workpath "$WORK/pyinstaller" \
    || die "PyInstaller 构建失败"

[ -d "$APP" ] || die "没生成 $APP"

# 收尾校验：资源必须真的进去了，否则程序能启动但画面是空的
for rel in "Contents/Frameworks/assets/sprites" "Contents/Frameworks/assets/sounds"; do
    [ -d "$APP/$rel" ] || die "缺少 $rel（datas 没打进去）"
done
[ -f "$APP/Contents/Info.plist" ] || die "缺少 Info.plist（BUNDLE 阶段失败）"
echo "    OK  $APP"

# ----------------------------------------------------------------- 5. 签名
# 注意顺序：先签名再打 dmg，否则 dmg 里的 app 是未签名的。
step "代码签名"

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    echo "    用证书: $CODESIGN_IDENTITY"
    codesign --force --deep --options runtime --timestamp \
        --sign "$CODESIGN_IDENTITY" "$APP" || die "签名失败"
else
    echo "    ad-hoc 签名（没有开发者证书；用户首次打开需右键 → 打开）"
    codesign --force --deep --sign - "$APP" || die "ad-hoc 签名失败"
fi
codesign --verify --strict --verbose=2 "$APP" 2>&1 | sed 's/^/    /' || true

# ------------------------------------------------------------------- 6. dmg
step "打包 dmg"

STAGE="$WORK/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"     # 拖拽安装的落点
rm -f "$DMG"
hdiutil create -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null \
    || die "hdiutil 打包失败"
rm -rf "$STAGE"
echo "    OK  $DMG ($(du -h "$DMG" | cut -f1))"

# --------------------------------------------------------------- 7. 公证
if [ -n "${NOTARY_PROFILE:-}" ]; then
    step "公证（notarytool）"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
        || die "公证失败（看上面的 status 日志）"
    xcrun stapler staple "$DMG" || die "装订票据失败"
    echo "    已公证并装订：用户双击即可打开"
else
    echo
    echo "提示：没有公证（未设置 NOTARY_PROFILE）。用户首次打开会被 Gatekeeper 拦一次，"
    echo "      让他们右键 → 打开，或执行: xattr -cr /Applications/$NAME.app"
fi

echo
printf '\033[32m完成。\033[0m\n'
echo "  * $DMG"
echo "  * 配置在 ~/Library/Application Support/roachHub/config.toml"
echo "  * 日志在 ~/Library/Logs/roachHub/roachHub.log"
