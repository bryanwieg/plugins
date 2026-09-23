<?php

namespace OPNsense\WanHaDhcp\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Core\Hasync;
use OPNsense\WanHaDhcp\Local;
use OPNsense\WanHaDhcp\Shared;

class StatusController extends ApiControllerBase
{
    public function carriersAction()
    {
        $backend = new Backend();
        $devices = json_decode($backend->configdRun('interface list assign-opts'), true) ?? [];
        $ifconfig = json_decode($backend->configdRun('interface list ifconfig'), true) ?? [];
        $shared = new Shared();
        $managedInterface = (string)$shared->managed_interface ?: 'wan';
        $blocked = Local::blockedCarrierDevices($managedInterface);
        $result = [];

        foreach ($devices as $name => $details) {
            if ($name === 'wanha0' || isset($blocked[$name])) {
                continue;
            }

            $runtimeReason = Local::carrierRuntimeEligibility($name, $devices, $ifconfig);
            if ($runtimeReason !== null) {
                $blocked[$name] = $runtimeReason;
                continue;
            }

            $group = $details['optgroup'] ?? '';
            $result[$name] = [
                'name' => $name,
                'label' => $details['value'] ?? $name,
                'type' => $group,
            ];
        }

        ksort($result, SORT_NATURAL);
        return ['items' => array_values($result), 'blocked' => $blocked];
    }

    public function generateMacAction()
    {
        $bytes = random_bytes(6);
        $bytes[0] = chr((ord($bytes[0]) | 0x02) & 0xFE);
        return ['mac' => implode(':', str_split(bin2hex($bytes), 2))];
    }

    private function reduceGlobalCarpRole(array $interfaces)
    {
        $states = [];

        foreach ($interfaces as $details) {
            foreach (($details['carp'] ?? []) as $carp) {
                if (!empty($carp['status'])) {
                    $states[] = strtoupper($carp['status']);
                }
            }
        }

        if (empty($states)) {
            return 'INDETERMINATE';
        }
        if (in_array('BACKUP', $states, true)) {
            return 'BACKUP';
        }
        foreach ($states as $state) {
            if ($state !== 'MASTER') {
                return 'INDETERMINATE';
            }
        }
        return 'MASTER';
    }

    public function environmentAction()
    {
        $backend = new Backend();
        $interfaces = json_decode($backend->configdRun('interface list ifconfig'), true) ?? [];
        $carp = json_decode($backend->configdRun('interface show carp'), true) ?? [];
        $globalRole = !empty($carp['allow']) ? $this->reduceGlobalCarpRole($interfaces) : 'INDETERMINATE';

        $shared = new Shared();
        $local = new Local();
        $hasync = new Hasync();
        $config = Config::getInstance()->object();

        $managedName = (string)$shared->managed_interface ?: 'wan';
        $managed = !empty($config->interfaces->$managedName) ? $config->interfaces->$managedName : null;
        $syncItems = array_filter(explode(',', (string)$hasync->syncitems));

        return [
            'global_role' => $globalRole,
            'carp' => $carp,
            'interfaces' => $interfaces,
            'pfsync_runtime' => json_decode($backend->configdRun('filter list pfsync json'), true) ?? [],
            'ha' => [
                'disable_preempt' => (string)$hasync->disablepreempt,
                'pfsync_interface' => (string)$hasync->pfsyncinterface,
                'pfsync_peer' => (string)$hasync->pfsyncpeerip,
                'pfsync_version' => (string)$hasync->pfsyncversion,
                'pfsync_defer' => (string)$hasync->pfsyncdefer,
                'xmlrpc_target' => (string)$hasync->synchronizetoip,
                'plugin_sync_enabled' => in_array('wan-ha-dhcp', $syncItems, true),
            ],
            'managed' => [
                'name' => $managedName,
                'device' => $managed !== null ? (string)$managed->if : '',
                'enabled' => $managed !== null ? !empty((string)$managed->enable) : false,
                'ipv4' => $managed !== null ? (string)$managed->ipaddr : '',
                'ipv6' => $managed !== null ? (string)$managed->ipaddrv6 : '',
                'spoof_mac' => $managed !== null ? (string)$managed->spoofmac : '',
            ],
            'plugin' => [
                'enabled' => !empty((string)$shared->enabled),
                'shared_mac' => (string)$shared->shared_mac,
                'failback_delay' => (string)$shared->failback_delay,
                'local_carrier' => (string)$local->carrier,
            ],
        ];
    }
}
