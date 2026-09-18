#!/usr/bin/env python3
"""Runs as root at container start (feature entrypoint).

Allows internet access but rejects everything aimed at local/private networks.
If any rule fails to apply, the container does not start (fail closed).
"""

import os
import subprocess
import sys
from pathlib import Path

PRIVATE_V4 = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
]
SSH_PORT = "2222"


def rule(command, *arguments):
    """Applies one rule. A failure aborts the start, so the sandbox is never
    incomplete: no firewall, no container."""
    try:
        subprocess.run([command, *arguments], check=True)
    except (subprocess.CalledProcessError, OSError) as error:
        sys.exit(f"sandbox: {command} {' '.join(arguments)} failed: {error}")


def main():
    # Features installed after the sandbox feature may have added sudoers
    # entries. no-new-privileges already stops sudo from elevating; this
    # removes them too.
    for entry in Path("/etc/sudoers.d").glob("*"):
        entry.unlink()

    rule("iptables", "-F", "OUTPUT")
    rule("iptables", "-F", "INPUT")
    rule("iptables", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
    # Replies to inbound SSH (from the Docker gateway). New connections to
    # local networks are still rejected below, so they never become established.
    rule("iptables", "-A", "OUTPUT", "-m", "conntrack",
         "--ctstate", "ESTABLISHED", "-j", "ACCEPT")
    for network in PRIVATE_V4:
        rule("iptables", "-A", "OUTPUT", "-d", network,
             "-j", "REJECT", "--reject-with", "icmp-net-prohibited")

    # No inbound connections except replies and SSH (sshd feature, port 2222).
    rule("iptables", "-A", "INPUT", "-i", "lo", "-j", "ACCEPT")
    rule("iptables", "-A", "INPUT", "-m", "conntrack",
         "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
    rule("iptables", "-A", "INPUT", "-p", "tcp", "--dport", SSH_PORT, "-j", "ACCEPT")
    rule("iptables", "-P", "INPUT", "DROP")

    # IPv6 is not needed; block it entirely (covers link-local and ULA LAN
    # addresses).
    if subprocess.run(["ip6tables", "-L"], capture_output=True).returncode == 0:
        rule("ip6tables", "-F", "OUTPUT")
        rule("ip6tables", "-F", "INPUT")
        rule("ip6tables", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
        rule("ip6tables", "-A", "INPUT", "-i", "lo", "-j", "ACCEPT")
        rule("ip6tables", "-P", "OUTPUT", "DROP")
        rule("ip6tables", "-P", "INPUT", "DROP")

    print("sandbox: local networks blocked", flush=True)

    # Hand over to the container's actual command, as "exec $@" did.
    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
