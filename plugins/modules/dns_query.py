#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) mcowser-p
# Apache License 2.0 (see LICENSE or https://www.apache.org/licenses/LICENSE-2.0)
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: dns_query
short_description: Resolve a DNS record from the managed host, optionally against a specific server
description:
  - Performs a DNS lookup from the managed host. With C(server) set it
    speaks the DNS wire protocol directly to that server (UDP with a
    TCP retry when the answer is truncated), which proves the server
    itself answers - no dig/nslookup/bind-utils needed on the target.
  - Without C(server) it uses the system resolver for A/AAAA/PTR, and
    for other record types sends a wire query to the first nameserver
    in /etc/resolv.conf.
  - The module never changes the target.
options:
  name:
    description:
      - Record to look up. For C(qtype=PTR) a plain IPv4 address is
        accepted and reversed into C(in-addr.arpa) form automatically.
    type: str
    required: true
  qtype:
    description: Record type to query.
    type: str
    choices: [A, AAAA, CNAME, PTR, TXT, SRV, MX, NS, SOA]
    default: A
  server:
    description: DNS server (IP or resolvable name) to query directly.
    type: str
  port:
    description: DNS server port.
    type: int
    default: 53
  timeout:
    description: Seconds to wait for a reply on each try.
    type: float
    default: 5
  tries:
    description: Query (re)transmissions before giving up.
    type: int
    default: 2
  expected_values:
    description:
      - Values that must all be present among the answers (equal to,
        or contained in, an answer - so an SRV target matches its full
        C(prio weight port target) answer string). Comparison is
        case-insensitive.
    type: list
    elements: str
author:
  - mcowser-p
"""

EXAMPLES = r"""
- name: The DR domain controller itself resolves our app record
  mcowser_p.can_i_reach.dns_query:
    name: app.corp.example
    server: 10.48.0.10
    expected_values: [10.40.2.20]

- name: AD SRV records exist (system resolver)
  mcowser_p.can_i_reach.dns_query:
    name: _ldap._tcp.dc._msdcs.corp.example
    qtype: SRV
"""

RETURN = r"""
answers:
  description: Answer values (all answer-section records, CNAMEs included).
  returned: always
  type: list
  elements: str
rcode:
  description: DNS response code name (NOERROR, NXDOMAIN, SERVFAIL, ...) or C(SYSTEM) for resolver-library lookups.
  returned: always
  type: str
server:
  description: Where the answer came from.
  returned: always
  type: str
elapsed:
  description: Seconds until the verdict.
  returned: always
  type: float
"""

import binascii
import errno
import os
import re
import socket
import struct
import time

from ansible.module_utils.basic import AnsibleModule

QTYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def encode_qname(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna") if any(ord(c) > 127 for c in label) else label.encode("ascii")
        if not 0 < len(raw) < 64:
            raise ValueError("bad label %r in %r" % (label, name))
        out += struct.pack("B", len(raw)) + raw
    return out + b"\x00"


def build_query(qname, qtype_code):
    qid = struct.unpack(">H", os.urandom(2))[0]
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # RD set
    question = encode_qname(qname) + struct.pack(">HH", qtype_code, 1)  # IN
    return qid, header + question


def parse_name(msg, offset):
    """Decode a possibly-compressed domain name. Returns (name, next_offset)."""
    labels = []
    end = None
    jumps = 0
    while True:
        if offset >= len(msg):
            raise ValueError("truncated name")
        length = msg[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(msg):
                raise ValueError("truncated compression pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | msg[offset + 1]
            jumps += 1
            if jumps > 32:
                raise ValueError("compression pointer loop")
            continue
        if length == 0:
            if end is None:
                end = offset + 1
            return ".".join(labels), end
        offset += 1
        labels.append(msg[offset:offset + length].decode("ascii", "replace"))
        offset += length


def decode_rdata(msg, rtype, rdata_offset, rdlength):
    rdata = msg[rdata_offset:rdata_offset + rdlength]
    if rtype == 1 and rdlength == 4:
        return socket.inet_ntoa(rdata)
    if rtype == 28 and rdlength == 16:
        return socket.inet_ntop(socket.AF_INET6, rdata)
    if rtype in (2, 5, 12):  # NS, CNAME, PTR
        return parse_name(msg, rdata_offset)[0]
    if rtype == 15:  # MX
        pref = struct.unpack(">H", rdata[:2])[0]
        return "%d %s" % (pref, parse_name(msg, rdata_offset + 2)[0])
    if rtype == 33:  # SRV
        prio, weight, port = struct.unpack(">HHH", rdata[:6])
        return "%d %d %d %s" % (prio, weight, port, parse_name(msg, rdata_offset + 6)[0])
    if rtype == 16:  # TXT
        parts, pos = [], 0
        while pos < len(rdata):
            ln = rdata[pos]
            parts.append(rdata[pos + 1:pos + 1 + ln].decode("utf-8", "replace"))
            pos += 1 + ln
        return "".join(parts)
    if rtype == 6:  # SOA
        mname, after = parse_name(msg, rdata_offset)
        rname, after = parse_name(msg, after)
        serial = struct.unpack(">I", msg[after:after + 4])[0]
        return "%s %s %d" % (mname, rname, serial)
    return binascii.hexlify(rdata).decode("ascii")


def parse_response(msg, expected_qid):
    if len(msg) < 12:
        raise ValueError("response shorter than a DNS header")
    qid, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", msg[:12])
    if qid != expected_qid:
        raise ValueError("response id mismatch")
    if not flags & 0x8000:
        raise ValueError("response is not a response (QR unset)")
    result = {"rcode": flags & 0x000F, "tc": bool(flags & 0x0200), "answers": []}
    offset = 12
    for _ in range(qdcount):
        _name, offset = parse_name(msg, offset)
        offset += 4
    for _ in range(ancount):
        _name, offset = parse_name(msg, offset)
        rtype, _cls, _ttl, rdlength = struct.unpack(">HHIH", msg[offset:offset + 10])
        result["answers"].append(decode_rdata(msg, rtype, offset + 10, rdlength))
        offset += 10 + rdlength
    return result


def udp_exchange(server_addr, packet, qid, timeout, tries):
    """Returns a parsed response dict, or raises socket.timeout/OSError."""
    family = socket.getaddrinfo(server_addr[0], server_addr[1], 0, socket.SOCK_DGRAM)[0][0]
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.connect(server_addr)
        last_exc = socket.timeout("no response")
        for _attempt in range(max(tries, 1)):
            try:
                sock.send(packet)
                data = sock.recv(4096)
                return parse_response(data, qid)
            except (socket.timeout, ValueError) as exc:
                last_exc = exc
        if isinstance(last_exc, ValueError):
            raise last_exc
        raise socket.timeout("no response after %d tries" % tries)
    finally:
        sock.close()


def tcp_exchange(server_addr, packet, qid, timeout):
    family = socket.getaddrinfo(server_addr[0], server_addr[1], 0, socket.SOCK_STREAM)[0][0]
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(server_addr)
        sock.sendall(struct.pack(">H", len(packet)) + packet)
        header = b""
        while len(header) < 2:
            chunk = sock.recv(2 - len(header))
            if not chunk:
                raise ValueError("connection closed mid-response")
            header += chunk
        expected = struct.unpack(">H", header)[0]
        body = b""
        while len(body) < expected:
            chunk = sock.recv(expected - len(body))
            if not chunk:
                raise ValueError("connection closed mid-response")
            body += chunk
        return parse_response(body, qid)
    finally:
        sock.close()


def resolv_conf_nameserver():
    try:
        with open("/etc/resolv.conf") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
    except OSError:
        pass
    return None


def reverse_v4(ip):
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def check_expected(module, answers, result):
    expected = module.params.get("expected_values") or []
    if not expected:
        return
    lowered = [a.lower() for a in answers]
    missing = [e for e in expected
               if not any(e.lower() == a or e.lower() in a for a in lowered)]
    if missing:
        result["msg"] = "expected value(s) not in answers: %s (got: %s)" % (
            ", ".join(missing), ", ".join(answers) or "nothing")
        module.fail_json(**result)


def system_lookup(module, qname, qtype, started):
    """A/AAAA/PTR via the resolver library; anything else needs a wire query."""
    result = {"server": "system", "rcode": "SYSTEM", "answers": []}
    try:
        if qtype == "A" or qtype == "AAAA":
            family = socket.AF_INET if qtype == "A" else socket.AF_INET6
            infos = socket.getaddrinfo(qname, None, family, socket.SOCK_STREAM)
            seen = []
            for info in infos:
                addr = info[4][0]
                if addr not in seen:
                    seen.append(addr)
            result["answers"] = seen
        elif qtype == "PTR":
            result["answers"] = [socket.gethostbyaddr(qname)[0]]
        else:
            return False  # caller falls back to a wire query
    except (socket.gaierror, socket.herror, OSError) as exc:
        result.update(elapsed=round(time.time() - started, 3),
                      msg="%s does not resolve via the system resolver (%s)" % (qname, exc))
        module.fail_json(**result)
    result["elapsed"] = round(time.time() - started, 3)
    check_expected(module, result["answers"], result)
    module.exit_json(changed=False, **result)
    return True


def wire_lookup(module, qname, qtype, server, port, started):
    timeout = module.params["timeout"]
    tries = module.params["tries"]
    result = {"server": "%s:%d" % (server, port), "rcode": "", "answers": []}

    def bail(msg):
        result["elapsed"] = round(time.time() - started, 3)
        result["msg"] = msg
        module.fail_json(**result)

    try:
        server_ip = socket.getaddrinfo(server, port, 0, socket.SOCK_DGRAM)[0][4][0]
    except socket.gaierror as exc:
        bail("cannot resolve DNS server %s: %s" % (server, exc))
    try:
        qid, packet = build_query(qname, QTYPES[qtype])
    except ValueError as exc:
        bail(str(exc))
    try:
        response = udp_exchange((server_ip, port), packet, qid, timeout, tries)
        if response["tc"]:
            response = tcp_exchange((server_ip, port), packet, qid, timeout)
    except socket.timeout:
        bail("no response from %s (timeout after %d x %.1fs)" % (server, tries, timeout))
    except ValueError as exc:
        bail("malformed response from %s: %s" % (server, exc))
    except OSError as exc:
        if exc.errno in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH):
            bail("%s refused or unreachable on port %d (%s)" % (server, port, exc.strerror))
        bail("query to %s failed: %s" % (server, exc))

    result["rcode"] = RCODES.get(response["rcode"], "RCODE%d" % response["rcode"])
    result["answers"] = response["answers"]
    result["elapsed"] = round(time.time() - started, 3)
    if response["rcode"] != 0:
        bail("%s answered %s for %s/%s" % (server, result["rcode"], qname, qtype))
    if not response["answers"]:
        bail("%s answered NOERROR but returned no %s records for %s" % (server, qtype, qname))
    check_expected(module, result["answers"], result)
    module.exit_json(changed=False, **result)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", required=True),
            qtype=dict(type="str", choices=sorted(QTYPES), default="A"),
            server=dict(type="str"),
            port=dict(type="int", default=53),
            timeout=dict(type="float", default=5),
            tries=dict(type="int", default=2),
            expected_values=dict(type="list", elements="str"),
        ),
        supports_check_mode=True,
    )
    qname = module.params["name"]
    qtype = module.params["qtype"]
    if qtype == "PTR" and IPV4_RE.match(qname) and module.params["server"]:
        qname = reverse_v4(qname)
    started = time.time()

    server = module.params["server"]
    if server:
        wire_lookup(module, qname, qtype, server, module.params["port"], started)
    if system_lookup(module, qname, qtype, started):
        return
    # System resolver cannot answer this qtype directly - use the first
    # configured nameserver on the wire instead.
    fallback = resolv_conf_nameserver()
    if not fallback:
        module.fail_json(
            msg="qtype %s needs a wire query and no nameserver was found in "
                "/etc/resolv.conf - pass server=" % qtype,
            server="system", rcode="", answers=[], elapsed=round(time.time() - started, 3),
        )
    if qtype == "PTR" and IPV4_RE.match(qname):
        qname = reverse_v4(qname)
    wire_lookup(module, qname, qtype, fallback, 53, started)


if __name__ == "__main__":
    main()
