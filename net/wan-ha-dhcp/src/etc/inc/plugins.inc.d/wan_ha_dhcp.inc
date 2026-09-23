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
            ],
        ],
    ]];
}

function wan_ha_dhcp_prepare_device($device)
{
    if ($device !== 'wanha0lagg') {
        return null;
    }

    /*
     * Prototype Gate A owns the final abstraction choice and command ordering.
     * Creating a detached LAGG is intentionally the only dataplane mutation in
     * this initial scaffold. No carrier is attached here.
     */
    if (!does_interface_exist($device)) {
        if (legacy_interface_create('lagg', $device) === null) {
            return null;
        }
        mwexecf('/sbin/ifconfig %s laggproto failover', [$device]);
    }

    $details = legacy_interfaces_details($device);
    if (empty($details[$device]['laggproto'])) {
        log_msg(sprintf(
            'WAN HA DHCP refused device %s because it exists but is not a LAGG interface',
            $device
        ), LOG_ERR);
        return null;
    }
    if ($details[$device]['laggproto'] !== 'failover') {
        log_msg(sprintf(
            'WAN HA DHCP refused device %s because LAGG protocol %s is not failover',
            $device,
            $details[$device]['laggproto']
        ), LOG_ERR);
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
