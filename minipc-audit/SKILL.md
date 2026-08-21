---
name: minipc-audit
description: Monthly maintenance and security audit of the mini-PC (homeabit). Checks SSH hardening, firewall scoping, what is actually exposed, Tailscale as the real perimeter, Docker containment, privilege escalation paths, secret/PII permissions and patch state — then reports drift against a baseline.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [cron, security, maintenance, telegram]
---

# Mini-PC Audit

A deterministic monthly check that the hardening done on 2026-08-21 is still in
force, plus routine maintenance state. It **changes nothing** — every command is
read-only.

## Running it

```bash
cd ~/.hermes/skills/minipc-audit && python3 scripts/audit.py
```

`--quiet` prints only the checks that are not OK. Exit code is 1 if anything
FAILed, 0 otherwise.

## What it checks, and why each one is there

Each check exists because something specific went wrong or was nearly missed.

### SSH & access
- **`sshd -T` says `passwordauthentication no`.** Read via `sshd -T`, never by
  grepping `sshd_config`. That distinction is the whole point: `Include
  /etc/ssh/sshd_config.d/*.conf` sits at line 12, `PasswordAuthentication no` at
  line 66, and `50-cloud-init.conf` sets it back to `yes`. **sshd honours the
  first value it reads**, so the drop-in wins. A 2026-08-20 audit read the main
  file, concluded "key-only", and was wrong — passwords were accepted.
- **`01-hardening.conf` exists.** The `01-` prefix is load-bearing: it must sort
  ahead of `50-cloud-init.conf`, and stay ahead if cloud-init rewrites its file.
- **Failed SSH attempts over 30 days.** Zero is the expected value and is itself
  evidence that port 22 is not internet-reachable — an exposed SSH port collects
  thousands of attempts within hours.

### Firewall & exposure
- **ufw active, default-deny, and port 22 not open to `Anywhere`.** A bare
  `22/tcp ALLOW IN Anywhere` (no interface qualifier) means the modem-side
  segment can reach SSH.
- **Only expected ports on `0.0.0.0`/`[::]`.** This matters *more* than the ufw
  rules — see below.
- **New listeners vs baseline.** Anything that starts listening between audits.

### Tailscale — the real perimeter
The single most important thing this audit encodes:

> **Tailscale bypasses ufw entirely.** `-A INPUT -j ts-input` is inserted ahead
> of every ufw chain, and `ts-input` ends with `-i tailscale0 -j ACCEPT`. Traffic
> arriving over the tailnet is accepted before ufw ever sees it.

Consequences, all of which are checked:
- ufw rules mentioning `tailscale0` are **decorative**. The audit re-verifies the
  chain order each month, so if a Tailscale update ever changes it, that shows up.
- Any service bound to `0.0.0.0` is reachable from every tailnet node no matter
  what the firewall says. **Narrow socket binds are the containment mechanism**,
  which is why `wildcard_ports` is checked so strictly.
- The **ACL packet filter** is read from the enforced netmap, not the admin
  console, and compared against `tailnet_allowed_ports`.
- **Unexpected tailnet nodes** are a FAIL: a new device means someone added it to
  the account. Your Google account is part of the perimeter now.
- `RunSSH` must stay false (it would bypass sshd's key requirement) and the node
  must not advertise subnet routes (that would expose the whole LAN).
- **Tailnet Lock is reported as INFO, not FAIL.** It is deliberately off: Android
  cannot act as a signing node, so this host would be the only signer and losing
  it would make the tailnet unrecoverable. Revisit when a second CLI-capable
  device joins.

### Docker containment
- **`docker-modem-guard.service` enabled and active, and its `DOCKER-USER` rule
  present.** Docker's DNAT lands in `nat/PREROUTING` and its ACCEPT in
  `DOCKER-FORWARD`, both ahead of every ufw chain — so **ufw cannot police
  published container ports**. `DOCKER-USER` is the one chain Docker will not
  clobber. It is a systemd unit because dockerd flushes its chains on restart; a
  bare `iptables -I` silently disappears.
- **Every DNAT rule carries an explicit `-d`.** A published port with no address
  is bound to every interface, including the untrusted modem-side one. That is
  how a finance app ended up answering outside the firewall.
- **Expected containers running.**

### Privilege
- **No blanket `NOPASSWD` sudo.** Both of these are root equivalence:
  `NOPASSWD /bin/systemctl` (`systemctl link` an arbitrary unit, then start it)
  and `NOPASSWD /usr/bin/docker` (`docker run -v /:/host --privileged`).
- **The `docker` group is empty.** Membership alone is root, no sudo needed.

### Secrets & PII
Every path in `sensitive_paths` must be owner-only. Covers credentials *and*
portfolio data. The 2026-08-20 audit found three `.env` files; there were six —
the one it missed held bank-scraper credentials.

### Maintenance
Pending security updates, reboot-required, unattended-upgrades, Ubuntu Pro/ESM
and livepatch, failed units, expected services, disk space.

`/boot/efi` is reported as **INFO and should stay that way**. It sits ~87% full
and that is correct: kernels and initramfs live in `/boot` on the root
filesystem, while the ESP holds only ~4 MB of bootloader that does not grow. The
rest is HP firmware and the Windows bootloader — **Windows is still installed**,
so deleting `EFI/Microsoft` would break booting it.

## What this audit deliberately will NOT tell you

**Whether anything is exposed to the internet.** It cannot know, and says so.

NAT hairpin is disabled, so probing the WAN address from inside the house returns
"closed" for every port — which is indistinguishable from a clean result. Any
in-house check would produce a falsely reassuring answer.

Confirming it needs an **off-network vantage**: tether a laptop to a phone
hotspot and probe the WAN address. Do this quarterly. Last confirmed 2026-08-21:
**zero open ports**.

One trap when you do: **mobile carriers intercept port 53**, so a bare TCP
connect reports it "open" for *any* destination. Always control against an RFC
5737 TEST-NET address (`192.0.2.1`, `198.51.100.1`, `203.0.113.1`) — nothing can
legitimately answer there, so if those look "open" too, you are measuring the
carrier, not your network.

The general lesson, which cost time twice in one day: **a single vantage point is
not a general property.** "Firewalled from the LAN" is not "firewalled". "Open
from a carrier" is not "open".

## Installation / maintenance

Privileged checks run through a small root-owned helper:

```bash
sudo bash scripts/install-root-helper.sh     # after any change to minipc-audit-root.sh
```

It installs to `/usr/local/sbin/minipc-audit-root`, authorised by a narrow
`NOPASSWD` rule in `/etc/sudoers.d/roy650-services`. It **must** live there and
not in the git checkout: a `NOPASSWD` rule pointing at a user-writable file is a
root shell with extra steps.

If the helper is missing, the audit still runs and marks those checks
**UNKNOWN** — it never downgrades an unverifiable check to "fine".

Baseline lives in `config.yaml` (gitignored — this repo is public). When you
deliberately change the box, update the baseline in the same breath.
