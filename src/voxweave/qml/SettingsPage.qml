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
    readonly property bool realtimeActive: ["starting", "running", "stopping"].indexOf(
        root.bridge.realtime.status.state
    ) >= 0
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
                        columns: 2
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
                        title: root.bridge.text("section.runtime")
                        badgeText: root.bridge.maintenance.runtimeText.length > 2 ? root.bridge.text("badge.loaded") : root.bridge.text("badge.waiting")
                        badgeTone: root.bridge.maintenance.runtimeText.length > 2 ? "info" : "neutral"
                    }
                    AppScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 330
                        clip: true
                        AppTextArea {
                            width: parent.width
                            text: root.bridge.maintenance.runtimeText.length > 2 ? root.bridge.maintenance.runtimeText : root.bridge.text("empty.runtime.detail")
                            readOnly: true
                        }
                    }
                }

                Item { Layout.preferredHeight: 2 }
            }
        }
    }
}
