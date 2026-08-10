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
    property url pendingArchiveRoot

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
