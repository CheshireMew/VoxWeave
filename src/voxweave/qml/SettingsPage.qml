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
    title: root.bridge.text(root.bridge.language, "storage.archive.confirm_title")
    standardButtons: Basic.Dialog.Ok | Basic.Dialog.Cancel
    contentItem: Label {
        text: root.bridge.text(root.bridge.language, "storage.archive.confirm_detail")
        color: root.theme.text
        font.family: root.theme.uiFont
        wrapMode: Text.Wrap
    }
    onAccepted: {
        var states = []
        if (archiveCompleted.checked) states.push("completed")
        if (archiveFailed.checked) states.push("failed")
        if (archiveCancelled.checked) states.push("cancelled")
        if (archiveInterrupted.checked) states.push("interrupted")
        root.bridge.maintenance.archiveArtifactStates(
            root.pendingArchiveRoot, archiveDays.value, states)
    }
}

    objectName: "settingsPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.settings")
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
                        title: root.bridge.text(root.bridge.language, "section.audio_devices")
                    }

                    FieldLabel { text: root.bridge.text(root.bridge.language, "field.audio_host") }
                    RowLayout {
                        Layout.fillWidth: true
                        AppComboBox {
                            id: audioHostApi
                            objectName: "settingsAudioHostApi"
                            Layout.fillWidth: true
                            model: root.devicePayload.hostapis || []
                            textRole: "name"
                            valueRole: "id"
                            emptyText: root.bridge.text(root.bridge.language, "audio.no_devices")
                            enabled: !root.realtimeActive && count > 0
                            onActivated: Qt.callLater(root.selectDefaultAudioRoute)
                        }
                        AppButton {
                            text: root.bridge.text(root.bridge.language, "action.refresh")
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
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.input_device") }
                            AppComboBox {
                                id: audioInputDevice
                                objectName: "settingsAudioInputDevice"
                                Layout.fillWidth: true
                                model: root.inputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text(root.bridge.language, "audio.no_input")
                                enabled: !root.realtimeActive && count > 0
                                onActivated: root.saveCurrentAudioRoute()
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.output_device") }
                            AppComboBox {
                                id: audioOutputDevice
                                objectName: "settingsAudioOutputDevice"
                                Layout.fillWidth: true
                                model: root.outputDevices
                                textRole: "name"
                                valueRole: "id"
                                emptyText: root.bridge.text(root.bridge.language, "audio.no_output")
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
                                ? root.bridge.text(root.bridge.language, "audio.test.running")
                                : root.bridge.text(root.bridge.language, "audio.test.input")
                            enabled: !root.realtimeActive && !root.bridge.realtime.audioTesting
                                && audioInputDevice.currentIndex >= 0
                            onClicked: root.bridge.realtime.testAudioDevice(
                                "input", Number(audioInputDevice.currentValue)
                            )
                        }
                        AppButton {
                            text: root.bridge.realtime.audioTesting
                                && root.bridge.realtime.audioTest.mode === "output"
                                ? root.bridge.text(root.bridge.language, "audio.test.running")
                                : root.bridge.text(root.bridge.language, "audio.test.output")
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
                                ? root.bridge.text(root.bridge.language, "audio.test.completed")
                                : root.bridge.text(root.bridge.language, "audio.test.failed")
                            tone: root.bridge.realtime.audioTest.state === "completed"
                                ? "success" : "danger"
                        }
                        Label {
                            visible: root.bridge.realtime.audioTest.mode === "input"
                                && root.bridge.realtime.audioTest.state === "completed"
                            text: root.bridge.text(root.bridge.language, "audio.test.level") + " "
                                + Math.round(Number(root.bridge.realtime.audioTest.peak || 0) * 100) + "%"
                            color: root.theme.textMuted
                            font.pixelSize: 11
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "audio.headphones_hint")
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
                            title: root.bridge.text(root.bridge.language, "label.data_root")
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
                            text: root.bridge.text(root.bridge.language, "action.open_folder")
                            onClicked: root.bridge.maintenance.openDataRoot()
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language, "section.diagnostics")
                        }
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text(root.bridge.language, "settings.diagnostics.detail")
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
                                    ? root.bridge.text(root.bridge.language, "task.state.running") : root.bridge.text(root.bridge.language, "action.inspect")
                                kind: "primary"
                                enabled: !root.bridge.activity.busyKeys.includes("runtime-inspect")
                                onClicked: root.bridge.maintenance.inspectRuntime()
                            }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("diagnostics-export")
                                    ? root.bridge.text(root.bridge.language, "task.state.running")
                                    : root.bridge.text(root.bridge.language, "action.export_diagnostics")
                                enabled: !root.bridge.activity.busyKeys.includes("diagnostics-export")
                                onClicked: diagnosticDialog.open()
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 10
                            AppCheckBox { id: archiveCompleted; text: "Completed results"; checked: true }
                            AppCheckBox { id: archiveFailed; text: "Failed runs"; checked: true }
                            AppCheckBox { id: archiveCancelled; text: "Cancelled runs"; checked: true }
                            AppCheckBox { id: archiveInterrupted; text: "Interrupted runs"; checked: true }
                        }
                        StatusPill {
                            visible: root.bridge.maintenance.diagnosticPath.length > 0
                            text: root.bridge.text(root.bridge.language, "badge.exported")
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
                            title: root.bridge.text(root.bridge.language, "section.storage_archive")
                            badgeText: root.bridge.text(root.bridge.language, "badge.manual_only")
                            badgeTone: "warning"
                        }
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text(root.bridge.language, "storage.archive.detail")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.bridge.maintenance.storage.categories !== undefined
                            text: "Results: "
                                + Number((root.bridge.maintenance.storage.categories.results || {}).bytes || 0)
                                + " bytes · Intermediates: "
                                + Number((root.bridge.maintenance.storage.categories.intermediates || {}).bytes || 0)
                                + " bytes · Failed runs: "
                                + Number((root.bridge.maintenance.storage.categories.failed_runs || {}).bytes || 0)
                                + " bytes"
                            color: root.theme.textMuted
                            wrapMode: Text.Wrap
                        }
                        FieldLabel { text: "Restore archived task IDs" }
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField {
                                id: restoreTaskIds
                                Layout.fillWidth: true
                                placeholderText: "task-id-1, task-id-2"
                            }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("storage-restore")
                                    ? "Restoring…" : "Restore"
                                enabled: restoreTaskIds.text.trim().length > 0
                                    && !root.bridge.activity.busyKeys.includes("storage-restore")
                                onClicked: root.bridge.maintenance.restoreArtifacts(
                                    restoreTaskIds.text)
                            }
                        }
                        FieldLabel { text: "Move VoxWeave data root" }
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField {
                                id: migrationTarget
                                objectName: "storageMigrationTarget"
                                Layout.fillWidth: true
                                placeholderText: "D:\\VoxWeaveData-New (must not exist yet)"
                            }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes(
                                    "storage-migration-plan") ? "Planning…" : "Plan migration"
                                enabled: migrationTarget.text.trim().length > 0
                                    && !root.bridge.activity.busyKeys.includes(
                                        "storage-migration-plan")
                                onClicked: root.bridge.maintenance.planStorageMigration(
                                    migrationTarget.text.trim())
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: Boolean(root.bridge.maintenance.storage.migration_plan)
                            text: root.bridge.maintenance.storage.migration_plan
                                ? "Files: "
                                    + root.bridge.maintenance.storage.migration_plan.file_count
                                    + " · Bytes: "
                                    + root.bridge.maintenance.storage.migration_plan.total_bytes
                                    + " · Conflicts: "
                                    + (root.bridge.maintenance.storage.migration_plan.conflicts || []).join("; ")
                                : ""
                            color: (root.bridge.maintenance.storage.migration_plan
                                && (root.bridge.maintenance.storage.migration_plan.conflicts || []).length > 0)
                                ? root.theme.danger : root.theme.textMuted
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            visible: Boolean(root.bridge.maintenance.storage.migration_plan)
                            text: "Verify, copy, switch data root, and restart"
                            kind: "primary"
                            enabled: root.bridge.maintenance.storage.migration_plan
                                && (root.bridge.maintenance.storage.migration_plan.conflicts || []).length === 0
                            onClicked: root.bridge.maintenance.prepareStorageMigration()
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            FieldLabel { text: root.bridge.text(root.bridge.language, "storage.archive.age") }
                            AppSpinBox {
                                id: archiveDays
                                from: 1
                                to: 3650
                                value: 30
                            }
                            Item { Layout.fillWidth: true }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("storage-archive")
                                    ? root.bridge.text(root.bridge.language, "task.state.running") : root.bridge.text(root.bridge.language, "action.archive")
                                kind: "primary"
                                enabled: !root.bridge.activity.busyKeys.includes("storage-archive")
                                onClicked: archiveDialog.open()
                            }
                            AppButton {
                                text: root.bridge.activity.busyKeys.includes("storage-inspect")
                                    ? root.bridge.text(root.bridge.language, "task.state.running")
                                    : root.bridge.text(root.bridge.language, "action.inspect_storage")
                                enabled: !root.bridge.activity.busyKeys.includes("storage-inspect")
                                onClicked: root.bridge.maintenance.inspectStorage(archiveDays.value)
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.bridge.maintenance.storage.total_bytes !== undefined
                            text: root.bridge.text(root.bridge.language, "storage.summary")
                                .arg((Number(root.bridge.maintenance.storage.total_bytes) / 1073741824).toFixed(2))
                                .arg(Number(root.bridge.maintenance.storage.reclaimable_task_count || 0))
                                .arg((Number(root.bridge.maintenance.storage.reclaimable_bytes || 0) / 1073741824).toFixed(2))
                            color: root.theme.textMuted
                            wrapMode: Text.Wrap
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.updates")
                        badgeText: root.bridge.maintenance.updateInfo.update_available
                            ? root.bridge.text(root.bridge.language, "update.available")
                            : root.bridge.text(root.bridge.language, "update.current")
                        badgeTone: root.bridge.maintenance.updateInfo.update_available
                            ? "success" : "neutral"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.maintenance.updateInfo.latest_version
                            ? root.bridge.text(root.bridge.language, "update.version")
                                .arg(root.bridge.maintenance.updateInfo.current_version)
                                .arg(root.bridge.maintenance.updateInfo.latest_version)
                            : root.bridge.text(root.bridge.language, "update.detail")
                        color: root.theme.textMuted
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            text: root.bridge.activity.busyKeys.includes("update-check")
                                ? root.bridge.text(root.bridge.language, "task.state.running")
                                : root.bridge.text(root.bridge.language, "action.check_updates")
                            enabled: !root.bridge.activity.busyKeys.includes("update-check")
                            onClicked: root.bridge.maintenance.checkForUpdates()
                        }
                        AppButton {
                            visible: Boolean(root.bridge.maintenance.updateInfo.update_available)
                                && !root.bridge.maintenance.updateInfo.downloaded_path
                            text: root.bridge.activity.busyKeys.includes("update-download")
                                ? root.bridge.text(root.bridge.language, "task.state.running")
                                : root.bridge.text(root.bridge.language, "action.download_update")
                            enabled: !root.bridge.activity.busyKeys.includes("update-download")
                            onClicked: root.bridge.maintenance.downloadUpdate()
                        }
                        AppButton {
                            visible: Boolean(root.bridge.maintenance.updateInfo.downloaded_path)
                                && !root.bridge.maintenance.updateInfo.install_path
                            text: root.bridge.activity.busyKeys.includes("update-install")
                                ? "Installing…" : "Install side by side"
                            enabled: !root.bridge.activity.busyKeys.includes("update-install")
                            onClicked: root.bridge.maintenance.installUpdate()
                        }
                        AppButton {
                            visible: root.bridge.maintenance.updateInfo.state === "installed"
                                || root.bridge.maintenance.updateInfo.state === "rolled_back"
                            text: "Activate and restart"
                            kind: "primary"
                            onClicked: root.bridge.maintenance.activateUpdate()
                        }
                        AppButton {
                            text: "Roll back to installed version"
                            onClicked: root.bridge.maintenance.rollbackUpdate()
                        }
                        AppButton {
                            visible: Boolean(root.bridge.maintenance.updateInfo.release_url)
                            text: root.bridge.maintenance.updateInfo.downloaded_path
                                ? root.bridge.text(root.bridge.language, "action.open_download")
                                : root.bridge.text(root.bridge.language, "action.open_release")
                            onClicked: root.bridge.maintenance.openUpdate()
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.runtime_install")
                        badgeText: root.runtimeInspecting
                            ? root.bridge.text(root.bridge.language, "task.state.running")
                            : root.bridge.maintenance.runtimeReady
                                ? root.bridge.text(root.bridge.language, "runtime.ready")
                                : root.bridge.text(root.bridge.language, "runtime.not_ready")
                        badgeTone: root.runtimeInspecting
                            ? "info"
                            : root.bridge.maintenance.runtimeReady ? "success" : "warning"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "runtime.install_detail")
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
                                ? root.bridge.text(root.bridge.language, "task.state.running")
                                : root.bridge.activity.busyKeys.includes("runtime-install")
                                ? root.bridge.text(root.bridge.language, "runtime.installing")
                                : root.bridge.maintenance.runtimeReady
                                    ? root.bridge.text(root.bridge.language, "runtime.installed")
                                    : root.bridge.text(root.bridge.language, "runtime.install")
                            kind: "primary"
                            enabled: !root.bridge.maintenance.runtimeReady
                                && !root.runtimeInspecting
                                && !root.bridge.activity.busyKeys.includes("runtime-install")
                            onClicked: root.bridge.maintenance.installRuntime()
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: root.bridge.activity.busyKeys.includes("runtime-install")
                            text: root.bridge.text(root.bridge.language, "runtime.install_wait")
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
                        Label { text: root.bridge.text(root.bridge.language, "runtime.device") + ": " + root.bridge.maintenance.runtimeInfo.device; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "Python: " + root.bridge.maintenance.runtimeInfo.python; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "RVC: " + root.bridge.maintenance.runtimeInfo.rvc_root; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: "FFmpeg: " + root.bridge.maintenance.runtimeInfo.ffmpeg; color: root.theme.textMuted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                    }
                    AppCheckBox {
                        text: root.bridge.text(root.bridge.language, "runtime.show_details")
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
                            text: root.bridge.maintenance.runtimeText.length > 2 ? root.bridge.maintenance.runtimeText : root.bridge.text(root.bridge.language, "empty.runtime.detail")
                            readOnly: true
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader { Layout.fillWidth: true; title: root.bridge.text(root.bridge.language, "section.background_service") }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "service.exit_behavior")
                        color: root.theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        text: root.bridge.text(root.bridge.language, "service.stop")
                        kind: "danger"
                        enabled: !root.realtimeActive
                        onClicked: root.bridge.stopBackgroundService()
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.about")
                        badgeText: "v" + root.bridge.applicationVersion
                        badgeTone: "neutral"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "about.detail")
                        color: root.theme.textMuted
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        text: root.bridge.text(root.bridge.language, "about.project_page")
                        onClicked: root.bridge.openProjectPage()
                    }
                }

                Item { Layout.preferredHeight: 2 }
            }
        }
    }
}
