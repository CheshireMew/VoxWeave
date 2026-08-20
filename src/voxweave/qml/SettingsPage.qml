pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: root
    required property var bridge
    required property var theme
    property var devicePayload: ({"hostapis": [], "devices": []})
    property url pendingArchiveRoot
    property bool showRuntimeDetails: false
    readonly property bool realtimeActive: ["starting", "running", "stopping"].indexOf(
        root.bridge.realtime.status.state
    ) >= 0
    readonly property bool runtimeInspecting: root.bridge.activity.busyKeys.includes(
        "runtime-inspect"
    )
    readonly property var inputDevices: (devicePayload.devices || []).filter(function(device) {
        return Number(device.input_channels) > 0
            && Number(device.hostapi_id) === Number(audioHostApi.currentValue)
    })
    readonly property var outputDevices: (devicePayload.devices || []).filter(function(device) {
        return Number(device.output_channels) > 0
            && Number(device.hostapi_id) === Number(audioHostApi.currentValue)
    })

    function comboValueIndex(combo, value) {
        for (var index = 0; index < combo.count; ++index) {
            if (String(combo.valueAt(index)) === String(value)) return index
        }
        return -1
    }

    function restoreAudioDevices() {
        var route = root.bridge.realtime.audioRoute || ({})
        var hostIndex = root.comboValueIndex(audioHostApi, route.hostapi_id)
        audioHostApi.currentIndex = hostIndex >= 0
            ? hostIndex : (audioHostApi.count > 0 ? 0 : -1)
        Qt.callLater(function() {
            var inputIndex = root.comboValueIndex(audioInputDevice, route.input_device)
            audioInputDevice.currentIndex = inputIndex >= 0
                ? inputIndex : (audioInputDevice.count > 0 ? 0 : -1)
            var outputIndex = root.comboValueIndex(audioOutputDevice, route.output_device)
            audioOutputDevice.currentIndex = outputIndex >= 0
                ? outputIndex : (audioOutputDevice.count > 0 ? 0 : -1)
        })
    }

    function saveCurrentAudioRoute() {
        if (audioInputDevice.currentIndex < 0 || audioOutputDevice.currentIndex < 0)
            return
        root.bridge.realtime.saveAudioRoute(
            Number(audioInputDevice.currentValue),
            Number(audioOutputDevice.currentValue)
        )
    }

    function selectDefaultAudioRoute() {
        var inputIndex = root.comboValueIndex(
            audioInputDevice, root.devicePayload.default_input_device
        )
        audioInputDevice.currentIndex = inputIndex >= 0
            ? inputIndex : (audioInputDevice.count > 0 ? 0 : -1)
        var outputIndex = root.comboValueIndex(
            audioOutputDevice, root.devicePayload.default_output_device
        )
        audioOutputDevice.currentIndex = outputIndex >= 0
            ? outputIndex : (audioOutputDevice.count > 0 ? 0 : -1)
        root.saveCurrentAudioRoute()
    }

    onDevicePayloadChanged: Qt.callLater(restoreAudioDevices)
    Component.onCompleted: Qt.callLater(restoreAudioDevices)

    Connections {
        target: root.bridge.realtime
        function onAudioRouteChanged() { Qt.callLater(root.restoreAudioDevices) }
    }

FileDialog {
    id: diagnosticDialog
    fileMode: FileDialog.SaveFile
    currentFolder: root.bridge.maintenance.dataRootUrl
    nameFilters: ["JSON (*.json)"]
    onAccepted: root.bridge.maintenance.exportDiagnostics(selectedFile)
}
FolderDialog {
    id: archiveDialog
    onAccepted: {
        root.pendingArchiveRoot = selectedFolder
        archiveConfirmation.open()
    }
}

Basic.Dialog {
    id: archiveConfirmation
    modal: true
    anchors.centerIn: parent
    width: Math.min(420, root.width - 48)
    height: 180
    title: root.bridge.text("storage.archive.confirm_title")
    standardButtons: Basic.Dialog.Ok | Basic.Dialog.Cancel
    contentItem: Label {
        text: root.bridge.text("storage.archive.confirm_detail")
        color: root.theme.text
        font.family: root.theme.uiFont
        wrapMode: Text.Wrap
    }
    onAccepted: root.bridge.maintenance.archiveArtifacts(root.pendingArchiveRoot, archiveDays.value)
}

    objectName: "settingsPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.settings")
        }

        AppScrollView {
            id: settingsScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: settingsScroll.availableWidth
                spacing: 10

                AppPanel {
                    objectName: "settingsAudioPanel"
                    Layout.fillWidth: true

                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.audio_devices")
                    }

                    FieldLabel { text: root.bridge.text("field.audio_host") }
                    RowLayout {
                        Layout.fillWidth: true
                        AppComboBox {
                            id: audioHostApi
                            objectName: "settingsAudioHostApi"
                            Layout.fillWidth: true
                            model: root.devicePayload.hostapis || []
                            textRole: "name"
                            valueRole: "id"
                            emptyText: root.bridge.text("audio.no_devices")
                            enabled: !root.realtimeActive && count > 0
                            onActivated: Qt.callLater(root.selectDefaultAudioRoute)
                        }
                        AppButton {
                            text: root.bridge.text("action.refresh")
                            enabled: !root.realtimeActive
                            onClicked: root.bridge.realtime.refreshDevices()
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 2 : 1
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.input_device") }
                            AppComboBox {
                                id: audioInputDevice
                                objectName: "settingsAudioInputDevice"
                                Layout.fillWidth: true
                                model: root.inputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text("audio.no_input")
                                enabled: !root.realtimeActive && count > 0
                                onActivated: root.saveCurrentAudioRoute()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.output_device") }
                            AppComboBox {
                                id: audioOutputDevice
                                objectName: "settingsAudioOutputDevice"
                                Layout.fillWidth: true
                                model: root.outputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text("audio.no_output")
                                enabled: !root.realtimeActive && count > 0
                                onActivated: root.saveCurrentAudioRoute()
                            }
                        }
                    }

                    Flow {
                        Layout.fillWidth: true
                        Layout.preferredHeight: childrenRect.height
                        spacing: 8
                        AppButton {
                            text: root.bridge.realtime.audioTesting
                                && root.bridge.realtime.audioTest.mode === "input"
                                ? root.bridge.text("audio.test.running")
                                : root.bridge.text("audio.test.input")
                            enabled: !root.realtimeActive && !root.bridge.realtime.audioTesting
                                && audioInputDevice.currentIndex >= 0
                            onClicked: root.bridge.realtime.testAudioDevice(
                                "input", Number(audioInputDevice.currentValue)
                            )
                        }
                        AppButton {
                            text: root.bridge.realtime.audioTesting
                                && root.bridge.realtime.audioTest.mode === "output"
                                ? root.bridge.text("audio.test.running")
                                : root.bridge.text("audio.test.output")
                            enabled: !root.realtimeActive && !root.bridge.realtime.audioTesting
                                && audioOutputDevice.currentIndex >= 0
                            onClicked: root.bridge.realtime.testAudioDevice(
                                "output", Number(audioOutputDevice.currentValue)
                            )
                        }
                        StatusPill {
                            visible: String(root.bridge.realtime.audioTest.state || "").length > 0
                                && root.bridge.realtime.audioTest.state !== "running"
                            text: root.bridge.realtime.audioTest.state === "completed"
                                ? root.bridge.text("audio.test.completed")
                                : root.bridge.text("audio.test.failed")
                            tone: root.bridge.realtime.audioTest.state === "completed"
                                ? "success" : "danger"
                        }
                        Label {
                            visible: root.bridge.realtime.audioTest.mode === "input"
                                && root.bridge.realtime.audioTest.state === "completed"
                            text: root.bridge.text("audio.test.level") + " "
                                + Math.round(Number(root.bridge.realtime.audioTest.peak || 0) * 100) + "%"
                            color: root.theme.textMuted
                            font.pixelSize: 11
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("audio.headphones_hint")
                        color: root.theme.warning
                        font.family: root.theme.uiFont
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: width > 740 ? 2 : 1
                    columnSpacing: 10
                    rowSpacing: 10

                    AppPanel {
                        Layout.fillWidth: true
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text("label.data_root")
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 58
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            border.color: root.theme.border
                            Label {
                                anchors.fill: parent
                                anchors.margins: 9
                                text: root.bridge.maintenance.dataRoot
                                color: root.theme.textMuted
                                font.family: root.theme.monoFont
                                font.pixelSize: 10
                                verticalAlignment: Text.AlignVCenter
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                        AppButton {
                            text: root.bridge.text("action.open_folder")
                            onClicked: root.bridge.maintenance.openDataRoot()
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text("section.diagnostics")
                        }
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("settings.diagnostics.detail")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("runtime-inspect")
                                    ? root.bridge.text("task.state.running") : root.bridge.text("action.inspect")
                                kind: "primary"
                                enabled: !root.bridge.activity.busyKeys.includes("runtime-inspect")
                                onClicked: root.bridge.maintenance.inspectRuntime()
                            }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("diagnostics-export")
                                    ? root.bridge.text("task.state.running")
                                    : root.bridge.text("action.export_diagnostics")
                                enabled: !root.bridge.activity.busyKeys.includes("diagnostics-export")
                                onClicked: diagnosticDialog.open()
                            }
                        }
                        StatusPill {
                            visible: root.bridge.maintenance.diagnosticPath.length > 0
                            text: root.bridge.text("badge.exported")
                            tone: "success"
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.bridge.maintenance.diagnosticPath.length > 0
                            text: root.bridge.maintenance.diagnosticPath
                            color: root.theme.textDim
                            font.family: root.theme.monoFont
                            font.pixelSize: 9
                            elide: Text.ElideMiddle
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text("section.storage_archive")
                            badgeText: root.bridge.text("badge.manual_only")
                            badgeTone: "warning"
                        }
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("storage.archive.detail")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            FieldLabel { text: root.bridge.text("storage.archive.age") }
                            AppSpinBox {
                                id: archiveDays
                                from: 1
                                to: 3650
                                value: 30
                            }
                            Item { Layout.fillWidth: true }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("storage-archive")
                                    ? root.bridge.text("task.state.running") : root.bridge.text("action.archive")
                                kind: "primary"
                                enabled: !root.bridge.activity.busyKeys.includes("storage-archive")
                                onClicked: archiveDialog.open()
                            }
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.runtime_install")
                        badgeText: root.runtimeInspecting
                            ? root.bridge.text("task.state.running")
                            : root.bridge.maintenance.runtimeReady
                                ? root.bridge.text("runtime.ready")
                                : root.bridge.text("runtime.not_ready")
                        badgeTone: root.runtimeInspecting
                            ? "info"
                            : root.bridge.maintenance.runtimeReady ? "success" : "warning"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("runtime.install_detail")
                        color: root.theme.textMuted
                        font.family: root.theme.uiFont
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            objectName: "installRuntimeButton"
                            text: root.runtimeInspecting
                                ? root.bridge.text("task.state.running")
                                : root.bridge.activity.busyKeys.includes("runtime-install")
                                ? root.bridge.text("runtime.installing")
                                : root.bridge.maintenance.runtimeReady
                                    ? root.bridge.text("runtime.installed")
                                    : root.bridge.text("runtime.install")
                            kind: "primary"
                            enabled: !root.bridge.maintenance.runtimeReady
                                && !root.runtimeInspecting
                                && !root.bridge.activity.busyKeys.includes("runtime-install")
                            onClicked: root.bridge.maintenance.installRuntime()
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.bridge.activity.busyKeys.includes("runtime-install")
                            text: root.bridge.text("runtime.install_wait")
                            color: root.theme.warning
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 2 : 1
                        columnSpacing: 10
                        rowSpacing: 6
                        Label { text: root.bridge.text("runtime.device") + ": " + root.bridge.maintenance.runtimeInfo.device; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "Python: " + root.bridge.maintenance.runtimeInfo.python; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "RVC: " + root.bridge.maintenance.runtimeInfo.rvc_root; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "FFmpeg: " + root.bridge.maintenance.runtimeInfo.ffmpeg; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                    }
                    AppCheckBox {
                        text: root.bridge.text("runtime.show_details")
                        checked: root.showRuntimeDetails
                        onToggled: root.showRuntimeDetails = checked
                    }
                    AppScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 330
                        visible: root.showRuntimeDetails
                        clip: true
                        AppTextArea {
                            width: parent.width
                            text: root.bridge.maintenance.runtimeText.length > 2 ? root.bridge.maintenance.runtimeText : root.bridge.text("empty.runtime.detail")
                            readOnly: true
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text("section.background_service") }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("service.exit_behavior")
                        color: root.theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        text: root.bridge.text("service.stop")
                        kind: "danger"
                        enabled: !root.realtimeActive
                        onClicked: root.bridge.stopBackgroundService()
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.about")
                        badgeText: "v" + root.bridge.applicationVersion
                        badgeTone: "neutral"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("about.detail")
                        color: root.theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        text: root.bridge.text("about.project_page")
                        onClicked: root.bridge.openProjectPage()
                    }
                }

                Item { Layout.preferredHeight: 2 }
            }
        }
    }
}
