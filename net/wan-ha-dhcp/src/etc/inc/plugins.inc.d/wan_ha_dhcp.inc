<?php

/*
 * Copyright (C) 2026 Resolver Plugins contributors
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 */

function wan_ha_dhcp_devices()
{
    $exclude = [];
    $config = \OPNsense\Core\Config::getInstance()->object();
    $carrier = trim((string)($config->OPNsense->WanHaDhcpLocal->carrier ?? ''));
    $wanha_assigned = false;

    if (!empty($config->interfaces)) {
        foreach ($config->interfaces->children() as $interface) {
            if ((string)$interface->if === 'wanha0lagg') {
                $wanha_assigned = true;
                break;
            }
        }
    }

    if ($wanha_assigned && !empty($carrier)) {
        $exclude[] = $carrier;
    }

    return [[
        'function' => 'wan_ha_dhcp_prepare_device',
        'configurable' => true,
        'pattern' => '^wanha[0-9]+lagg$',
        'reload' => true,
        'spoofmac' => false,
        'volatile' => true,
        'type' => 'wanha',
        'names' => [
            'wanha0lagg' => [
                'descr' => gettext('wanha0lagg (WAN HA DHCP)'),
                'ifdescr' => gettext('WAN HA DHCP'),
                'name' => 'wanha0lagg',
                'exclude' => $exclude,
            ],
        ],
    ]];
}

function wan_ha_dhcp_prepare_device($device)
{
    if ($device !== 'wanha0lagg') {
        return null;
    }

    $runtime_dir = '/var/run/wan-ha-dhcp';
    $ownership_marker = $runtime_dir . '/device.wanha0lagg';
    $created = false;

    /*
     * Prototype Gate A owns the final abstraction choice and command ordering.
     * Creating a detached LAGG is intentionally the only dataplane mutation in
     * this initial scaffold. No carrier is attached here.
     */
    if (!does_interface_exist($device)) {
        if (legacy_interface_create('lagg', $device) === null) {
            return null;
        }
        $created = true;
        mwexecf('/sbin/ifconfig %s laggproto failover', [$device]);

        if (!is_dir($runtime_dir) && !mkdir($runtime_dir, 0755, true)) {
            log_msg('WAN HA DHCP could not create its runtime directory', LOG_ERR);
            legacy_interface_destroy($device);
            return null;
        }
        if (file_put_contents($ownership_marker, $device . PHP_EOL, LOCK_EX) === false) {
            log_msg('WAN HA DHCP could not record ownership of its virtual device', LOG_ERR);
            legacy_interface_destroy($device);
            return null;
        }
    } elseif (!is_file($ownership_marker)) {
        /*
         * A same-name device not created by this plugin in the current boot is
         * an ownership collision. Never assume it is safe merely because it is
         * also a failover LAGG.
         */
        log_msg(sprintf(
            'WAN HA DHCP refused existing device %s because its ownership marker is missing',
            $device
        ), LOG_ERR);
        return null;
    }

    $details = legacy_interfaces_details($device);
    if (empty($details[$device]['laggproto'])) {
        log_msg(sprintf(
            'WAN HA DHCP refused device %s because it exists but is not a LAGG interface',
            $device
        ), LOG_ERR);
        if ($created) {
            @unlink($ownership_marker);
            legacy_interface_destroy($device);
        }
        return null;
    }
    if ($details[$device]['laggproto'] !== 'failover') {
        log_msg(sprintf(
            'WAN HA DHCP refused device %s because LAGG protocol %s is not failover',
            $device,
            $details[$device]['laggproto']
        ), LOG_ERR);
        if ($created) {
            @unlink($ownership_marker);
            legacy_interface_destroy($device);
        }
        return null;
    }

    return $device;
}

function wan_ha_dhcp_xmlrpc_sync()
{
    return [[
        'description' => gettext('WAN HA DHCP'),
        'section' => 'OPNsense.WanHaDhcpShared',
        'id' => 'wan-ha-dhcp',
    ]];
}
