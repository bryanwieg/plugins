#!/usr/local/bin/python3

"""
Pure decision and command-planning logic for os-wan-ha-dhcp.

This module deliberately does not read OPNsense configuration and does not
execute commands.  Runtime integration is kept at the boundary so the
fencing primitive can be replaced if the prototype gates disprove the
current LAGG candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import secrets
import re
from typing import Iterable


WANHA_DEVICE = "wanha0lagg"
_MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class GlobalRole(str, Enum):
    MASTER = "MASTER"
    BACKUP = "BACKUP"
    INDETERMINATE = "INDETERMINATE"


class DesiredAttachment(str, Enum):
    ATTACHED = "ATTACHED"
    FENCED = "FENCED"
    UNMANAGED = "UNMANAGED"


@dataclass(frozen=True)
class InterfaceSnapshot:
    name: str
    exists: bool = False
    up: bool = False
    link_up: bool = False
    mac: str | None = None
    lagg_protocol: str | None = None
    mtu: int | None = None
    lagg_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservedState:
    carp_states: tuple[str, ...] = ()
    carp_allowed: bool = True
    carp_maintenance: bool = False
    carrier: InterfaceSnapshot | None = None
    wanha: InterfaceSnapshot | None = None


@dataclass(frozen=True)
class Settings:
    enabled: bool
    carrier: str
    shared_mac: str
    managed_by_wanha: bool = True
    managed_mtu: int | None = None


@dataclass(frozen=True)
class DesiredState:
    role: GlobalRole
    attachment: DesiredAttachment
    reason: str


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    reason: str

    def shell_display(self) -> str:
        return " ".join(self.argv)


@dataclass
class Plan:
    desired: DesiredState
    commands: list[Command] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_mac(value: str) -> str:
    value = value.strip().lower()
    if not _MAC_RE.fullmatch(value):
        raise ValueError("invalid MAC address")
    return value


def validate_shared_mac(value: str) -> tuple[bool, str]:
    try:
        normalized = normalize_mac(value)
    except ValueError:
        return False, "invalid MAC address syntax"

    octets = bytes(int(part, 16) for part in normalized.split(":"))
    if octets == b"\x00" * 6:
        return False, "all-zero MAC address is not usable"
    if octets == b"\xff" * 6:
        return False, "broadcast MAC address is not usable"
    if octets[0] & 0x01:
        return False, "multicast MAC address is not usable"
    return True, ""


def is_locally_administered_unicast(value: str) -> bool:
    ok, _ = validate_shared_mac(value)
    if not ok:
        return False
    first = int(normalize_mac(value).split(":")[0], 16)
    return bool(first & 0x02) and not bool(first & 0x01)


def generate_private_mac() -> str:
    # Set the locally-administered bit and clear the multicast bit.
    octets = bytearray(secrets.token_bytes(6))
    octets[0] = (octets[0] | 0x02) & 0xFE
    return ":".join(f"{byte:02x}" for byte in octets)


def is_virtual_router_mac(value: str) -> bool:
    """Return True for the IANA VRRP/CARP virtual-router prefix."""
    try:
        normalized = normalize_mac(value)
    except ValueError:
        return False
    return normalized.startswith("00:00:5e:00:01:")


def reduce_carp_role(states: Iterable[str]) -> GlobalRole:
    """
    Match OPNsense's global active/passive semantic.

    A node is MASTER only when at least one CARP state exists and every
    observed state is MASTER.  Any BACKUP state makes the node BACKUP.
    INIT, mixed unknown states, disabled/no states, and parse failures are
    indeterminate and therefore fail closed.
    """
    normalized = tuple(str(state).strip().upper() for state in states if str(state).strip())
    if not normalized:
        return GlobalRole.INDETERMINATE
    if "BACKUP" in normalized:
        return GlobalRole.BACKUP
    if all(state == "MASTER" for state in normalized):
        return GlobalRole.MASTER
    return GlobalRole.INDETERMINATE


def desired_state(settings: Settings, observed: ObservedState) -> DesiredState:
    if not settings.managed_by_wanha:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.UNMANAGED,
            "managed OPNsense interface is not assigned to wanha0lagg",
        )

    if not settings.enabled:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "plugin disabled",
        )

    valid_mac, error = validate_shared_mac(settings.shared_mac)
    if not valid_mac:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            f"invalid shared MAC: {error}",
        )

    if not observed.carp_allowed:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "CARP is administratively disabled",
        )

    if observed.carp_maintenance:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "persistent CARP maintenance mode is active",
        )

    role = reduce_carp_role(observed.carp_states)
    if role is not GlobalRole.MASTER:
        return DesiredState(role, DesiredAttachment.FENCED, f"global CARP role is {role.value}")

    if not settings.carrier.strip():
        return DesiredState(role, DesiredAttachment.FENCED, "local carrier is not configured")

    carrier = observed.carrier
    if carrier is None or not carrier.exists:
        return DesiredState(role, DesiredAttachment.FENCED, "configured local carrier is missing")
    if not carrier.link_up:
        return DesiredState(role, DesiredAttachment.FENCED, "configured local carrier has no link")

    return DesiredState(role, DesiredAttachment.ATTACHED, "global CARP MASTER and local carrier healthy")


def plan_reconcile(settings: Settings, observed: ObservedState) -> Plan:
    """
    Build a deterministic mutation plan for the provisional single-member
    LAGG design.

    No command is executed here.  In particular, demotion plans remove every
    carrier before any non-safety-critical cleanup: fence first.
    """
    desired = desired_state(settings, observed)
    plan = Plan(desired=desired)

    wanha = observed.wanha or InterfaceSnapshot(name=WANHA_DEVICE, exists=False)

    if wanha.exists and wanha.lagg_protocol is None:
        plan.desired = DesiredState(
            desired.role,
            DesiredAttachment.FENCED,
            "wanha0lagg exists but is not the expected LAGG abstraction",
        )
        plan.warnings.append(
            "refusing to mutate an existing non-LAGG interface named wanha0lagg"
        )
        return plan

    if wanha.exists and wanha.lagg_protocol != "failover":
        plan.desired = DesiredState(
            desired.role,
            DesiredAttachment.FENCED,
            f"wanha0lagg uses unsupported LAGG protocol {wanha.lagg_protocol}",
        )
        plan.warnings.append(
            "refusing to mutate wanha0lagg unless its LAGG protocol is failover"
        )
        return plan

    members = tuple(wanha.lagg_members)
    member_present = settings.carrier in members
    foreign_members = tuple(member for member in members if member != settings.carrier)

    if desired.attachment is DesiredAttachment.UNMANAGED:
        # Pre-migration / configuration-only state. Never touch the carrier.
        return plan

    if desired.attachment is DesiredAttachment.FENCED:
        # Remove every observed member.  This also fences a stale previous
        # carrier after a node-local configuration change.
        for member in members:
            plan.commands.append(
                Command(
                    ("/sbin/ifconfig", WANHA_DEVICE, "-laggport", member),
                    "fence ISP Layer-2 path before cleanup",
                )
            )
        if (
            observed.carrier is not None
            and observed.carrier.exists
            and observed.carrier.up
        ):
            plan.commands.append(
                Command(
                    ("/sbin/ifconfig", settings.carrier, "down"),
                    "keep the explicitly configured standby ISP carrier administratively silent",
                )
            )
        if wanha.exists and wanha.up:
            plan.commands.append(
                Command(
                    ("/sbin/ifconfig", WANHA_DEVICE, "down"),
                    "leave fenced logical WAN administratively down",
                )
            )
        return plan

    shared_mac = normalize_mac(settings.shared_mac)
    already_correct = (
        wanha.exists
        and wanha.up
        and wanha.lagg_protocol == "failover"
        and members == (settings.carrier,)
        and observed.carrier is not None
        and observed.carrier.up
        and (
            settings.managed_mtu is None
            or wanha.mtu == settings.managed_mtu
        )
        and wanha.mac is not None
        and wanha.mac.lower() == shared_mac
    )
    if already_correct:
        return plan

    # MASTER path.
    if not wanha.exists:
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", "lagg", "create"),
                "create a numbered LAGG; capture the returned device name",
            )
        )
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", "<created-lagg>", "name", WANHA_DEVICE),
                "rename the newly-created LAGG to the stable WAN HA device name",
            )
        )
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", WANHA_DEVICE, "laggproto", "failover"),
                "set the WAN HA LAGG protocol explicitly",
            )
        )

    # A stale/foreign member is an unsafe path. Fence it before preparing the
    # desired carrier.
    for member in foreign_members:
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", WANHA_DEVICE, "-laggport", member),
                "remove stale carrier before ownership transition without changing its administrative state",
            )
        )

    # Keep the logical interface down while member/MAC are being prepared.
    if wanha.up:
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", WANHA_DEVICE, "down"),
                "prepare WAN without forwarding during ownership transition",
            )
        )

    if (
        observed.carrier is not None
        and observed.carrier.exists
        and not observed.carrier.up
    ):
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", settings.carrier, "up"),
                "ensure the configured active carrier is administratively up",
            )
        )

    if not member_present:
        target_mtu = settings.managed_mtu
        if (
            target_mtu is not None
            and observed.carrier is not None
            and observed.carrier.mtu != target_mtu
        ):
            plan.commands.append(
                Command(
                    (
                        "/sbin/ifconfig",
                        settings.carrier,
                        "mtu",
                        str(target_mtu),
                    ),
                    "align carrier MTU with the native managed WAN before LAGG attachment",
                )
            )
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", WANHA_DEVICE, "laggport", settings.carrier),
                "attach local carrier after MASTER revalidation",
            )
        )

    if settings.managed_mtu is not None and wanha.mtu != settings.managed_mtu:
        plan.commands.append(
            Command(
                (
                    "/sbin/ifconfig",
                    WANHA_DEVICE,
                    "mtu",
                    str(settings.managed_mtu),
                ),
                "apply explicitly configured managed-WAN MTU to the logical LAGG",
            )
        )

    if wanha.mac is None or wanha.mac.lower() != shared_mac:
        plan.commands.append(
            Command(
                ("/sbin/ifconfig", WANHA_DEVICE, "ether", shared_mac),
                "apply shared ISP-facing Ethernet identity",
            )
        )

    plan.commands.append(
        Command(
            ("/sbin/ifconfig", WANHA_DEVICE, "up"),
            "enable logical WAN after member and MAC are prepared",
        )
    )

    if is_virtual_router_mac(shared_mac):
        plan.warnings.append(
            "shared MAC is in the CARP/VRRP virtual-router range and may be rejected upstream"
        )
    return plan


@dataclass(frozen=True)
class FailbackState:
    healthy_since: float | None = None


@dataclass(frozen=True)
class FailbackDecision:
    state: FailbackState
    allow_preempt: bool
    remaining_seconds: float
    reason: str


def evaluate_failback(
    *,
    now: float,
    delay_seconds: int,
    local_is_master: bool,
    local_healthy: bool,
    state: FailbackState,
) -> FailbackDecision:
    """
    Evaluate policy only; it does not manipulate CARP.

    A recovered BACKUP waits before it may preempt. Native CARP advskew still
    decides whether it would preempt at all, so the plugin does not need its
    own primary/secondary role setting or peer liveness detector. If the peer
    disappears, native CARP promotes the local node; local_is_master then makes
    the hold immediately irrelevant.
    """
    delay = max(0, int(delay_seconds))

    if local_is_master:
        return FailbackDecision(FailbackState(), True, 0.0, "local node is already MASTER")

    if not local_healthy:
        return FailbackDecision(
            FailbackState(),
            False,
            float(delay),
            "local health is not continuously good; failback timer reset",
        )

    if delay == 0:
        return FailbackDecision(FailbackState(), True, 0.0, "failback delay is disabled")

    healthy_since = state.healthy_since if state.healthy_since is not None else now
    elapsed = max(0.0, now - healthy_since)
    remaining = max(0.0, delay - elapsed)
    if remaining <= 0:
        return FailbackDecision(
            FailbackState(healthy_since),
            True,
            0.0,
            "continuous healthy failback delay completed",
        )

    return FailbackDecision(
        FailbackState(healthy_since),
        False,
        remaining,
        "recovered node is in failback hold while peer remains MASTER",
    )


def parse_carp_states(ifconfig_text: str) -> tuple[str, ...]:
    states: list[str] = []
    for line in ifconfig_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("carp: "):
            parts = stripped.split()
            if len(parts) >= 2:
                states.append(parts[1].upper())
    return tuple(states)


def parse_interface_snapshot(name: str, ifconfig_text: str) -> InterfaceSnapshot:
    up = False
    link_up = False
    mac: str | None = None
    lagg_protocol: str | None = None
    mtu: int | None = None
    members: list[str] = []
    exists = False

    for raw in ifconfig_text.splitlines():
        line = raw.rstrip()
        if line.startswith(f"{name}:"):
            exists = True
            mtu_match = re.search(r"\bmtu\s+(\d+)", line)
            if mtu_match:
                mtu = int(mtu_match.group(1))
            match = re.search(r"<([^>]*)>", line)
            flags = set(match.group(1).split(",")) if match else set()
            up = "UP" in flags
        elif exists and line and not line[0].isspace():
            break
        elif exists:
            stripped = line.strip()
            if stripped.startswith("ether "):
                mac = stripped.split()[1].lower()
            elif stripped.startswith("status:"):
                link_up = stripped.split(":", 1)[1].strip().lower() == "active"
            elif stripped.startswith("laggproto "):
                parts = stripped.split()
                if len(parts) >= 2:
                    lagg_protocol = parts[1]
            elif stripped.startswith("laggport:"):
                # Typical FreeBSD form: laggport: ix0 flags=...
                member = stripped.split()[1]
                members.append(member)

    return InterfaceSnapshot(
        name=name,
        exists=exists,
        up=up,
        link_up=link_up,
        mac=mac,
        lagg_protocol=lagg_protocol,
        mtu=mtu,
        lagg_members=tuple(members),
    )
