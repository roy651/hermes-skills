#!/usr/bin/env bash
# Privileged half of the monthly mini-PC audit. Emits JSON on stdout; changes nothing.
#
# WHY THIS LIVES IN /usr/local/sbin AND NOT IN THE GIT CHECKOUT
# ------------------------------------------------------------
# It is invoked through a NOPASSWD sudoers rule. A NOPASSWD rule pointing at a
# file the invoking user can write is a root shell with extra steps -- the user
# just edits the script. /usr/local/sbin is root:root drwxr-xr-x, so roy650 can
# neither replace this file nor create a new one there. The git checkout is
# user-writable, so the source lives there but the INSTALLED copy must not.
# scripts/install-root-helper.sh does that copy (as root).
#
# Everything here is read-only by construction: no writes, no service changes.
set -u

j_str () { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

echo '{'

# --- sshd -------------------------------------------------------------------
# `sshd -T` is the ONLY trustworthy source for these. Reading sshd_config by eye
# is how the 2026-08-20 audit got this wrong: `Include` sits at line 12, before
# `PasswordAuthentication no` at line 66, and 50-cloud-init.conf sets it back to
# "yes". sshd takes the FIRST value it reads, so the drop-in wins. Grep lies here.
printf '  "sshd_effective": '
sshd -T 2>/dev/null \
  | grep -iE '^(passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication|permitemptypasswords) ' \
  | sort | j_str
echo ','

printf '  "sshd_dropins": '
ls -1 /etc/ssh/sshd_config.d/ 2>/dev/null | j_str
echo ','

# --- ufw --------------------------------------------------------------------
printf '  "ufw_status": '
ufw status verbose 2>/dev/null | j_str
echo ','

# --- netfilter --------------------------------------------------------------
# The INPUT chain ORDER is the finding that the original audit missed entirely:
# Tailscale inserts `-A INPUT -j ts-input` AHEAD of every ufw chain, and
# ts-input ends with `-i tailscale0 -j ACCEPT`. So ufw does not police tailnet
# traffic at all. We capture the order so a future change is visible.
printf '  "iptables_input": '
iptables -S INPUT 2>/dev/null | j_str
echo ','

printf '  "iptables_ts_input": '
iptables -S ts-input 2>/dev/null | j_str
echo ','

printf '  "iptables_docker_user": '
iptables -S DOCKER-USER 2>/dev/null | j_str
echo ','

# Docker's DNAT lands in nat/PREROUTING, ahead of every filter chain, which is
# why ufw cannot police published container ports. A published port with no
# `-d <addr>` is bound to every interface -- including the modem-side one.
printf '  "iptables_nat_docker": '
iptables -t nat -S DOCKER 2>/dev/null | j_str
echo ','

# --- docker -----------------------------------------------------------------
# roy650 is deliberately NOT in the docker group any more (it was root
# equivalence), so container facts have to be gathered here.
printf '  "docker_ps": '
docker ps -a --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null | j_str
echo ','

printf '  "docker_port_bindings": '
{ for c in $(docker ps -q 2>/dev/null); do
    docker inspect "$c" --format '{{.Name}} {{.HostConfig.PortBindings}}' 2>/dev/null
  done; } | j_str
echo ','

# --- tailscale --------------------------------------------------------------
# The packet filter is what Tailscale ACTUALLY enforces for tailnet traffic.
# Since ufw is bypassed, this plus the socket binds are the only real controls.
printf '  "tailscale_packet_filter": '
tailscale debug netmap 2>/dev/null \
  | python3 -c 'import json,sys
try: m=json.load(sys.stdin)
except Exception: print("", end=""); raise SystemExit
print(json.dumps(m.get("PacketFilter") or [], indent=None))' 2>/dev/null | j_str
echo ','

# --- sudo -------------------------------------------------------------------
# Blanket `NOPASSWD: /bin/systemctl` is root equivalence (`systemctl link` an
# arbitrary unit, then start it). So is NOPASSWD docker, and so is docker group
# membership. All three were removed 2026-08-21; this detects them coming back.
printf '  "sudo_nopasswd": '
{ grep -rhE 'NOPASSWD' /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -v '^\s*#'; } | j_str
echo ','

printf '  "docker_group_members": '
getent group docker | cut -d: -f4 | j_str
echo ','

# --- patch state ------------------------------------------------------------
printf '  "reboot_required": '
{ [ -f /var/run/reboot-required ] && cat /var/run/reboot-required.pkgs 2>/dev/null || echo ""; } | j_str
echo ','

printf '  "unattended_upgrades": '
systemctl is-active unattended-upgrades 2>/dev/null | j_str

echo '}'
