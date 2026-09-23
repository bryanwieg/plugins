<?php

namespace OPNsense\WanHaDhcp;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class Local extends BaseModel
{
    public static function blockedCarrierDevices($managedInterface = 'wan')
    {
        $config = Config::getInstance()->object();
        $blocked = [];

        if (!empty($config->interfaces)) {
            foreach ($config->interfaces->children() as $name => $interface) {
                $device = trim((string)$interface->if);
                if (!empty($device) && $name !== $managedInterface) {
                    $blocked[$device] = sprintf(
                        gettext('already assigned to interface %s'),
                        strtoupper($name)
                    );
                }
            }
        }

        if (!empty($config->vlans)) {
            foreach ($config->vlans->children() as $vlan) {
                $parent = trim((string)$vlan->if);
                if (!empty($parent)) {
                    $blocked[$parent] = gettext('is the parent of a configured VLAN');
                }
            }
        }

        if (!empty($config->laggs)) {
            foreach ($config->laggs->children() as $lagg) {
                foreach (array_filter(explode(',', (string)$lagg->members)) as $member) {
                    $blocked[$member] = sprintf(
                        gettext('is a member of configured LAGG %s'),
                        (string)$lagg->laggif
                    );
                }
            }
        }

        if (!empty($config->bridges)) {
            foreach ($config->bridges->children() as $bridge) {
                foreach (array_filter(explode(',', (string)$bridge->members)) as $member) {
                    if (!empty($config->interfaces->$member->if)) {
                        $device = (string)$config->interfaces->$member->if;
                        $blocked[$device] = sprintf(
                            gettext('is a member of configured bridge %s'),
                            (string)$bridge->bridgeif
                        );
                    }
                }
            }
        }

        if (!empty($config->ppps)) {
            foreach ($config->ppps->children() as $ppp) {
                foreach (array_filter(explode(',', (string)$ppp->ports)) as $port) {
                    $blocked[$port] = sprintf(
                        gettext('is used by configured %s interface'),
                        strtoupper((string)$ppp->type)
                    );
                }
            }
        }

        return $blocked;
    }

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

        $managedInterface = (string)$shared->managed_interface ?: 'wan';
        $blocked = self::blockedCarrierDevices($managedInterface);
        if (!empty($blocked[$carrier])) {
            $messages->appendMessage(new Message(
                sprintf(
                    gettext('The selected carrier cannot be used because it %s.'),
                    $blocked[$carrier]
                ),
                $this->carrier->getInternalXMLTagName()
            ));
        }

        return $messages;
    }
}
