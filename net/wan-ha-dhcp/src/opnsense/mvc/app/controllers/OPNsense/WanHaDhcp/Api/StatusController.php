<?php

namespace OPNsense\WanHaDhcp\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class StatusController extends ApiControllerBase
{
    public function carriersAction()
    {
        $backend = new Backend();
        $devices = json_decode($backend->configdRun('interface list assign-opts'), true) ?? [];
        $result = [];

        foreach ($devices as $name => $details) {
            if ($name === 'wanha0') {
                continue;
            }

            $group = $details['optgroup'] ?? '';
            if (!in_array($group, ['hardware', 'vlan'], true)) {
                continue;
            }

            $result[$name] = [
                'name' => $name,
                'label' => $details['value'] ?? $name,
                'type' => $group,
            ];
        }

        ksort($result, SORT_NATURAL);
        return ['items' => array_values($result)];
    }

    public function generateMacAction()
    {
        $bytes = random_bytes(6);
        $bytes[0] = chr((ord($bytes[0]) | 0x02) & 0xFE);
        return ['mac' => implode(':', str_split(bin2hex($bytes), 2))];
    }

    public function environmentAction()
    {
        $backend = new Backend();

        return [
            'carp' => json_decode($backend->configdRun('interface show carp'), true) ?? [],
            'interfaces' => json_decode($backend->configdRun('interface list ifconfig'), true) ?? [],
            'pfsync' => json_decode($backend->configdRun('filter list pfsync json'), true) ?? [],
        ];
    }
}
