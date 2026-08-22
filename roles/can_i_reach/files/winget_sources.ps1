# Opt-in dynamic winget source check: parse `winget source list` and
# probe every configured source URL. winget is a per-user MSIX app, so
# in WinRM/SYSTEM sessions the binary is frequently not resolvable even
# when installed - that degrades to status=skipped, never to a false
# failure. Sets $Ansible.Result = @{ status; detail; sources }.
param(
    [double]$TimeoutSec = 5
)
$Ansible.Changed = $false

$winget = Get-Command winget.exe -ErrorAction SilentlyContinue
if (-not $winget) {
    $Ansible.Result = @{ status = 'skipped'; sources = @()
                         detail = 'winget not resolvable in this session (per-user MSIX; absent on most Server SKUs)' }
    return
}

$raw = (& $winget.Source source list 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    $Ansible.Result = @{ status = 'fail'; sources = @()
                         detail = "winget source list failed: $($raw.Trim())" }
    return
}

$sources = @()
$past = $false
foreach ($line in ($raw -split "`r?`n")) {
    if ($line -match '^-{5,}') { $past = $true; continue }
    if ($past -and $line.Trim()) {
        $parts = $line.Trim() -split '\s+', 2
        if ($parts.Count -eq 2 -and $parts[1] -match '^https?://') {
            $sources += @{ name = $parts[0]; url = $parts[1].Trim() }
        }
    }
}
if ($sources.Count -eq 0) {
    $Ansible.Result = @{ status = 'fail'; sources = @()
                         detail = 'winget reported no sources' }
    return
}

$bad = @()
foreach ($s in $sources) {
    try {
        $null = Invoke-WebRequest -Uri $s.url -Method Head -UseBasicParsing -TimeoutSec ([int]$TimeoutSec)
    } catch {
        # any HTTP response (403/404/405...) still proves reachability
        if (-not $_.Exception.Response) { $bad += $s.name }
    }
}
if ($bad.Count -eq 0) {
    $Ansible.Result = @{ status = 'ok'; sources = $sources
                         detail = ('all {0} configured source(s) reachable: {1}' -f $sources.Count, (($sources | ForEach-Object { $_.name }) -join ', ')) }
} else {
    $Ansible.Result = @{ status = 'fail'; sources = $sources
                         detail = ('unreachable winget source(s): {0}' -f ($bad -join ', ')) }
}
