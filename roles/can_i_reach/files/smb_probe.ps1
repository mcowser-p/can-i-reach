# SMB share probe for Windows targets: raw 445/tcp reachability plus an
# access attempt on the UNC. Access-denied still proves the network
# path (usually credential delegation / the WinRM double hop) and is
# reported as warn, not fail.
# Sets $Ansible.Result = @{ status = ok|warn|fail; detail = ... }.
param(
    [Parameter(Mandatory = $true)][string]$Unc,
    [bool]$TestAccess = $true,
    [double]$TimeoutSec = 5
)
$Ansible.Changed = $false

$server = (($Unc -replace '^\\\\', '') -split '\\')[0]
if (-not $server) {
    $Ansible.Result = @{ status = 'fail'; detail = "cannot parse a server name out of '$Unc'" }
    return
}

$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $iar = $tcp.BeginConnect($server, 445, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne([int]($TimeoutSec * 1000))) {
        throw "connection timed out after ${TimeoutSec}s"
    }
    $tcp.EndConnect($iar)
} catch {
    $tcp.Close()
    $Ansible.Result = @{ status = 'fail'; detail = "445/tcp unreachable on ${server}: $($_.Exception.Message)" }
    return
}
$tcp.Close()

if (-not $TestAccess) {
    $Ansible.Result = @{ status = 'ok'; detail = "445/tcp open on $server (access not tested)" }
    return
}

try {
    $null = Get-ChildItem -LiteralPath $Unc -ErrorAction Stop | Select-Object -First 1
    $Ansible.Result = @{ status = 'ok'; detail = "445/tcp open and $Unc lists" }
} catch [System.UnauthorizedAccessException] {
    $Ansible.Result = @{ status = 'warn'
                         detail = "$server reachable but access to $Unc denied (credential delegation / double hop?)" }
} catch {
    $e = $_.Exception
    if ($e.InnerException -is [System.UnauthorizedAccessException] -or
        $e.Message -match 'Access.*denied|denied.*access') {
        $Ansible.Result = @{ status = 'warn'
                             detail = "$server reachable but access to $Unc denied (credential delegation / double hop?)" }
    } else {
        $Ansible.Result = @{ status = 'fail'
                             detail = "445/tcp open on $server but ${Unc} failed: $($e.Message)" }
    }
}
