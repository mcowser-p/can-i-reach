# can-i-reach 📡

Network preflight for freshly deployed (or about-to-be-deployed) hosts,
run **from the host itself** — because the only vantage point that
matters for "will this server work here" is the server's own. One role,
Linux and Windows targets, one report, one verdict.

It answers the questions you'd otherwise answer by hand with a pile of
`nc`, `dig`, `Test-NetConnection` and shrugging: can this box reach its
endpoints, do the domain controllers (and the DR ones) actually answer
DNS, is a real time source talking, does the proxy work — and is direct
egress *blocked* like it's supposed to be — and can the box feed itself
from apt/dnf, WSUS/Windows Update, winget, and the deployment shares?

The collection ships two tiny pure-stdlib modules so nothing has to be
installed on targets: `dns_query` speaks raw DNS on the wire to any
server you point it at (installing `dig` from a repo you haven't proven
reachable yet would be circular), and `udp_probe` sends real datagrams,
including a genuine NTP client exchange.

## The check catalog

| layer | what gets proven | Linux | Windows |
|---|---|---|---|
| endpoint | tcp / udp / icmp / http(s)+path, expected statuses | `wait_for`, `udp_probe`, `uri`, `ping` | `win_wait_for`, `UdpClient`, `win_uri`, `Test-Connection` |
| dns | every query via the system resolver AND directly against each DNS server / DC / DR DC, optional expected answers, any qtype incl. SRV | `dns_query` (raw wire, TCP fallback) | `Resolve-DnsName [-Server -DnsOnly]` |
| dc | AD port sweep per controller (53, 88, 135, 389, 445, 464, 636, 3268, 3269 by default), per-DC lookup of the domain apex, `_ldap._tcp.dc._msdcs` SRV | `wait_for` + `dns_query` | `win_wait_for` + `Resolve-DnsName` |
| dc *(agentless)* | the same port sweep with **no interpreter, no collections, no packages** on the target — and it separates `timeout` (firewalled) from `refused` (closed) | `raw` + bash `/dev/tcp` under `timeout` | `raw` + `[Net.Sockets.TcpClient]` async connect |
| time | a real NTP exchange with each source (falls back to the DC list) — Kerberos dies beyond 5 minutes of skew | `udp_probe payload=ntp` (reply validated) | `w32tm /stripchart` (+ source discovery) |
| proxy | each proxy fetches its test URLs; `forbidden_urls` must NOT be reachable directly | `uri` + proxy env | `win_uri proxy_url` |
| pkg | Linux: real apt/dnf metadata refresh (or explicit URL probes). Windows: WSUS from var → registry policy → public WU endpoints; winget sources; SMB shares (445 + access) | `apt`/`dnf`/`dnf5`, `uri` | `win_reg_stat`, `win_uri`, probe scripts |

Every check lands in `can_i_reach_result.checks` as
`{id, layer, status: ok|warn|fail|skipped, detail, remediation}`.

## Agentless DC sweep

Set `can_i_reach_dc_agentless: true` and the domain-controller port sweep
runs through `ansible.builtin.raw` instead of `wait_for` — so it works on
a host that has no Python yet, no collections installed, and nothing you
are allowed to add.

```yaml
can_i_reach_dc_agentless: true
can_i_reach_dc_tcp_ports: [53, 88, 135, 389, 445, 464, 636, 3268, 3269]
can_i_reach_domain_controllers:
  - {name: dc1, address: 10.40.0.10}
  - {name: dr-dc1, address: 10.48.0.10, dr: true}
```

```text
[FAIL] dc       dc:dc1        unreachable tcp: 3268 timeout, 3269 refused
```

That distinction is the reason to use this mode even where Python exists:
**`timeout` means a firewall dropped the SYN, `refused` means the host is
up and nothing is listening on that port.** `wait_for` reports both as one
undifferentiated failure.

Implementation notes worth knowing:

- **Linux** uses bash's `/dev/tcp` redirection. That is a *bash* feature —
  `sh` is dash on Debian/Ubuntu and does not have it — so the probe
  invokes `bash` explicitly while the surrounding loop stays POSIX.
- `/dev/tcp` has **no timeout of its own** and would hang for
  `tcp_syn_retries` (~130 s) on a dropped SYN, so coreutils `timeout`
  bounds every probe; its exit code 124 is what distinguishes filtered
  from refused.
- **Windows** uses `TcpClient.BeginConnect` with
  `AsyncWaitHandle.WaitOne(ms)`, deliberately **not**
  `Test-NetConnection` — that cmdlet accepts no timeout and blocks ~20 s
  per dropped port, which turns a sweep of a firewalled DC into minutes.
- A target with no `bash` (or no `timeout`) records **`skipped`** with a
  hint, never a false pass.
- Addresses and ports are interpolated into a shell command, so
  `validate.yml` rejects any DC address outside the hostname/IP charset
  and any port that is not an integer 1–65535.

Only the DC port sweep is agentless. The DNS, time, proxy and package
layers still use the collection's modules and need an interpreter.

## Quick start

```yaml
# requirements.yml (until it's on Galaxy, install from git)
collections:
  - name: git+https://github.com/mcowser-p/can-i-reach.git
    type: git
    version: main
```

```yaml
- name: Network preflight
  hosts: new_servers
  gather_facts: true
  vars:
    can_i_reach_domain_controllers:
      - {name: dc1, address: 10.40.0.10}
      - {name: dr-dc1, address: 10.48.0.10, dr: true}
    can_i_reach_dns_queries:
      - {name: app.corp.example, expected_values: [10.40.2.20]}
    can_i_reach_endpoints:
      - {name: app-db, host: 10.40.2.11, port: 1433}
      - {name: api-health, host: api.corp.example, port: 443, path: /healthz}
    can_i_reach_proxies:
      - {name: corp, url: "http://proxy.corp.example:3128",
         test_urls: [https://deb.debian.org]}
  roles:
    - mcowser_p.can_i_reach.can_i_reach
```

Or use the shipped playbook against a whole inventory:
`ansible-playbook -i inventory mcowser_p.can_i_reach.preflight -e @examples/org-config.yml`.
Every knob is documented inline in
[`roles/can_i_reach/defaults/main.yml`](roles/can_i_reach/defaults/main.yml)
and exercised in [`examples/org-config.yml`](examples/org-config.yml).

## What one run tells you

```
can-i-reach report (11 checks):
  [OK  ] endpoint app-db                         connected in 0.02s
  [FAIL] endpoint api-health                     status -1 from /healthz
  [OK  ] dns      dns@10.48.0.10:app.corp.example  answers: 10.40.2.20 [DR DC]
  [OK  ] dc       dc:dc1                         all 9 tcp ports reachable
  [OK  ] time     ntp:dc1                        NTP reply ok (stratum 2)
  ...
verdict: 1 failed, 0 warnings
```

The suite always runs to completion, then fails the play **once**,
listing everything — `can_i_reach_fail_on: fail | warn | never` decides
the threshold (`never` turns the role into a pure reporter). Set
`can_i_reach_fail_fast: true` when a pipeline should abort at the first
failing probe batch instead. `can_i_reach_checks` allowlists check ids;
`can_i_reach_report_path` drops a YAML report on the target; downstream
plays can consume `can_i_reach_result` directly. Probes are read-only
and still run under `--check` — the one deliberate side effect is the
apt/dnf metadata refresh (`can_i_reach_repo_become` gates its
escalation, `repo_mode: urls` avoids it entirely).

## UDP, honestly

A UDP probe can prove three things only: the port **answered**, the
port is **closed** (ICMP port-unreachable came back), or **silence** —
and silence proves nothing (`open|filtered`). Silent UDP probes are
therefore recorded as `warn`, unless the entry sets
`expect_reply: true`, which makes silence a `fail`. That's also why DNS
on 53 and NTP on 123 are tested with real protocol exchanges instead of
blind datagrams: a parsed answer is a definitive verdict.

## Windows targets

Windows hosts are first-class: every layer has a native implementation
(`ansible.windows` + built-in PowerShell probes, no extra software on
the target) over WinRM, psrp, or SSH. Three sharp edges worth knowing:

- **SMB and the double hop**: access-denied on a UNC from a WinRM
  session usually means credential delegation, not the network — so
  `smb:` checks record it as `warn` with the 445 handshake already
  proven. Only timeouts and routing failures are `fail`.
- **winget** is a per-user MSIX app and typically not resolvable under
  WinRM/SYSTEM (and absent on Server SKUs), so the default checks probe
  the well-known source CDNs; `can_i_reach_winget_dynamic: true`
  additionally parses `winget source list` when the binary exists.
- **WSUS precedence**: `can_i_reach_wsus_url` if set, else the
  registry policy (`WUServer` + `UseWUServer=1`), else the public
  Windows Update endpoints with deliberately broad accepted statuses
  (their roots 403/404 by design — reachability is the signal).
  `can_i_reach_wu_deep_check: true` runs a real Update Agent COM
  search: definitive, but it can take minutes.

## Remediation

Opt-in, explicit, and honest about its limits — most connectivity
faults live in firewalls and infrastructure, not on the target. A fix
in `can_i_reach_remediate` runs only when a failed or warned check
points at it, and the whole suite re-runs afterwards to verify:

- `sync_time` — `chronyc makestep` + service restart / `w32tm /resync /force`
- `flush_dns` — `resolvectl flush-caches` (or restart systemd-resolved) / `Clear-DnsClientCache`

## Testing

`molecule test` (docker driver) spins AlmaLinux 9 and Ubuntu 24.04
containers, stands up loopback services in prepare (http, UDP echo,
dnsmasq, a fake NTP server), runs the role with one intentionally
failing check under `fail_on: never`, and asserts on the resulting
records. Windows is tested by a real playbook
([`tests/windows/preflight.yml`](tests/windows/preflight.yml)) against
a `windows-latest` runner over both WinRM and SSH — gated behind the
`test-windows` PR label (or a manual dispatch) because it's slow.

## Releasing

semantic-release on `main` with the angular preset (scoped conventional
commits; `chore` never releases). The version is stamped into
`galaxy.yml` at release time, the collection artifact lands on the
GitHub release, and it's published to Galaxy when `GALAXY_API_KEY` is
configured.

## License

Apache-2.0
