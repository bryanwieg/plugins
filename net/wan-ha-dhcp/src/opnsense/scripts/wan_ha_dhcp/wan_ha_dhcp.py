#!/usr/local/bin/python3

"""Experimental CLI for os-wan-ha-dhcp.

The current implementation is intentionally read-only by default.  It can
generate a shared MAC, reduce CARP state, and print a provisional LAGG command
plan.  Runtime mutation is left for the prototype-gated controller work.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from core import (
    InterfaceSnapshot,
    ObservedState,
    Settings,
    WANHA_DEVICE,
    generate_private_mac,
    parse_carp_states,
    parse_interface_snapshot,
    plan_reconcile,
    validate_shared_mac,
)


def read_ifconfig() -> str:
    return subprocess.run(
        ["/sbin/ifconfig", "-a"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def read_carp_admin() -> tuple[bool, bool]:
    result = subprocess.run(
        ["/usr/local/sbin/configctl", "interface", "show", "carp"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout or "{}")
    return bool(int(payload.get("allow", 0))), bool(payload.get("maintenancemode", False))


def cmd_generate_mac(_args: argparse.Namespace) -> int:
    print(generate_private_mac())
    return 0


def cmd_validate_mac(args: argparse.Namespace) -> int:
    ok, message = validate_shared_mac(args.mac)
    print(json.dumps({"valid": ok, "message": message}))
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    data = read_ifconfig()
    carp_states = parse_carp_states(data)
    carrier = parse_interface_snapshot(args.carrier, data)
    wanha = parse_interface_snapshot(WANHA_DEVICE, data)

    settings = Settings(
        enabled=args.enabled,
        carrier=args.carrier,
        shared_mac=args.shared_mac,
        managed_by_wanha=args.managed_by_wanha,
        managed_mtu=args.mtu,
    )
    carp_allowed, carp_maintenance = read_carp_admin()
    observed = ObservedState(
        carp_states=carp_states,
        carp_allowed=carp_allowed,
        carp_maintenance=carp_maintenance,
        carrier=carrier,
        wanha=wanha,
    )
    plan = plan_reconcile(settings, observed)

    payload = {
        "carp_states": list(carp_states),
        "carp_allowed": carp_allowed,
        "carp_maintenance": carp_maintenance,
        "global_role": plan.desired.role.value,
        "desired_attachment": plan.desired.attachment.value,
        "reason": plan.desired.reason,
        "carrier": carrier.__dict__,
        "wanha": wanha.__dict__,
        "commands": [
            {"argv": list(command.argv), "reason": command.reason}
            for command in plan.commands
        ],
        "warnings": plan.warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental WAN HA DHCP controller")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-mac", help="generate a locally administered unicast MAC")
    gen.set_defaults(func=cmd_generate_mac)

    validate = sub.add_parser("validate-mac", help="validate a candidate shared MAC")
    validate.add_argument("mac")
    validate.set_defaults(func=cmd_validate_mac)

    status = sub.add_parser("status", help="show observed state and dry-run reconcile plan")
    status.add_argument("--carrier", required=True)
    status.add_argument("--shared-mac", required=True)
    status.add_argument("--enabled", action="store_true")
    status.add_argument(
        "--managed-by-wanha",
        action="store_true",
        help="assert that the logical managed WAN is already assigned to wanha0",
    )
    status.add_argument(
        "--mtu",
        type=int,
        default=None,
        help="native managed WAN MTU to inherit for dry-run planning",
    )
    status.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
