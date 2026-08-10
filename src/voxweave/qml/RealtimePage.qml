pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var bridge
    required property var theme
    property var readyModels: []
    property var devicePayload: ({"hostapis": [], "devices": []})
    property var session: ({"state": "idle", "stage": "idle", "metrics": {}})
    readonly property var worker: session.worker || ({"state": "not_started", "model_ready": false})
    readonly property var metrics: session.metrics || ({})
    readonly property bool active: ["starting", "running", "stopping"].indexOf(session.state) >= 0
    readonly property var inputDevices: (devicePayload.devices || []).filter(function(device) {
        return device.input_channels > 0 && device.hostapi_id === hostApi.currentValue
    })
    readonly property var outputDevices: (devicePayload.devices || []).filter(function(device) {
        return device.output_channels > 0 && device.hostapi_id === hostApi.currentValue
    })

    objectName: "realtimePage"

    function stateText(state) {
        var key = "realtime.state." + state
        return bridge.text(key)
    }

    function stateTone(state) {
        if (state === "running") return session.stage === "overloaded" ? "warning" : "success"
        if (state === "failed") return "danger"
        if (state === "interrupted") return "warning"
        if (state === "starting" || state === "stopping") return "info"
        return "neutral"
    }

    function workerStateText(state) {
        return bridge.text("realtime.worker." + state)
    }

    function workerTone(state) {
        if (state === "ready") return "success"
        if (state === "warming" || state === "starting") return "info"
        if (state === "failed") return "danger"
        return "neutral"
    }

    function meterValue(peak) {
        var value = Math.max(Number(peak || 0), 0.000001)
        var decibels = 20 * Math.log(value) / Math.LN10
        return Math.max(0, Math.min(1, (decibels + 60) / 60))
    }

    function selectDefaultDevices() {
        var hosts = devicePayload.hostapis || []
        var devices = devicePayload.devices || []
        var defaultInput = Number(devicePayload.default_input_device)
        var hostIndex = 0
        for (var i = 0; i < devices.length; ++i) {
            if (devices[i].id === defaultInput) {
                for (var j = 0; j < hosts.length; ++j) {
                    if (hosts[j].id === devices[i].hostapi_id) hostIndex = j
                }
            }
        }
        hostApi.currentIndex = hostIndex
        Qt.callLater(function() {
            inputDevice.currentIndex = 0
            outputDevice.currentIndex = 0
            for (var inputIndex = 0; inputIndex < root.inputDevices.length; ++inputIndex) {
                if (root.inputDevices[inputIndex].id === defaultInput)
                    inputDevice.currentIndex = inputIndex
            }
            var defaultOutput = Number(root.devicePayload.default_output_device)
            for (var outputIndex = 0; outputIndex < root.outputDevices.length; ++outputIndex) {
                if (root.outputDevices[outputIndex].id === defaultOutput)
                    outputDevice.currentIndex = outputIndex
            }
        })
    }

    onDevicePayloadChanged: Qt.callLater(selectDefaultDevices)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.realtime")
        }

        Label {
            Layout.fillWidth: true
            text: root.bridge.text("realtime.subtitle")
            color: root.theme.textMuted
            font.family: root.theme.uiFont
            font.pixelSize: 12
            wrapMode: Text.Wrap
        }

        AppScrollView {
            id: realtimeScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: realtimeScroll.availableWidth
                spacing: 10

                AppPanel {
                    Layout.fillWidth: true

                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.realtime_status")
                        badgeText: root.stateText(root.session.state || "idle")
                        badgeTone: root.stateTone(root.session.state || "idle")
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: root.bridge.text("realtime.worker.label")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        StatusPill {
                            objectName: "realtimeWarmupStatus"
                            text: root.workerStateText(root.worker.state || "not_started")
                            tone: root.workerTone(root.worker.state || "not_started")
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.session.state === "running"
                        Label {
                            text: root.bridge.text("realtime.voice.label")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        StatusPill {
                            objectName: "realtimeVadStatus"
                            text: root.metrics.speech_detected
                                ? root.bridge.text("realtime.voice.detected")
                                : root.bridge.text("realtime.voice.waiting")
                            tone: root.metrics.speech_detected ? "info" : "neutral"
                        }
                        StatusPill {
                            objectName: "realtimeVoiceStatus"
                            text: root.metrics.rvc_inference_active
                                ? root.bridge.text("realtime.voice.converting")
                                : root.bridge.text("realtime.voice.listening")
                            tone: root.metrics.rvc_inference_active ? "success" : "neutral"
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: root.session.state === "failed" && Boolean(root.session.error)
                        text: root.session.error || ""
                        color: root.theme.danger
                        font.family: root.theme.uiFont
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 3
                        columnSpacing: 8
                        rowSpacing: 8

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            Column {
                                anchors.centerIn: parent
                                spacing: 3
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: root.bridge.text("realtime.metric.latency"); color: root.theme.textDim; font.pixelSize: 10 }
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: String((root.session.metrics || {}).estimated_latency_ms || "—") + ((root.session.metrics || {}).estimated_latency_ms ? " ms" : ""); color: root.theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            Column {
                                anchors.centerIn: parent
                                spacing: 3
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: root.bridge.text("realtime.metric.infer"); color: root.theme.textDim; font.pixelSize: 10 }
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: String((root.session.metrics || {}).infer_ms || "—") + ((root.session.metrics || {}).infer_ms ? " ms" : ""); color: root.theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            Column {
                                anchors.centerIn: parent
                                spacing: 3
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: root.bridge.text("realtime.metric.xruns"); color: root.theme.textDim; font.pixelSize: 10 }
                                Label { anchors.horizontalCenter: parent.horizontalCenter; text: String((root.session.metrics || {}).xruns || 0); color: (root.session.metrics || {}).xruns > 0 ? root.theme.warning : root.theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        visible: root.session.state === "running"
                        columns: 2
                        columnSpacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: root.bridge.text("realtime.level.input"); color: root.theme.textDim; font.pixelSize: 10 }
                                Item { Layout.fillWidth: true }
                                Label { text: String(Number(root.metrics.input_db || -120).toFixed(1)) + " dB"; color: root.theme.textDim; font.pixelSize: 10 }
                            }
                            AppProgressBar {
                                objectName: "realtimeInputLevel"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: root.meterValue(root.metrics.peak_in)
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: root.bridge.text("realtime.level.output"); color: root.theme.textDim; font.pixelSize: 10 }
                                Item { Layout.fillWidth: true }
                                Label { text: String(Math.round(Number(root.metrics.vad_probability || 0) * 100)) + "% VAD"; color: root.theme.textDim; font.pixelSize: 10 }
                            }
                            AppProgressBar {
                                objectName: "realtimeOutputLevel"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: root.meterValue(root.metrics.peak_out)
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: root.session.stage === "overloaded"
                        text: root.bridge.text("realtime.overloaded")
                        color: root.theme.warning
                        font.family: root.theme.uiFont
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text("section.realtime_route") }

                    FieldLabel { text: root.bridge.text("field.audio_host") }
                    RowLayout {
                        Layout.fillWidth: true
                        AppComboBox {
                            id: hostApi
                            Layout.fillWidth: true
                            model: root.devicePayload.hostapis || []
                            textRole: "name"
                            valueRole: "id"
                            emptyText: root.bridge.text("realtime.no_devices")
                            enabled: !root.active && count > 0
                            onCurrentValueChanged: {
                                inputDevice.currentIndex = 0
                                outputDevice.currentIndex = 0
                            }
                        }
                        AppButton {
                            text: root.bridge.text("action.refresh")
                            enabled: !root.active
                            onClicked: root.bridge.realtime.refreshDevices()
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.input_device") }
                            AppComboBox {
                                id: inputDevice
                                Layout.fillWidth: true
                                model: root.inputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text("realtime.no_input")
                                enabled: !root.active && count > 0
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.output_device") }
                            AppComboBox {
                                id: outputDevice
                                Layout.fillWidth: true
                                model: root.outputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text("realtime.no_output")
                                enabled: !root.active && count > 0
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("realtime.headphones_hint")
                        color: root.theme.warning
                        font.family: root.theme.uiFont
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text("section.realtime_voice") }

                    FieldLabel { text: root.bridge.text("field.model") }
                    AppComboBox {
                        id: realtimeModel
                        Layout.fillWidth: true
                        model: root.readyModels
                        textRole: "localized_name"
                        valueRole: "id"
                        emptyText: root.bridge.text("empty.models.short")
                        enabled: !root.active && count > 0
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.latency_mode") }
                            AppComboBox {
                                id: latencyMode
                                Layout.fillWidth: true
                                model: [
                                    {"label": root.bridge.text("realtime.latency.low"), "value": 0.25},
                                    {"label": root.bridge.text("realtime.latency.balanced"), "value": 0.5},
                                    {"label": root.bridge.text("realtime.latency.stable"), "value": 1.0}
                                ]
                                textRole: "label"
                                valueRole: "value"
                                currentIndex: 1
                                enabled: !root.active
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.pitch") }
                            AppSlider {
                                id: pitchSlider
                                objectName: "realtimePitchSlider"
                                Layout.fillWidth: true
                                from: -36
                                to: 36
                                value: 0
                                stepSize: 1
                                showPositiveSign: true
                                accessibleName: root.bridge.text("field.pitch")
                                enabled: !root.active
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.f0") }
                            AppComboBox {
                                id: f0Method
                                Layout.fillWidth: true
                                model: [{"label": "RMVPE", "value": "rmvpe"}, {"label": "FCPE", "value": "fcpe"}, {"label": "PM", "value": "pm"}]
                                textRole: "label"
                                valueRole: "value"
                                enabled: !root.active
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.vad_threshold") }
                            AppSlider {
                                id: vadThresholdSlider
                                objectName: "realtimeVadThresholdSlider"
                                Layout.fillWidth: true
                                from: 10
                                to: 90
                                value: 35
                                stepSize: 1
                                suffix: "%"
                                accessibleName: root.bridge.text("field.vad_threshold")
                                enabled: !root.active
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.index_rate_percent") }
                            AppSlider {
                                id: indexRateSlider
                                objectName: "realtimeIndexRateSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: 72
                                stepSize: 1
                                suffix: "%"
                                accessibleName: root.bridge.text("field.index_rate_percent")
                                enabled: !root.active
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.rms_mix_percent") }
                            AppSlider {
                                id: rmsMixSlider
                                objectName: "realtimeRmsMixSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: 25
                                stepSize: 1
                                suffix: "%"
                                accessibleName: root.bridge.text("field.rms_mix_percent")
                                enabled: !root.active
                            }
                        }
                    }

                    AppCheckBox {
                        id: testMode
                        objectName: "realtimeTestMode"
                        Layout.fillWidth: true
                        text: root.bridge.text("realtime.test_mode")
                        checked: false
                        enabled: !root.active
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("realtime.latency_hint")
                            color: root.theme.textDim
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            objectName: "realtimeStartButton"
                            text: root.bridge.text("action.start_realtime")
                            kind: "primary"
                            enabled: !root.active
                                && realtimeModel.count > 0
                                && inputDevice.count > 0
                                && outputDevice.count > 0
                                && realtimeModel.currentIndex >= 0
                                && inputDevice.currentIndex >= 0
                                && outputDevice.currentIndex >= 0
                            onClicked: root.bridge.realtime.startSession(
                                realtimeModel.currentValue,
                                Number(inputDevice.currentValue),
                                Number(outputDevice.currentValue),
                                pitchSlider.value,
                                f0Method.currentValue,
                                indexRateSlider.value / 100.0,
                                rmsMixSlider.value / 100.0,
                                vadThresholdSlider.value / 100.0,
                                Number(latencyMode.currentValue),
                                testMode.checked
                            )
                        }
                        AppButton {
                            objectName: "realtimeStopButton"
                            text: root.bridge.text("action.stop_realtime")
                            kind: "danger"
                            enabled: root.active && root.session.state !== "stopping"
                            onClicked: root.bridge.realtime.stopSession()
                        }
                    }
                }

                Item { Layout.preferredHeight: 2 }
            }
        }
    }
}
