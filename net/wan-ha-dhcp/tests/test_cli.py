import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opnsense"
    / "scripts"
    / "wan_ha_dhcp"
)
sys.path.insert(0, str(SCRIPT_DIR))

SPEC = importlib.util.spec_from_file_location(
    "wan_ha_dhcp_cli",
    SCRIPT_DIR / "wan_ha_dhcp.py",
)
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


class ConfigLoadingTests(unittest.TestCase):
    def test_load_configured_settings_keeps_shared_local_and_native_wan_roles(self):
        payloads = {
            "OPNsense.WanHaDhcpShared": {
                "enabled": "1",
                "managed_interface": "wan",
                "shared_mac": "02:11:22:33:44:55",
                "failback_delay": "120",
            },
            "OPNsense.WanHaDhcpLocal": {
                "carrier": "ix0",
            },
            "interfaces.wan": {
                "enable": "1",
                "if": cli.WANHA_DEVICE,
                "ipaddr": "dhcp",
                "ipaddrv6": "none",
                "mtu": "1400",
            },
        }

        with patch.object(cli, "pluginctl_get", side_effect=lambda path: payloads.get(path, {})):
            settings, managed_name, managed = cli.load_configured_settings()

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.carrier, "ix0")
        self.assertEqual(settings.shared_mac, "02:11:22:33:44:55")
        self.assertTrue(settings.managed_by_wanha)
        self.assertEqual(settings.managed_mtu, 1400)
        self.assertEqual(managed_name, "wan")
        self.assertEqual(managed["if"], cli.WANHA_DEVICE)

    def test_missing_or_invalid_mtu_stays_unset(self):
        payloads = {
            "OPNsense.WanHaDhcpShared": {
                "enabled": "0",
                "managed_interface": "wan",
                "shared_mac": "",
            },
            "OPNsense.WanHaDhcpLocal": {
                "carrier": "hn1",
            },
            "interfaces.wan": {
                "if": "hn1",
                "ipaddr": "dhcp",
                "mtu": "not-an-integer",
            },
        }

        with patch.object(cli, "pluginctl_get", side_effect=lambda path: payloads.get(path, {})):
            settings, _, _ = cli.load_configured_settings()

        self.assertFalse(settings.enabled)
        self.assertFalse(settings.managed_by_wanha)
        self.assertIsNone(settings.managed_mtu)


if __name__ == "__main__":
    unittest.main()
