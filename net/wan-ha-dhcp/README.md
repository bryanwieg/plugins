# os-wan-ha-dhcp experimental scaffold

This directory contains the first non-production implementation slice for the
design in [../../docs/wan-ha-dhcp.md](../../docs/wan-ha-dhcp.md).

The current code intentionally **does not automatically move a live WAN
carrier**.  The FreeBSD dataplane primitive, DHCP carrier behavior, and
failback mechanism are still subject to Prototype Gates A-C in the design.

Implemented so far:

- OPNsense plugin package skeleton.
- Separate cluster-shared and node-local configuration models.
- XMLRPC registration of the shared model only.
- Registration of the stable `wanha0lagglagg` virtual WAN candidate.
- Carrier candidate discovery from OPNsense hardware/VLAN assignment options.
- Secure locally administered unicast MAC generation.
- Pure Python global-CARP-role reduction and fail-closed desired-state logic.
- Provisional single-member-LAGG command planning.
- Dry-run CLI status/plan output.
- Unit tests for role reduction, MAC rules, fencing order, idempotence, stale
  member fencing, and model metadata.

## Safety boundary

Installation must not be treated as approval to migrate a production WAN.
Automatic CARP hooks and automatic command execution are deliberately absent.

Intentional package removal is guarded while any logical interface is still
assigned to `wanha0lagglagg`; the guard explicitly permits normal package upgrades.

The only plugin-side interface creation currently proposed by the integration
scaffold is an **empty/detached** `wanha0lagglagg` LAGG candidate when OPNsense asks
the registered device to be prepared.  Gate A must validate that behavior on
OPNsense 26.7 before runtime ownership code is enabled.

## Local tests

From the repository root:

```sh
python3 -m unittest discover -s net/wan-ha-dhcp/tests -v
```

OPNsense/FreeBSD PHP/plugin lint should also be run using the repository's
normal plugin lint target once a target build environment is available.

## Dry-run controller inspection on OPNsense

The Python CLI does not execute the returned command plan:

```sh
/usr/local/bin/python3 /usr/local/opnsense/scripts/wan_ha_dhcp/wan_ha_dhcp.py \
    status \
    --enabled \
    --carrier ix0 \
    --shared-mac 02:11:22:33:44:55
```

Use the node's actual configured carrier in place of `ix0`.  The JSON output
includes:

- all observed CARP states;
- derived global role;
- desired attachment state;
- carrier and `wanha0lagglagg` observations;
- the provisional command plan;
- warnings.

The output is intended to help execute Prototype Gate A/B with Codex or a
human operator before automatic mutations are added.

## Development rule

Do not add an automatic execution path merely to make the plugin appear
complete.  The implementation must preserve the design's fail-closed and
fence-first invariants, and unresolved behavior must remain behind an explicit
prototype gate.
