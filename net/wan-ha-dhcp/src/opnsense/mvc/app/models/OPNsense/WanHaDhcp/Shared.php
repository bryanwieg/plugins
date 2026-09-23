<?php

namespace OPNsense\WanHaDhcp;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class Shared extends BaseModel
{
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        if (empty((string)$this->enabled)) {
            return $messages;
        }

        $mac = strtolower(trim((string)$this->shared_mac));
        if (empty($mac)) {
            $messages->appendMessage(new Message(
                gettext('A shared WAN MAC is required when WAN HA DHCP is enabled.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        } elseif (filter_var($mac, FILTER_VALIDATE_MAC)) {
            $octets = array_map('hexdec', explode(':', $mac));
            if ($mac === '00:00:00:00:00:00' || $mac === 'ff:ff:ff:ff:ff:ff' || ($octets[0] & 0x01)) {
                $messages->appendMessage(new Message(
                    gettext('The shared WAN MAC must be a usable unicast address.'),
                    $this->shared_mac->getInternalXMLTagName()
                ));
            }
        }

        $local = new Local();
        if (empty(trim((string)$local->carrier))) {
            $messages->appendMessage(new Message(
                gettext('Configure a valid local WAN carrier on this node before enabling WAN HA DHCP.'),
                $this->enabled->getInternalXMLTagName()
            ));
        } elseif ($local->performValidation(true)->count() !== 0) {
            $messages->appendMessage(new Message(
                gettext('The node-local WAN carrier configuration is not valid.'),
                $this->enabled->getInternalXMLTagName()
            ));
        }

        $ifconfig = json_decode((new Backend())->configdRun('interface list ifconfig'), true) ?? [];
        if (empty($ifconfig['wanha0']['laggproto'])) {
            $messages->appendMessage(new Message(
                gettext('wanha0 must exist as a LAGG interface before WAN HA DHCP can be enabled.'),
                $this->enabled->getInternalXMLTagName()
            ));
        }

        $interface = (string)$this->managed_interface;
        $config = Config::getInstance()->object();
        if (empty($config->interfaces->$interface)) {
            $messages->appendMessage(new Message(
                gettext('The managed interface does not exist.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
            return $messages;
        }

        $managed = $config->interfaces->$interface;
        if ((string)$managed->ipaddr !== 'dhcp') {
            $messages->appendMessage(new Message(
                gettext('Version 1 requires the managed interface to use IPv4 DHCP.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        $ipv6 = strtolower(trim((string)$managed->ipaddrv6));
        if (!empty($ipv6) && $ipv6 !== 'none') {
            $messages->appendMessage(new Message(
                gettext('IPv6 on the managed WAN is not supported by version 1.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        if ((string)$managed->if !== 'wanha0') {
            $messages->appendMessage(new Message(
                gettext('The managed interface must be assigned to wanha0 before WAN HA DHCP can be enabled.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        if (!empty((string)$managed->spoofmac)) {
            $messages->appendMessage(new Message(
                gettext('Remove the native interface MAC spoof setting and use the plugin Shared WAN MAC instead.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        }

        return $messages;
    }
}
