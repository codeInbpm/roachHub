# 启动方式、图标与"只留一个窗口"

## 1. 为什么会出现两个窗口

双击 `main.py`（或 `.py` 关联到 Python Launcher）时，实际链路是：

```
explorer.exe  →  py.exe  →  python.exe  →  你的脚本
```

`python.exe` 是**控制台程序**，Windows 会为它配一个控制台窗口 —— 任务栏里那个黑底
`>_` 加 Python 图标的窗口。加上托盘图标，就是用户看到的"两个窗口"。

### 判据：这个控制台到底是谁的

不能无脑 `ShowWindow(hwnd, SW_HIDE)`：用户从 cmd / Windows Terminal 里
`python main.py` 时，那个控制台是**他自己的终端窗口**，藏掉等于把人家的窗口弄没了。

`src/utils/console_window.py` 用 `GetConsoleProcessList` + `QueryFullProcessImageNameW`
把控制台里**所有**进程的名字列出来，只有**每一个**都是 Python 启动器/解释器时才动手：

```
双击：      names = ['python.exe', 'py.exe']                      → 藏
终端里跑：  names = ['python.exe', 'cmd.exe', 'bash.exe', ...]     → 不动
```

> 曾经想用"控制台里只有 1 个进程"当判据，实测不成立：双击链路里 `py.exe` 和
> `python.exe` 都在同一个控制台里，`GetConsoleProcessList` 返回 2。
> 也试过 `GetConsoleWindow()` 的可见性当判据，那是循环论证。

### 三种启动方式

| 方式 | 结果 |
| --- | --- |
| 双击 `roachHub.pyw` | **完全没有控制台**（`.pyw` 关联到 `pythonw.exe`）—— 最干净 |
| 双击 `main.py` | 控制台出现后在 `main()` 开头被隐藏（肉眼是"闪一下"） |
| `python main.py --keep-console` | 保留控制台看日志 |
| `python main.py --debug` | 保留控制台 + DEBUG 日志 + 写 `logs/roachHub.log` |

控制台被藏起来时，日志自动改成写 `logs/roachHub.log`（1MB × 3 滚动），否则出问题无从查起。

顺带设了 `SetCurrentProcessExplicitAppUserModelID("roachHub.DesktopSimulator")`，
任务栏分组、通知、跳转列表都归到 roachHub 名下，而不是 `python.exe`。

## 2. 图标体系

两个文件，各管一段：

| 文件 | 用在哪 | 设计 |
| --- | --- | --- |
| `assets/icons/roachhub.ico` | 窗口标题栏、任务栏、快捷方式、对话框 | 深色圆角徽章 + 居中蟑螂 |
| `assets/icons/tray_icon.ico` | 系统托盘 | 只有虫子（无底板），16px 也能认出来 |

两者都含 **16 / 24 / 32 / 48 / 64 / 128 / 256** 七档，由 `tools/make_icon.py` 一次生成。

### 尺寸策略（不这么做就会糊）

同一个形象在 16px 和 256px 下不能用同一张图：

| 尺寸 | 做法 | 原因 |
| --- | --- | --- |
| ≤ 32px | **矢量剪影**（躯干 + 前胸背板 + 头 + 六足 + 两根触须，琥珀渐变） | 真实贴图缩到这个尺寸，腿会碎成一片浅灰，形状比纹理重要 |
| 48~256px | 真实贴图（AI 渲染的高保真虫体） | 这个尺寸的质感才有意义 |

徽章底板：暖黑渐变 + 左上高光 + 虫体后面的暖光晕 + 一圈琥珀色细边
（`QColor(236,156,78,84)`）—— 有这圈边，它在深色控制台上不会糊成一团黑。

## 3. 底图从哪来，怎么抠

底图是 AI 生成的 1024×1024 写实蟑螂（俯视、对称）。请求的是"透明背景"，
但生成模型把透明**画成了棋盘格像素**（`Format_RGB32`，没有 alpha 通道），所以必须自己抠：

1. **flood fill**：从四条边灌进去，只吃"亮度 ≥ 210 且彩度 ≤ 16"的像素 —— 棋盘格两个色号
   `(253,253,251)` / `(229,227,225)` 都满足，虫体不满足；
2. **只对边缘 4px 的像素做软 alpha**，内部保持 255：
   公式是 `a = (242 - min(r,g,b)) / (242 - 40)`；
   这样浅色膜区、腿上的高光不会被误判成半透明（不然会"鬼影"）；
3. **反解颜色去白边**：`C = a·F + (1-a)·B` → `F = (C - (1-a)·B) / a`，`B` 取 242。
   不反解的话，深色背景上会有一圈灰毛边；
4. **干掉贴纸白边**：生成模型给虫子描了一圈"又白又灰"的边（彩度 < 18 且亮度 > 150），
   在边缘带内直接判背景；
5. **裁内容包围盒**：包围盒只认"深色或带彩度"的像素，顺手把右下角的水印裁掉。

## 4. 重新生成

```bash
python tools/make_icon.py                          # 写到 assets/icons/
python tools/make_icon.py --master 新底图.png         # 换底图重做
python tools/make_icon.py --preview                # 只出对比图
```

产物：

- `assets/icons/roachhub.ico`、`tray_icon.ico` —— 真·多尺寸 ICO（≤64 用 BMP 条目，128/256 用 PNG）
- `assets/icons/roachhub_512.png`、`tray_icon.png`、`roachhub_master.png`（抠好的母版）
- `tools/out/icon_preview.png` —— **按真实尺寸渲染再放大**的对比图（16/24/32/48/64），
  放大是为了看清"这个尺寸下还认不认得出是蟑螂"

> 预览必须用真实尺寸去渲染，不能拿 256px 的图缩小着看 —— 缩小的结果和 Windows
> 实际取用的那一档不是一回事（`drawImage` 的采样和 ICO 的档位选择不完全等价）。

## 5. 验收

```bash
QT_QPA_PLATFORM=offscreen python tools/interaction_test.py   # 82 项，第 12 组是图标/单窗口
```

第 12 组断言：两个 ICO 存在且含 7 档、16px 托盘图标不是空白（> 30 个不透明像素）、
控制台标题栏图标已换、`QApplication` 级图标已设、`.pyw` 入口存在、
启动器白名单覆盖 `py/python/pythonw`、**当前控制台里混着 shell 时判定为共享终端且不动手**。
