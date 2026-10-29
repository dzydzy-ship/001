<#
  店子宠物 / AI 商家营销运营助手 —— 通用启动器（换一台电脑也能直接跑）

  用法（一般不用手动执行，双击根目录的 一键启动.bat 即可）：
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launcher.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launcher.ps1 -Port 9000
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launcher.ps1 -Local
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launcher.ps1 -Diagnose

  相对旧版 一键启动.bat 解决的五件事：
    1) Python 自动探测（py 启动器 / 注册表 / 常见安装目录），校验版本 >= 3.10，
       并跳过 Microsoft Store 的 python.exe 空壳占位符（where python 能查到但跑不起来的那种）；
    2) .venv 从别的电脑拷过来会失效（里面写死了原机器的绝对路径），检测到就自动备份重建；
    3) 依赖缺失自动安装，PyPI 官方源失败自动切清华 / 阿里 / 腾讯镜像重试；
    4) 端口被占用自动顺延，识别“服务已经在运行”避免开两份；
    5) 默认监听 0.0.0.0，启动后打印本机 + 局域网地址，同网段的其他电脑和手机可直接打开。
#>
param(
    [int]$Port = 0,
    [string]$BindAddress = '',
    [switch]$Local,
    [switch]$NoBrowser,
    [switch]$Diagnose,
    [switch]$ForceReinstall
)

$ErrorActionPreference = 'Stop'
$script:Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

# 子进程统一 UTF-8，避免中文窗口/日志乱码
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------
function Write-Line { param([string]$Text = '', [string]$Color = 'Gray') if ([string]::IsNullOrEmpty($Text)) { Write-Host '' } else { Write-Host $Text -ForegroundColor $Color } }
function Write-Step { param([string]$Text) Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Hint { param([string]$Text) Write-Host "    $Text" -ForegroundColor DarkGray }
function Write-OkLine { param([string]$Text) Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-WarnLine { param([string]$Text) Write-Host "    [!]  $Text" -ForegroundColor Yellow }
function Write-FailLine { param([string]$Text) Write-Host "    [X]  $Text" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
$script:VenvDir = Join-Path $script:Root '.venv'
$script:VenvPy = Join-Path $script:VenvDir 'Scripts\python.exe'
$script:ReqFile = Join-Path $script:Root 'requirements.txt'
$script:LogDir = Join-Path $script:Root 'logs'

# ---------------------------------------------------------------------------
# .env 读取（不覆盖系统里已经设置的环境变量）
# ---------------------------------------------------------------------------
$script:EnvValues = @{}

function Read-DotEnv {
    $path = Join-Path $script:Root '.env'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    foreach ($line in [System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8)) {
        $t = $line.Trim()
        if ($t.Length -eq 0) { continue }
        if ($t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim()
        if ($k -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        if ($v.Length -ge 2) {
            $first = $v.Substring(0, 1)
            $last = $v.Substring($v.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $v = $v.Substring(1, $v.Length - 2)
            }
        }
        $script:EnvValues[$k] = $v
    }
}

function Import-DotEnv {
    foreach ($k in $script:EnvValues.Keys) {
        if (-not (Test-Path -Path "env:$k")) {
            Set-Item -Path "env:$k" -Value $script:EnvValues[$k]
        }
    }
}

# ---------------------------------------------------------------------------
# Python 探测
# ---------------------------------------------------------------------------
$script:PyCandidates = New-Object System.Collections.ArrayList
$script:PySeen = @{}

function Add-PyCandidate {
    param($Cand)
    if (-not $Cand -or -not $Cand.File) { return }
    $key = ($Cand.File + '|' + ($Cand.Prefix -join ' ')).ToLowerInvariant()
    if ($script:PySeen.ContainsKey($key)) { return }
    $script:PySeen[$key] = 1
    [void]$script:PyCandidates.Add($Cand)
}

function Get-PythonInfo {
    param([string]$File, [string[]]$Prefix = @())

    if (-not $File) { return $null }

    $isPath = $File.Contains('\')
    if ($isPath) {
        # Microsoft Store 的“应用执行别名”是个空壳，必须跳过
        if ($File -match '(?i)\\WindowsApps\\') { return $null }
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return $null }
    } else {
        $cmdInfo = Get-Command $File -ErrorAction SilentlyContinue
        if (-not $cmdInfo) { return $null }
        # 解析出来还是 Store 别名就不要真去执行（会弹商店 / 拖慢启动）
        if ($cmdInfo.Source -and $cmdInfo.Source -match '(?i)\\WindowsApps\\') { return $null }
    }

    try {
        # 刻意不用 % 格式化和引号：PowerShell 5.1 给 native 命令传参时会把内层双引号搞丢
        $expr = 'import sys;print(sys.version.split()[0])'
        $out = & $File @Prefix -c $expr 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $first = (($out | Out-String).Trim() -split "`r?`n")[0].Trim()
        if ($first -notmatch '^\d+\.\d+\.\d+$') { return $null }
        $ver = [version]$first
        if ($ver.Major -lt 3) { return $null }
        if ($ver.Major -eq 3 -and $ver.Minor -lt 10) { return $null }

        $full = $File
        if (-not $isPath) {
            $cmdInfo = Get-Command $File -ErrorAction SilentlyContinue
            if ($cmdInfo -and $cmdInfo.Source) { $full = $cmdInfo.Source }
        }
        return [pscustomobject]@{ File = $full; Prefix = $Prefix; Version = $ver; Raw = $first }
    } catch {
        return $null
    }
}

function Find-Python {
    $script:PyCandidates = New-Object System.Collections.ArrayList
    $script:PySeen = @{}

    # 1) py 启动器（python.org 安装包会装，能拿到“最新 3.x”，最可靠）
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        Add-PyCandidate (Get-PythonInfo -File 'py' -Prefix @('-3'))
        $pyList = & py -0p 2>$null
        foreach ($row in $pyList) {
            if ($row -match '([A-Za-z]:\\.+?python\.exe)') {
                Add-PyCandidate (Get-PythonInfo -File $matches[1])
            }
        }
    }

    # 2) PATH 里的 python / python3
    foreach ($name in @('python', 'python3')) {
        Add-PyCandidate (Get-PythonInfo -File $name)
    }

    # 3) 注册表（python.org 安装包会写，即使没勾选 Add to PATH 也能找到）
    $hives = @(
        'HKLM:\SOFTWARE\Python\PythonCore',
        'HKCU:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore'
    )
    foreach ($hive in $hives) {
        if (-not (Test-Path -LiteralPath $hive)) { continue }
        foreach ($key in @(Get-ChildItem -LiteralPath $hive -ErrorAction SilentlyContinue)) {
            $installPathKey = Join-Path $key.PSPath 'InstallPath'
            if (-not (Test-Path -LiteralPath $installPathKey)) { continue }
            $props = Get-ItemProperty -LiteralPath $installPathKey -ErrorAction SilentlyContinue
            $dir = $null
            if ($props) { $dir = $props.'(default)' }
            if ($dir) {
                Add-PyCandidate (Get-PythonInfo -File (Join-Path $dir 'python.exe'))
            }
        }
    }

    # 4) 常见安装目录（兜底）
    $globList = New-Object System.Collections.ArrayList
    if ($env:LOCALAPPDATA) {
        [void]$globList.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'))
        [void]$globList.Add((Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3*\python.exe'))
    }
    if ($env:ProgramFiles) {
        [void]$globList.Add((Join-Path $env:ProgramFiles 'Python3*\python.exe'))
    }
    if (${env:ProgramFiles(x86)}) {
        [void]$globList.Add((Join-Path ${env:ProgramFiles(x86)} 'Python3*\python.exe'))
    }
    [void]$globList.Add('C:\Python3*\python.exe')
    foreach ($pattern in $globList) {
        foreach ($item in @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue)) {
            Add-PyCandidate (Get-PythonInfo -File $item.FullName)
        }
    }

    $usable = @($script:PyCandidates | Where-Object { $_ } | Sort-Object -Property @{ Expression = { $_.Version }; Descending = $true })
    if ($usable.Count -eq 0) { return $null }
    return $usable[0]
}

# ---------------------------------------------------------------------------
# 虚拟环境 / 依赖
# ---------------------------------------------------------------------------
function Test-VenvUsable {
    if (-not (Test-Path -LiteralPath $script:VenvPy -PathType Leaf)) { return $false }
    try {
        $out = & $script:VenvPy -c "import sys;print(sys.prefix)" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $false }
        $prefix = (($out | Out-String).Trim() -split "`r?`n")[0].Trim()
        if (-not $prefix) { return $false }
        $a = Resolve-Path -LiteralPath $prefix -ErrorAction SilentlyContinue
        $b = Resolve-Path -LiteralPath $script:VenvDir -ErrorAction SilentlyContinue
        if (-not $a -or -not $b) { return $false }
        return ($a.Path.TrimEnd('\') -ieq $b.Path.TrimEnd('\'))
    } catch {
        return $false
    }
}

function Remove-BrokenVenv {
    $bak = Join-Path $script:Root ('.venv.broken-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    try {
        Move-Item -LiteralPath $script:VenvDir -Destination $bak -Force
        Write-WarnLine "旧的 .venv 不可用（虚拟环境不能跨电脑复用），已改名保存为 $(Split-Path -Leaf $bak)，稍后可手动删除。"
    } catch {
        Write-WarnLine "旧的 .venv 无法改名（可能被占用），尝试直接删除…"
        try { Remove-Item -LiteralPath $script:VenvDir -Recurse -Force -ErrorAction Stop } catch {
            Write-FailLine "无法删除 .venv：$($_.Exception.Message)"
            Write-Hint "请关闭正在运行的旧服务窗口/杀毒软件后重试，或手动删除 $script:VenvDir"
            exit 3
        }
    }
}

function New-Venv {
    param($PyInfo)
    Write-Step "创建虚拟环境（第一次运行，约 10-30 秒）"
    $venvArgs = @($PyInfo.Prefix) + @('-m', 'venv', $script:VenvDir)
    & $PyInfo.File @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $script:VenvPy -PathType Leaf)) {
        Write-FailLine "虚拟环境创建失败（Python：$($PyInfo.File)）。"
        Write-Hint "常见原因：Python 安装不完整（缺 venv 组件）、目录无写入权限、被杀毒软件拦截。"
        Write-Hint "建议：卸掉 Python 后从 https://www.python.org/downloads/ 重装，安装时勾选 Add python.exe to PATH。"
        exit 3
    }
    Write-OkLine "虚拟环境已创建：$($script:VenvDir)"
}

function Test-Deps {
    $probe = 'import fastapi, uvicorn, sqlalchemy, pydantic, httpx, yaml, apscheduler, openpyxl, xlrd, multipart'
    & $script:VenvPy -c $probe 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Install-Deps {
    Write-Step "安装依赖（首次约 1-3 分钟，请保持网络畅通）"
    & $script:VenvPy -m pip --version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Hint "venv 里没有 pip，尝试修复…"
        & $script:VenvPy -m ensurepip --upgrade 2>$null | Out-Null
    }

    $mirrors = @(
        @{ Name = 'PyPI 官方源'; Args = @() },
        @{ Name = '清华 TUNA 镜像'; Args = @('-i', 'https://pypi.tuna.tsinghua.edu.cn/simple', '--trusted-host', 'pypi.tuna.tsinghua.edu.cn') },
        @{ Name = '阿里云镜像'; Args = @('-i', 'https://mirrors.aliyun.com/pypi/simple', '--trusted-host', 'mirrors.aliyun.com') },
        @{ Name = '腾讯云镜像'; Args = @('-i', 'https://mirrors.cloud.tencent.com/pypi/simple', '--trusted-host', 'mirrors.cloud.tencent.com') }
    )

    $installed = $false
    foreach ($m in $mirrors) {
        Write-Hint "下载源：$($m.Name)"
        $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--retries', '3', '--timeout', '60') + $m.Args + @('-r', $script:ReqFile)
        & $script:VenvPy @pipArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Deps)) {
            $installed = $true
            break
        }
        Write-WarnLine "$($m.Name) 安装失败，换下一个源重试…"
    }

    if (-not $installed) {
        Write-FailLine "依赖安装失败（所有下载源都试过了）。"
        Write-Hint "1. 检查网络/代理，确认能打开 https://pypi.org 或镜像站；"
        Write-Hint "2. 公司网络可能拦截了 pip，试试手机热点；"
        Write-Hint "3. 手动装：`"$script:VenvPy`" -m pip install -r `"$script:ReqFile`" -i https://pypi.tuna.tsinghua.edu.cn/simple"
        exit 3
    }
    Write-OkLine "依赖安装完成"
}

function Ensure-Environment {
    $script:PyDesc = ''
    $venvOk = Test-VenvUsable

    if (-not $venvOk -and (Test-Path -LiteralPath $script:VenvDir)) {
        Remove-BrokenVenv
    }

    if (-not $venvOk) {
        Write-Step "检查 Python"
        $pyInfo = Find-Python
        if (-not $pyInfo) {
            Write-FailLine "本机没有可用的 Python 3.10+（Microsoft Store 的 python.exe 空壳已自动排除）。"
            Write-Hint "装一个就行（任选其一）："
            Write-Hint "  1) Win10/11 自带 winget，在本窗口执行：winget install -e --id Python.Python.3.12"
            Write-Hint "  2) 官网 https://www.python.org/downloads/ 下载安装，务必勾选 Add python.exe to PATH"
            Write-Hint "装完关掉本窗口，重新双击 一键启动.bat 即可，不用重启电脑。"
            exit 2
        }
        Write-OkLine "使用 Python $($pyInfo.Raw)：$($pyInfo.File)"
        $script:PyDesc = "Python $($pyInfo.Raw)"
        New-Venv -PyInfo $pyInfo
    } else {
        # 已有可用 venv：不必再找系统 Python
        $v = & $script:VenvPy -c "import sys;print(sys.version.split()[0])" 2>$null
        $script:PyDesc = 'Python ' + (($v | Out-String).Trim())
        Write-OkLine "复用已有虚拟环境（$script:PyDesc）"
    }

    $hash = ''
    if (Test-Path -LiteralPath $script:ReqFile) {
        $hash = (Get-FileHash -LiteralPath $script:ReqFile -Algorithm SHA256).Hash
    }
    $marker = ''
    if ($hash) { $marker = Join-Path $script:VenvDir ('.deps-' + $hash.Substring(0, 16) + '.ok') }

    if (-not $ForceReinstall -and $marker -and (Test-Path -LiteralPath $marker) -and (Test-Deps)) {
        Write-OkLine "依赖已就绪（跳过安装）"
    } else {
        Install-Deps
        if ($marker) {
            if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue }
            Set-Content -LiteralPath $marker -Value 'ok' -Encoding ASCII
        }
    }
}

# ---------------------------------------------------------------------------
# 端口 / 网络 / 防火墙
# ---------------------------------------------------------------------------
function Test-PortFree {
    param([int]$PortToTest)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $PortToTest)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { try { $listener.Stop() } catch {} }
    }
}

function Test-PortListening {
    param([int]$PortToTest)
    try {
        $conn = Get-NetTCPConnection -LocalPort $PortToTest -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        return [bool]$conn
    } catch {
        return $false
    }
}

function Test-AppAt {
    # 直接开原生 socket 请求，禁用系统代理：否则在开着代理 / VPN 的机器上，
    # 对 127.0.0.1 的健康检查可能被转发出去，或对被过滤的端口长时间卡住。
    param([int]$PortToTest)
    $resp = $null
    $reader = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create(("http://127.0.0.1:{0}/health" -f $PortToTest))
        $req.Proxy = $null
        $req.Timeout = 2000
        $req.ReadWriteTimeout = 2000
        $req.KeepAlive = $false
        $resp = $req.GetResponse()
        if ($resp.StatusCode -ne [System.Net.HttpStatusCode]::OK) { return $false }
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $body = $reader.ReadToEnd()
        return ($body -match '"status"\s*:\s*"ok"')
    } catch {
        return $false
    } finally {
        if ($reader) { try { $reader.Close() } catch {} }
        if ($resp) { try { $resp.Close() } catch {} }
    }
}

function Find-RunningInstance {
    param([int]$StartPort)
    $end = [Math]::Min($StartPort + 20, 65535)
    $listening = @{}
    try {
        foreach ($c in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
            $listening[[int]$c.LocalPort] = $true
        }
    } catch {}
    for ($p = $StartPort; $p -le $end; $p++) {
        if (-not $listening.ContainsKey($p)) { continue }
        if (Test-AppAt -PortToTest $p) { return $p }
    }
    return 0
}

function Get-LanAddresses {
    $result = New-Object System.Collections.ArrayList
    try {
        $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop
        foreach ($ip in $ips) {
            if ($ip.IPAddress -match '^(127\.|169\.254\.|0\.)') { continue }
            if ($ip.AddressState -and $ip.AddressState -ne 'Preferred') { continue }
            if ($ip.InterfaceAlias -match '(?i)Loopback|vEthernet|VMware|VirtualBox|Tailscale|ZeroTier|TAP') { continue }
            [void]$result.Add([string]$ip.IPAddress)
        }
    } catch { }

    if ($result.Count -eq 0) {
        try {
            $name = [System.Net.Dns]::GetHostName()
            foreach ($a in [System.Net.Dns]::GetHostAddresses($name)) {
                if ($a.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and -not [System.Net.IPAddress]::IsLoopback($a)) {
                    [void]$result.Add($a.IPAddressToString)
                }
            }
        } catch { }
    }
    return @($result | Select-Object -Unique)
}

function Test-FirewallRule {
    try {
        $rules = @(Get-NetFirewallRule -DisplayName '店子宠物*' -ErrorAction SilentlyContinue)
        return ($rules.Count -gt 0)
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# 启动信息 / 诊断
# ---------------------------------------------------------------------------
function Save-StartupInfo {
    param([int]$UsePort, [string]$Bind, [string]$PyDesc)
    try {
        New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
        $lan = @(Get-LanAddresses)
        $lines = @(
            "时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
            "项目目录: $script:Root",
            "Python: $PyDesc",
            "虚拟环境: $script:VenvPy",
            "监听: ${Bind}:$UsePort",
            "本机访问: http://127.0.0.1:$UsePort",
            "局域网: $(($lan | ForEach-Object { "http://${_}:$UsePort" }) -join ' | ')"
        )
        [System.IO.File]::WriteAllLines((Join-Path $script:LogDir '最近一次启动.txt'), [string[]]$lines, [System.Text.Encoding]::UTF8)
    } catch { }
}

function Show-Banner {
    param([int]$UsePort, [string]$Bind, [string]$PyDesc)
    $lan = @(Get-LanAddresses)
    Write-Host ''
    Write-Host ('  ' + ('=' * 68)) -ForegroundColor DarkCyan
    Write-Host '   服务已启动，可以用了' -ForegroundColor Green
    Write-Host ''
    Write-Host "   本机访问    http://127.0.0.1:$UsePort" -ForegroundColor White
    if ($Bind -eq '127.0.0.1') {
        Write-Host '   监听模式    仅本机（127.0.0.1），其他电脑/手机访问不到' -ForegroundColor Yellow
        Write-Host '               需要给别人看：去掉 -Local，或把 .env 里 APP_HOST 改回 0.0.0.0' -ForegroundColor DarkGray
    } elseif ($lan.Count -gt 0) {
        foreach ($ip in $lan) {
            Write-Host "   局域网访问  http://${ip}:$UsePort" -ForegroundColor White
        }
        Write-Host '               同一 Wi-Fi / 同一网段的其他电脑、手机浏览器直接打开上面地址' -ForegroundColor DarkGray
    } else {
        Write-Host '   局域网访问  未检测到局域网 IP（可能只连了 VPN 或没联网）' -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host "   Python      $PyDesc" -ForegroundColor DarkGray
    Write-Host "   监听地址    ${Bind}:$UsePort" -ForegroundColor DarkGray
    Write-Host '   停止服务    在本窗口按 Ctrl+C，或直接关闭本窗口' -ForegroundColor DarkGray
    Write-Host ('  ' + ('=' * 68)) -ForegroundColor DarkCyan
    Write-Host ''
}

function Invoke-Diagnose {
    $report = New-Object System.Collections.ArrayList
    function Add-Row([string]$Text) { [void]$report.Add($Text); Write-Host $Text }

    Write-Host ''
    Write-Host '  AI 商家营销运营助手 · 环境诊断' -ForegroundColor White
    Write-Host "  生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host ''

    Add-Row "[系统] $([System.Environment]::OSVersion.VersionString)  64位=$([System.Environment]::Is64BitOperatingSystem)"
    Add-Row "[目录] $script:Root"
    if ($script:Root -match '(?i)OneDrive') {
        Add-Row "[警告] 项目在 OneDrive 目录里，SQLite 数据库可能被同步/占用而报错，建议移到普通目录（如 D:\店子宠物）。"
    }
    if ($script:Root -match '[^\x00-\x7F]') {
        Add-Row "[提示] 项目路径含中文，一般没问题；个别老版本工具链可能出问题，若报错可换纯英文路径试试。"
    }
    if ($script:Root.Length -gt 180) {
        Add-Row "[警告] 项目路径过长（$($script:Root.Length) 字符），Windows 路径限制可能报错，建议放到 C:\店子宠物 之类短路径。"
    }

    Add-Row ''
    Add-Row '[Python]'
    $py = Find-Python
    if ($py) {
        Add-Row "  可用：$($py.Raw)  $($py.File)"
    } else {
        Add-Row '  未找到可用的 Python 3.10+（双击启动时会提示安装方式）'
    }

    Add-Row ''
    Add-Row '[虚拟环境]'
    if (Test-VenvUsable) {
        Add-Row "  可用：$script:VenvPy"
        $v = & $script:VenvPy -c "import sys;print(sys.version)" 2>$null
        Add-Row "  版本：$(($v | Out-String).Trim())"
        if (Test-Deps) { Add-Row '  依赖：全部就绪' } else { Add-Row '  依赖：有缺失（启动时会自动安装）' }
    } else {
        Add-Row '  尚未创建或已失效（首次启动会自动创建）'
    }

    Add-Row ''
    Add-Row '[端口]'
    $found = Find-RunningInstance -StartPort 8000
    if ($found -gt 0) {
        Add-Row "  检测到服务已在 $found 端口运行：http://127.0.0.1:$found"
    }
    foreach ($p in @(8000, 8001, 8010)) {
        if (Test-PortFree -PortToTest $p) { Add-Row "  $p 空闲" } else { Add-Row "  $p 已被占用" }
    }
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $procName = ''
        try { $procName = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName } catch { }
        Add-Row "  8000 占用进程：$procName (PID $($conn.OwningProcess))"
    }

    Add-Row ''
    Add-Row '[局域网]'
    $lan = @(Get-LanAddresses)
    if ($lan.Count -gt 0) {
        foreach ($ip in $lan) { Add-Row "  本机局域网 IP：$ip" }
    } else {
        Add-Row '  未检测到局域网 IP'
    }
    if (Test-FirewallRule) {
        Add-Row '  防火墙：已放行（店子宠物* 规则存在）'
    } else {
        Add-Row '  防火墙：未放行；若别的电脑打不开，双击 允许局域网访问.bat（需管理员确认）'
    }

    Add-Row ''
    Add-Row '[应用自检]'
    if (Test-Path -LiteralPath $script:VenvPy) {
        $importOut = & $script:VenvPy -c "import app.main; print('app.main import OK')" 2>&1
        Add-Row "  $((($importOut | Out-String).Trim()))"
    } else {
        Add-Row '  跳过（虚拟环境未创建）'
    }

    Add-Row ''
    Add-Row '[最近一次启动记录]'
    $lastLog = Join-Path $script:LogDir '最近一次启动.txt'
    if (Test-Path -LiteralPath $lastLog) {
        foreach ($line in [System.IO.File]::ReadAllLines($lastLog, [System.Text.Encoding]::UTF8)) { Add-Row "  $line" }
    } else {
        Add-Row '  暂无'
    }

    try {
        New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
        $outFile = Join-Path $script:LogDir '诊断报告.txt'
        [System.IO.File]::WriteAllLines($outFile, [string[]]$report, [System.Text.Encoding]::UTF8)
        Write-Host ''
        Write-Host "  诊断报告已保存：$outFile" -ForegroundColor Green
        Write-Host '  发给帮你排查的人时，把上面的文字和这个文件一起发过去即可。'
        Write-Host ''
    } catch { }
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
trap {
    Write-Host ''
    Write-Host "  [启动器异常] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  目录：$script:Root" -ForegroundColor DarkGray
    Write-Host '  可双击 诊断工具.bat 生成排查报告。' -ForegroundColor Yellow
    exit 9
}

Write-Host ''
Write-Host '  AI 商家营销运营助手 · 通用启动器' -ForegroundColor White
Write-Host "  项目目录：$script:Root" -ForegroundColor DarkGray

Read-DotEnv
Import-DotEnv

# 监听地址 / 端口：命令行 > .env > 默认
if ($Port -le 0) {
    $fromEnv = 0
    if ($env:APP_PORT -and [int]::TryParse($env:APP_PORT, [ref]$fromEnv) -and $fromEnv -gt 0) {
        $Port = $fromEnv
    } else {
        $Port = 8000
    }
}
$script:BindAddr = $BindAddress
if (-not $script:BindAddr -and $env:APP_HOST) { $script:BindAddr = $env:APP_HOST.Trim() }
if ($Local) { $script:BindAddr = '127.0.0.1' }
if (-not $script:BindAddr) { $script:BindAddr = '0.0.0.0' }

if ($Diagnose) {
    Invoke-Diagnose
    exit 0
}

# 已经在跑就别再起第二个（避免两个进程同时写 SQLite）
$running = Find-RunningInstance -StartPort $Port
if ($running -gt 0) {
    Write-OkLine "检测到服务已经在运行：http://127.0.0.1:$running"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$running" | Out-Null }
    Write-Host '  直接打开上面的地址即可；要重启就先关掉原来的服务窗口。' -ForegroundColor DarkGray
    exit 0
}

Ensure-Environment

# 端口选择：被占用就往后顺延
$chosen = 0
if (Test-PortFree -PortToTest $Port) {
    $chosen = $Port
} else {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $owner = ''
    if ($conn) {
        $pname = ''
        try { $pname = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName } catch { }
        $owner = "（被 $pname / PID $($conn.OwningProcess) 占用）"
    }
    Write-WarnLine "端口 $Port 已被别的程序占用$owner，自动换端口。"
    for ($p = $Port + 1; $p -le [Math]::Min($Port + 30, 65535); $p++) {
        if (Test-PortFree -PortToTest $p) { $chosen = $p; break }
    }
    if ($chosen -eq 0) {
        Write-FailLine "从 $Port 往后 30 个端口都被占用了，没法启动。"
        Write-Hint "先关掉一些程序，或用命令指定端口：一键启动.bat -Port 9000"
        exit 4
    }
    Write-Hint "实际使用端口：$chosen（服务地址以启动后打印的为准）"
}

if ($script:BindAddr -ne '127.0.0.1' -and -not (Test-FirewallRule)) {
    Write-WarnLine "Windows 防火墙可能拦截其他电脑的访问。"
    Write-Hint "需要从别的电脑/手机打开时，双击一次 允许局域网访问.bat（会弹管理员确认），之后不用再点。"
}

Save-StartupInfo -UsePort $chosen -Bind $script:BindAddr -PyDesc $script:PyDesc

Write-Step "启动服务（首次启动要写示例数据、采集热点，最多等 90 秒）"
$uvArgs = @('-m', 'uvicorn', 'app.main:app', '--host', $script:BindAddr, '--port', "$chosen", '--log-level', 'info')
$serverProc = Start-Process -FilePath $script:VenvPy -ArgumentList $uvArgs -WorkingDirectory $script:Root -NoNewWindow -PassThru

$deadline = (Get-Date).AddSeconds(90)
$healthy = $false
$lastNotice = Get-Date
while ((Get-Date) -lt $deadline) {
    if ($serverProc.HasExited) { break }
    if ((Test-PortListening $chosen) -and (Test-AppAt -PortToTest $chosen)) { $healthy = $true; break }
    if (((Get-Date) - $lastNotice).TotalSeconds -ge 10) {
        Write-Hint '还在启动中…（首次启动数据库和热点采集会慢一点）'
        $lastNotice = Get-Date
    }
    Start-Sleep -Milliseconds 800
}

if (-not $healthy) {
    Write-Host ''
    Write-FailLine "服务没有成功启动。"
    if ($serverProc.HasExited) {
        Write-Hint "Python 进程已退出（退出码 $($serverProc.ExitCode)）。常见原因："
        Write-Hint "  · 端口被占用（本启动器一般会自动换端口，除非被安全软件拦）"
        Write-Hint "  · 依赖没装好：删掉 .venv 后重新双击 一键启动.bat"
        Write-Hint "  · data\app.db 被占用/损坏：先关掉其他服务窗口；仍不行就把 data 目录整个改名为 data.bak 再启动"
        Write-Hint "  · 安全软件（360/火绒等）拦截了 python.exe"
    } else {
        Write-Hint "超时未就绪。上面 uvicorn 的日志里有具体报错，按报错处理；也可以双击 诊断工具.bat。"
    }
    try { if (-not $serverProc.HasExited) { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue } } catch { }
    exit 4
}

Write-OkLine '健康检查通过'
if (-not $NoBrowser) {
    try { Start-Process "http://127.0.0.1:$chosen" | Out-Null } catch { }
}

Show-Banner -UsePort $chosen -Bind $script:BindAddr -PyDesc $script:PyDesc

try {
    Wait-Process -Id $serverProc.Id
} finally {
    if (-not $serverProc.HasExited) {
        try { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
}

Write-Host ''
Write-Host '  服务已停止。' -ForegroundColor DarkGray
exit 0
