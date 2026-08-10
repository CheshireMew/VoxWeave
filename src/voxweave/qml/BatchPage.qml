pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: root
    required property var bridge
    required property var theme
    property var models: []
    property var batches: []

FolderDialog {
    id: inputFolderDialog
    onAccepted: batchInput.text = selectedFolder
}
FolderDialog {
    id: outputFolderDialog
    onAccepted: batchOutput.text = selectedFolder
}

    objectName: "batchPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.batch")
            StatusPill { text: root.batches.length + " " + root.bridge.text("label.batch_rules"); tone: root.batches.length > 0 ? "info" : "neutral" }
            AppIconButton {
                glyph: "\uE72C"
                accessibleName: root.bridge.text("action.refresh")
                onClicked: root.bridge.batchRules.refresh()
            }
        }

        AppScrollView {
            id: batchScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: batchScroll.availableWidth
                spacing: 10

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.batch_rule")
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.input_dir") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField { id: batchInput; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.input_dir") }
                                AppButton { text: root.bridge.text("action.choose"); onClicked: inputFolderDialog.open() }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.output_dir") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField { id: batchOutput; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.output_dir") }
                                AppButton { text: root.bridge.text("action.choose"); onClicked: outputFolderDialog.open() }
                            }
                        }
                    }

                    FieldLabel { text: root.bridge.text("field.model") }
                    AppComboBox {
                        id: batchModel
                        Layout.fillWidth: true
                        model: root.models
                        textRole: "localized_name"
                        valueRole: "id"
                        emptyText: root.bridge.text("empty.models.short")
                        enabled: root.models.length > 0
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: width < 600 ? 72 : 56
                        radius: root.theme.radiusMedium
                        color: root.theme.field
                        border.color: root.theme.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 9
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Label { text: root.bridge.text("batch.watch"); color: root.theme.text; font.family: root.theme.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Label {
                                    Layout.fillWidth: true
                                    text: root.bridge.text("batch.watch.detail")
                                    color: root.theme.textDim
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 11
                                    wrapMode: Text.Wrap
                                }
                            }
                            AppCheckBox { id: watchCheck; text: root.bridge.text("action.enable") }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("hint.batch")
                            color: root.theme.textDim
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            text: root.bridge.text("action.create_batch")
                            kind: "primary"
                            enabled: batchInput.text.length > 0 && batchOutput.text.length > 0 && batchModel.currentIndex >= 0
                            onClicked: root.bridge.batchRules.create(batchInput.text, batchOutput.text, batchModel.currentValue, watchCheck.checked)
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    visible: root.batches.length > 0
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.batch_rules")
                    }
                    Repeater {
                        model: root.batches
                        delegate: Rectangle {
                            id: batchRule
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 92
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            border.color: root.theme.border

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: batchRule.modelData.model_id + " · " + batchRule.modelData.preset_name
                                        color: root.theme.text
                                        font.family: root.theme.uiFont
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    StatusPill {
                                        text: batchRule.modelData.watch_enabled
                                            ? root.bridge.text("batch.state.watching")
                                            : root.bridge.text("batch.state.paused")
                                        tone: batchRule.modelData.watch_enabled ? "success" : "neutral"
                                    }
                                    AppButton {
                                        compact: true
                                        text: root.bridge.text("action.run_batch")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-run:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.run(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: Number(batchRule.modelData.item_counts.failed || 0)
                                            + Number(batchRule.modelData.item_counts.cancelled || 0)
                                            + Number(batchRule.modelData.item_counts.interrupted || 0) > 0
                                        text: root.bridge.text("action.retry_failed")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-retry:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.retry(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        text: batchRule.modelData.watch_enabled
                                            ? root.bridge.text("action.pause_watch")
                                            : root.bridge.text("action.resume_watch")
                                        onClicked: root.bridge.batchRules.setWatch(
                                            batchRule.modelData.id, !batchRule.modelData.watch_enabled
                                        )
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: batchRule.modelData.input_root + "  →  " + batchRule.modelData.output_root
                                    color: root.theme.textDim
                                    font.family: root.theme.monoFont
                                    font.pixelSize: 9
                                    elide: Text.ElideMiddle
                                }
                            }
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.batch_behavior")
                        badgeText: root.bridge.text("badge.non_destructive")
                        badgeTone: "success"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text("batch.behavior.detail")
                        color: root.theme.textMuted
                        font.family: root.theme.uiFont
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                        lineHeight: 1.35
                    }
                }

                Item { Layout.fillHeight: true; Layout.minimumHeight: 2 }
            }
        }
    }
}
