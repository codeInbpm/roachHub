# 打包与跨平台适配（Windows / macOS）

本文说明产物怎么出、资源怎么定位、以及 macOS 上为什么必须另写一套平台层。

## 1. 平台能力矩阵

| 能力 | Windows | macOS | 实现 |
| --- | --- | --- | --- |
| 全屏置顶覆盖层 | ✅ | ✅ | `overlay_window` + `platform_utils` |
| 只在虫子身上接收鼠标 | ✅ `WM_NCHITTEST` | ✅ `ignoresMouseEvents` 轮询 | `win_hit_test` / `mac_hit_test` |
| 全局热键（4 个） | ✅ `RegisterHotKey` | ✅ Carbon `RegisterEventHotKey` | `hotkeys` / `mac_hotkeys` |
| 面板遮挡检测（"洞"跟着 z-order 走） | ✅ Win32 z-order 遍历 | ✅ `CGWindowListCopyWindowInfo` | `win_occlusion` / `mac_occlusion` |
| 压住任务栏 / Dock / 菜单栏 | ✅ `WS_EX_*` + 覆盖全屏 | ✅ level 1000 + 跨 Space | `platform_utils._setup_*` |
| 托盘图标 | ✅ | ✅（菜单栏） | `tray` |
| 配置 / 日志落盘 | ✅ exe 旁 | ✅ `~/Library/...` | `app_paths` |

不支持检测的平台（Linux）会退化成 `QRegion` 遮罩 4Hz 重建 —— 能跑，但没有逐点精度。

## 2. 构建

```bash
# Windows：便携目录（默认）→ dist\windows\roachHub\
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# 单文件 exe（设置不持久，见下）
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -OneFile

# macOS：.app → dmg（必须在 mac 上跑，PyInstaller 不能交叉编译）
./packaging/build_macos.sh
CODESIGN_IDENTITY="Developer ID Application: ..." NOTARY_PROFILE=xxx ./packaging/build_macos.sh
```

构建描述在 `packaging/roachHub.spec`，两条脚本都只是它的驱动 + 校验 + 收尾。

**一个真踩过的坑**：平铺布局的 `contents_directory="."` 必须写在 **`EXE()`** 上。
PyInstaller 6.22 起 `COLLECT` 不再读自己这个参数，而是从 `EXE` 继承
（`self.contents_directory = arg.contents_directory`）；写在 `COLLECT` 里会被
`**kwargs` 静默吃掉，产物照旧落进 `_internal/` —— 而 `Config` / `ResourceManager`
按 `_MEIPASS` 根查找，装完就是"能启动、但没贴图没声音、设置也不保存"。
判断依据：产物目录里**不该**出现 `_internal`，`config.toml` 必须和 exe 同级。

## 3. 资源与可写路径

`ResourceManager` / `Config` / `food_sprites` 历史上都是按
`Path(__file__).resolve().parent.parent` 定位的，冻结后它等于 `sys._MEIPASS`。于是：

| 形态 | `_MEIPASS` | 资源 | 配置 / 日志 |
| --- | --- | --- | --- |
| 开发 | 项目根 | 项目根 | 项目根（`logs/`） |
| Windows 便携目录 | exe 旁（spec 用 `contents_directory="."`） | exe 旁 `assets/` | exe 旁 `config.toml` + `logs\` |
| Windows 单文件 | `%TEMP%\_MEIxxxx`，退出即删 | 临时目录 | **写进去就丢** —— 这就是单文件模式"设置不持久"的原因 |
| macOS `.app` | `Contents/Frameworks` | 同名子目录 | `~/Library/Application Support/roachHub`、`~/Library/Logs/roachHub` |

mac 之所以不能用 `_MEIPASS` 存配置：那是 `.app` 内部，写入会改掉包内容 →
代码签名 seal 失效 → 公证过的包下一次启动直接被 Gatekeeper 拒。

路径推导只在 `src/utils/app_paths.py` 里做一次，其它模块不许自己算。
打包时随包发一份 `config.toml`，mac 首次启动会把它**播种**到用户目录
（而不是用 `DEFAULT_CONFIG` 重写，否则用户调好的参数白调）。

## 4. macOS 上为什么每一块都要重写

### 4.1 鼠标穿透

Windows 的 `WM_NCHITTEST` 是"逐点问系统"，mac 没有等价物：`NSView.hitTest:`
只在事件已经进了本应用之后才被调用，挡不住"点到桌面图标"。

`QRegion` 遮罩也不能当兜底：mac 上 `setMask` 走窗口形状/图层遮罩，
"形状外能否穿透"取决于 Qt 实现细节，而兜底路径里"持拍时整屏接管"要把遮罩设成
全屏 —— 一旦语义不成立，**整块桌面会点不动**，这是不能接受的失败模式。

现在的做法：`NSWindow.ignoresMouseEvents` 是硬开关，每帧用 `QCursor.pos()`
判一次"这个点该不该接管"，然后开关它。判据复用同一个 `accepts_mouse_at()`，
所以两平台手感一致。代价是最多一帧（16ms）延迟。

### 4.2 全局热键

另两条路都要授权：`addGlobalMonitorForEvents` 需要"辅助功能"，`CGEventTap`
还要输入监控，而且授权绑代码签名 —— 每次重新打包都要用户重授一次。
Carbon 的 `RegisterEventHotKey` 不需要任何权限，QHotkey 等库也走这条。

坑：修饰键是**独立的一套位**（cmd 0x0100 / shift 0x0200 / option 0x0800 /
control 0x1000），和 Windows `MOD_*` 不能混用；键码表也是 `kVK_*`，
只能按**键名**从 `key_label(vk)` 再查一次。

### 4.3 遮挡检测

`CGWindowListCopyWindowInfo` 返回的数组就是前到后的顺序，语义与 Win32 的
`GetTopWindow`/`GW_HWNDNEXT` 一一对应：往后走，遇到自己的面板窗口就停，
途中属于别的进程、layer 0、且与面板相交的窗口就是"压在面板上"的。
靠 `ownerPID` 滤掉自己（覆盖层在第 1000 层，永远在面板之前）。
只读结构化字段（pid/layer/bounds/number），**不需要屏幕录制授权**。

### 4.4 窗口层级

`WindowStaysOnTopHint` 只管到 `NSFloatingWindowLevel`(3)，而 Dock 在第 20 层、
菜单栏第 24 层。要 `setLevel:` 到 `kCGScreenSaverWindowLevel`(1000)，
再加 `CanJoinAllSpaces | Stationary | FullScreenAuxiliary | IgnoresCycle`
（否则切 Space / 进全屏 App 时虫子凭空消失），以及 `hidesOnDeactivate = NO`
（`LSUIElement` 应用失活默认隐藏自己的窗口）。

## 5. 已知差异与限制

- **首次点击可能只激活应用**：边框窗口不是 `NSPanel`，mac 上没有
  `WS_EX_NOACTIVATE` 那种"点击不夺焦"的开关。表现为"第一下只是把应用叫起来，
  第二下才拖得动"。
- **穿透有一帧延迟**：见 4.1。快速划过时偶尔会漏掉一次按下。
- **未公证的包**：首次打开被 Gatekeeper 拦，需右键 → 打开，或
  `xattr -cr /Applications/roachHub.app`。要免这一步就设 `CODESIGN_IDENTITY`
  + `NOTARY_PROFILE`。
- **mac 专用代码只能在真机验证**：Windows 上跑得到的只有"降级不崩"和
  "键码表 / 修饰键换算"这些纯数据部分（见 `tools/interaction_test.py` 的 `[14]`）。

## 6. 打包后的自检

```bash
# 两边都能跑（离屏）
python tools/interaction_test.py      # 含 [14] 跨平台适配层
python tools/smoke_test.py
python tools/swatter_test.py
```

真机 mac 上另外确认四件事：热键四个都生效、虫子身上能点中而桌面照常可点、
控制台沉到浏览器后面时不再出现"空洞"、切 Space / 进全屏 App 后虫子还在。

## 7. 发布（GitHub Actions）

`.github/workflows/release.yml`：

* **触发**：push `v*` tag（如 `v0.1.0`）→ 出两个包并挂到 GitHub Release；
  `workflow_dispatch` 手动跑只构建、不发布，用来验证"换台机器能不能打包"。
* **Windows**：`windows-latest` 跑 `build_windows.ps1` → `roachHub-windows-x64.zip`。
* **macOS（Apple Silicon）**：`macos-latest` 跑 `build_macos.sh --arch universal2`；通用二进制
  要求解释器和所有依赖都是双架构，PySide6 的 macOS wheel 是 universal2，但 setup-python
  装的解释器不是（实测直接失败）—— 所以会自动退回 runner 原生架构（arm64），文件名按
  `lipo -archs` 的真实结果命名，不会出现"写着 universal2 其实只有 arm64"。
* **macOS（Intel）**：另开一个 `macos-26-intel` job 原生构建 x86_64。它带
  `continue-on-error`，镜像不可用时只是少一个包，不影响其余两个发布。
* **为什么 Windows 也要在 CI 里重打一遍**：Release 附件只能由 CI 的 `GITHUB_TOKEN`
  上传；本地打出来的包只能手动拖，无法自动化。

资产命名不要改：README 的下载表格和 Release 说明都按这两个名字写。
