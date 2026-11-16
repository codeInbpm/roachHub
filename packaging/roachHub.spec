# -*- mode: python ; coding: utf-8 -*-
"""roachHub 的 PyInstaller 打包描述。

**不要直接 `pyinstaller roachHub.spec`**，用 `packaging/build_windows.ps1`
（它会先校验解释器/装 PyInstaller、再收尾校验产物）。

两条硬约束决定了这个文件的写法：

1. **资源按 `__file__` 定位**：`ResourceManager.get_assets_dir()` 是
   `Path(__file__).parent.parent / "assets"`，`food_sprites` 是 `parents[2]`。
   冻结后 `src/**.py` 被放进 `sys._MEIPASS/src/`，所以 assets 必须落在
   `_MEIPASS/assets` —— 也就是 `datas` 里的目的地要写 `assets/...`。
2. **config.toml 要落在 `_MEIPASS` 根**：`Config` 默认路径同样是
   `Path(__file__).parent.parent / "config.toml"`。丢了它程序也能跑（会写默认值），
   但用户改过的设置会全部消失，所以必须随包发一份。

`contents_directory="."` 是为了让 config.toml / assets 直接躺在 exe 旁边，
而不是塞进 PyInstaller 6.x 默认的 `_internal/`（那样用户根本找不到配置文件，
而 Config 又不会去 exe 根目录找）。
"""

import os
import sys
from pathlib import Path

import PySide6

ROOT = Path(SPECPATH).resolve().parent          # packaging/ 的上一级 = 项目根
NAME = "roachHub"
ONEFILE = os.environ.get("ROACHHUB_ONEFILE", "0") == "1"
IS_MAC = sys.platform == "darwin"

# ---------------------------------------------------------------- 随包资源
# assets/raw 是生成贴图用的原始素材（5.3MB），运行时不读，不打进去。
datas = [
    (str(ROOT / "assets" / "sprites"), "assets/sprites"),
    (str(ROOT / "assets" / "sounds"), "assets/sounds"),
    (str(ROOT / "assets" / "icons" / "roachhub.ico"), "assets/icons"),
    (str(ROOT / "assets" / "icons" / "tray_icon.ico"), "assets/icons"),
    (str(ROOT / "assets" / "icons" / "tray_icon.png"), "assets/icons"),
    (str(ROOT / "config.toml"), "."),
]

# QtMultimedia 的 ffmpeg 后端：multimedia 插件由官方 hook 收，但 avcodec-63.dll
# 这类运行库躺在 PySide6 根目录，部分 hook 版本会漏。漏了不会崩 —— QSoundEffect
# 会静默降级成没声音（voice.py 里 catch 住只打 warning），所以这里显式补一遍。
_pyside_dir = Path(PySide6.__file__).resolve().parent
binaries = [(str(p), ".") for p in _pyside_dir.glob("av*.dll")]
binaries += [(str(p), ".") for p in _pyside_dir.glob("libav*.dylib")]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # voice.py / squash_voice.py 是在函数里 import 的，静态分析能扫到，
        # 但这几个是"没有声音也能跑"的软依赖，显式列出来免得静默丢功能。
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # tools/ 只在开发期用（依赖 Pillow），不该被跟着进来
        "tools",
        "tkinter",
        "PIL",
        "numpy",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon_win = ROOT / "assets" / "icons" / "roachhub.ico"
_icon_mac = ROOT / "assets" / "icons" / "roachhub.icns"

_exe_kwargs = dict(
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 双击不出黑框；日志走 logs/roachHub.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


def _collect(*args, **kwargs):
    """onedir 收尾。

    `contents_directory` 是 PyInstaller 6.x 的参数（"." = 老式平铺布局）；
    老版本没有这个关键字，退化成默认的 `_internal/` —— 那种情况下
    config.toml 会落在 `_internal/config.toml`，脚本收尾时会把它打印出来。

    用签名探测而不是 try/except：COLLECT 内部抛的 TypeError 不能被这里吞掉，
    否则真正的构建错误会被伪装成"参数不支持"。
    """
    import inspect

    if "contents_directory" in inspect.signature(COLLECT).parameters:
        return COLLECT(*args, contents_directory=".", **kwargs)
    print("[spec] 当前 PyInstaller 不支持 contents_directory，退回 _internal/ 布局")
    return COLLECT(*args, **kwargs)


if IS_MAC:
    # ------------------------------------------------------------ macOS 分支
    # 用 packaging/build_macos.sh 驱动（它负责生成 .icns、签名、打 dmg）。
    # 代码侧的 mac 适配在 src/utils 里：mac_hotkeys（Carbon 全局热键）、
    # mac_hit_test（ignoresMouseEvents 轮询穿透）、mac_occlusion（CGWindowList
    # 遮挡检测）、platform_utils._setup_macos_styles（层级 / 跨 Space）。
    # 这几块**只能在真机 mac 上验证**，Windows 上只验证了"拿不到原生能力时降级不崩"。
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
              icon=str(_icon_mac) if _icon_mac.exists() else None, **_exe_kwargs)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False, strip=False)
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(_icon_mac) if _icon_mac.exists() else None,
        bundle_identifier="com.roachhub.desktop",
        info_plist={
            # 不出现在 Dock / Cmd-Tab 里：它是个托盘 + 覆盖层程序
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleDisplayName": "roachHub",
            "CFBundleShortVersionString": "0.1.0",
            # 集显/独显切换的声明，免得全屏 60FPS 覆盖层被系统判成"静态应用"
            "NSSupportsAutomaticGraphicsSwitching": True,
        },
    )
elif ONEFILE:
    # 单文件 exe。**代价**：启动时解包到临时目录，`_MEIPASS` 是一次性的，
    # Config.save() 写进去的设置在退出后就没了（每次启动都是默认配置）。
    # 想要"一个 exe 并且设置能存住"，需要把 Config 的默认路径改成用户目录。
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        icon=str(_icon_win) if _icon_win.exists() else None,
        **_exe_kwargs,
    )
else:
    # 便携目录（默认）：exe + 依赖平铺在一个文件夹里，整个文件夹拷走就能用。
    exe = EXE(
        pyz, a.scripts, [], exclude_binaries=True,
        icon=str(_icon_win) if _icon_win.exists() else None,
        **_exe_kwargs,
    )
    coll = _collect(exe, a.binaries, a.datas, name=NAME, upx=False, strip=False)
