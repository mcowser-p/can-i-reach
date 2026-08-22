#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) mcowser-p
# Apache License 2.0 (see LICENSE or https://www.apache.org/licenses/LICENSE-2.0)
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: udp_probe
short_description: Probe a UDP port from the managed host
description:
  - Sends a UDP datagram to C(host):C(port) from the managed host and
    classifies the outcome. UDP is connectionless, so the probe can
    prove three things only - the port answered (C(responded)), the
    port is definitively closed (an ICMP port-unreachable came back),
    or nothing came back (C(open|filtered) - silence proves nothing).
  - With C(payload=ntp) a real NTP v4 client packet is sent and the
    reply is validated (48+ bytes, mode 4), turning the probe into a
    genuine time-source check.
  - The module never changes the target.
options:
  host:
    description: Host name or IP address to probe.
    type: str
    required: true
  port:
    description: UDP port to probe.
    type: int
    required: true
  timeout:
    description: Seconds to wait for a reply on each try.
    type: float
    default: 5
  tries:
    description: Datagram (re)transmissions before giving up.
    type: int
    default: 2
  payload:
    description:
      - Datagram body. C(empty) sends a zero-length datagram, C(ntp)
        sends an NTP v4 client packet and validates the reply,
        C(hex) sends the raw bytes given in C(payload_hex).
    type: str
    choices: [empty, ntp, hex]
    default: empty
  payload_hex:
    description: Hex string (e.g. C(deadbeef)) sent when C(payload=hex).
    type: str
  expect_reply:
    description:
      - When true, silence after all tries is a failure. When false,
        silence exits successfully with C(state=open|filtered) so the
        caller can record it as a warning.
    type: bool
    default: false
author:
  - mcowser-p
"""

EXAMPLES = r"""
- name: Syslog over UDP reachable (best effort)
  mcowser_p.can_i_reach.udp_probe:
    host: log.corp.example
    port: 514

- name: Domain controller answers NTP
  mcowser_p.can_i_reach.udp_probe:
    host: dc1.corp.example
    port: 123
    payload: ntp
    expect_reply: true
"""

RETURN = r"""
state:
  description: One of C(responded), C(open|filtered) or C(closed).
  returned: always
  type: str
responded:
  description: Whether a reply datagram arrived.
  returned: always
  type: bool
elapsed:
  description: Seconds until the verdict.
  returned: always
  type: float
detail:
  description: Human-readable outcome (includes NTP stratum when known).
  returned: always
  type: str
"""

import binascii
import errno
import socket
import time

from ansible.module_utils.basic import AnsibleModule

NTP_CLIENT_PACKET = b"\x23" + b"\x00" * 47  # LI=0 VN=4 mode=3 (client)


def build_payload(module):
    kind = module.params["payload"]
    if kind == "ntp":
        return NTP_CLIENT_PACKET
    if kind == "hex":
        raw = (module.params.get("payload_hex") or "").replace(" ", "")
        try:
            return binascii.unhexlify(raw)
        except (binascii.Error, ValueError):
            module.fail_json(msg="payload_hex is not valid hex: %s" % raw)
    return b""


def validate_ntp_reply(data):
    """Return (ok, detail) for an alleged NTP server reply."""
    if len(data) < 48:
        return False, "reply too short for NTP (%d bytes)" % len(data)
    mode = data[0] & 0x07
    if mode != 4:
        return False, "reply is not an NTP server response (mode %d)" % mode
    stratum = data[1]
    if stratum == 0:
        return False, "NTP kiss-of-death reply (stratum 0)"
    return True, "NTP reply ok (stratum %d)" % stratum


def run(module):
    host = module.params["host"]
    port = module.params["port"]
    timeout = module.params["timeout"]
    tries = module.params["tries"]
    expect_reply = module.params["expect_reply"]
    is_ntp = module.params["payload"] == "ntp"
    payload = build_payload(module)

    started = time.time()

    def elapsed():
        return round(time.time() - started, 3)

    try:
        addrinfo = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)[0]
    except socket.gaierror as exc:
        module.fail_json(
            msg="cannot resolve %s: %s" % (host, exc),
            state="unresolvable", responded=False,
            elapsed=elapsed(), detail="name resolution failed",
        )

    family, socktype, proto, _canon, sockaddr = addrinfo
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    try:
        # A connected UDP socket surfaces ICMP port-unreachable as
        # ECONNREFUSED on the next send/recv - the only definitive
        # "closed" signal UDP can give us.
        sock.connect(sockaddr)
        for _attempt in range(max(tries, 1)):
            try:
                sock.send(payload)
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                if exc.errno in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH):
                    module.fail_json(
                        msg="%s:%d is closed or unreachable (%s)" % (host, port, exc.strerror),
                        state="closed", responded=False,
                        elapsed=elapsed(), detail="ICMP unreachable received",
                    )
                raise
            # Got a reply.
            if is_ntp:
                ok, detail = validate_ntp_reply(data)
                if not ok:
                    module.fail_json(
                        msg="%s:%d answered but %s" % (host, port, detail),
                        state="responded", responded=True,
                        elapsed=elapsed(), detail=detail,
                    )
                module.exit_json(
                    changed=False, state="responded", responded=True,
                    elapsed=elapsed(), detail=detail,
                )
            module.exit_json(
                changed=False, state="responded", responded=True,
                elapsed=elapsed(),
                detail="reply of %d bytes" % len(data),
            )
    finally:
        sock.close()

    # Silence on every try.
    detail = "no reply after %d tries of %.1fs (open|filtered)" % (tries, timeout)
    if expect_reply:
        module.fail_json(
            msg="%s:%d gave no reply and expect_reply is set" % (host, port),
            state="open|filtered", responded=False,
            elapsed=elapsed(), detail=detail,
        )
    module.exit_json(
        changed=False, state="open|filtered", responded=False,
        elapsed=elapsed(), detail=detail,
    )


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", required=True),
            timeout=dict(type="float", default=5),
            tries=dict(type="int", default=2),
            payload=dict(type="str", choices=["empty", "ntp", "hex"], default="empty"),
            payload_hex=dict(type="str"),
            expect_reply=dict(type="bool", default=False),
        ),
        required_if=[("payload", "hex", ["payload_hex"])],
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
