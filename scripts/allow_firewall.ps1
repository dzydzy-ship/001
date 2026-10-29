<#
  为「AI 商家营销运营助手」放行 Windows 防火墙入站端口（局域网访问用）。

  特点：
    · 只放行 TCP 8000-8020（启动器自动顺延端口时也能覆盖）；
    · 只允许「本地子网」来源，公网 IP 依然进不来；
    · 规则名以「店子宠物」开头，方便一次性删除。

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1 -StartPort 9000 -EndPort 9010
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1 -Remove
#>
param(
    [int]$StartPort = 8000,
    [int]$EndPort = 8020,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$RulePrefix = '店子宠物'

function Test-IsAdmin {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 没有管理员权限：弹 UAC 用管理员窗口重跑自己
if (-not (Test-IsAdmin)) {
    Write-Host ''
    Write-Host '  添加/删除防火墙规则需要管理员权限，正在弹出授权窗口…' -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-StartPort', "$StartPort", '-EndPort', "$EndPort")
    if ($Remove) { $argList += '-Remove' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait
        Write-Host ''
        Write-Host '  管理员窗口已处理完（结果在上面 / 已写入 logs\防火墙结果.txt）。' -ForegroundColor Green
    } catch {
        Write-Host ''
        Write-Host '  已取消授权（或 UAC 被拒绝），没有修改任何系统设置。' -ForegroundColor Yellow
        Write-Host '  不授权也能用，只是其他电脑可能打不开 http://本机IP:端口 ，本机访问不受影响。'
    }
    exit 0
}

$logDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs'

function Save-Result {
    param([string[]]$Lines)
    try {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        [System.IO.File]::WriteAllLines((Join-Path $logDir '防火墙结果.txt'), $Lines, [System.Text.Encoding]::UTF8)
    } catch { }
}

if ($Remove) {
    $rules = @(Get-NetFirewallRule -DisplayName "$RulePrefix*" -ErrorAction SilentlyContinue)
    $lines = @()
    if ($rules.Count -eq 0) {
        $lines += '没有找到需要删除的规则。'
    } else {
        foreach ($r in $rules) {
            Remove-NetFirewallRule -DisplayName $r.DisplayName -ErrorAction SilentlyContinue
            $lines += "已删除规则：$($r.DisplayName)"
        }
    }
    foreach ($l in $lines) { Write-Host "  $l" -ForegroundColor Green }
    Save-Result $lines
    exit 0
}

$displayName = "$RulePrefix Web 局域网 $StartPort-$EndPort"
$existing = @(Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue)

$lines = @()
if ($existing.Count -gt 0) {
    $lines += "防火墙规则已存在，无需重复添加：$displayName"
} else {
    try {
        New-NetFirewallRule -DisplayName $displayName -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort "$StartPort-$EndPort" -RemoteAddress LocalSubnet -Profile Any `
            -Description 'AI 商家营销运营助手：只允许本地子网访问 Web 端口' -ErrorAction Stop | Out-Null
        $lines += "已添加防火墙规则：$displayName"
        $lines += "放行端口：TCP $StartPort-$EndPort"
        $lines += '来源限制：仅本地子网（同一 Wi-Fi / 路由器下的设备），公网进不来'
    } catch {
        $lines += "添加规则失败：$($_.Exception.Message)"
        $lines += '可以尝试：以管理员身份打开 PowerShell 再执行 scripts\allow_firewall.ps1'
        foreach ($l in $lines) { Write-Host "  $l" -ForegroundColor Red }
        Save-Result $lines
        exit 1
    }
}

foreach ($l in $lines) { Write-Host "  $l" -ForegroundColor Green }
Write-Host ''
Write-Host '  下一步：重新双击 一键启动.bat ，把窗口里「局域网访问」那一行的地址发给其他电脑/手机。' -ForegroundColor Gray
Save-Result $lines
exit 0
