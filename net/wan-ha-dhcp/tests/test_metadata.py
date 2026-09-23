from pathlib import Path
import re
import xml.etree.ElementTree as ET
import unittest


PLUGIN = Path(__file__).resolve().parents[1]


class MetadataTests(unittest.TestCase):
    def test_mvc_xml_is_well_formed(self):
        xml_files = [
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Shared.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Local.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Menu/Menu.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/ACL/ACL.xml",
            PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/WanHaDhcp/forms/shared.xml",
            PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/WanHaDhcp/forms/local.xml",
        ]
        for path in xml_files:
            with self.subTest(path=path):
                ET.parse(path)

    def test_local_carrier_is_not_in_shared_model(self):
        shared = (
            PLUGIN
            / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Shared.xml"
        ).read_text()
        local = (
            PLUGIN
            / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Local.xml"
        ).read_text()
        self.assertNotIn("<carrier", shared)
        self.assertIn("<carrier", local)

    def test_shared_config_registers_without_local_config(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/wan_ha_dhcp.inc"
        ).read_text()
        self.assertIn(
            "'section' => 'OPNsense.WanHaDhcpShared'",
            integration,
        )
        self.assertNotIn(
            "'section' => 'OPNsense.WanHaDhcpLocal'",
            integration,
        )

    def test_virtual_device_name_avoids_core_lagg_collision(self):
        # OPNsense 26.7 legacy virtual classification splits names on digits
        # and looks for known tokens such as "lagg". The plugin name must
        # contain that token after a digit without beginning with "lagg".
        device = "wanha0lagg"
        tokens = [token for token in re.split(r"\d+", device) if token]
        self.assertIn("lagg", tokens)
        self.assertFalse(device.startswith("lagg"))

    def test_device_registration_is_fail_closed(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/wan_ha_dhcp.inc"
        ).read_text()
        self.assertIn("'pattern' => '^wanha[0-9]+lagg$'", integration)
        self.assertIn("'spoofmac' => false", integration)
        self.assertIn("'volatile' => true", integration)
        self.assertIn("'name' => 'wanha0lagg'", integration)

    def test_no_double_renamed_device_literals(self):
        for path in (PLUGIN / "src").rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            with self.subTest(path=path):
                self.assertNotIn("wanha0lagglagg", content)

    def test_registration_functions_are_unique(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/wan_ha_dhcp.inc"
        ).read_text()
        self.assertEqual(integration.count("function wan_ha_dhcp_devices("), 1)
        self.assertEqual(integration.count("function wan_ha_dhcp_prepare_device("), 1)
        self.assertEqual(integration.count("function wan_ha_dhcp_xmlrpc_sync("), 1)

    def test_uninstall_guard_uses_stable_device_name(self):
        pre = (PLUGIN / "+PRE_DEINSTALL.pre").read_text()
        post = (PLUGIN / "+POST_DEINSTALL.post").read_text()
        self.assertIn("wanha0lagg", pre)
        self.assertIn("wanha0lagg", post)
        self.assertNotIn("<if>wanha0</if>", pre)


if __name__ == "__main__":
    unittest.main()
