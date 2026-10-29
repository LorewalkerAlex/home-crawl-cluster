#!/usr/bin/env python3
"""User-run Caddy LAN Web/API entry; DNS and Worker migration are separate steps."""

import argparse
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request


EXPECTED_FILE = "4c912285f0c4b7b23bb7a0f06a2fb84fd7973e49cd442681627375dbbb5078ac"
EXPECTED_COMPOSE = "ec35ed62e63fb50263239776e5cbc53e7757e04634ece00a7e1406aaf4e7ac08"
EXPECTED_ACTIVE = "73d149a8cfe47953c0273028940471373bd08345d9f9e110dd2769c9690b12e4"
LAN_IP = "192.168.1.110"
LAN_NETWORK = "192.168.1.0/24"
NETWORK_NAME = "crawlab_default"
CONFIG = Path("/srv/apps/caddy/Caddyfile")
MARKER = "# BEGIN crawlab-lan-web"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def active_hash():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:2019/config/", timeout=5) as response:
        return sha(response.read())


def docker_subnets():
    raw = subprocess.check_output([
        "docker", "network", "inspect", NETWORK_NAME,
        "--format", "{{json .IPAM.Config}}",
    ], text=True, timeout=15)
    networks = []
    for entry in json.loads(raw):
        network = ipaddress.ip_network(entry["Subnet"])
        if network.version != 4:
            continue
        allowed = (ipaddress.ip_network("10.0.0.0/8"),
                   ipaddress.ip_network("172.16.0.0/12"),
                   ipaddress.ip_network("192.168.0.0/16"))
        if not any(network.subnet_of(parent) for parent in allowed):
            raise RuntimeError("Unexpected Docker network range; stop for review")
        networks.append(str(network))
    if not networks:
        raise RuntimeError("No IPv4 subnet found for crawlab_default")
    return sorted(set(networks))


def build_candidate(original, networks):
    text = original.decode("utf-8")
    if MARKER in text or "127.0.0.1:13003" in text:
        raise RuntimeError("Crawlab route may already exist; do not append a duplicate")
    # Derive a sibling of the already configured Tududi host, without embedding
    # the private hostname or DNS credentials in this reusable script.
    hosts = set(re.findall(r"^https://tududi\.([a-z0-9.-]+)\s*\{", text, re.M))
    if len(hosts) != 1:
        raise RuntimeError("Cannot identify one existing Tududi parent domain")
    hostname = "crawlab." + hosts.pop()
    if hostname in text:
        raise RuntimeError("Crawlab hostname already occurs; stop for review")
    ranges = " ".join([LAN_NETWORK] + networks)
    block = """
# BEGIN crawlab-lan-web
https://%s:4129 {
    bind %s
    @outside_crawlab not remote_ip %s
    respond @outside_crawlab "Forbidden" 403
    reverse_proxy 127.0.0.1:13003
}
# END crawlab-lan-web
""" % (hostname, LAN_IP, ranges)
    return original + (b"" if original.endswith(b"\n") else b"\n") + block.encode(), hostname


def write_in_place(path, content):
    # Keep the inode: Caddy uses an existing single-file Docker bind mount.
    with path.open("r+b") as stream:
        stream.write(content)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def mounted_hash():
    return subprocess.check_output([
        "docker", "exec", "caddy", "sha256sum", "/etc/caddy/Caddyfile",
    ], text=True, timeout=15).split()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Validate, back up, update and reload Caddy")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo python3; default mode previews without changes")
    if CONFIG.is_symlink() or not CONFIG.is_file():
        raise RuntimeError("Caddyfile is not the expected regular file")
    original = CONFIG.read_bytes()
    compose = CONFIG.with_name("compose.yaml")
    if sha(original) != EXPECTED_FILE or sha(compose.read_bytes()) != EXPECTED_COMPOSE:
        raise RuntimeError("Caddy files changed since review; stop without overwriting")
    if active_hash() != EXPECTED_ACTIVE:
        raise RuntimeError("Active Caddy configuration changed; stop for review")
    if mounted_hash() != EXPECTED_FILE:
        raise RuntimeError("Caddy container does not see the reviewed file")
    networks = docker_subnets()
    candidate, hostname = build_candidate(original, networks)
    print("LAN URL: https://{}:4129".format(hostname), flush=True)
    print("DNS expectation (existing wildcard covers this host): {} -> {}".format(hostname, LAN_IP), flush=True)
    print("Additional source ranges for this site only: " + ", ".join(networks), flush=True)
    print("Candidate adds one LAN HTTPS site; existing routes stay byte-for-byte", flush=True)
    if not args.apply:
        print(candidate[len(original):].decode(), flush=True)
        print("PREVIEW ONLY: no files, DNS, firewall or services changed")
        return 0
    print("1/3 Validate complete candidate in the existing Caddy container", flush=True)
    subprocess.run([
        "docker", "exec", "-i", "caddy", "caddy", "validate",
        "--config", "-", "--adapter", "caddyfile",
    ], input=candidate, check=True, timeout=60)
    if CONFIG.read_bytes() != original or active_hash() != EXPECTED_ACTIVE:
        raise RuntimeError("Configuration changed during validation; nothing written")
    if sha(compose.read_bytes()) != EXPECTED_COMPOSE:
        raise RuntimeError("Caddy Compose changed during validation; nothing written")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = CONFIG.with_name("Caddyfile.before-crawlab-lan-" + stamp)
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    print("Rollback file: " + str(backup), flush=True)
    print("Cleanup trigger: remove this copy after this Caddy/TLS routing stage passes; do not wait for DNS or Worker work", flush=True)
    reload_command = ["docker", "exec", "caddy", "caddy", "reload",
                      "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
    try:
        print("2/3 Write in place and verify host/container file hashes", flush=True)
        write_in_place(CONFIG, candidate)
        if sha(CONFIG.read_bytes()) != sha(candidate) or mounted_hash() != sha(candidate):
            raise RuntimeError("Host/container candidate hash mismatch")
        print("3/3 Reload Caddy", flush=True)
        subprocess.run(reload_command, check=True, timeout=30)
    except Exception:
        # Do not overwrite a different concurrent change during failure recovery.
        current_hash = sha(CONFIG.read_bytes())
        if current_hash not in (sha(original), sha(candidate)):
            print("Concurrent file change detected; rollback was not written. Review the rollback file", file=sys.stderr)
            raise
        write_in_place(CONFIG, original)
        try:
            subprocess.run(reload_command, check=True, timeout=30)
            print("Previous file and Caddy runtime restored; keep the copy until rollback is checked", file=sys.stderr)
        except Exception:
            print("Rollback reload failed; retain the rollback file for diagnosis", file=sys.stderr)
        raise
    print("APPLIED: Caddy reloaded; HTTPS certificate and access checks are next", flush=True)
    print("SHA256: " + sha(candidate), flush=True)
    print("DNS, firewall, Compose, images and Worker settings were not changed", flush=True)
    print("After this routing stage is accepted, cleanup: rm -- '{}'".format(backup), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("STOP: {}: {}".format(type(error).__name__, error), file=sys.stderr)
        sys.exit(1)
