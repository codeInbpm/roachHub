<#
.SYNOPSIS
    roachHub 的 Windows 打包脚本（PyInstaller → 便携 exe 目录）。

.DESCRIPTION
    默认产出「便携目录」：dist\windows\roachHub\ 里是 roachHub.exe + 依赖 + assets
    + config.toml，整个文件夹拷走就能在别的机器上跑（不用装 Python）。

    为什么不默认单文件：Config 的路径是 `Path(__file__).parent.parent / "config.toml"`，
    单文件模式下它落在 PyInstaller 的解包临时目录里，退出即删 —— 用户改的设置每次
    启动都会回到默认值。要单文件又想要设置存住，得先改 Config 的落地目录。

.PARAMETER OneFile
    打成单个 roachHub.exe（启动稍慢、设置不持久，见上）。

.PARAMETER SkipInstall
    不执行 pip install，直接用在离线/已装好 PyInstaller 的环境。

.PARAMETER KeepOutput
    保留上一轮的 dist/build（默认先删干净，避免残留的旧贴图混进包里）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -OneFile
#>
[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$SkipInstall,
    [switch]$KeepOutput
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot      # packaging\ 的上一级
$name   = 'roachHub'
$dist   = Join-Path $root 'dist\windows'
$work   = Join-Path $root 'build\windows'
$spec   = Join-Path $root 'packaging\roachHub.spec'

Set-Location -LiteralPath $root

function Write-Step([string]$text) {
    Write-Host ''
    Write-Host "==> $text" -ForegroundColor Cyan
}

function Assert-Ok([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what 失败（退出码 $LASTEXITCODE）" }
}

# --------------------------------------------------------------- 1. 解释器
Write-Step '定位 Python'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw '找不到 Python。请先在项目根目录用 `python -m venv .venv` 建虚拟环境并 `pip install -r requirements.txt`。'
    }
    $python = $cmd.Source
    Write-Host "    未找到 .venv，改用系统解释器: $python" -ForegroundColor Yellow
} else {
    Write-Host "    $python"
}

# 打包用的解释器必须自己装过 PySide6，否则打出来的包是空壳
& $python -c "import PySide6, sys; print('    Python', sys.version.split()[0], '| PySide6', PySide6.__version__)"
Assert-Ok '检查 PySide6'

# ------------------------------------------------------------- 2. PyInstaller
if (-not $SkipInstall) {
    Write-Step '安装/更新 PyInstaller'
    & $python -m pip install --upgrade --disable-pip-version-check 'pyinstaller>=6.10'
    Assert-Ok '安装 PyInstaller'
} else {
    Write-Step '跳过安装（-SkipInstall）'
}

& $python -c "import PyInstaller; print('    PyInstaller', PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller 不可用：去掉 -SkipInstall 重跑，或先 `pip install pyinstaller`。'
}

# ----------------------------------------------------------------- 3. 清理
Write-Step '清理旧产物'

if (-not $KeepOutput) {
    foreach ($p in @($dist, $work)) {
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Recurse -Force
            Write-Host "    已删除 $p"
        }
    }
}

if ($OneFile) {
    $env:ROACHHUB_ONEFILE = '1'
    Write-Host '    模式: 单文件 exe（注意：配置文件不持久）' -ForegroundColor Yellow
} else {
    $env:ROACHHUB_ONEFILE = '0'
    Write-Host '    模式: 便携目录'
}

# ----------------------------------------------------------------- 4. 构建
Write-Step "PyInstaller 构建 -> $dist"

& $python -m PyInstaller $spec --noconfirm --distpath $dist --workpath $work
Assert-Ok 'PyInstaller 构建'

# --------------------------------------------------------------- 5. 校验产物
Write-Step '校验产物'

if ($OneFile) {
    $exePath = Join-Path $dist "$name.exe"
    if (-not (Test-Path -LiteralPath $exePath)) { throw "没找到 $exePath" }

    $sizeMb = [math]::Round((Get-Item -LiteralPath $exePath).Length / 1MB, 1)
    Write-Host "    OK  $exePath  ($sizeMb MB)"
    Write-Host ''
    Write-Host '    单文件包把 assets / config.toml 一起塞进了 exe，运行时解包到临时目录。' -ForegroundColor Yellow
    Write-Host '    因此：设置改完退出即失效；每次启动多 1~3 秒解包时间。' -ForegroundColor Yellow
} else {
    $appDir = Join-Path $dist $name
    $exePath = Join-Path $appDir "$name.exe"
    if (-not (Test-Path -LiteralPath $exePath)) { throw "没找到 $exePath" }

    $assetsDir = Join-Path $appDir 'assets\sprites'
    if (-not (Test-Path -LiteralPath $assetsDir)) { throw "没找到精灵图目录 $assetsDir（datas 打包失败）" }

    $soundsDir = Join-Path $appDir 'assets\sounds'
    if (-not (Test-Path -LiteralPath $soundsDir)) { throw "没找到音效目录 $soundsDir" }

    # config.toml 的位置取决于 PyInstaller 版本：6.x 支持平铺（就在 exe 旁），
    # 老版本会塞进 _internal\。两种都算通过，但要把真实路径告诉用户。
    $cfg = Join-Path $appDir 'config.toml'
    if (-not (Test-Path -LiteralPath $cfg)) {
        $cfg = Join-Path $appDir '_internal\config.toml'
    }
    if (-not (Test-Path -LiteralPath $cfg)) { throw '找不到 config.toml（Config 会退回默认值，但改完存不下来）' }

    $totalMb = [math]::Round(((Get-ChildItem -LiteralPath $appDir -Recurse -File |
                               Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    Write-Host "    OK  $exePath  (整包 $totalMb MB)"
    Write-Host "    配置文件: $cfg"
}

Write-Host ''
Write-Host '完成。' -ForegroundColor Green
Write-Host '  * 整个目录拷到目标机器即可运行，不需要装 Python。'
Write-Host '  * 日志写在「启动时的工作目录\logs\roachHub.log」（快捷方式默认就是这个目录）。'
Write-Host '  * 首次运行若杀软误报，把 PyInstaller 的 bootloader 加白名单即可（常见误报）。'
