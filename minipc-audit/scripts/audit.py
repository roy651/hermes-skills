#!/usr/bin/env python3
"""Monthly maintenance + security audit for the mini-PC. Read-only; prints a report.

Run:
    python3 scripts/audit.py              # full report
    python3 scripts/audit.py --quiet      # only non-OK checks (for ad-hoc use)

Design notes
------------
Deterministic on purpose. Every check is a comparison against `config.yaml`, so
"is this still true?" is answered in code rather than by a model re-reasoning
about it each month. The cron job just relays stdout.

Privileged facts come from /usr/local/sbin/minipc-audit-root via a narrow
NOPASSWD sudoers rule. If that is missing the audit still runs and marks the
privileged checks UNKNOWN -- it never silently downgrades them to "fine".
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
ROOT_HELPER = "/usr/local/sbin/minipc-audit-root"

OK, WARN, FAIL, INFO, UNKNOWN = "OK", "WARN", "FAIL", "INFO", "UNKNOWN"
ICON = {OK: "✅", WARN: "🟡", FAIL: "🔴", INFO: "ℹ️", UNKNOWN: "❔"}


@dataclass
class Check:
    section: str
    name: str
    status: str
    detail: str = ""


checks: list[Check] = []


def record(section: str, name: str, status: str, detail: str = "") -> None:
    checks.append(Check(section, name, status, detail))


def sh(cmd: list[str] | str, timeout: int = 20) -> str:
    """Run a command, returning stdout (stderr folded in). Never raises."""
    try:
        proc = subprocess.run(
            cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001 - a failed probe is data, not a crash
        return f"<error: {exc}>"


def port_is_open(host: str, port: int, timeout: int = 2) -> bool:
    """Single TCP connect. Used only for modem drift, never as proof of exposure."""
    try:
        return subprocess.run(
            ["nc", "-z", "-w", str(timeout), host, str(port)],
            capture_output=True, timeout=timeout + 3,
        ).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def load_config() -> dict:
    path = SKILL / "config.yaml"
    if not path.exists():
        sys.exit(
            f"missing {path}\n"
            "Copy config.example.yaml -> config.yaml and fill in this host's expected state.\n"
            "It is gitignored: it holds addresses and port expectations that do not belong in a public repo."
        )
    return yaml.safe_load(path.read_text())


def load_privileged() -> dict | None:
    """Privileged facts, or None if the helper is unavailable."""
    if not Path(ROOT_HELPER).exists():
        return None
    raw = sh(["sudo", "-n", ROOT_HELPER], timeout=60)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# A. SSH and remote access
# ---------------------------------------------------------------------------
def check_ssh(priv: dict | None, cfg: dict) -> None:
    sec = "SSH & access"
    if priv is None:
        record(sec, "sshd effective config", UNKNOWN, "root helper unavailable")
        return

    effective = priv.get("sshd_effective", "")
    # Only `sshd -T` is authoritative. Reading sshd_config directly is exactly how
    # the 2026-08-20 audit concluded "password auth is off" when it was on:
    # Include (line 12) precedes the `no` (line 66), and the cloud-init drop-in
    # sets "yes". First value read wins.
    if "passwordauthentication no" in effective:
        record(sec, "password auth disabled", OK, "sshd -T: passwordauthentication no")
    elif "passwordauthentication yes" in effective:
        record(sec, "password auth disabled", FAIL,
               "sshd -T reports YES. Check for a drop-in sorting before 01-hardening.conf.")
    else:
        record(sec, "password auth disabled", UNKNOWN, "could not read sshd -T")

    if "permitrootlogin yes" in effective:
        record(sec, "root login restricted", FAIL, "PermitRootLogin yes")
    else:
        record(sec, "root login restricted", OK)

    dropins = priv.get("sshd_dropins", "")
    if "01-hardening.conf" in dropins:
        record(sec, "hardening drop-in present", OK,
               "01- prefix keeps it ahead of 50-cloud-init.conf")
    else:
        record(sec, "hardening drop-in present", FAIL,
               "01-hardening.conf missing — cloud-init's 'yes' would win")

    fails = sh('journalctl _COMM=sshd --since "30 days ago" --no-pager 2>/dev/null '
               '| grep -icE "Failed password|Invalid user|authentication failure"').strip()
    count = int(fails) if fails.isdigit() else -1
    if count < 0:
        record(sec, "failed SSH attempts (30d)", UNKNOWN, "journal unreadable")
    elif count == 0:
        record(sec, "failed SSH attempts (30d)", OK, "0 — consistent with no internet exposure")
    else:
        record(sec, "failed SSH attempts (30d)", WARN,
               f"{count} failed/invalid attempts — investigate the sources")


# ---------------------------------------------------------------------------
# B. Firewall and what is actually exposed
# ---------------------------------------------------------------------------
def check_firewall(priv: dict | None, cfg: dict) -> None:
    sec = "Firewall & exposure"
    if priv is None:
        record(sec, "ufw rules", UNKNOWN, "root helper unavailable")
    else:
        ufw = priv.get("ufw_status", "")
        record(sec, "ufw enabled", OK if "Status: active" in ufw else FAIL,
               "" if "Status: active" in ufw else "ufw is NOT active")
        record(sec, "default deny incoming", OK if "deny (incoming)" in ufw else FAIL)

        # A bare "22/tcp ALLOW IN Anywhere" (no interface qualifier) means the
        # untrusted modem-side segment can reach SSH.
        blanket = [ln for ln in ufw.splitlines()
                   if re.match(r"\s*22/tcp\s+ALLOW IN\s+Anywhere", ln) and " on " not in ln]
        record(sec, "port 22 not open to all", FAIL if blanket else OK,
               blanket[0].strip() if blanket else "scoped to LAN + tailscale0")

    # Wildcard binds matter MORE than firewall rules here, because Tailscale
    # bypasses ufw entirely (see check_tailscale). A socket bound to 0.0.0.0 is
    # reachable from the tailnet no matter what ufw says.
    listeners = sh("ss -tlnH")
    wildcard = []
    for line in listeners.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        local = cols[3]
        if local.startswith("0.0.0.0:") or local.startswith("[::]:"):
            wildcard.append(local.rsplit(":", 1)[-1])

    allowed = {str(p) for p in cfg["expect"]["wildcard_ports"]}
    unexpected = sorted(set(wildcard) - allowed)
    if unexpected:
        record(sec, "only expected wildcard binds", FAIL,
               f"unexpected on 0.0.0.0/[::]: {', '.join(unexpected)} "
               f"(reachable from the tailnet regardless of ufw)")
    else:
        record(sec, "only expected wildcard binds", OK,
               f"only {', '.join(sorted(allowed))} — everything else loopback or address-bound")

    # Drift: any listener we have not seen before is worth a look.
    current = sorted({c.split()[3] for c in listeners.splitlines() if len(c.split()) >= 4})
    known = set(cfg["expect"]["listeners"])
    new = [l for l in current if l not in known]
    record(sec, "no new listeners vs baseline", WARN if new else OK,
           f"new: {', '.join(new)}" if new else "")


# ---------------------------------------------------------------------------
# C. Tailscale — this is the perimeter, not ufw
# ---------------------------------------------------------------------------
def check_tailscale(priv: dict | None, cfg: dict) -> None:
    sec = "Tailscale (the real perimeter)"

    # Re-verify the property itself rather than assuming it still holds: if a
    # Tailscale update ever put ts-input AFTER the ufw chains, the conclusions
    # in this whole section change.
    if priv:
        order = priv.get("iptables_input", "")
        ts_at = order.find("ts-input")
        ufw_at = order.find("ufw-before-input")
        if ts_at >= 0 and ufw_at >= 0:
            record(sec, "ts-input still precedes ufw", INFO,
                   "yes — ufw does NOT filter tailnet traffic; socket binds + ACL are the controls"
                   if ts_at < ufw_at else
                   "CHANGED: ufw now runs first — re-evaluate the tailnet assumptions")
        pf = priv.get("tailscale_packet_filter", "")
        expected_ports = cfg["expect"]["tailnet_allowed_ports"]
        if not pf:
            record(sec, "ACL restricts tailnet", UNKNOWN, "packet filter unreadable")
        else:
            ports_seen = sorted(set(re.findall(r'"[Ff]irst":\s*(\d+)', pf)))
            allowed = sorted({str(p) for p in expected_ports})
            if ports_seen and set(ports_seen) - set(allowed):
                record(sec, "ACL restricts tailnet", WARN,
                       f"filter permits ports {', '.join(ports_seen)}; expected {', '.join(allowed)}")
            else:
                record(sec, "ACL restricts tailnet", OK, f"ports {', '.join(allowed)} only")

    prefs = sh("tailscale debug prefs")
    try:
        p = json.loads(prefs)
        record(sec, "Tailscale SSH server off", OK if not p.get("RunSSH") else FAIL,
               "" if not p.get("RunSSH") else "RunSSH=true bypasses your sshd key requirement")
        routes = p.get("AdvertiseRoutes")
        record(sec, "not a subnet router", OK if not routes else WARN,
               "" if not routes else f"advertising {routes} — exposes the LAN to the tailnet")
    except (json.JSONDecodeError, AttributeError):
        record(sec, "tailscale prefs", UNKNOWN, "could not parse")

    serve = sh("tailscale serve status")
    record(sec, "nothing published via serve/funnel",
           OK if "No serve config" in serve else WARN, serve.strip()[:120])

    status = sh("tailscale status --json")
    try:
        s = json.loads(status)
        peers = [v.get("HostName") for v in (s.get("Peer") or {}).values()]
        expected = set(cfg["expect"]["tailnet_nodes"])
        unexpected = sorted(set(peers) - expected)
        missing = sorted(expected - set(peers))
        if unexpected:
            record(sec, "no unexpected tailnet nodes", FAIL,
                   f"UNKNOWN NODE(S): {', '.join(unexpected)} — someone added a device")
        else:
            record(sec, "no unexpected tailnet nodes", OK, f"peers: {', '.join(peers) or 'none'}")
        if missing:
            record(sec, "expected nodes present", WARN, f"absent: {', '.join(missing)}")
    except (json.JSONDecodeError, AttributeError):
        record(sec, "tailnet node list", UNKNOWN, "could not parse")

    lock = sh("tailscale lock status")
    if "Tailnet Lock is NOT enabled" in lock:
        # Deliberate as of 2026-08-21: Android cannot be a signing node, so
        # homeabit would be the only signer and losing it would be unrecoverable.
        record(sec, "Tailnet Lock", INFO,
               "off (deliberate — needs a 2nd CLI-capable signer; Android cannot sign)")
    else:
        record(sec, "Tailnet Lock", OK, "enabled")


# ---------------------------------------------------------------------------
# D. Docker containment
# ---------------------------------------------------------------------------
def check_docker(priv: dict | None, cfg: dict) -> None:
    sec = "Docker containment"
    guard_iface = cfg["modem_interface"]

    unit_enabled = sh("systemctl is-enabled docker-modem-guard.service").strip()
    unit_active = sh("systemctl is-active docker-modem-guard.service").strip()
    record(sec, "modem guard unit enabled", OK if unit_enabled == "enabled" else FAIL, unit_enabled)
    record(sec, "modem guard unit active", OK if unit_active == "active" else FAIL, unit_active)

    if priv is None:
        record(sec, "DOCKER-USER rule present", UNKNOWN, "root helper unavailable")
        return

    du = priv.get("iptables_docker_user", "")
    # dockerd flushes its own chains on restart, which is why this is a systemd
    # unit rather than a one-shot iptables command.
    if f"-i {guard_iface} -j DROP" in du:
        record(sec, "DOCKER-USER rule present", OK, f"DROP from {guard_iface}")
    else:
        record(sec, "DOCKER-USER rule present", FAIL,
               f"guard missing — containers are reachable from {guard_iface}")

    # Docker's DNAT sits in nat/PREROUTING, ahead of every ufw chain, so ufw
    # cannot police published ports. A DNAT with no `-d` matches every interface.
    nat = priv.get("iptables_nat_docker", "")
    unscoped = [ln for ln in nat.splitlines() if "DNAT" in ln and " -d " not in ln]
    record(sec, "published ports address-scoped", FAIL if unscoped else OK,
           "; ".join(unscoped) if unscoped else "all DNAT rules carry an explicit -d")

    ps = priv.get("docker_ps", "")
    for name in cfg["expect"]["containers"]:
        running = any(line.startswith(f"{name}|Up") for line in ps.splitlines())
        record(sec, f"container {name} running", OK if running else FAIL,
               "" if running else "not running — check its systemd unit")


# ---------------------------------------------------------------------------
# E. Privilege
# ---------------------------------------------------------------------------
def check_privilege(priv: dict | None, cfg: dict) -> None:
    sec = "Privilege"
    if priv is None:
        record(sec, "no blanket NOPASSWD sudo", UNKNOWN, "root helper unavailable")
        return

    nopass = priv.get("sudo_nopasswd", "")
    # Each of these is root equivalence:
    #   NOPASSWD /bin/systemctl  -> `systemctl link` an arbitrary unit, start it
    #   NOPASSWD /usr/bin/docker -> `docker run -v /:/host --privileged`
    blanket = []
    for line in nopass.splitlines():
        if re.search(r"NOPASSWD:\s*(/usr)?/bin/systemctl\s*(,|$)", line):
            blanket.append("blanket systemctl")
        if re.search(r"NOPASSWD:.*(/usr)?/bin/docker\s*(,|$)", line):
            blanket.append("blanket docker")
    record(sec, "no blanket NOPASSWD sudo", FAIL if blanket else OK,
           ", ".join(blanket) if blanket else "only scoped unit commands + the audit helper")

    members = priv.get("docker_group_members", "").strip()
    record(sec, "docker group empty", OK if not members else FAIL,
           f"members: {members} (docker group == root)" if members else "no members")


# ---------------------------------------------------------------------------
# F. Secrets and portfolio PII
# ---------------------------------------------------------------------------
def check_permissions(cfg: dict) -> None:
    sec = "Secrets & PII"
    loose = []
    for pattern in cfg["sensitive_paths"]:
        for path in Path("/").glob(pattern.lstrip("/")):
            try:
                mode = path.stat().st_mode
            except OSError:
                continue
            if mode & 0o077:  # any group or other bit
                loose.append(f"{oct(mode & 0o777)[2:]} {path}")
    record(sec, "secrets and PII are owner-only", FAIL if loose else OK,
           "; ".join(loose[:6]) if loose else "all 600/700")


# ---------------------------------------------------------------------------
# G. Patch state and general health (the maintenance half)
# ---------------------------------------------------------------------------
def check_maintenance(priv: dict | None, cfg: dict) -> None:
    sec = "Maintenance"

    upgradable = sh("apt list --upgradable 2>/dev/null | tail -n +2")
    total = len([l for l in upgradable.splitlines() if l.strip()])
    security = len([l for l in upgradable.splitlines() if "security" in l.lower()])
    record(sec, "no pending security updates", OK if security == 0 else FAIL,
           f"{security} security / {total} total upgradable")

    if priv is not None:
        pending = priv.get("reboot_required", "").strip()
        record(sec, "no reboot pending", OK if not pending else WARN,
               pending.replace("\n", ", ") if pending else "")
        record(sec, "unattended-upgrades running",
               OK if priv.get("unattended_upgrades", "").strip() == "active" else WARN)

    pro = sh("pro status")
    record(sec, "Ubuntu Pro / ESM enabled",
           OK if re.search(r"esm-apps\s+yes\s+enabled", pro) else WARN,
           "esm-apps enabled" if "esm-apps" in pro else "not attached — 19+ security updates gated")
    record(sec, "livepatch enabled",
           OK if re.search(r"livepatch\s+yes\s+enabled", pro) else INFO)

    # Most services here are *user* units, and `systemctl --user` needs
    # XDG_RUNTIME_DIR to reach the session bus. Without it every user unit looks
    # dead. Distinguish "cannot measure" from "not running" -- reporting six
    # healthy services as FAIL because of a missing env var is exactly the kind
    # of false alarm that makes a monthly report get ignored.
    user_bus = "Failed to connect" not in sh("systemctl --user is-system-running")

    failed = sh("systemctl --failed --no-legend --no-pager").strip()
    failed_user = sh("systemctl --user --failed --no-legend --no-pager").strip() if user_bus else ""
    both = "; ".join(x for x in (failed, failed_user) if x and "0 loaded" not in x)
    record(sec, "no failed units", OK if not both else WARN, both[:200])

    for unit in cfg["expect"]["services"]:
        state = sh(f"systemctl --user is-active {unit}").strip() if user_bus else ""
        if state != "active":
            state = sh(f"systemctl is-active {unit}").strip() or state
        if state == "active":
            record(sec, f"service {unit}", OK)
        elif user_bus:
            record(sec, f"service {unit}", FAIL, state or "unknown")
        else:
            record(sec, f"service {unit}", UNKNOWN,
                   "user bus unreachable (no XDG_RUNTIME_DIR) — not measured")

    root_use = sh("df -h / | tail -1").split()
    if len(root_use) >= 5:
        pct = int(root_use[4].rstrip("%"))
        record(sec, "root filesystem space", OK if pct < 85 else WARN,
               f"{root_use[4]} used, {root_use[3]} free")

    # NOTE: /boot/efi sits ~87% full and that is FINE. Kernels and initramfs go to
    # /boot on the root filesystem; the ESP holds only ~4MB of bootloader, which
    # does not grow. The rest is HP firmware + the Windows bootloader (Windows is
    # still installed). Reported as INFO so nobody "fixes" it by deleting things.
    efi = sh("df -h /boot/efi | tail -1").split()
    if len(efi) >= 5:
        record(sec, "/boot/efi usage", INFO,
               f"{efi[4]} used — expected; ESP holds only the bootloader, kernels live on /")


# ---------------------------------------------------------------------------
# H. Things this audit deliberately cannot answer
# ---------------------------------------------------------------------------
def check_blind_spots(cfg: dict) -> None:
    sec = "Not measurable from here"
    record(sec, "internet-facing exposure", INFO,
           "Requires an OFF-NETWORK vantage. NAT hairpin is disabled, so probing the WAN "
           "address from inside returns 'closed' for everything and looks like a clean result. "
           "Re-run the hotspot probe quarterly. Last confirmed 2026-08-21: zero open ports. "
           "Beware: mobile carriers intercept port 53, so nc reports it 'open' for ANY address — "
           "always control with an RFC 5737 TEST-NET address before believing a finding.")

    modem = cfg.get("modem_ip")
    iface = cfg.get("modem_interface")
    if not (modem and shutil.which("nc")):
        return

    # The modem sits UPSTREAM of pfSense, so pfSense cannot protect it. We only
    # watch for drift here: its surface is already known-bad and the fix (bridge
    # mode) is a conversation with the ISP, not something this audit can do.
    known = {str(p) for p in cfg["expect"]["modem_ports_known"]}
    open_ports = [
        str(port)
        for port in cfg["expect"]["modem_ports_known"]
        if port_is_open(modem, port)
    ]
    new_ports = sorted(set(open_ports) - known)
    record("Modem (upstream of pfSense)", "modem port surface unchanged",
           WARN if new_ports else INFO,
           f"new ports: {', '.join(new_ports)}" if new_ports
           else f"open: {', '.join(open_ports) or 'none reachable'} via {iface}")


# ---------------------------------------------------------------------------
def render(quiet: bool) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bad = [c for c in checks if c.status in (FAIL, WARN)]
    unknown = [c for c in checks if c.status == UNKNOWN]

    if bad:
        headline = f"🔴 {len([c for c in bad if c.status == FAIL])} failing, " \
                   f"{len([c for c in bad if c.status == WARN])} to review"
    elif unknown:
        headline = f"🟡 All checks passed, {len(unknown)} could not be verified"
    else:
        headline = "✅ All checks passed"

    lines = [f"🛡️ Mini-PC monthly audit — {now}", "", headline, ""]

    if bad:
        lines.append("NEEDS ATTENTION")
        for c in bad:
            lines.append(f"{ICON[c.status]} {c.name}")
            if c.detail:
                lines.append(f"    {c.detail}")
        lines.append("")

    if quiet:
        return "\n".join(lines).rstrip()

    lines.append("FULL RESULTS")
    current_section = None
    for check in checks:
        if check.section != current_section:
            if current_section is not None:
                lines.append("")
            lines.append(f"— {check.section} —")
            current_section = check.section
        suffix = f" · {check.detail}" if check.detail else ""
        lines.append(f"{ICON[check.status]} {check.name}{suffix}")

    return "\n".join(lines).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="only show non-OK checks")
    args = ap.parse_args()

    cfg = load_config()
    priv = load_privileged()
    if priv is None:
        record("Audit", "privileged checks", UNKNOWN,
               f"{ROOT_HELPER} unavailable — firewall, sshd and docker checks were NOT run. "
               "Install it with scripts/install-root-helper.sh")

    check_ssh(priv, cfg)
    check_firewall(priv, cfg)
    check_tailscale(priv, cfg)
    check_docker(priv, cfg)
    check_privilege(priv, cfg)
    check_permissions(cfg)
    check_maintenance(priv, cfg)
    check_blind_spots(cfg)

    print(render(args.quiet))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
