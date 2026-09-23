<script>
$(document).ready(function() {
    function loadCarrierOptions() {
        const dfObj = $.Deferred();

        ajaxCall("/api/wanhadhcp/status/carriers", {}, function(data, status) {
            if (status !== "success") {
                dfObj.reject();
                return;
            }

            const carrier = $("#wanhalocal\\.carrier");
            carrier.empty();
            carrier.append($("<option>", {value: "", text: "{{ lang._('Select a local carrier') }}"}));

            (data.items || []).forEach(function(item) {
                carrier.append($("<option>", {
                    value: item.name,
                    text: item.label + " [" + item.type + "]"
                }));
            });

            carrier.selectpicker("refresh");
            dfObj.resolve();
        });

        return dfObj;
    }

    ajaxCall("/api/wanhadhcp/status/environment", {}, function(data, status) {
        if (status === "success") {
            $("#globalRole").text(data.global_role || "UNKNOWN");
            $("#carpDemotion").text((data.carp && data.carp.demotion !== undefined) ? data.carp.demotion : "?");
            $("#carpAllowed").text((data.carp && data.carp.allow !== undefined) ? data.carp.allow : "?");
            $("#carpMaintenance").text((data.carp && data.carp.maintenancemode) ? "{{ lang._('Yes') }}" : "{{ lang._('No') }}");
            $("#managedDevice").text((data.managed && data.managed.device) ? data.managed.device : "?");
            $("#managedIPv4").text((data.managed && data.managed.ipv4) ? data.managed.ipv4 : "?");
            $("#managedIPv6").text((data.managed && data.managed.ipv6) ? data.managed.ipv6 : "{{ lang._('None') }}");
            $("#nativeSpoofMac").text((data.managed && data.managed.spoof_mac) ? data.managed.spoof_mac : "{{ lang._('None') }}");
            $("#pfsyncInterface").text((data.ha && data.ha.pfsync_interface) ? data.ha.pfsync_interface : "{{ lang._('Disabled') }}");
            $("#pfsyncPeer").text((data.ha && data.ha.pfsync_peer) ? data.ha.pfsync_peer : "{{ lang._('Not configured') }}");
            $("#xmlrpcTarget").text((data.ha && data.ha.xmlrpc_target) ? data.ha.xmlrpc_target : "{{ lang._('Not configured') }}");
            $("#pluginSync").text((data.ha && data.ha.plugin_sync_enabled) ? "{{ lang._('Enabled') }}" : "{{ lang._('Not selected') }}");
            if (data.managed && data.managed.spoof_mac) {
                $("#existingMac").data("mac", data.managed.spoof_mac).show();
            }

            const warnings = $("#readinessWarnings");
            warnings.empty();
            (data.warnings || []).forEach(function(message) {
                warnings.append($("<li>").text(message));
            });
            if ((data.warnings || []).length === 0) {
                warnings.append($("<li>").text("{{ lang._('No migration-readiness warnings detected.') }}"));
            }
        }
    });

    loadCarrierOptions().always(function() {
        mapDataToFormUI({
            "frm_SharedSettings": "/api/wanhadhcp/shared/get",
            "frm_LocalSettings": "/api/wanhadhcp/local/get"
        }).done(function() {
            $(".selectpicker").selectpicker("refresh");
        });
    });

    $("#existingMac").click(function() {
        const mac = $(this).data("mac");
        if (mac) {
            $("#wanhashared\\.shared_mac").val(mac);
        }
    });

    $("#generateMac").click(function() {
        ajaxCall("/api/wanhadhcp/status/generate_mac", {}, function(data, status) {
            if (status === "success" && data.mac) {
                $("#wanhashared\\.shared_mac").val(data.mac);
            }
        });
    });

    $("#saveSettings").click(function() {
        const local = $.Deferred();

        // Persist node-local carrier selection first.  A shared enable must not
        // succeed locally after the carrier save failed.
        saveFormToEndpoint(
            "/api/wanhadhcp/local/set",
            "frm_LocalSettings",
            local.resolve,
            true,
            local.reject
        );

        local.done(function() {
            const shared = $.Deferred();
            saveFormToEndpoint(
                "/api/wanhadhcp/shared/set",
                "frm_SharedSettings",
                shared.resolve,
                true,
                shared.reject
            );
            shared.done(function() {
                $("#saveResult").text("{{ lang._('Configuration saved. Runtime carrier mutation is not enabled in this experimental scaffold.') }}");
            });
        });
    });
});
</script>

<section class="page-content-main">
    <div class="alert alert-warning">
        {{ lang._('Experimental implementation scaffold. The dataplane fencing mechanism remains prototype-gated and this page does not yet activate automatic WAN carrier movement.') }}
    </div>

    <div class="content-box">
        <div class="col-md-12">
            <h4>{{ lang._('Detected HA State') }}</h4>
            <table class="table table-condensed">
                <tr><td>{{ lang._('Global CARP role') }}</td><td id="globalRole">...</td></tr>
                <tr><td>{{ lang._('CARP demotion') }}</td><td id="carpDemotion">...</td></tr>
                <tr><td>{{ lang._('CARP allowed') }}</td><td id="carpAllowed">...</td></tr>
                <tr><td>{{ lang._('CARP maintenance mode') }}</td><td id="carpMaintenance">...</td></tr>
                <tr><td>{{ lang._('Managed WAN device') }}</td><td id="managedDevice">...</td></tr>
                <tr><td>{{ lang._('Managed WAN IPv4 type') }}</td><td id="managedIPv4">...</td></tr>
                <tr><td>{{ lang._('Managed WAN IPv6 type') }}</td><td id="managedIPv6">...</td></tr>
                <tr><td>{{ lang._('Native WAN spoof MAC') }}</td><td id="nativeSpoofMac">...</td></tr>
                <tr><td>{{ lang._('pfsync interface') }}</td><td id="pfsyncInterface">...</td></tr>
                <tr><td>{{ lang._('pfsync peer') }}</td><td id="pfsyncPeer">...</td></tr>
                <tr><td>{{ lang._('XMLRPC target') }}</td><td id="xmlrpcTarget">...</td></tr>
                <tr><td>{{ lang._('WAN HA DHCP config sync') }}</td><td id="pluginSync">...</td></tr>
            </table>
            <h5>{{ lang._('Readiness warnings') }}</h5>
            <ul id="readinessWarnings"><li>...</li></ul>
        </div>
    </div>

    <br/>

    <div class="content-box">
        {{ partial("layout_partials/base_form", ['fields': shared, 'id': 'frm_SharedSettings']) }}
        <div class="col-md-12">
            <button class="btn btn-default" id="generateMac" type="button">
                {{ lang._('Generate Private MAC') }}
            </button>
            <button class="btn btn-default" id="existingMac" type="button" style="display:none">
                {{ lang._('Use Existing WAN Spoof MAC') }}
            </button>
            <br/><br/>
        </div>
    </div>

    <br/>

    <div class="content-box">
        {{ partial("layout_partials/base_form", ['fields': local, 'id': 'frm_LocalSettings']) }}
    </div>

    <br/>

    <div class="content-box">
        <div class="col-md-12">
            <br/>
            <button class="btn btn-primary" id="saveSettings" type="button">
                {{ lang._('Save') }}
            </button>
            <span id="saveResult"></span>
            <br/><br/>
        </div>
    </div>
</section>
