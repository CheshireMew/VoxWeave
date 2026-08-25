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
    readonly property bool canPrepare: root.canStart
        && ["starting", "warming", "ready"].indexOf(root.worker.state) < 0
    readonly property bool canRelease: !root.active
        && ["starting", "warming", "ready", "failed"].indexOf(root.worker.state) >= 0
    readonly property var parameterSpecs: root.bridge.realtime.parameterSpecs || ({})

    objectName: "realtimePage"

    function comboValueIndex(combo, value) {
        for (var i = 0; i < combo.count; ++i) {
            if (String(combo.valueAt(i)) === String(value)) return i
        }
        return -1
    }

    function parameterSpec(name) {
        return root.parameterSpecs[name] || ({})
    }

    function parameterOptions(name) {
        var options = root.parameterSpec(name).options || []
        var result = []
        for (var i = 0; i < options.length; ++i) {
            var option = options[i]
            result.push({
                "value": option.value,
                "label": option.label_key
                    ? root.bridge.text(root.bridge.language, option.label_key)
                    : String(option.label || option.value)
            })
        }
        return result
    }

    function disabledReason() {
        if (root.active) return ""
        if (!root.bridge.maintenance.runtimeReady)
            return root.bridge.text(root.bridge.language, "realtime.disabled.runtime")
        if (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0)
            return root.bridge.text(root.bridge.language, "realtime.disabled.model")
        if (!Boolean(root.bridge.realtime.audioRoute.ready))
            return root.bridge.text(root.bridge.language, "realtime.disabled.audio")
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
            ,"push_to_talk": Boolean(pushToTalkMode.checked)
        }
    }

    function currentSessionArguments(testModeValue) {
        var preferences = root.currentPreferences()
        var route = root.bridge.realtime.audioRoute || ({})
        return {
            "model": preferences.model,
            "input_device": Number(route.input_device),
            "output_device": Number(route.output_device),
            "pitch": preferences.pitch,
            "f0": preferences.f0,
            "index_rate": preferences.index_rate,
            "rms_mix_rate": preferences.rms_mix_rate,
            "vad_threshold": preferences.vad_threshold,
            "input_gate_db": preferences.input_gate_db,
            "block_seconds": preferences.block_seconds,
            "test_mode": Boolean(testModeValue),
            "recording": Boolean(recordMode.checked)
            ,"push_to_talk": Boolean(pushToTalkMode.checked)
        }
    }

    function currentSceneSettings() {
        var preferences = root.currentPreferences()
        return {
            "model": preferences.model,
            "hostapi": preferences.hostapi,
            "input_device": preferences.input_device,
            "output_device": preferences.output_device,
            "pitch": preferences.pitch,
            "f0": preferences.f0,
            "index_rate": preferences.index_rate,
            "rms_mix_rate": preferences.rms_mix_rate,
            "vad_threshold": preferences.vad_threshold,
            "input_gate_db": preferences.input_gate_db,
            "block_seconds": preferences.block_seconds,
            "test_mode": Boolean(testMode.checked),
            "recording": Boolean(recordMode.checked)
            ,"push_to_talk": Boolean(pushToTalkMode.checked)
        }
    }

    function currentHotkeys() {
        return {
            "start_stop": String(sceneStartHotkey.text),
            "bypass": String(sceneBypassHotkey.text),
            "mute": String(sceneMuteHotkey.text)
            ,"push_to_talk": String(scenePushToTalkHotkey.text)
        }
    }

    function persistCurrentPreferences() {
        root.bridge.realtime.savePreferences(root.currentPreferences())
    }

    function saveCurrentPreferences() {
        root.persistCurrentPreferences()
    }

    function applyModelRecommendations() {
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
        root.persistCurrentPreferences()
    }

    function applySelectedModelRecommendations() {
        root.applyModelRecommendations()
    }

    function restoreModel() {
        var saved = root.bridge.realtime.preferences || ({})
        var index = root.comboValueIndex(realtimeModel, saved.model || "")
        realtimeModel.currentIndex = index >= 0 ? index : (realtimeModel.count > 0 ? 0 : -1)
        if (index < 0 && realtimeModel.currentIndex >= 0)
            root.applyModelRecommendations()
    }

    function prepareSelectedModel() {
        var route = root.bridge.realtime.audioRoute || ({})
        if (!root.pageActive || !root.bridge.maintenance.runtimeReady
                || root.active || realtimeModel.currentIndex < 0 || !Boolean(route.ready))
            return
        root.bridge.realtime.prepareModel(root.currentSessionArguments(false))
    }

    function restoreControls() {
        var saved = root.bridge.realtime.preferences || ({})
        pitchSlider.value = Number(saved.pitch)
        vadThresholdSlider.value = Number(saved.vad_threshold) * 100
        inputGateSlider.value = Number(saved.input_gate_db)
        indexRateSlider.value = Number(saved.index_rate) * 100
        rmsMixSlider.value = Number(saved.rms_mix_rate) * 100
        testMode.checked = Boolean(saved.test_mode)
        pushToTalkMode.checked = Boolean(saved.push_to_talk)
        var f0Index = root.comboValueIndex(f0Method, saved.f0)
        f0Method.currentIndex = f0Index >= 0 ? f0Index : 0
        var latencyIndex = root.comboValueIndex(latencyMode, saved.block_seconds)
        latencyMode.currentIndex = latencyIndex >= 0 ? latencyIndex : 1
        root.restoreModel()
    }

    onReadyModelsChanged: Qt.callLater(restoreModel)
    onPageActiveChanged: {
        if (root.initialized && !root.pageActive && !root.active)
            root.bridge.realtime.releaseModel()
    }
    Component.onCompleted: Qt.callLater(function() {
        root.restoreControls()
        root.initialized = true
    })

    Connections {
        target: root.bridge.realtime
        function onPreferencesChanged() { Qt.callLater(root.restoreControls) }
        function onAudioRouteChanged() {
            if (root.worker.state === "ready") root.bridge.realtime.releaseModel()
        }
    }
    Connections {
        target: root.bridge.maintenance
        function onRuntimeChanged() {
            if (!root.bridge.maintenance.runtimeReady
                    && root.worker.state !== "not_started")
                root.bridge.realtime.releaseModel()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.realtime")
        }

        AppPanel {
            objectName: "realtimePrimaryControls"
            Layout.fillWidth: true

            GridLayout {
                objectName: "realtimeActionRow"
                Layout.fillWidth: true
                columns: width >= 1000 ? 7 : (width >= 760 ? 5 : 3)
                columnSpacing: 8
                AppButton {
                    objectName: "realtimePrepareButton"
                    Layout.fillWidth: true
                    text: root.bridge.text(root.bridge.language, "action.prepare_realtime")
                    enabled: root.canPrepare
                    onClicked: {
                        root.saveCurrentPreferences()
                        root.prepareSelectedModel()
                    }
                }
                AppButton {
                    objectName: "realtimeStartButton"
                    Layout.fillWidth: true
                    text: root.bridge.text(root.bridge.language, "action.start_realtime")
                    kind: "primary"
                    enabled: root.canStart
                    onClicked: {
                        root.saveCurrentPreferences()
                        root.bridge.realtime.startSession(
                            root.currentSessionArguments(testMode.checked))
                    }
                }
                AppButton {
                    objectName: "realtimeStopButton"
                    Layout.fillWidth: true
                    text: root.bridge.text(root.bridge.language, "action.stop_realtime")
                    kind: "danger"
                    enabled: root.active && root.session.state !== "stopping"
                    onClicked: root.bridge.realtime.stopSession()
                }
                AppButton {
                    objectName: "realtimeBypassButton"
                    Layout.fillWidth: true
                    text: root.metrics.bypass
                        ? root.bridge.text(root.bridge.language, "action.disable_bypass")
                        : root.bridge.text(root.bridge.language, "action.enable_bypass")
                    enabled: root.session.state === "running"
                    onClicked: root.bridge.realtime.toggleBypass()
                }
                AppButton {
                    objectName: "realtimeMuteButton"
                    Layout.fillWidth: true
                    text: root.metrics.muted
                        ? root.bridge.text(root.bridge.language, "action.unmute")
                        : root.bridge.text(root.bridge.language, "action.mute")
                    enabled: root.session.state === "running"
                    onClicked: root.bridge.realtime.toggleMute()
                }
                AppButton {
                    objectName: "realtimeReleaseButton"
                    Layout.fillWidth: true
                    text: root.bridge.text(root.bridge.language, "action.release_realtime_model")
                    enabled: root.canRelease
                    onClicked: root.bridge.realtime.releaseModel()
                }
                AppComboBox {
                    id: realtimeModel
                    objectName: "realtimeModelSelector"
                    Layout.fillWidth: true
                    model: root.readyModels
                    textRole: "localized_name"
                    valueRole: "id"
                    emptyText: root.bridge.text(root.bridge.language, "empty.models.short")
                    Accessible.name: root.bridge.text(root.bridge.language, "field.model")
                    enabled: !root.active && count > 0
                    onActivated: root.applySelectedModelRecommendations()
                }
            }

            Label {
                Layout.fillWidth: true
                visible: ["starting", "warming", "ready"].indexOf(root.worker.state) >= 0
                text: root.bridge.text(root.bridge.language,
                    root.active ? "realtime.priority_notice_active" : "realtime.priority_notice_warm")
                color: root.theme.warning
                font.family: root.theme.uiFont
                font.pixelSize: 11
                wrapMode: Text.Wrap
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
                    ? root.bridge.text(root.bridge.language, "realtime.disabled.action.runtime")
                    : (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0)
                    ? root.bridge.text(root.bridge.language, "realtime.disabled.action.model")
                    : root.bridge.text(root.bridge.language, "realtime.disabled.action.audio")
                onClicked: root.navigateRequested(
                    !root.bridge.maintenance.runtimeReady ? 6
                    : (realtimeModel.count <= 0 || realtimeModel.currentIndex < 0) ? 3 : 6
                )
            }

            AppCheckBox {
                id: testMode
                objectName: "realtimeTestMode"
                Layout.fillWidth: true
                text: root.bridge.text(root.bridge.language, "realtime.test_mode")
                checked: false
                enabled: !root.active
                onClicked: root.saveCurrentPreferences()
            }
            AppCheckBox {
                id: recordMode
                objectName: "realtimeRecordMode"
                Layout.fillWidth: true
                text: root.bridge.text(root.bridge.language, "realtime.record_dry_wet")
                checked: false
                enabled: !root.active
            }
            AppCheckBox {
                id: pushToTalkMode
                objectName: "realtimePushToTalkMode"
                Layout.fillWidth: true
                text: "Push to talk (hold the global hotkey)"
                checked: false
                enabled: !root.active
                onClicked: root.saveCurrentPreferences()
            }
            AppButton {
                Layout.fillWidth: true
                visible: root.active
                text: root.metrics.recording
                    ? root.bridge.text(root.bridge.language, "action.stop_recording")
                    : root.bridge.text(root.bridge.language, "action.start_recording")
                onClicked: root.bridge.realtime.toggleRecording()
            }
            AppButton {
                objectName: "realtimeMiniPanelButton"
                Layout.fillWidth: true
                text: "Open mini controls"
                onClicked: root.bridge.realtime.showMiniPanel()
            }
            RowLayout {
                Layout.fillWidth: true
                visible: root.session.state === "stopped"
                    && Boolean(root.metrics.recording_manifest_path)
                AppTextField {
                    id: recordingProjectName
                    Layout.fillWidth: true
                    text: "Realtime recording"
                    placeholderText: "Offline project name"
                }
                AppButton {
                    text: "Continue as offline project"
                    enabled: recordingProjectName.text.trim().length > 0
                    onClicked: root.bridge.realtime.promoteRecording(
                        recordingProjectName.text.trim())
                }
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

                RealtimeStatusPanel {
                    Layout.row: 1
                    Layout.fillWidth: true
                    bridge: root.bridge
                    theme: root.theme
                    session: root.session
                }

                AppPanel {
                    objectName: "realtimeVoicePanel"
                    Layout.row: 0
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text(root.bridge.language, "section.realtime_voice") }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.latency_mode") }
                            AppComboBox {
                                id: latencyMode
                                objectName: "realtimeLatencyMode"
                                Layout.fillWidth: true
                                model: root.parameterOptions("block_seconds")
                                textRole: "label"
                                valueRole: "value"
                                currentIndex: 0
                                enabled: !root.active
                                onActivated: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.f0") }
                            AppComboBox {
                                id: f0Method
                                objectName: "realtimeF0Method"
                                Layout.fillWidth: true
                                model: root.parameterOptions("f0")
                                textRole: "label"
                                valueRole: "value"
                                enabled: !root.active
                                onActivated: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.pitch") }
                            AppSlider {
                                id: pitchSlider
                                objectName: "realtimePitchSlider"
                                Layout.fillWidth: true
                                from: Number(root.parameterSpec("pitch").ui_minimum)
                                to: Number(root.parameterSpec("pitch").ui_maximum)
                                value: Number(root.parameterSpec("pitch").ui_default)
                                stepSize: Number(root.parameterSpec("pitch").ui_step)
                                showPositiveSign: true
                                accessibleName: root.bridge.text(root.bridge.language, "field.pitch")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.vad_threshold") }
                            AppSlider {
                                id: vadThresholdSlider
                                objectName: "realtimeVadThresholdSlider"
                                Layout.fillWidth: true
                                from: Number(root.parameterSpec("vad_threshold").ui_minimum)
                                to: Number(root.parameterSpec("vad_threshold").ui_maximum)
                                value: Number(root.parameterSpec("vad_threshold").ui_default)
                                stepSize: Number(root.parameterSpec("vad_threshold").ui_step)
                                suffix: "%"
                                accessibleName: root.bridge.text(root.bridge.language, "field.vad_threshold")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.input_gate_db") }
                            AppSlider {
                                id: inputGateSlider
                                objectName: "realtimeInputGateSlider"
                                Layout.fillWidth: true
                                from: Number(root.parameterSpec("input_gate_db").ui_minimum)
                                to: Number(root.parameterSpec("input_gate_db").ui_maximum)
                                value: Number(root.parameterSpec("input_gate_db").ui_default)
                                stepSize: Number(root.parameterSpec("input_gate_db").ui_step)
                                suffix: " dB"
                                accessibleName: root.bridge.text(root.bridge.language, "field.input_gate_db")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.index_rate_percent") }
                            AppSlider {
                                id: indexRateSlider
                                objectName: "realtimeIndexRateSlider"
                                Layout.fillWidth: true
                                from: Number(root.parameterSpec("index_rate").ui_minimum)
                                to: Number(root.parameterSpec("index_rate").ui_maximum)
                                value: Number(root.parameterSpec("index_rate").ui_default)
                                stepSize: Number(root.parameterSpec("index_rate").ui_step)
                                suffix: "%"
                                accessibleName: root.bridge.text(root.bridge.language, "field.index_rate_percent")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.rms_mix_percent") }
                            AppSlider {
                                id: rmsMixSlider
                                objectName: "realtimeRmsMixSlider"
                                Layout.fillWidth: true
                                from: Number(root.parameterSpec("rms_mix_rate").ui_minimum)
                                to: Number(root.parameterSpec("rms_mix_rate").ui_maximum)
                                value: Number(root.parameterSpec("rms_mix_rate").ui_default)
                                stepSize: Number(root.parameterSpec("rms_mix_rate").ui_step)
                                suffix: "%"
                                accessibleName: root.bridge.text(root.bridge.language, "field.rms_mix_percent")
                                enabled: !root.active
                                onUserEdited: root.saveCurrentPreferences()
                            }
                        }
                    }

                }

                AppPanel {
                    objectName: "realtimeScenesPanel"
                    Layout.row: 2
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.realtime_scenes")
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 8
                        AppComboBox {
                            id: sceneSelector
                            objectName: "realtimeSceneSelector"
                            Layout.fillWidth: true
                            model: root.bridge.realtime.scenes
                            textRole: "name"
                            valueRole: "id"
                            emptyText: root.bridge.text(root.bridge.language, "empty.realtime_scenes")
                            onCurrentIndexChanged: {
                                var scene = root.bridge.realtime.scenes[currentIndex]
                                if (!scene) return
                                sceneName.text = String(scene.name)
                                sceneStartHotkey.text = String(scene.hotkeys.start_stop)
                                sceneBypassHotkey.text = String(scene.hotkeys.bypass)
                                sceneMuteHotkey.text = String(scene.hotkeys.mute)
                                scenePushToTalkHotkey.text = String(
                                    scene.hotkeys.push_to_talk || "Ctrl+Alt+F12")
                            }
                            onActivated: {
                                var scene = root.bridge.realtime.scenes[currentIndex]
                                if (!scene) return
                                sceneName.text = String(scene.name)
                                sceneStartHotkey.text = String(scene.hotkeys.start_stop)
                                sceneBypassHotkey.text = String(scene.hotkeys.bypass)
                                sceneMuteHotkey.text = String(scene.hotkeys.mute)
                                scenePushToTalkHotkey.text = String(
                                    scene.hotkeys.push_to_talk || "Ctrl+Alt+F12")
                            }
                        }
                        AppTextField {
                            id: sceneName
                            Layout.fillWidth: true
                            placeholderText: root.bridge.text(root.bridge.language, "placeholder.scene_name")
                        }
                        AppTextField {
                            id: sceneStartHotkey
                            Layout.fillWidth: true
                            text: "Ctrl+Alt+F9"
                            placeholderText: root.bridge.text(root.bridge.language, "placeholder.hotkey_start")
                        }
                        AppTextField {
                            id: sceneBypassHotkey
                            Layout.fillWidth: true
                            text: "Ctrl+Alt+F10"
                            placeholderText: root.bridge.text(root.bridge.language, "placeholder.hotkey_bypass")
                        }
                        AppTextField {
                            id: sceneMuteHotkey
                            Layout.fillWidth: true
                            text: "Ctrl+Alt+F11"
                            placeholderText: root.bridge.text(root.bridge.language, "placeholder.hotkey_mute")
                        }
                        AppTextField {
                            id: scenePushToTalkHotkey
                            objectName: "realtimePushToTalkHotkey"
                            Layout.fillWidth: true
                            text: "Ctrl+Alt+F12"
                            placeholderText: "Push-to-talk hotkey"
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.scene_create")
                                enabled: sceneName.text.trim().length > 0 && !root.active
                                onClicked: root.bridge.realtime.createScene({
                                    "name": sceneName.text.trim(),
                                    "settings": root.currentSceneSettings(),
                                    "hotkeys": root.currentHotkeys()
                                })
                            }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.scene_update")
                                enabled: sceneSelector.currentIndex >= 0 && !root.active
                                onClicked: {
                                    var scene = root.bridge.realtime.scenes[sceneSelector.currentIndex]
                                    root.bridge.realtime.updateScene({
                                        "scene_id": scene.id,
                                        "expected_revision": scene.revision,
                                        "name": sceneName.text.trim(),
                                        "settings": root.currentSceneSettings(),
                                        "hotkeys": root.currentHotkeys()
                                    })
                                }
                            }
                            AppButton {
                                text: {
                                    var scene = root.bridge.realtime.scenes[
                                        sceneSelector.currentIndex]
                                    return scene && scene.archived
                                        ? root.bridge.text(root.bridge.language,
                                            "action.restore")
                                        : root.bridge.text(root.bridge.language,
                                            "scene.archive")
                                }
                                enabled: sceneSelector.currentIndex >= 0 && !root.active
                                onClicked: {
                                    var scene = root.bridge.realtime.scenes[
                                        sceneSelector.currentIndex]
                                    root.bridge.realtime.archiveScene(scene.id,
                                        scene.revision, !scene.archived)
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.scene_prepare")
                                enabled: sceneSelector.currentIndex >= 0 && !root.active
                                    && !root.bridge.realtime.scenes[
                                        sceneSelector.currentIndex].archived
                                onClicked: root.bridge.realtime.applyScene(
                                    String(sceneSelector.currentValue), false)
                            }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.scene_start")
                                kind: "primary"
                                enabled: sceneSelector.currentIndex >= 0 && !root.active
                                    && !root.bridge.realtime.scenes[
                                        sceneSelector.currentIndex].archived
                                onClicked: root.bridge.realtime.applyScene(
                                    String(sceneSelector.currentValue), true)
                            }
                        }
                    }
                }

                AppPanel {
                    objectName: "realtimeRoutingPanel"
                    Layout.row: 3
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.audio_routing")
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.realtime.routing.virtual_audio_available
                            ? root.bridge.text(root.bridge.language, "routing.detected").arg(
                                root.bridge.realtime.routing.detected_products.join(", "))
                            : root.bridge.text(root.bridge.language, "routing.not_detected")
                        color: root.bridge.realtime.routing.virtual_audio_available
                            ? root.theme.success : root.theme.warning
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        model: root.bridge.realtime.routing.instructions || []
                        delegate: Label {
                            required property string modelData
                            Layout.fillWidth: true
                            text: "• " + modelData
                            color: root.theme.textMuted
                            wrapMode: Text.Wrap
                        }
                    }
                    AppButton {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "action.refresh_routing")
                        onClicked: root.bridge.realtime.inspectRouting()
                    }
                    AppButton {
                        objectName: "realtimeCalibrationButton"
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "action.calibrate_audio")
                        enabled: !root.active && Boolean(root.bridge.realtime.audioRoute.ready)
                        onClicked: root.bridge.realtime.calibrate()
                    }
                    AppButton {
                        objectName: "realtimeRoutingTestButton"
                        Layout.fillWidth: true
                        text: "Run end-to-end routing test"
                        enabled: !root.active && Boolean(root.bridge.realtime.audioRoute.ready)
                        onClicked: root.bridge.realtime.testRouting()
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.bridge.realtime.routingTest.passed !== undefined
                        text: (root.bridge.realtime.routingTest.passed ? "Routing passed" : "Routing failed")
                            + " · correlation "
                            + Number(root.bridge.realtime.routingTest.correlation || 0).toFixed(2)
                            + " · latency "
                            + Number(root.bridge.realtime.routingTest.latency_ms || 0).toFixed(1)
                            + " ms"
                        color: root.bridge.realtime.routingTest.passed
                            ? root.theme.success : root.theme.danger
                        wrapMode: Text.Wrap
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.bridge.realtime.calibration.measured_input_db !== undefined
                        text: root.bridge.text(root.bridge.language, "realtime.calibration.result")
                            .arg(Number(root.bridge.realtime.calibration.measured_input_db).toFixed(1))
                            .arg(Number(root.bridge.realtime.calibration.recommended_input_gate_db).toFixed(1))
                            + " · noise "
                            + Number(root.bridge.realtime.calibration.noise_floor_db || 0).toFixed(1)
                            + " dB · SNR "
                            + Number(root.bridge.realtime.calibration.snr_db || 0).toFixed(1)
                            + " dB · pitch "
                            + String(root.bridge.realtime.calibration.recommended_pitch || 0)
                            + " · index "
                            + Number(root.bridge.realtime.calibration.recommended_index_rate || 0).toFixed(2)
                        color: root.theme.textMuted
                        wrapMode: Text.Wrap
                    }
                }

                Item { Layout.row: 4; Layout.preferredHeight: 2 }
            }
        }
    }
}
