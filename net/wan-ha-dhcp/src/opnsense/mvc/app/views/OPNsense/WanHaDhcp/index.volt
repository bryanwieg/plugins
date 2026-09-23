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

    loadCarrierOptions().always(function() {
        mapDataToFormUI({
            "frm_SharedSettings": "/api/wanhadhcp/shared/get",
            "frm_LocalSettings": "/api/wanhadhcp/local/get"
        }).done(function() {
            $(".selectpicker").selectpicker("refresh");
        });
    });

    $("#generateMac").click(function() {
        ajaxCall("/api/wanhadhcp/status/generateMac", {}, function(data, status) {
            if (status === "success" && data.mac) {
                $("#wanhashared\\.shared_mac").val(data.mac);
            }
        });
    });

    $("#saveSettings").click(function() {
        const shared = $.Deferred();
        const local = $.Deferred();

        saveFormToEndpoint(
            "/api/wanhadhcp/shared/set",
            "frm_SharedSettings",
            shared.resolve,
            true,
            shared.reject
        );
        saveFormToEndpoint(
            "/api/wanhadhcp/local/set",
            "frm_LocalSettings",
            local.resolve,
            true,
            local.reject
        );

        $.when(shared, local).done(function() {
            $("#saveResult").text("{{ lang._('Configuration saved. Runtime carrier mutation is not enabled in this experimental scaffold.') }}");
        });
    });
});
</script>

<section class="page-content-main">
    <div class="alert alert-warning">
        {{ lang._('Experimental implementation scaffold. The dataplane fencing mechanism remains prototype-gated and this page does not yet activate automatic WAN carrier movement.') }}
    </div>

    <div class="content-box">
        {{ partial("layout_partials/base_form", ['fields': shared, 'id': 'frm_SharedSettings']) }}
        <div class="col-md-12">
            <button class="btn btn-default" id="generateMac" type="button">
                {{ lang._('Generate Private MAC') }}
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
