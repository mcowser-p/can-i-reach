# UDP probe for Windows targets (mirrors plugins/modules/udp_probe.py).
# Sets $Ansible.Result = @{ status = ok|warn|fail; detail = ... }.
# UDP epistemics: a reply proves open, ICMP port-unreachable (winsock
# 10054 on a connected UdpClient) proves closed, silence proves nothing
# and becomes warn unless ExpectReply demands an answer.
param(
    [Parameter(Mandatory = $true)][string]$TargetHost,
    [Parameter(Mandatory = $true)][int]$Port,
    [double]$TimeoutSec = 5,
    [int]$Tries = 2,
    [ValidateSet('empty', 'ntp')][string]$Payload = 'empty',
    [bool]$ExpectReply = $false
)
$Ansible.Changed = $false

$bytes = New-Object byte[] 0
if ($Payload -eq 'ntp') {
    $bytes = New-Object byte[] 48
    $bytes[0] = 0x23  # LI=0 VN=4 mode=3 (client)
}

$udp = New-Object System.Net.Sockets.UdpClient
$udp.Client.ReceiveTimeout = [int]($TimeoutSec * 1000)
try {
    $udp.Connect($TargetHost, $Port)
} catch {
    $Ansible.Result = @{ status = 'fail'; detail = "cannot resolve ${TargetHost}: $($_.Exception.Message)" }
    $udp.Close()
    return
}

$state = 'silent'
$detail = "no reply after $Tries tries of ${TimeoutSec}s (open|filtered)"
for ($i = 0; $i -lt [Math]::Max($Tries, 1); $i++) {
    try {
        $null = $udp.Send($bytes, $bytes.Length)
        $remote = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any, 0)
        $data = $udp.Receive([ref]$remote)
        if ($Payload -eq 'ntp') {
            if ($data.Length -ge 48 -and ($data[0] -band 7) -eq 4) {
                $state = 'responded'
                $detail = "NTP reply ok (stratum $($data[1]))"
            } else {
                $udp.Close()
                $Ansible.Result = @{ status = 'fail'; detail = 'answered, but not a valid NTP server response' }
                return
            }
        } else {
            $state = 'responded'
            $detail = "reply of $($data.Length) bytes"
        }
        break
    } catch {
        $se = $_.Exception
        while ($se -and $se -isnot [System.Net.Sockets.SocketException]) { $se = $se.InnerException }
        if ($se -and $se.ErrorCode -eq 10054) {
            # ICMP port unreachable surfaced as ConnectionReset
            $udp.Close()
            $Ansible.Result = @{ status = 'fail'; detail = "${TargetHost}:${Port} is closed (ICMP port unreachable)" }
            return
        }
        # 10060 = timeout; anything else: keep trying, report silence at the end
    }
}
$udp.Close()

if ($state -eq 'responded') {
    $Ansible.Result = @{ status = 'ok'; detail = $detail }
} elseif ($ExpectReply) {
    $Ansible.Result = @{ status = 'fail'; detail = "$detail - and a reply was required" }
} else {
    $Ansible.Result = @{ status = 'warn'; detail = $detail }
}
