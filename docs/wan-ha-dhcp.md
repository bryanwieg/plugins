# `os-wan-ha-dhcp` design specification and implementation plan

- **Status:** Proposed design; implementation is gated by the prototype tests in this document.
- **Target repository:** `resolver-plugins/plugins`
- **Proposed plugin path:** `net/wan-ha-dhcp/`
- **Package name:** `os-wan-ha-dhcp`
- **Initial platform:** OPNsense 26.7 and later
- **Scope:** IPv4 DHCP WAN high availability for an existing active/passive OPNsense CARP cluster.
- **Current implementation:** this branch contains an experimental, non-activating plugin scaffold with MVC configuration, device registration, read-only HA discovery, MAC generation, and a pure Python desired-state/command-planning engine. Automatic carrier mutation remains disabled pending Prototype Gates A-C.

## 1. Problem statement

Standard OPNsense CARP HA expects a movable virtual IP address. A DHCP-only ISP may provide only one dynamic public IPv4 address and may refuse the standardized CARP virtual-MAC range (`00:00:5e:00:01:xx`). In that environment, neither a normal public CARP VIP nor three public WAN addresses are available.

`os-wan-ha-dhcp` will allow an existing OPNsense CARP pair to expose **one ordinary shared Ethernet identity** to the ISP and run the normal OPNsense DHCP WAN on whichever node OPNsense has already elected MASTER.

The plugin does **not** implement a second HA election protocol and does **not** replace OPNsense DHCP, routing, NAT, gateway monitoring, PF, pfsync, or CARP.

The core design principle is:

> **OPNsense owns HA election and WAN networking. `os-wan-ha-dhcp` owns only the exclusive Layer-2 attachment of the managed DHCP WAN to the node OPNsense has elected MASTER.**

## 2. Goals

The plugin MUST:

1. Reuse OPNsense/FreeBSD CARP as the sole HA election authority.
2. Determine WAN ownership using OPNsense's existing global CARP semantics: the node is eligible to own WAN only when all configured active CARP instances are unequivocally `MASTER`.
3. Fail closed: `BACKUP`, `INIT`, mixed CARP state, disabled CARP, unknown state, or invalid plugin state MUST fence the ISP-facing WAN.
4. Present the same logical WAN kernel interface name on both nodes so PF/pfsync state references can match.
5. Permit a user-selected Ethernet-capable local carrier regardless of NIC driver (`ix`, `igb`, `igc`, `em`, `hn`, `vtnet`, `vmx`, `re`, VLAN, or future compatible drivers).
6. Present one configurable ordinary unicast shared MAC address to the ISP from the active node only.
7. Continue to use the native OPNsense WAN interface configuration and native DHCP client behavior.
8. Preserve OPNsense CARP maintenance mode, temporary CARP disable, demotion, advskew, preemption, pfsync, and XMLRPC behavior.
9. Permit configurable delayed failback without preventing emergency takeover if the current MASTER disappears during the delay.
10. Avoid OPNsense core patches.
11. Minimize dependencies on private OPNsense implementation details; prefer documented plugin hooks plus FreeBSD interface/CARP primitives.
12. Integrate with the existing Resolver Plugins package/release infrastructure after that infrastructure is generalized from its current BIND-specific form.
13. Preserve existing TCP/NAT sessions across failover when the ISP reissues the same public IPv4 lease and normal pfsync prerequisites are met.

## 3. Non-goals for v1

The first release will NOT attempt to support:

- IPv6, DHCPv6, DHCPv6-PD, shared DUID/IAID, or tracked-prefix HA.
- PPPoE as the directly managed carrier.
- Tunnels such as WireGuard, OpenVPN/tun, GIF, or GRE as carriers.
- Active/active or intentionally split CARP ownership.
- Multiple simultaneously managed DHCP WANs.
- Replacing or reimplementing OPNsense DHCP, gateway creation, routing, NAT, PF, pfsync, or CARP.
- Internet-reachability-based role election.
- Forcing an ISP to retain a DHCP lease when the ISP chooses to issue a different public address.
- OPNsense core modifications.

## 4. Existing OPNsense behavior used as authority

### 4.1 Global MASTER semantics

OPNsense already has a system-wide concept of a CARP MASTER node. Its current `carp_status.php` returns `MASTER` only when CARP instances exist and all observed CARP states are `MASTER`; if any CARP instance is `BACKUP`, the node is treated as `BACKUP`; mixed/other states are not MASTER.

OPNsense's HA configuration synchronization independently uses the same effective rule before master-only synchronization: if any configured CARP instance is not `MASTER`, synchronization exits as a backup node.

The plugin MUST follow this semantic. It MUST NOT select an arbitrary role VHID and MUST NOT implement voting or majority logic.

Relevant upstream code:

- `src/opnsense/scripts/monit/carp_status.php`
- `src/etc/rc.filter_synchronize` (`pre_check_master`)
- `src/etc/devd/carp.conf`
- `src/opnsense/service/conf/actions.d/actions_interface.conf`

### 4.2 Native CARP controls remain authoritative

The plugin MUST follow actual kernel CARP state and therefore naturally honor:

- Persistent CARP maintenance mode.
- Temporary CARP disable.
- CARP demotion.
- advskew priorities.
- Native preemption settings.
- Link/service-induced CARP demotion.

The plugin MUST NOT add a second manual-failover control when OPNsense's existing CARP controls already perform that function.

### 4.3 Native WAN configuration remains authoritative

The selected logical OPNsense WAN remains a normal interface configured in OPNsense. Existing settings remain in the native interface configuration, including where applicable:

- IPv4 type = DHCP.
- DHCP hostname / client-ID behavior.
- MTU and MSS.
- DHCP timing/options supported by OPNsense.
- Block private networks / bogons.
- DNS behavior.
- Gateway creation and monitoring.
- Firewall rules.
- Outbound NAT.
- `rc.newwanip` processing.

The plugin MUST NOT duplicate these settings.

## 5. User-visible configuration surface

The normal configuration surface is intentionally small.

### 5.1 Shared settings

These settings are cluster-wide and SHOULD be eligible for OPNsense XMLRPC synchronization:

1. **Enable WAN HA DHCP**
2. **Managed logical interface**
   - Default: `WAN`.
   - v1 validation requires IPv4 DHCP.
3. **Shared WAN MAC**
   - User-entered, imported from an existing WAN spoof MAC, or generated.
   - Generated addresses MUST be locally administered unicast addresses, e.g. `02:xx:xx:xx:xx:xx`, with cryptographically secure random remaining bits.
   - Validation MUST reject multicast, broadcast, all-zero, and otherwise invalid addresses.
   - The UI SHOULD warn for standardized virtual-router ranges such as CARP/VRRP MACs because some access networks reject them.
4. **Failback delay in seconds**
   - Default proposed value: 120 seconds.
   - Meaning: a recovered preferred node must remain continuously healthy for this period before it may preempt a living MASTER.

### 5.2 Node-local setting

The following setting MUST NOT be XMLRPC-synchronized:

1. **Local WAN carrier**
   - Selected independently on each firewall.
   - Example: `ix0` on a physical node and `hn1` on a Hyper-V node.
   - The UI MUST filter by capability, not driver-name allowlists.
   - v1 candidates are Ethernet and L2 VLAN interfaces that the chosen FreeBSD fencing primitive can safely use.

### 5.3 Settings that MUST NOT be duplicated

The plugin MUST NOT ask users to re-enter:

- CARP VIPs, VHIDs, password, advskew, or MASTER/BACKUP identity.
- pfsync interface or pfsync peer.
- XMLRPC synchronization peer.
- Public IPv4, gateway, subnet, DHCP server, or DNS.
- DHCP hostname/client-ID.
- MTU/MSS.
- ISP VLAN configuration if an existing eligible VLAN interface is used as the carrier.
- NAT or firewall configuration.
- Gateway-monitoring targets.
- Internal reconciliation timing unless a demonstrated requirement appears later.

## 6. Automatic discovery and validation

The plugin SHOULD discover and display, but not duplicate as configuration:

- OPNsense version.
- CARP enabled state.
- Persistent maintenance mode.
- Current CARP demotion.
- Native preemption configuration.
- All configured/active CARP instances and their states.
- Derived global CARP role (`MASTER`, `BACKUP`, mixed/unknown).
- pfsync interface, configured peer, runtime peer/node status, and defer setting.
- XMLRPC synchronization target when present.
- Logical OPNsense interface assignments.
- Selected logical interface IPv4 configuration type.
- Current WAN backing interface during migration.
- Current DHCP address, gateway, and lease/runtime status where available.
- Existing WAN spoof MAC.
- Available compatible local carrier interfaces.
- Carrier media and link state.
- Plugin virtual interface state.
- Effective shared MAC.

Validation MUST clearly distinguish between:

- **Configuration error**: cannot safely enable.
- **Node not currently eligible**: valid configuration but this node is BACKUP or lacks local carrier link.
- **Common upstream outage**: DHCP/gateway/Internet unavailable, but node-local HA eligibility is unchanged.

## 7. HA membership and role discovery

The plugin does not need its own configurable cluster-member list.

For a normal two-node OPNsense HA deployment it can derive useful peer context from:

- pfsync configuration and runtime nodes.
- XMLRPC synchronization target when configured.
- Local OPNsense system identity.

The absence of an XMLRPC target on a secondary is not an error; OPNsense treats that as normal.

v1 is designed and tested for a two-node active/passive CARP pair. Multi-node CARP topologies are outside the initial support contract.

## 8. Stable logical WAN abstraction

### 8.1 Requirement

Both nodes MUST expose the same kernel interface name to OPNsense/PF for the managed WAN. This addresses the pfsync/state-continuity problem created when one node uses, for example, `ix0` and the other uses `vlan0.100` or `hn1`.

The desired logical device name is provisionally **`wanha0lagg`**.

The suffix is deliberate. OPNsense 26.7 still has legacy paths whose virtual-interface classifier splits device names on digits and compares the resulting tokens against a hard-coded set including `lagg`. A bare `wanha0` would therefore be misclassified as physical. Conversely, a name beginning with `lagg` risks colliding with OPNsense's core-managed `^lagg` device family and normal `<laggs>` configuration. `wanha0lagg` is intended to satisfy both constraints: it contains a post-digit `lagg` token for virtual classification, but does not start with `lagg`. Prototype Gate A MUST verify this behavior on every supported OPNsense series.

Both nodes ultimately present:

```text
OPNsense logical WAN
        |
      wanha0lagg
        |
local carrier (MASTER only)
```

### 8.2 Provisional implementation: single-member LAGG

The leading implementation candidate is a FreeBSD LAGG used as an abstraction/fencing layer:

```text
HA-1: ix0  -> wanha0lagg -> OPNsense WAN
HA-2: hn1  -> wanha0lagg -> OPNsense WAN
```

On MASTER, the local carrier is inserted as the sole member. On BACKUP, `wanha0lagg` remains present but has no physical member.

This candidate is **not frozen** until Prototype Gate A succeeds. The required behavior is the contract; LAGG is currently the minimal candidate implementation.

The implementation MUST NOT contain NIC-driver-specific branches such as `if ix ... elif hn ...`.

### 8.3 Supported carrier model

The plugin SHOULD accept any local interface type proven compatible with the fencing primitive, including physical or virtual Ethernet and eligible L2 VLAN devices. Examples include:

- `ix`, `igb`, `igc`, `em`, `re`.
- Hyper-V `hn`.
- VirtIO `vtnet`.
- VMware `vmx`.
- Other FreeBSD Ethernet drivers.
- Existing VLAN interfaces when FreeBSD permits them as members of the selected abstraction.

Direct PPPoE/tunnel interfaces are excluded from v1.

## 9. Layer-2 fencing invariant

Safety depends on **physical/L2 exclusivity**, not on racing OPNsense DHCP process start/stop behavior.

The fundamental invariant is:

> A node that is not unequivocally global CARP MASTER MUST have no Layer-2 path from `wanha0lagg` to its selected ISP carrier.

Consequences:

- A BACKUP may still have a native DHCP process associated with the logical WAN; this is safe if it has no carrier member and cannot emit frames to the ISP.
- The plugin does not need to kill DHCP fast enough to guarantee safety.
- Promotion attaches the carrier only after re-validating global MASTER state.
- Demotion fences the carrier **before** cleanup.

### 9.1 Promotion ordering

Provisional promotion sequence:

1. Acquire transition lock.
2. Re-read all current kernel CARP states.
3. Abort unless global role is unequivocally MASTER.
4. Verify local carrier exists and is locally healthy.
5. Keep `wanha0lagg` non-forwarding/down while preparing it.
6. Attach/add the local carrier to the abstraction.
7. Apply the configured shared WAN MAC in the ordering proven by Prototype Gate A.
8. Verify effective interface/member MAC behavior.
9. Bring/allow `wanha0lagg` carrier up.
10. Allow native OPNsense DHCP behavior to converge.
11. Verify local controller invariants and enter `ACTIVE`.

### 9.2 Demotion ordering

1. Detect that global CARP state is no longer unequivocally MASTER.
2. Acquire transition lock.
3. **Fence first:** remove/detach the local carrier from `wanha0lagg`.
4. Verify that no L2 ISP path remains.
5. Perform any non-safety-critical cleanup/reconciliation.
6. Enter `STANDBY`.

## 10. Shared MAC behavior

Only the active node may expose the shared WAN MAC to the ISP-facing segment.

The shared MAC is deliberately separate from CARP's standardized virtual MAC and from either node's hardware MAC.

Current FreeBSD `lagg(4)` saves each member's original link-layer address when the port joins a LAGG and restores that saved address when the port is removed. The design therefore SHOULD rely on the kernel's normal LAGG detach semantics instead of maintaining a second persistent "native MAC" database. Prototype Gate A MUST still verify this behavior on the supported OPNsense/FreeBSD build.

The plugin MUST:

- Validate the shared MAC before enablement.
- Offer import of an existing OPNsense WAN spoof MAC during migration.
- Offer secure random locally administered unicast generation.
- Have one source of truth for the shared MAC.
- Prevent conflicting normal OPNsense WAN spoof-MAC configuration after migration, or validate/migrate it so two mechanisms cannot fight each other.

Exactly how FreeBSD LAGG/member MAC propagation behaves is part of Prototype Gate A.

## 11. Native DHCP behavior

The plugin does not implement DHCP.

The managed logical OPNsense interface remains IPv4 DHCP and continues to use OPNsense's native DHCP behavior. This keeps the plugin ISP-agnostic: providers that require hostname/client-ID or other supported DHCP options remain configured through normal OPNsense WAN settings.

The plugin MUST NOT encode behavior specific to a single ISP.

### 11.1 Lease continuity

State-preserving failover depends on the upstream DHCP server issuing the same public IPv4 address to the shared client identity.

The plugin can provide the same L2 identity and preserve native OPNsense DHCP settings, but it cannot force a provider to retain an address.

Lease-file/state replication is explicitly deferred until testing demonstrates a concrete requirement. It MUST NOT be added preemptively because doing so would unnecessarily couple the plugin to OPNsense DHCP internals.

## 12. Session preservation and pfsync

Session preservation is a first-class goal.

Required conditions include:

1. pfsync is healthy.
2. PF sees the same managed WAN kernel interface name on both nodes.
3. Firewall/NAT configuration is synchronized appropriately.
4. The new MASTER obtains the same public IPv4 address.
5. The shared MAC identity moves to the new active carrier.
6. Failover does not flush states unnecessarily.

When those conditions hold, established TCP/NAT sessions SHOULD survive in the same way normal OPNsense CARP/pfsync HA is intended to preserve them.

The plugin status page SHOULD surface pfsync health and whether `pfsync defer` is enabled, but MUST NOT silently modify that global OPNsense setting.

## 13. Global role logic

The controller MUST use the following fail-closed interpretation:

```text
CARP instances exist AND every current active CARP instance == MASTER
    => eligible MASTER

anything else
    => not WAN owner
```

Examples that MUST fence WAN:

- Any `BACKUP` CARP state.
- Any `INIT` state.
- Mixed MASTER/BACKUP or MASTER/INIT states.
- CARP disabled.
- No usable CARP instances.
- Failure to parse/obtain current state.

This intentionally means v1 requires active/passive CARP. Deliberate active/active/split-VIP deployments are unsupported.

## 14. Local health versus upstream health

The plugin MUST distinguish node-local eligibility from common-path Internet health.

### 14.1 Conditions that MAY make the node ineligible

Examples:

- Configured local carrier no longer exists.
- Local physical/virtual carrier link is down.
- Required `wanha0lagg` abstraction is missing or cannot be reconciled.
- Unsafe fencing state is detected.
- Controller cannot establish required local invariants.

### 14.2 Conditions that MUST NOT by themselves trigger HA movement

- DHCP has no lease.
- ISP-assigned gateway is unreachable.
- Public DNS is unreachable.
- External ping fails.
- Provider outage or ONT/PON upstream outage shared by both nodes.

These are common-path failures in a topology where both firewalls share the same switch/ONT/gateway. Failing over cannot repair them and would risk oscillation.

### 14.3 OPNsense service-health integration

Where node-local failure must influence CARP eligibility, the plugin SHOULD use OPNsense's supported CARP service-status facility (`rc.carp_service_status.d`) rather than directly owning CARP election.

A health check must answer only:

> Can this node safely assume the WAN role if native CARP elects it?

## 15. Failback policy

### 15.1 Required behavior

When the preferred node recovers while a healthy peer is already MASTER:

1. Do not immediately preempt.
2. Require `failback_delay` seconds of continuous local health.
3. After the delay, allow normal native CARP priority/preemption to operate.
4. If the current MASTER disappears during the hold, the recovering node MUST be able to take MASTER immediately; the delay must not create an avoidable outage.
5. Any new local health failure or reboot resets the hold timer.

### 15.2 Leading mechanism: temporary preemption suppression

FreeBSD 15 CARP source makes temporary preemption suppression the leading mechanism:

- In BACKUP state, `net.inet.carp.preempt=1` permits a faster local CARP instance to treat a slower living MASTER as down and preempt it.
- With `net.inet.carp.preempt=0`, that early preemption path is skipped.
- Ordinary MASTER timeout processing remains independent of the preemption check, so loss of advertisements can still promote the BACKUP.

OPNsense 26.7 maps its native "Disable preempt" setting onto the same sysctl at startup. The plugin MUST preserve that administrator baseline:

- If native preemption is disabled, the plugin never enables it; configured failback delay is effectively superseded by the stricter native policy.
- If native preemption is enabled, the plugin may temporarily set runtime preemption to `0` during recovery hold and restore `1` only after the hold expires or the node becomes MASTER.
- Periodic reconciliation must reassert the temporary hold if another OPNsense lifecycle action restores the baseline early.
- Loss/restart of the controller while preemption is suppressed is availability-safe: it may delay automatic failback, but it must not prevent normal MASTER-timeout takeover.

Prototype Gate C still MUST validate these source-backed semantics on OPNsense 26.7 and confirm no native maintenance/demotion interaction is broken. Native CARP service-health remains the mechanism for **local WAN eligibility failures**, not the preferred failback timer mechanism.

## 16. Eventing and reconciliation

The controller uses two paths with one reconciliation implementation.

### 16.1 Fast path

Use OPNsense's supported CARP plugin event path (`rc.syshook.d/carp`) to request an immediate reconcile after a CARP transition. OPNsense already routes FreeBSD `devd` CARP events into this hook and existing plugins use it.

The hook must remain small and non-blocking beyond a bounded local reconcile trigger.

### 16.2 Safety path

A lightweight controller/service performs local invariant reconciliation approximately every five seconds.

This interval is initially an implementation constant, not a user-visible tuning option.

The periodic path does not ping the Internet and does not conduct election. It only asserts local facts such as:

- current global CARP role;
- carrier attachment/fencing state;
- shared MAC state;
- local carrier existence/link;
- virtual WAN device existence.

### 16.3 Concurrency

All mutations MUST use a single transition lock. Reconciliation MUST be idempotent so duplicate CARP events, startup calls, manual reconcile actions, and the periodic loop converge to the same state.

## 17. Runtime controller state

Persistent configuration belongs in OPNsense `config.xml` via the plugin model.

Ephemeral state SHOULD live under `/var/run/wan-ha-dhcp/` and MUST NOT be XMLRPC-synchronized. Examples:

- current controller state;
- last transition/reason/time;
- failback hold deadline;
- current carrier-attached observation;
- PID/lock files.

No cache is required. Runtime state exists only to make transitions idempotent/observable and to avoid double-applying temporary failback controls.

A reboot must be safe even if runtime state is lost: startup must reconstruct truth from OPNsense configuration plus current FreeBSD interface/CARP state.

## 18. Boot behavior

Boot MUST be fail closed.

Preferred sequence:

1. Plugin/interface registration creates the stable logical WAN abstraction without an ISP carrier attached.
2. OPNsense may configure its normal DHCP WAN on that logical device; with no carrier this cannot leak upstream traffic.
3. CARP converges normally.
4. Controller starts/reconciles.
5. Only a node that is unequivocally global MASTER may attach its configured local carrier.

The implementation SHOULD avoid an early boot hook unless Prototype Gate A/B proves it necessary. A detached-by-default virtual WAN is preferred because safety then derives from FreeBSD dataplane state rather than hook timing.

## 19. Controller failure behavior

A transient Python/controller restart on the current MASTER SHOULD NOT immediately drop a working dataplane. The kernel interface/member association may remain intact while the controller restarts.

The controller MUST be supervised/restarted using an appropriate FreeBSD/OPNsense service mechanism.

If controller health becomes persistently unsafe, the plugin MAY report failure through OPNsense CARP service-health and allow native CARP to move ownership, but only after proving that doing so cannot create dual ownership.

The five-second reconciliation loop and CARP event fast path use the same idempotent reconciliation logic so a missed event or process restart self-heals.

## 20. Disable, uninstall, and upgrade behavior

### 20.1 Disable

Disabling the plugin MUST fail closed: the managed WAN carrier is fenced rather than leaving both nodes exposed.

### 20.2 Uninstall

If OPNsense `WAN` is still assigned to the plugin-owned `wanha0lagg`, uninstalling removes the management layer needed to recreate it. The UI and package lifecycle MUST therefore provide a strong warning/guard:

> Reassign the logical WAN away from `wanha0lagg` before uninstalling `os-wan-ha-dhcp`.

If a hard uninstall guard is practical within the plugin packaging framework, it should be preferred over a warning alone.

### 20.3 Upgrade

Package upgrade must preserve configuration and leave the current dataplane stable where possible. Reconciliation after upgrade must reconstruct state rather than assume the previous process survived.

No OPNsense core files may be patched in place.

## 21. UI/API design

### 21.1 Configuration page

Proposed normal fields:

```text
Enable                         [x]
Managed OPNsense interface     [ WAN                         v ]

Local WAN carrier              [ Intel X520 (ix0)           v ]
                               (local only; not synchronized)

Shared WAN MAC                 [ 02:xx:xx:xx:xx:xx ] [Generate]
Failback delay                 [ 120 ] seconds
```

The UI should show detected existing WAN device/spoof MAC during migration and offer safe import/suggestion actions.

### 21.2 Status page

Status SHOULD include:

- Global CARP role.
- CARP enabled/maintenance/demotion/preemption state.
- Number and alignment of CARP instances.
- Controller state (`ACTIVE`, `STANDBY`, `RECOVERY_HOLD`, `FAULT`, etc.).
- Managed logical interface.
- Local carrier and link state.
- `wanha0lagg` existence and carrier/member attachment.
- Shared and effective MAC.
- Native DHCP/public IPv4/gateway status where available.
- pfsync configuration/runtime health and defer status.
- Last transition, reason, and duration.

The BACKUP page must clearly state that a down/fenced WAN carrier is intentional rather than simply showing an unexplained red WAN.

### 21.3 Diagnostics/API

Provide bounded actions for:

- Validate configuration.
- Show discovered HA environment.
- Dry-run reconciliation.
- Reconcile now.
- Show global CARP derivation.
- Show carrier/member/MAC state.
- Show native DHCP/gateway observations.
- Export a diagnostic bundle suitable for issue reports.

Do not add a second manual failover button; use native OPNsense CARP controls.

## 22. Migration workflow

Migration should be wizard-assisted and deliberately reversible.

### 22.1 Preconditions

- Existing two-node OPNsense CARP HA is healthy.
- The managed ISP-facing DHCP WAN has **no CARP VIPs assigned to it**. Native CARP remains authoritative on the cluster's other HA interfaces, but the ISP-facing WAN itself must not emit a CARP virtual MAC.
- pfsync is configured if session preservation is desired.
- Selected managed interface is IPv4 DHCP.
- Each node has a compatible local carrier available.
- Shared L2 ISP segment can see whichever node is active.
- Hypervisors/switches allow the shared MAC to move between ports/vNICs (e.g. Hyper-V MAC spoofing where required).
- Native OPNsense WAN MAC spoofing is cleared; the plugin shared MAC is the only MAC source of truth.
- v1 rejects per-interface WAN hardware-offload overrides and custom media/mediaopt settings because those settings belong to the node-local carrier after migration. Global hardware settings continue to apply to physical interfaces; explicit WAN MTU is handled separately.

### 22.2 Safe deployment outline

1. Install the plugin on both nodes with **Enable WAN HA DHCP off**.
2. Configure each node's local carrier independently.
3. Configure the shared managed-interface, shared-MAC, and failback settings on the preferred configuration source while the plugin remains disabled.
4. Synchronize the **disabled** shared plugin configuration only after both nodes have valid node-local carrier configuration. A peer that has not yet migrated its logical WAN remains safe because the controller treats "managed interface is not assigned to `wanha0lagg`" as `UNMANAGED` and performs no carrier mutation.
5. Remove any CARP VIPs from the managed ISP-facing WAN, then validate native CARP/pfsync/global role and carrier compatibility on both nodes.
6. Create/validate `wanha0lagg` detached on both nodes.
7. Migrate the BACKUP logical WAN assignment to `wanha0lagg`; verify it remains fenced. This should not affect active Internet service.
8. Perform a controlled migration of the MASTER logical WAN assignment to `wanha0lagg`. Because the plugin is still disabled and the virtual WAN is intentionally detached, expect a bounded deployment interruption at this point.
9. Enable WAN HA DHCP on the MASTER only after its logical WAN is assigned to `wanha0lagg` and all local validation passes. The controller may then attach the local carrier, apply the shared MAC, and allow native DHCP to converge.
10. Synchronize/confirm the enabled shared setting to the already-migrated BACKUP and verify that it remains physically fenced.
11. Verify only MASTER emits ISP-facing frames/shared MAC.
12. Perform controlled failover tests before declaring deployment complete.

The UI/model MUST permit shared settings to be saved while disabled even when migration is incomplete, but MUST reject **enablement** until the local node has a valid carrier, a correctly created `wanha0lagg`, a DHCP/IPv4-only managed WAN assigned to that device, and no CARP VIP/native spoof-MAC conflict on the managed WAN.

Exact wizard automation is implementation-phase work; safety ordering is mandatory.

## 23. Repository and packaging plan

The plugin belongs in `resolver-plugins/plugins` and uses the repository's existing OPNsense plugin architecture rather than a parallel project layout.

The current Resolver Plugins control plane is intentionally BIND-centric. Before production publication of `os-wan-ha-dhcp`, the repository release infrastructure should be generalized to support multiple independently versioned plugins while preserving all existing `os-bind-rp` behavior and provenance checks.

### 23.1 Proposed release model

- `master` remains the CI/control plane.
- Add per-plugin release source branches, e.g.:
  - `release/bind-rp/<series>`
  - `release/wan-ha-dhcp/<series>`
- Generalize BIND-named release metadata/workflow assumptions into per-plugin profiles/manifests where needed.
- Continue using the signed `resolver-plugins/repository` distribution boundary.
- Do not weaken package fingerprints, provenance, or pin checks.
- Keep generalization behavior-preserving for BIND and covered by existing/focused regression tests.

The infrastructure generalization should be its own narrow PR before or independently from the plugin implementation PRs.

## 24. Proposed source layout

Exact paths are provisional but should follow normal OPNsense plugin conventions:

```text
net/wan-ha-dhcp/
├── Makefile
├── pkg-descr
├── src/
│   ├── etc/
│   │   ├── inc/plugins.inc.d/
│   │   │   └── wan_ha_dhcp.inc
│   │   ├── rc.syshook.d/
│   │   │   └── carp/
│   │   │       └── 50-wan-ha-dhcp
│   │   └── rc.carp_service_status.d/
│   │       └── wan-ha-dhcp
│   └── opnsense/
│       ├── mvc/app/
│       │   ├── controllers/OPNsense/WanHaDhcp/
│       │   ├── models/OPNsense/WanHaDhcp/
│       │   └── views/OPNsense/WanHaDhcp/
│       ├── scripts/wan_ha_dhcp/
│       │   └── ...
│       └── service/conf/actions.d/
│           └── actions_wan_ha_dhcp.conf
└── tests/
```

Only add abstractions that directly serve a requirement in this document.

## 25. Prototype gates before architecture freeze

Production dataplane mutation MUST remain gated by focused prototypes. Non-activating package/UI scaffolding and pure decision tests may precede those prototypes when they encode durable requirements, but exploratory mutation code must not be treated as production implementation until the relevant gate passes.

### Gate A — stable virtual WAN and hard fencing

Prove on OPNsense 26.7:

- A plugin-created virtual WAN abstraction can have a stable same name on both nodes.
- A single-member LAGG can accept representative physical and virtual Ethernet carriers and, if required, an L2 VLAN carrier.
- Member removal creates a real L2 fence while leaving the logical WAN object present.
- Member re-add works repeatedly.
- The shared MAC can be applied deterministically without the member/LAGG MAC rules overwriting it unexpectedly.
- Removing the final LAGG member restores the member's saved native MAC as current FreeBSD source specifies.
- An administratively fenced BACKUP carrier still exposes a reliable physical/media-link health signal suitable for local eligibility checks.
- Reboot recreates the abstraction detached by default.
- OPNsense can assign the logical WAN to the abstraction normally.
- OPNsense classifies `wanha0lagg` as virtual rather than physical, and it does not collide with the core-managed `^lagg` device family.
- Normal `interfaces_configure()` boot ordering invokes the plugin device-preparation callback before configuring a logical WAN assigned to `wanha0lagg`.
- An unset WAN MTU does not force the carrier to the empty LAGG's default MTU; an explicitly configured WAN MTU can be applied safely to the carrier before attachment.
- While the BACKUP carrier is administratively fenced/down, its physical/media link state remains observable well enough to distinguish local carrier failure from intentional standby fencing.
- No unexpected frames using either the shared MAC or the carrier's hardware MAC escape during attach/detach transitions beyond behavior explicitly accepted by the gate.

If LAGG cannot meet these requirements cleanly without brittle hooks, select another FreeBSD-native abstraction before proceeding. Do not paper over a failed gate with driver-specific code.

### Gate B — native DHCP convergence

Prove:

- OPNsense can keep the managed logical WAN configured as DHCP while the virtual carrier is detached.
- No DHCP frames reach the ISP while fenced.
- Adding the active carrier causes native DHCP to converge automatically, or identify the smallest documented/supported OPNsense reconfigure action required.
- Removing/re-adding the carrier does not require private PHP/core manipulation.
- Existing WAN settings continue to apply.
- Packet capture confirms the promoted node sends the configured shared MAC as DHCP `chaddr` and preserves any explicitly configured native OPNsense DHCP client identifier/hostname behavior.
- Determine whether a dhclient started while detached observes the post-attach shared MAC automatically. If not, use the documented `configctl interface reconfigure <logical-interface>` path after shared-MAC installation rather than private DHCP internals.
- Confirm that native interface reconfigure preserves the controller-applied shared MAC. OPNsense 26.7 currently suppresses native MAC replacement for registered device types with `spoofmac=false`, which is how `wanha0lagg` is registered.
- Record the per-interface lease database behavior (currently `/var/db/dhclient.leases.<device>`) and confirm that lack of lease-file replication does not break basic failover.

### Gate C — delayed failback

Compare candidate mechanisms and prove:

- A recovered node that native CARP would otherwise preempt from does not displace a living MASTER before `failback_delay` expires; preference remains defined only by native CARP advskew/preemption semantics.
- If the living MASTER fails during the delay, the recovering node takes over promptly.
- Existing administrator preemption settings are preserved.
- Reboot/restart during hold resets/reconstructs safely.

### Gate D — pfsync and session continuity

With both nodes using the same logical WAN kernel name:

- Confirm pfsync states reference the compatible interface identity.
- Establish a long-lived TCP flow through MASTER.
- Hard-stop/power-off MASTER.
- Confirm peer takes ownership and receives the same DHCP public IPv4 where the ISP permits it.
- Verify whether the established flow survives.
- Measure the planned-failover timeline from old-MASTER carrier detach to new-MASTER carrier attach and capture the ISP-facing segment for shared-MAC overlap/flapping.
- If measurable overlap is unsafe, test the smallest fixed internal promotion-settle delay needed; do not expose another user tuning knob unless evidence requires one.
- Repeat with pfsync defer off/on and document observed behavior without silently changing the user's setting.

## 26. Failure test matrix

At minimum test:

1. Normal MASTER → BACKUP planned CARP maintenance transition.
2. Hard MASTER power-off.
3. BACKUP reboot.
4. Preferred-node recovery and delayed failback.
5. Current MASTER fails during preferred-node failback hold.
6. Local WAN carrier cable/virtual link failure on MASTER.
7. Common ISP/gateway outage with both local carrier links healthy — MUST NOT flap ownership.
8. ISP recovers while original MASTER is powered off — surviving node must recover DHCP as MASTER.
9. CARP enters mixed state — WAN must fail closed.
10. CARP temporarily disabled through native OPNsense UI.
11. Persistent CARP maintenance mode.
12. Controller process restart on active node — should not cause gratuitous immediate dataplane loss.
13. Controller process absent/stuck long enough to be unhealthy.
14. OPNsense config reload/interface reconfigure.
15. Plugin package upgrade.
16. Plugin disable.
17. Uninstall guard/warning while `WAN` still uses `wanha0lagg`.
18. Shared MAC changed intentionally.
19. Wrong/invalid shared MAC rejected.
20. Hyper-V MAC spoofing disabled — validation/diagnostics must make failure understandable.
21. Physical Ethernet carrier on one node and Hyper-V/VirtIO/VMware carrier on peer.
22. Existing VLAN interface as local carrier, if Gate A confirms support.
23. Existing CARP VIP on the managed WAN — enablement MUST be rejected until removed.
24. Native WAN spoof MAC left configured — enablement MUST be rejected.
25. Per-interface hardware override or media/mediaopt settings on managed WAN — v1 MUST reject rather than silently misapply them.
26. Planned failover packet capture confirms bounded/no unsafe shared-MAC overlap.
27. Detach/reattach restores the carrier's original hardware MAC as expected.
28. Interface reconfigure on MASTER and BACKUP cannot accidentally reattach the BACKUP carrier.

## 27. Security and safety invariants

The following are blocking correctness requirements:

1. **Single-owner invariant under a non-partitioned CARP cluster:** the plugin MUST never deliberately attach a node that is not locally an unequivocal global CARP MASTER. As with ordinary two-node CARP, a network partition that causes both nodes to independently enter MASTER cannot be perfectly fenced without an external witness/fencing mechanism; the plugin MUST document this residual split-brain risk rather than claim to eliminate it.
2. **Fail-closed invariant:** local uncertainty never causes attachment.
3. **Fence-first invariant:** demotion detaches L2 before cleanup.
4. **No second election:** plugin follows CARP; it does not override CARP role decisions.
5. **No WAN-health flapping:** common Internet/gateway failure is not an automatic role trigger.
6. **No driver allowlist:** eligibility is capability-based.
7. **No core patching:** no production edits to OPNsense core scripts/PHP.
8. **One MAC source of truth:** no competing native spoof-MAC and plugin shared-MAC configuration.
9. **Node-local carrier isolation:** a carrier selection from one node is never synchronized onto the peer.
10. **Ephemeral-state reconstruction:** loss of `/var/run` state cannot cause dual ownership after reboot.

## 28. Requirement-to-architecture traceability

Repository policy requires every proposed abstraction/boundary/state store/retry/dependency/test to serve a current requirement. The following table records that mapping.

| Element | Current requirement served | Why it exists |
|---|---|---|
| Native CARP as sole election authority | Avoid split-brain between two election systems; preserve OPNsense maintenance/demotion semantics | Reuses an existing proven authority instead of inventing one |
| Global all-MASTER test | Keep all CARP ownership aligned with WAN ownership | Matches OPNsense's own master-only behavior |
| `wanha0lagg` stable logical WAN | pfsync/PF need matching WAN interface identity across heterogeneous NICs | Removes driver/interface-name mismatch from the PF-facing dataplane |
| Provisional single-member LAGG | Need stable WAN object plus reversible hard L2 carrier fence | Minimal FreeBSD-native candidate; prototype-gated |
| Node-local carrier field | Physical nodes may use `ix`, VM nodes `hn`, etc. | Cannot be shared/synchronized safely |
| Shared MAC field | ISP sees one stable ordinary DHCP Ethernet client across nodes | CARP MAC may be rejected; hardware MACs differ |
| Generated private MAC action | Generic deployment where no existing accepted MAC must be cloned | Produces a standards-compliant ordinary unicast identity |
| Failback delay | Avoid gratuitous second outage/MAC move immediately after preferred node recovers | Policy explicitly requested for stable recovery |
| Five-second reconcile loop | Recover from missed events/process restart/manual drift | Safety net, not election/Internet monitoring |
| CARP syshook fast path | Reduce failover latency | Supported OPNsense plugin integration already used by existing plugins |
| Transition lock | Prevent concurrent event/periodic reconciles from racing interface mutations | Required by dual trigger paths |
| `/var/run` runtime state | Idempotent failback timer/diagnostics without persisting ephemeral facts | Reconstructible and intentionally not synced |
| CARP service-health integration | A node with a broken local WAN carrier should not remain preferred MASTER | Uses native demotion instead of custom election |
| Native DHCP dependency | Remain ISP-agnostic and preserve OPNsense gateway/NAT behavior | Avoid duplicate DHCP implementation and private protocol assumptions |
| Prototype Gates A-D | Resolve concrete uncertainties before durable architecture/code | Prevent speculative complexity and brittle implementation |
| Failure test matrix | Protect exclusivity, outage behavior, and state continuity | Each test maps to an externally observable HA invariant |
| No lease replication in v1 | No demonstrated requirement yet | Avoid unnecessary OPNsense-internal coupling |
| No Internet health election | Both nodes share upstream path in target topology; failover cannot repair common outage | Avoid needless complexity/flapping |

## 29. Phased implementation plan

Keep changes reviewable and avoid a large initial plugin PR.

### Phase 0 — repository control-plane generalization

Separate PR:

- Generalize Resolver Plugins metadata/workflows from BIND-only assumptions to per-plugin profiles.
- Preserve `os-bind-rp` behavior exactly.
- Add focused regression coverage for existing BIND packaging/publication contracts.
- Do not mix WAN HA implementation into this infrastructure PR.

### Phase 1 — prototypes and architecture decision record

No production plugin release yet:

- Execute Gates A, B, and C on OPNsense 26.7.
- Record commands/results and choose the minimal fencing/failback mechanisms.
- Discard exploratory code that does not protect a durable behavior.
- Update this design if the chosen primitive changes.
- Obtain the independent-agent design/plan review required by repository `AGENTS.md` before implementation proceeds.

### Phase 2 — plugin skeleton and read-only discovery

Small PR:

- `net/wan-ha-dhcp` package skeleton.
- MVC model/controller/view.
- Shared/local config split.
- Interface/capability discovery.
- Read-only HA status/validation API.
- Secure private-MAC generator.
- No production carrier mutation yet.

### Phase 3 — dataplane controller

Focused PR:

- `wanha0lagg` lifecycle using the Gate A-selected primitive.
- Idempotent reconciliation and transition lock.
- CARP fast-path hook.
- Periodic safety reconciliation.
- Fail-closed fencing.
- Shared MAC application.
- Local health integration.
- Focused tests for pure decision/state logic and command planning.

### Phase 4 — failback and migration

Focused PR:

- Gate C-selected failback implementation.
- Migration/validation workflow for existing DHCP WAN.
- Existing spoof-MAC import/cleanup.
- Disable/uninstall safeguards.
- Status/diagnostics improvements.

### Phase 5 — HA integration qualification

- Gate D and full failure matrix on representative physical + virtual nodes.
- Verify same-IP session preservation.
- Verify common ISP outage does not flap.
- Verify maintenance mode and temporary CARP disable.
- Document external switch/hypervisor requirements.

### Phase 6 — 26.7+ release integration

- Add per-series release metadata/profile for `os-wan-ha-dhcp`.
- Build/package via generalized Resolver control plane.
- Run repository-required independent code review, `code-simplifier`, `test-suite-simplifier`, and documentation-impact review.
- Publish only after blocking correctness/security/compatibility findings are resolved.

## 30. Durable testing strategy

Prefer small pure-function tests for:

- global CARP role reduction;
- configuration validation;
- compatible-carrier filtering;
- MAC generation/validation;
- desired-state calculation;
- failback timer state transitions;
- command planning from observed → desired state.

Use integration tests/harnesses only where they protect a real boundary:

- FreeBSD interface mutation command behavior.
- OPNsense config/model/API integration.
- package lifecycle.
- runtime parsing of representative `ifconfig`/pfsync outputs.

Do not retain exploratory tests merely because they were useful during investigation or increase coverage.

## 31. Acceptance criteria for v1

A release candidate is acceptable only when all of the following are demonstrated:

1. Installs cleanly on supported OPNsense 26.7+ target(s) without core patching.
2. Two heterogeneous nodes can select different local Ethernet-capable carriers.
3. Both nodes expose the same PF-facing managed WAN interface name.
4. BACKUP emits no ISP-facing frames/shared MAC through the managed path.
5. Native CARP maintenance/demotion/disable controls continue to govern ownership.
6. Hard MASTER failure transfers carrier ownership automatically.
7. Local carrier failure on MASTER makes the peer eligible through native CARP handling.
8. Common ISP/gateway outage does not cause repeated ownership flapping.
9. Surviving MASTER recovers DHCP when provider connectivity returns even if the original MASTER remains down.
10. Configured failback delay works and does not block emergency takeover during the hold.
11. Native OPNsense DHCP/gateway/NAT behavior continues to work without duplicated DHCP configuration.
12. Shared/local configuration synchronization behaves correctly.
13. Plugin/controller restart does not create dual ownership.
14. Boot is fail closed.
15. Invalid/mixed CARP state is fail closed.
16. Session continuity succeeds when the ISP retains the public lease and normal pfsync prerequisites are satisfied.
17. Diagnostic output is sufficient to identify local carrier incompatibility, MAC-spoofing restrictions, CARP misalignment, and pfsync problems.
18. BIND package/release behavior remains unchanged by Resolver control-plane generalization.

## 32. Open questions intentionally left to prototypes

Only these implementation questions remain intentionally unresolved:

1. Is a single-member LAGG the cleanest stable `wanha0lagg` implementation on OPNsense 26.7, including renamed/interface-registration behavior?
2. What exact MAC/member operation ordering guarantees the shared MAC after attach?
3. Does native DHCP automatically reconverge on member/carrier reattachment, or is one documented `configctl` reconfigure action required?
4. Which failback-hold mechanism prevents normal preemption while preserving immediate takeover after loss of the current MASTER?
5. Is explicit lease-state replication necessary for any supported use case after same-MAC/native-DHCP testing? The default answer remains no unless evidence says otherwise.
6. Does an administratively down/detached carrier on supported physical and virtual NICs retain a reliable media-link signal for standby health checks?
7. Is any promotion-settle delay required to prevent unsafe transient same-MAC overlap during planned CARP transitions?

No other speculative subsystem should be introduced until one of these gates proves it necessary.

## 33. Repository process requirements

Implementation work must follow `resolver-plugins/plugins/AGENTS.md`, including:

- Do not open implementation PRs against official OPNsense repositories.
- Keep changes narrow and reviewable.
- Preserve Resolver package/provenance/signing boundaries.
- Independently review the written implementation plan before implementation.
- Independently review executable changes before declaring implementation PRs ready.
- Run the required code-simplifier and test-suite-simplifier review passes.
- Treat correctness, security, data-loss, compatibility, provenance, and public-contract findings as blocking.
- Keep exploratory/process artifacts out of durable source unless they protect a current product/maintainer contract.

## 34. Upstream/reference points for implementers

Review current OPNsense/FreeBSD sources for the target series before coding. Particularly relevant current paths include:

- OPNsense 26.7 build baseline:
  - Python 3.13 (`config/26.7/build.conf: PYTHON=313` in `opnsense/tools`).
- OPNsense core:
  - `src/opnsense/scripts/monit/carp_status.php`
  - `src/etc/rc.filter_synchronize`
  - `src/etc/devd/carp.conf`
  - `src/opnsense/service/conf/actions.d/actions_interface.conf`
  - `src/sbin/carp_service_status`
  - `src/etc/rc.carp_service_status.d/`
  - `src/etc/inc/interfaces.inc`
  - `src/etc/inc/plugins.inc`
- OPNsense plugins examples:
  - CARP-aware syshooks in `net/frr` and `net/mdns-repeater`
- Resolver Plugins:
  - `AGENTS.md`
  - `.resolver-plugins/`
  - `.github/ci/`
  - `.github/workflows/package-release.yml`
- FreeBSD:
  - `carp(4)`
  - `lagg(4)`
  - `ifconfig(8)`
  - `devd(8)`

Pin implementation decisions to OPNsense 26.7 source behavior first, then validate compatibility for later supported releases.