# DNS lookup for Windows targets (mirrors plugins/modules/dns_query.py).
# Sets $Ansible.Result = @{ status = ok|fail; detail = ...; answers = @() }.
# With -Server the query goes straight to that server (DnsOnly bypasses
# cache and hosts file); without it the system view is used, which is
# what applications actually see.
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [ValidateSet('A', 'AAAA', 'CNAME', 'PTR', 'TXT', 'SRV', 'MX', 'NS', 'SOA')][string]$QType = 'A',
    [string]$Server = '',
    [double]$TimeoutSec = 5,
    [string[]]$Expected = @()
)
$Ansible.Changed = $false
$start = Get-Date

try {
    $params = @{ Name = $Name; Type = $QType; ErrorAction = 'Stop' }
    if ($TimeoutSec -le 5) { $params.QuickTimeout = $true }
    if ($Server) {
        $params.Server = $Server
        $params.DnsOnly = $true
    }
    $records = Resolve-DnsName @params
    $answers = @()
    foreach ($r in $records) {
        switch ("$($r.Type)") {
            'A' { $answers += $r.IPAddress }
            'AAAA' { $answers += $r.IPAddress }
            'CNAME' { $answers += $r.NameHost }
            'PTR' { $answers += $r.NameHost }
            'NS' { $answers += $r.NameHost }
            'SRV' { $answers += ('{0} {1} {2} {3}' -f $r.Priority, $r.Weight, $r.Port, $r.NameTarget) }
            'MX' { $answers += ('{0} {1}' -f $r.Preference, $r.NameExchange) }
            'TXT' { $answers += ($r.Strings -join '') }
            'SOA' { $answers += ('{0} {1} {2}' -f $r.PrimaryServer, $r.NameAdministrator, $r.SerialNumber) }
            default { }
        }
    }
    $elapsed = [Math]::Round(((Get-Date) - $start).TotalSeconds, 3)
    if ($answers.Count -eq 0) {
        $Ansible.Result = @{ status = 'fail'; answers = @()
                             detail = "no $QType records for $Name" }
        return
    }
    $missing = @()
    foreach ($want in $Expected) {
        $hit = $false
        foreach ($a in $answers) {
            if ($a -ieq $want -or $a -ilike "*$want*") { $hit = $true; break }
        }
        if (-not $hit) { $missing += $want }
    }
    if ($missing.Count -gt 0) {
        $Ansible.Result = @{ status = 'fail'; answers = $answers
                             detail = ('expected value(s) not in answers: {0} (got: {1})' -f ($missing -join ', '), ($answers -join ', ')) }
        return
    }
    $Ansible.Result = @{ status = 'ok'; answers = $answers
                         detail = ('answers: {0} ({1}s)' -f ($answers -join ', '), $elapsed) }
} catch {
    $Ansible.Result = @{ status = 'fail'; answers = @()
                         detail = $_.Exception.Message.Trim() }
}
