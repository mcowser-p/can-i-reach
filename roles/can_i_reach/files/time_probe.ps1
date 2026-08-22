# Time-source probe for Windows targets: one real SNTP exchange via
# w32tm /stripchart. With -Discover it first asks the local w32time
# service for its configured source (w32tm /query /source).
# Sets $Ansible.Result = @{ status; detail; source }.
param(
    [string]$Source = '',
    [double]$TimeoutSec = 5,
    [bool]$Discover = $false
)
$Ansible.Changed = $false

$src = $Source
if (-not $src -and $Discover) {
    $raw = (& w32tm /query /source 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        $Ansible.Result = @{ status = 'fail'; source = ''
                             detail = "w32tm /query /source failed: $raw (is w32time running?)" }
        return
    }
    $src = ($raw -split ',')[0].Trim()
    if (-not $src -or $src -match 'Local CMOS Clock|Free-running System Clock') {
        $Ansible.Result = @{ status = 'fail'; source = $src
                             detail = "no usable time source configured ($src)" }
        return
    }
}
if (-not $src) {
    $Ansible.Result = @{ status = 'skipped'; source = ''; detail = 'no time source to test' }
    return
}

$out = (& w32tm /stripchart /computer:$src /samples:1 /dataonly 2>&1 | Out-String)
$lastLine = (($out.Trim() -split "`r?`n") | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -eq 0 -and $out -match '([+-]\d+\.\d+)s') {
    $Ansible.Result = @{ status = 'ok'; source = $src
                         detail = ('offset {0}s from {1}' -f $Matches[1], $src) }
} elseif ($LASTEXITCODE -eq 0) {
    # stripchart exits 0 but prints 'error: 0x...' lines when the host
    # gives no NTP response
    $Ansible.Result = @{ status = 'fail'; source = $src
                         detail = ('no NTP response from {0}: {1}' -f $src, $lastLine) }
} else {
    $Ansible.Result = @{ status = 'fail'; source = $src
                         detail = ('w32tm failed for {0}: {1}' -f $src, $lastLine) }
}
