pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    required property var bridge
    required property var theme
    property var readyModels: []
    property var session: ({"state": "idle", "stage": "idle", "metrics": {}})
    property bool pageActive: false
    property bool initialized: false
    signal navigateRequested(int index)
    readonly property var worker: session.worker || ({"state": "not_started", "model_ready": false})
    readonly property var metrics: session.metrics || ({})
    readonly property bool active: ["starting", "running", "stopping"].indexOf(session.state) >= 0
    readonly property bool canStart: !root.active
        && root.bridge.maintenance.runtimeReady
        && realtimeModel.count > 0
        && realtimeModel.currentIndex >= 0
        && Boolean(root.bridge.realtime.audioRoute.ready)

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

    function comboValueIndex(combo, value) {
        for (var i = 0; i < combo.count; ++i) {
            if (String(combo.valueAt(i)) === String(value)) return i
        }
        return -1
    }

    function disabledReason() {
        if (root.active) return ""
        if (!root.bridge.maintenance.runtimeReady)
            return root.bridge.text("realtime.disabled.runtime")
        if (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0)
            return root.bridge.text("realtime.disabled.model")
        if (!Boolean(root.bridge.realtime.audioRoute.ready))
            return root.bridge.text("realtime.disabled.audio")
        return ""
    }

    function currentPreferences() {
        var saved = root.bridge.realtime.preferences || ({})
        var route = root.bridge.realtime.audioRoute || ({})
        return {
            "model": realtimeModel.currentIndex >= 0
                ? String(realtimeModel.currentValue) : String(saved.model || ""),
            "hostapi": String(route.hostapi || saved.hostapi || ""),
            "input_device": String(route.input_device_name || saved.input_device || ""),
            "output_device": String(route.output_device_name || saved.output_device || ""),
            "pitch": Math.round(Number(pitchSlider.value)),
            "f0": String(f0Method.currentValue),
            "index_rate": Number(indexRateSlider.value) / 100.0,
            "rms_mix_rate": Number(rmsMixSlider.value) / 100.0,
            "vad_threshold": Number(vadThresholdSlider.value) / 100.0,
            "input_gate_db": Number(inputGateSlider.value),
            "block_seconds": Number(latencyMode.currentValue),
            "test_mode": Boolean(testMode.checked)
        }
    }

    function persistCurrentPreferences(prewarm) {
        root.bridge.realtime.savePreferences(root.currentPreferences())
        if (Boolean(prewarm)) prewarmTimer.restart()
    }

    function saveCurrentPreferences() {
        root.persistCurrentPreferences(true)
    }

    function applyModelRecommendations(prewarm) {
        if (realtimeModel.currentIndex < 0
                || !root.readyModels[realtimeModel.currentIndex]) return
        var values = root.readyModels[realtimeModel.currentIndex].recommended || ({})
        if (values.pitch !== undefined) pitchSlider.value = Number(values.pitch)
        if (values.f0 !== undefined) {
            var f0Index = root.comboValueIndex(f0Method, values.f0)
            if (f0Index >= 0) f0Method.currentIndex = f0Index
        }
        if (values.index_rate !== undefined)
            indexRateSlider.value = Number(values.index_rate) * 100
        if (values.rms_mix_rate !== undefined)
            rmsMixSlider.value = Number(values.rms_mix_rate) * 100
        root.persistCurrentPreferences(prewarm)
    }

    function applySelectedModelRecommendations() {
        root.applyModelRecommendations(true)
    }

    function restoreModel() {
        var saved = root.bridge.realtime.preferences || ({})
        var index = root.comboValueIndex(realtimeModel, saved.model || "")
        realtimeModel.currentIndex = index >= 0 ? index : (realtimeModel.count > 0 ? 0 : -1)
        if (index < 0 && realtimeModel.currentIndex >= 0)
            root.applyModelRecommendations(false)
    }

    function prewarmSelectedModel() {
        var route = root.bridge.realtime.audioRoute || ({})
        if (!root.pageActive || !root.bridge.maintenance.runtimeReady
                || root.active || realtimeModel.currentIndex < 0 || !Boolean(route.ready))
            return
        root.bridge.realtime.prepareModel(
            String(realtimeModel.currentValue),
            Number(route.input_device),
            Number(route.output_device),
            Math.round(Number(pitchSlider.value)),
            String(f0Method.currentValue),
            Number(indexRateSlider.value) / 100.0,
            Number(rmsMixSlider.value) / 100.0,
            Number(vadThresholdSlider.value) / 100.0,
            Number(inputGateSlider.value),
            Number(latencyMode.currentValue)
        )
    }

    function restoreControls() {
        var saved = root.bridge.realtime.preferences || ({})
        pitchSlider.value = Number(saved.pitch)
        vadThresholdSlider.value = Number(saved.vad_threshold) * 100
        inputGateSlider.value = Number(saved.input_gate_db)
        indexRateSlider.value = Number(saved.index_rate) * 100
        rmsMixSlider.value = Number(saved.rms_mix_rate) * 100
        testMode.checked = Boolean(saved.test_mode)
        var f0Index = root.comboValueIndex(f0Method, saved.f0)
        f0Method.currentIndex = f0Index >= 0 ? f0Index : 0
        var latencyIndex = root.comboValueIndex(latencyMode, saved.block_seconds)
        latencyMode.currentIndex = latencyIndex >= 0 ? latencyIndex : 1
        root.restoreModel()
    }

    onReadyModelsChanged: Qt.callLater(restoreModel)
    onPageActiveChanged: {
        if (root.initialized && root.pageActive) prewarmTimer.restart()
        else if (!root.active) root.bridge.realtime.releaseModel()
    }
    Component.onCompleted: Qt.callLater(function() {
        root.restoreControls()
        root.initialized = true
    })

    Connections {
        target: root.bridge.realtime
        function onPreferencesChanged() { Qt.callLater(root.restoreControls) }
        function onAudioRouteChanged() {
            if (root.worker.state !== "not_started") prewarmTimer.restart()
        }
    }
    Connections {
        target: root.bridge.maintenance
        function onRuntimeChanged() {
            if (root.pageActive && root.bridge.maintenance.runtimeReady
                    && root.worker.state !== "not_started")
                prewarmTimer.restart()
        }
    }

    Timer {
        id: prewarmTimer
        interval: 250
        repeat: false
        onTriggered: root.prewarmSelectedModel()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.realtime")
        }

        AppPanel {
            objectName: "realtimePrimaryControls"
            Layout.fillWidth: true

            GridLayout {
                objectName: "realtimeActionRow"
                Layout.fillWidth: true
                columns: 3
                columnSpacing: 8
                AppButton {
                    objectName: "realtimeStartButton"
                    Layout.fillWidth: true
                    text: root.bridge.text("action.start_realtime")
                    kind: "primary"
                    enabled: root.canStart
                    onClicked: {
                        root.saveCurrentPreferences()
                        root.bridge.realtime.startSession(
                            realtimeModel.currentValue,
                            Number(root.bridge.realtime.audioRoute.input_device),
                            Number(root.bridge.realtime.audioRoute.output_device),
                            pitchSlider.value,
                            f0Method.currentValue,
                            indexRateSlider.value / 100.0,
                            rmsMixSlider.value / 100.0,
                            vadThresholdSlider.value / 100.0,
                            inputGateSlider.value,
                            Number(latencyMode.currentValue),
                            testMode.checked
                        )
                    }
                }
                AppButton {
                    objectName: "realtimeStopButton"
                    Layout.fillWidth: true
                    text: root.bridge.text("action.stop_realtime")
                    kind: "danger"
                    enabled: root.active && root.session.state !== "stopping"
                    onClicked: root.bridge.realtime.stopSession()
                }
                AppComboBox {
                    id: realtimeModel
                    objectName: "realtimeModelSelector"
                    Layout.fillWidth: true
                    model: root.readyModels
                    textRole: "localized_name"
                    valueRole: "id"
                    emptyText: root.bridge.text("empty.models.short")
                    Accessible.name: root.bridge.text("field.model")
                    enabled: !root.active && count > 0
                    onActivated: root.applySelectedModelRecommendations()
                }
            }


            Label {
                objectName: "realtimeDisabledReason"
                Layout.fillWidth: true
                visible: root.disabledReason().length > 0
                text: root.disabledReason()
                color: root.theme.warning
                font.family: root.theme.uiFont
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            AppButton {
                Layout.fillWidth: true
                visible: root.disabledReason().length > 0 && !root.active
                text: !root.bridge.maintenance.runtimeReady
                    ? root.bridge.text("realtime.disabled.action.runtime")
                    : (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0)
                    ? root.bridge.text("realtime.disabled.action.model")
                    : root.bridge.text("realtime.disabled.action.audio")
                onClicked: root.navigateRequested(
                    !root.bridge.maintenance.runtimeReady ? 5
                    : (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0) ? 2 : 5
                )
            }

            AppCheckBox {
                id: testMode
                objectName: "realtimeTestMode"
                Layout.fillWidth: true
                text: root.bridge.text("realtime.test_mode")
                checked: false
                enabled: !root.active
                onClicked: root.saveCurrentPreferences()
            }
        }

        AppScrollView {
            id: realtimeScroll
            objectName: "realtimeScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            GridLayout {
                width: realtimeScroll.availableWidth
                columns: 1
                columnSpacing: 0
                rowSpacing: 10

                AppPanel {
                    objectName: "realtimeStatusPanel"
                    Layout.row: 1
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
                            text: root.metrics.test_mode && root.metrics.rvc_inference_active
                                ? root.bridge.text("realtime.voice.recording")
                                : root.metrics.speech_detected
                                ? root.bridge.text("realtime.voice.detected")
                                : root.bridge.text("realtime.voice.waiting")
                            tone: root.metrics.speech_detected ? "info" : "neutral"
                        }
                        StatusPill {
                            objectName: "realtimeVoiceStatus"
                            text: root.metrics.test_mode && root.metrics.playback_active
                                ? root.bridge.text("realtime.voice.playing")
                                : root.metrics.test_mode && root.metrics.rvc_inference_active
                                ? root.bridge.text("realtime.voice.processing")
                                : root.metrics.rvc_inference_active
                                ? root.bridge.text("realtime.voice.converting")
                                : root.bridge.text("realtime.voice.listening")
                            tone: root.metrics.playback_active || root.metrics.rvc_inference_active
                                ? "success" : "neutral"
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
                    objectName: "realtimeVoicePanel"
                    Layout.row: 0
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text("section.realtime_voice") }

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
                                objectName: "realtimeLatencyMode"
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
                                onActivated: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.f0") }
                            AppComboBox {
                                id: f0Method
                                objectName: "realtimeF0Method"
                                Layout.fillWidth: true
                                model: [{"label": "RMVPE", "value": "rmvpe"}, {"label": "FCPE", "value": "fcpe"}, {"label": "PM", "value": "pm"}]
                                textRole: "label"
                                valueRole: "value"
                                enabled: !root.active
                                onActivated: root.saveCurrentPreferences()
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
                                onUserEdited: root.saveCurrentPreferences()
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
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.input_gate_db") }
                            AppSlider {
                                id: inputGateSlider
                                objectName: "realtimeInputGateSlider"
                                Layout.fillWidth: true
                                from: -60
                                to: -20
                                value: -30
                                stepSize: 1
                                suffix: " dB"
                                accessibleName: root.bridge.text("field.input_gate_db")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
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
                                onUserEdited: root.saveCurrentPreferences()
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
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                    }

                }

                Item { Layout.row: 2; Layout.preferredHeight: 2 }
            }
        }
    }
}
