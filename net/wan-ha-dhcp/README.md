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
- Registration of the stable `wanha0lagg` virtual WAN candidate.
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
assigned to `wanha0lagg`; the guard explicitly permits normal package upgrades.

The only plugin-side interface creation currently proposed by the integration
scaffold is an **empty/detached** `wanha0lagg` LAGG candidate when OPNsense asks
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
- carrier and `wanha0lagg` observations;
- the provisional command plan;
- warnings.

The output is intended to help execute Prototype Gate A/B with Codex or a
human operator before automatic mutations are added.


## Appliance qualification runbook

The following work is for Codex or a human operator with shell access to an
OPNsense 26.7+ test node.  Do **not** perform Gate A/B mutation commands on the
currently active production WAN carrier.  Capture the initial state first and
use a disposable/dedicated carrier, the HA BACKUP during a controlled window,
or an isolated test segment.

### Read-only preflight

Record this output on both nodes before changing interface state:

```sh
opnsense-version
/usr/local/sbin/configctl interface show carp
/sbin/sysctl net.inet.carp
/sbin/ifconfig -Lmv
/usr/local/sbin/configctl filter list pfsync json
/usr/local/sbin/pluginctl -X
```

After the scaffold is installed, verify registration and the detached device:

```sh
/usr/local/sbin/pluginctl -d wanha
/usr/local/sbin/pluginctl -d wanha0lagg
test -f /var/run/wan-ha-dhcp/device.wanha0lagg
/sbin/ifconfig wanha0lagg
```

The registration callback may create only an **empty failover LAGG**.  It must
not attach a carrier.

For a configured carrier, record at least:

```sh
carrier=ix0   # replace with this node's test carrier
/sbin/ifconfig -Lmv "$carrier"
/sbin/ifconfig -Lmv wanha0lagg
```

Preserve the original carrier MAC/hwaddr, MTU, flags, media/status, and any
VLAN parent/tag information in the test notes.

### Gate A: LAGG/fencing primitive

On an isolated carrier only, test the candidate ordering while observing both
the carrier and `wanha0lagg` after every command:

```sh
carrier=ix0
shared_mac=02:11:22:33:44:55

/sbin/ifconfig wanha0lagg down
/sbin/ifconfig "$carrier" up
/sbin/ifconfig wanha0lagg laggport "$carrier"
/sbin/ifconfig wanha0lagg ether "$shared_mac"
/sbin/ifconfig wanha0lagg up

/sbin/ifconfig -Lmv "$carrier"
/sbin/ifconfig -Lmv wanha0lagg
```

Then fence it:

```sh
/sbin/ifconfig wanha0lagg -laggport "$carrier"
/sbin/ifconfig "$carrier" down
/sbin/ifconfig wanha0lagg down

/sbin/ifconfig -Lmv "$carrier"
/sbin/ifconfig -Lmv wanha0lagg
```

Gate A evidence must answer all of these:

- Does add/remove work repeatedly on physical and virtual NICs?
- Does setting the shared MAC **after member attachment** produce the expected
  ISP-facing MAC?
- Does member removal restore the saved hardware/native MAC?
- When the detached carrier is administratively down, does `ifconfig` still
  report a useful physical/media `status: active` vs `no carrier` signal?
- Does a VLAN interface work as a member where required?
- Does `wanha0lagg` remain classified as a virtual/plugin device by OPNsense?
- Does reboot/interface configuration recreate it detached before DHCP?
- With no explicit WAN MTU, is carrier MTU left untouched? With an explicit
  MTU, can it be applied before attachment and on the LAGG without error?
- Does packet capture show any unexpected frame from the BACKUP carrier while
  the design says it is fenced?

Also compare the opposite MAC ordering (set the LAGG MAC before member attach)
on the isolated segment.  Retain whichever ordering consistently preserves the
configured shared MAC across representative drivers; do not encode a
driver-specific branch.

### Gate B: native DHCP convergence and identity

Do not replace OPNsense DHCP.  Test the normal logical DHCP interface while
`wanha0lagg` is detached, then attach the isolated/upstream carrier and apply
the shared MAC.

Capture DHCP on the actual upstream-facing carrier:

```sh
tcpdump -eni "$carrier" -vvv 'udp port 67 or udp port 68'
```

Verify:

- no DHCP reaches upstream while `wanha0lagg` has no member;
- DHCP `chaddr` is the configured shared MAC after promotion;
- Option 61/hostname behavior matches the existing native OPNsense WAN
  configuration;
- the resulting address, subnet, gateway, DNS, route, NAT, and gateway monitor
  are created through normal OPNsense mechanisms.

First observe whether the already-running detached dhclient reacts correctly to
carrier attachment.  If it does not, use the supported native restart path
after the member and shared MAC are in place:

```sh
/usr/local/sbin/configctl interface reconfigure wan
```

Replace `wan` with the selected managed logical interface.  OPNsense 26.7
suppresses native MAC replacement for this plugin device because it is
registered with `spoofmac=false`, so the reconfigure path should preserve the
controller-applied shared MAC; verify this on the appliance.

Inspect the per-interface lease database as part of the test:

```sh
ls -l /var/db/dhclient.leases.*
```

Do not add lease replication unless same-MAC failover testing demonstrates a
specific failure that cannot be handled by the native DHCP server/client
exchange.

### Gate C: delayed failback

Record the native baseline first:

```sh
/sbin/sysctl -n net.inet.carp.preempt
```

FreeBSD source indicates that `preempt=0` suppresses taking MASTER from a
living slower peer but does not suppress ordinary takeover after MASTER
advertisements time out.  Validate that behavior on the actual pair:

1. Place the recovered/preferred node in BACKUP with a living peer MASTER.
2. Temporarily suppress preemption on the recovering node only.
3. Confirm it does not reclaim MASTER before the configured hold expires.
4. During a separate run, make the current MASTER disappear while the hold is
   active and confirm the recovering node still becomes MASTER promptly.
5. Restore the administrator's baseline exactly.
6. Repeat with native OPNsense "Disable preempt" already configured and confirm
   the plugin would never enable preemption contrary to that policy.

Do not use Internet/gateway reachability as the failback timer's health input.

### Gate D: pfsync/session continuity and transition overlap

Before testing, confirm both nodes use the same PF-facing managed WAN device
name and that pfsync is healthy.  Establish a long-lived TCP/NAT flow through
MASTER, then perform controlled and hard failure tests.

Record:

- PF/pfsync state on both nodes before/after;
- public DHCP address before/after;
- whether the long-lived flow survives;
- behavior with pfsync defer disabled and enabled;
- timestamps for old-MASTER carrier detach and new-MASTER carrier attach.

Capture the ISP-facing L2 segment during planned failover.  There must not be
an unsafe period where both nodes deliberately expose the shared MAC.  If a
small promotion settle delay is empirically required, keep it an internal
constant first; do not add a user-facing tuning option without evidence.

### Cleanup after isolated Gate A tests

Before returning a test carrier to other use, ensure it is no longer a member
and verify its original MAC/MTU/admin state:

```sh
/sbin/ifconfig wanha0lagg -laggport "$carrier" 2>/dev/null || :
/sbin/ifconfig -Lmv "$carrier"
/sbin/ifconfig -Lmv wanha0lagg
```

Do not destroy `wanha0lagg` if an OPNsense logical interface is assigned to
it.  The package uninstall guard enforces the same safety rule.


## Development rule

Do not add an automatic execution path merely to make the plugin appear
complete.  The implementation must preserve the design's fail-closed and
fence-first invariants, and unresolved behavior must remain behind an explicit
prototype gate.
