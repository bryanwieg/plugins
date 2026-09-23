from pathlib import Path
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
        shared = (PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Shared.xml").read_text()
        local = (PLUGIN / "src/opnsense/mvc/app/models/OPNsense/WanHaDhcp/Local.xml").read_text()
        self.assertNotIn("<carrier", shared)
        self.assertIn("<carrier", local)

    def test_shared_config_registers_without_local_config(self):
        integration = (PLUGIN / "src/etc/inc/plugins.inc.d/wan_ha_dhcp.inc").read_text()
        self.assertIn("'section' => 'OPNsense.WanHaDhcpShared'", integration)
        self.assertNotIn("'section' => 'OPNsense.WanHaDhcpLocal'", integration)


if __name__ == "__main__":
    unittest.main()
