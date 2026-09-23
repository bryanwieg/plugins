<?php

namespace OPNsense\WanHaDhcp;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class Local extends BaseModel
{
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);
        $carrier = trim((string)$this->carrier);
        $shared = new Shared();

        if (empty($carrier)) {
            if (!empty((string)$shared->enabled)) {
                $messages->appendMessage(new Message(
                    gettext('A local WAN carrier is required when WAN HA DHCP is enabled.'),
                    $this->carrier->getInternalXMLTagName()
                ));
            }
            return $messages;
        }

        if ($carrier === 'wanha0') {
            $messages->appendMessage(new Message(
                gettext('wanha0 cannot be its own local carrier.'),
                $this->carrier->getInternalXMLTagName()
            ));
            return $messages;
        }

        $devices = json_decode((new Backend())->configdRun('interface list assign-opts'), true) ?? [];
        $group = $devices[$carrier]['optgroup'] ?? null;
        if (!in_array($group, ['hardware', 'vlan'], true)) {
            $messages->appendMessage(new Message(
                gettext('Select an eligible physical Ethernet or VLAN carrier reported by OPNsense.'),
                $this->carrier->getInternalXMLTagName()
            ));
            return $messages;
        }

        $config = Config::getInstance()->object();
        $managedInterface = (string)$shared->managed_interface;

        if (!empty($config->interfaces)) {
            foreach ($config->interfaces->children() as $name => $interface) {
                if ((string)$interface->if === $carrier && $name !== $managedInterface) {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext('The selected carrier is already assigned to interface %s.'),
                            strtoupper($name)
                        ),
                        $this->carrier->getInternalXMLTagName()
                    ));
                    break;
                }
            }
        }

        /*
         * A raw Ethernet parent with configured VLAN children is not a safe
         * carrier: moving it into wanha0 would also disrupt those VLANs.
         * Select the ISP VLAN interface itself instead when appropriate.
         */
        if ($group === 'hardware' && !empty($config->vlans)) {
            foreach ($config->vlans->children() as $vlan) {
                if ((string)$vlan->if === $carrier) {
                    $messages->appendMessage(new Message(
                        gettext('The selected physical carrier is a parent of configured VLANs; select an eligible VLAN interface or a dedicated carrier instead.'),
                        $this->carrier->getInternalXMLTagName()
                    ));
                    break;
                }
            }
        }

        return $messages;
    }
}
