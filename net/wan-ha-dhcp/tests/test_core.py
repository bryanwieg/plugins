import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


CORE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opnsense"
    / "scripts"
    / "wan_ha_dhcp"
    / "core.py"
)
SPEC = importlib.util.spec_from_file_location("wan_ha_dhcp_core", CORE)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


class MacTests(unittest.TestCase):
    def test_rejects_multicast_and_broadcast(self):
        self.assertFalse(core.validate_shared_mac("01:00:5e:00:00:01")[0])
        self.assertFalse(core.validate_shared_mac("ff:ff:ff:ff:ff:ff")[0])

    def test_generated_mac_is_local_unicast(self):
        with patch.object(core.secrets, "token_bytes", return_value=bytes.fromhex("001122334455")):
            mac = core.generate_private_mac()
        self.assertEqual(mac, "02:11:22:33:44:55")
        self.assertTrue(core.is_locally_administered_unicast(mac))

    def test_flags_virtual_router_range(self):
        self.assertTrue(core.is_virtual_router_mac("00:00:5e:00:01:ed"))
        self.assertFalse(core.is_virtual_router_mac("02:00:5e:00:01:ed"))


class CarpRoleTests(unittest.TestCase):
    def test_all_master_is_master(self):
        self.assertEqual(
            core.reduce_carp_role(["MASTER", "MASTER"]),
            core.GlobalRole.MASTER,
        )

    def test_any_backup_is_backup(self):
        self.assertEqual(
            core.reduce_carp_role(["MASTER", "BACKUP"]),
            core.GlobalRole.BACKUP,
        )

    def test_init_mixed_or_empty_is_indeterminate(self):
        self.assertEqual(core.reduce_carp_role(["MASTER", "INIT"]), core.GlobalRole.INDETERMINATE)
        self.assertEqual(core.reduce_carp_role(["INIT"]), core.GlobalRole.INDETERMINATE)
        self.assertEqual(core.reduce_carp_role([]), core.GlobalRole.INDETERMINATE)


class DesiredStateTests(unittest.TestCase):
    def settings(self):
        return core.Settings(True, "ix0", "02:11:22:33:44:55")

    def observed(self, states=("MASTER",), link=True, member=False, wanha_up=False):
        return core.ObservedState(
            carp_states=states,
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=link),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=wanha_up,
                link_up=member and link,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",) if member else (),
            ),
        )

    def test_pre_migration_state_is_passive(self):
        settings = core.Settings(
            enabled=False,
            carrier="ix0",
            shared_mac="02:11:22:33:44:55",
            managed_by_wanha=False,
        )
        observed = self.observed()
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.UNMANAGED)
        self.assertEqual(plan.commands, [])

    def test_enabled_but_not_migrated_is_still_passive(self):
        settings = core.Settings(
            enabled=True,
            carrier="ix0",
            shared_mac="02:11:22:33:44:55",
            managed_by_wanha=False,
        )
        observed = self.observed()
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.UNMANAGED)
        self.assertEqual(plan.commands, [])

    def test_master_with_healthy_carrier_attaches(self):
        desired = core.desired_state(self.settings(), self.observed())
        self.assertEqual(desired.attachment, core.DesiredAttachment.ATTACHED)

    def test_carp_disabled_fences_even_with_master_states(self):
        observed = self.observed()
        observed = core.ObservedState(
            carp_states=observed.carp_states,
            carp_allowed=False,
            carrier=observed.carrier,
            wanha=observed.wanha,
        )
        desired = core.desired_state(self.settings(), observed)
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_persistent_maintenance_fences_even_with_master_states(self):
        observed = self.observed()
        observed = core.ObservedState(
            carp_states=observed.carp_states,
            carp_allowed=True,
            carp_maintenance=True,
            carrier=observed.carrier,
            wanha=observed.wanha,
        )
        desired = core.desired_state(self.settings(), observed)
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_backup_fences(self):
        desired = core.desired_state(self.settings(), self.observed(states=("BACKUP",)))
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_common_upstream_status_is_not_an_input(self):
        # There is deliberately no gateway/DHCP/Internet health input.
        desired = core.desired_state(self.settings(), self.observed())
        self.assertEqual(desired.attachment, core.DesiredAttachment.ATTACHED)

    def test_local_link_failure_fences_even_when_master(self):
        desired = core.desired_state(self.settings(), self.observed(link=False))
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)


class PlannerTests(unittest.TestCase):
    def settings(self):
        return core.Settings(True, "ix0", "02:11:22:33:44:55")

    def test_demotion_fences_before_down(self):
        observed = core.ObservedState(
            carp_states=("BACKUP",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.commands[0].argv[-2:], ("-laggport", "ix0"))
        self.assertEqual(plan.commands[1].argv, ("/sbin/ifconfig", "ix0", "down"))
        self.assertEqual(plan.commands[2].argv, ("/sbin/ifconfig", core.WANHA_DEVICE, "down"))

    def test_correct_active_state_is_idempotent(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.commands, [])

    def test_fenced_state_removes_all_stale_members(self):
        observed = core.ObservedState(
            carp_states=("BACKUP",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0", "hn1"),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.commands[0].argv[-2:], ("-laggport", "ix0"))
        self.assertEqual(plan.commands[1].argv[-2:], ("-laggport", "hn1"))
        self.assertEqual(plan.commands[2].argv, ("/sbin/ifconfig", "ix0", "down"))
        self.assertEqual(plan.commands[3].argv, ("/sbin/ifconfig", core.WANHA_DEVICE, "down"))

    def test_foreign_member_is_detached_but_not_forced_down(self):
        settings = core.Settings(True, "hn1", "02:11:22:33:44:55")
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("hn1", exists=True, up=True, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",),
            ),
        )
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.commands[0].argv[-2:], ("-laggport", "ix0"))
        self.assertNotIn(("/sbin/ifconfig", "ix0", "down"), [cmd.argv for cmd in plan.commands])

    def test_member_present_but_carrier_admin_down_is_recovered(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=False, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertIn(("/sbin/ifconfig", "ix0", "up"), [cmd.argv for cmd in plan.commands])

    def test_non_lagg_wanha_collision_is_not_mutated(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol=None,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.FENCED)
        self.assertEqual(plan.commands, [])
        self.assertTrue(plan.warnings)

    def test_non_failover_wanha_is_not_mutated(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                lagg_protocol="lacp",
                mtu=1500,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.FENCED)
        self.assertEqual(plan.commands, [])

    def test_unset_mtu_does_not_copy_empty_lagg_default_to_carrier(self):
        settings = core.Settings(True, "ix0", "02:11:22:33:44:55", managed_mtu=None)
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True, mtu=9000),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(settings, observed)
        self.assertNotIn(
            ("/sbin/ifconfig", "ix0", "mtu", "1500"),
            [command.argv for command in plan.commands],
        )
        self.assertEqual(plan.commands[0].argv[-2:], ("laggport", "ix0"))

    def test_custom_mtu_is_applied_to_carrier_before_attachment(self):
        settings = core.Settings(True, "ix0", "02:11:22:33:44:55", managed_mtu=1400)
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.commands[0].argv, ("/sbin/ifconfig", "ix0", "mtu", "1400"))
        self.assertEqual(plan.commands[1].argv[-2:], ("laggport", "ix0"))

    def test_active_explicit_mtu_drift_is_reconciled_on_lagg(self):
        settings = core.Settings(True, "ix0", "02:11:22:33:44:55", managed_mtu=1400)
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=True,
                link_up=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",),
            ),
        )
        plan = core.plan_reconcile(settings, observed)
        argv = [command.argv for command in plan.commands]
        self.assertIn(
            ("/sbin/ifconfig", core.WANHA_DEVICE, "mtu", "1400"),
            argv,
        )
        self.assertNotIn(
            ("/sbin/ifconfig", core.WANHA_DEVICE, "-laggport", "ix0"),
            argv,
        )

    def test_down_carrier_is_brought_up_before_lagg_attachment(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=False, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        self.assertEqual(plan.commands[0].argv, ("/sbin/ifconfig", "ix0", "up"))
        self.assertEqual(plan.commands[1].argv[-2:], ("laggport", "ix0"))

    def test_promotion_attaches_before_mac_then_up(self):
        observed = core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True, mtu=1500),
            wanha=core.InterfaceSnapshot(
                core.WANHA_DEVICE,
                exists=True,
                up=False,
                mac="00:aa:bb:cc:dd:ee",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=(),
            ),
        )
        plan = core.plan_reconcile(self.settings(), observed)
        argv = [command.argv for command in plan.commands]
        self.assertEqual(argv[0][-2:], ("laggport", "ix0"))
        self.assertEqual(argv[1][-2:], ("ether", "02:11:22:33:44:55"))
        self.assertEqual(argv[2][-1], "up")


class FailbackTests(unittest.TestCase):
    def test_preferred_node_waits_while_peer_master_is_alive(self):
        decision = core.evaluate_failback(
            now=100.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=True,
            state=core.FailbackState(),
        )
        self.assertFalse(decision.allow_preempt)
        self.assertEqual(decision.state.healthy_since, 100.0)
        self.assertEqual(decision.remaining_seconds, 120.0)

    def test_hold_expires_after_continuous_health(self):
        decision = core.evaluate_failback(
            now=221.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=True,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertTrue(decision.allow_preempt)
        self.assertEqual(decision.remaining_seconds, 0.0)

    def test_native_master_transition_bypasses_hold(self):
        decision = core.evaluate_failback(
            now=110.0,
            delay_seconds=120,
            local_is_master=True,
            local_healthy=True,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertTrue(decision.allow_preempt)
        self.assertIsNone(decision.state.healthy_since)

    def test_health_failure_resets_hold(self):
        decision = core.evaluate_failback(
            now=150.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=False,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertFalse(decision.allow_preempt)
        self.assertIsNone(decision.state.healthy_since)


class ParseTests(unittest.TestCase):
    SAMPLE = """ix0: flags=1008943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
        ether 00:e0:ed:73:20:4e
        carp: MASTER vhid 100 advbase 1 advskew 0
        status: active
wanha0lagg: flags=1008943<UP,BROADCAST,RUNNING> metric 0 mtu 1500
        ether 02:11:22:33:44:55
        laggproto failover lagghash l2,l3,l4
        laggport: ix0 flags=5<MASTER,ACTIVE>
        status: active
"""

    def test_parse_carp(self):
        self.assertEqual(core.parse_carp_states(self.SAMPLE), ("MASTER",))

    def test_parse_interface(self):
        snap = core.parse_interface_snapshot("wanha0lagg", self.SAMPLE)
        self.assertTrue(snap.exists)
        self.assertTrue(snap.up)
        self.assertTrue(snap.link_up)
        self.assertEqual(snap.mac, "02:11:22:33:44:55")
        self.assertEqual(snap.lagg_protocol, "failover")
        self.assertEqual(snap.mtu, 1500)
        self.assertEqual(snap.lagg_members, ("ix0",))


if __name__ == "__main__":
    unittest.main()
