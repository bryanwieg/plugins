#!/usr/local/bin/python3

"""Experimental CLI for os-wan-ha-dhcp.

The current implementation is intentionally read-only by default.  It can
generate a shared MAC, reduce CARP state, and print a provisional LAGG command
plan.  Runtime mutation is left for the prototype-gated controller work.
"""

from __future__ import annotations

import argparse
import json
import os
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


def pluginctl_get(path: str) -> dict:
    result = subprocess.run(
        ["/usr/local/sbin/pluginctl", "-g", path],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_configured_settings() -> tuple[Settings, str, dict]:
    shared = pluginctl_get("OPNsense.WanHaDhcpShared")
    local = pluginctl_get("OPNsense.WanHaDhcpLocal")

    managed_name = str(shared.get("managed_interface") or "wan")
    managed = pluginctl_get(f"interfaces.{managed_name}")

    mtu = managed.get("mtu")
    try:
        managed_mtu = int(mtu) if str(mtu).strip() else None
    except (TypeError, ValueError):
        managed_mtu = None

    settings = Settings(
        enabled=str(shared.get("enabled", "0")) == "1",
        carrier=str(local.get("carrier") or ""),
        shared_mac=str(shared.get("shared_mac") or ""),
        managed_by_wanha=str(managed.get("if") or "") == WANHA_DEVICE,
        managed_mtu=managed_mtu,
    )
    return settings, managed_name, managed


def cmd_generate_mac(_args: argparse.Namespace) -> int:
    print(generate_private_mac())
    return 0


def cmd_validate_mac(args: argparse.Namespace) -> int:
    ok, message = validate_shared_mac(args.mac)
    print(json.dumps({"valid": ok, "message": message}))
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    if args.from_config:
        settings, managed_name, managed_config = load_configured_settings()
        config_source = "opnsense"
    else:
        if not args.carrier or not args.shared_mac:
            raise SystemExit("--carrier and --shared-mac are required unless --from-config is used")
        settings = Settings(
            enabled=args.enabled,
            carrier=args.carrier,
            shared_mac=args.shared_mac,
            managed_by_wanha=args.managed_by_wanha,
            managed_mtu=args.mtu,
        )
        managed_name = None
        managed_config = {}
        config_source = "arguments"

    data = read_ifconfig()
    carp_states = parse_carp_states(data)
    carrier = parse_interface_snapshot(settings.carrier, data)
    wanha = parse_interface_snapshot(WANHA_DEVICE, data)

    carp_allowed, carp_maintenance = read_carp_admin()
    observed = ObservedState(
        carp_states=carp_states,
        carp_allowed=carp_allowed,
        carp_maintenance=carp_maintenance,
        wanha_owned=os.path.isfile("/var/run/wan-ha-dhcp/device.wanha0lagg"),
        carrier=carrier,
        wanha=wanha,
    )
    plan = plan_reconcile(settings, observed)

    payload = {
        "config_source": config_source,
        "managed_interface": managed_name,
        "managed_config": managed_config,
        "settings": {
            "enabled": settings.enabled,
            "carrier": settings.carrier,
            "shared_mac": settings.shared_mac,
            "managed_by_wanha": settings.managed_by_wanha,
            "managed_mtu": settings.managed_mtu,
        },
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
    status.add_argument(
        "--from-config",
        action="store_true",
        help="load shared/local plugin settings and managed WAN settings through pluginctl",
    )
    status.add_argument("--carrier")
    status.add_argument("--shared-mac")
    status.add_argument("--enabled", action="store_true")
    status.add_argument(
        "--managed-by-wanha",
        action="store_true",
        help="assert that the logical managed WAN is already assigned to wanha0lagg",
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
