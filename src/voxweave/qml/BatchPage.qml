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
    property string editingBatchId: ""
    property bool showArchived: false
    readonly property var visibleBatches: root.batches.filter(function(rule) {
        return root.showArchived || rule.state !== "archived"
    })

    function modelIndex(modelId) {
        for (var index = 0; index < batchModel.count; ++index)
            if (String(batchModel.valueAt(index)) === String(modelId)) return index
        return -1
    }

    function applyRecommendations() {
        if (batchModel.currentIndex < 0 || !root.models[batchModel.currentIndex]) return
        var values = root.models[batchModel.currentIndex].recommended || ({})
        if (values.pitch !== undefined) batchPitch.value = Number(values.pitch)
        if (values.f0 !== undefined)
            batchF0.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
        if (values.index_rate !== undefined) batchIndexRate.value = Number(values.index_rate)
        if (values.rms_mix_rate !== undefined) batchRmsMix.value = Number(values.rms_mix_rate)
        if (values.protect !== undefined) batchProtect.value = Number(values.protect)
    }

    function editRule(rule) {
        root.editingBatchId = String(rule.id)
        batchInput.text = rule.input_root
        batchOutput.text = rule.output_root
        batchModel.currentIndex = root.modelIndex(rule.model_id)
        batchPresetName.text = rule.preset_name || "custom"
        var values = rule.preset || ({})
        batchPitch.value = Number(values.pitch === undefined ? 0 : values.pitch)
        batchF0.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0 || "rmvpe")
        batchIndexRate.value = Number(values.index_rate === undefined ? 0.72 : values.index_rate)
        batchRmsMix.value = Number(values.rms_mix_rate === undefined ? 0.25 : values.rms_mix_rate)
        batchProtect.value = Number(values.protect === undefined ? 0.33 : values.protect)
        batchMode.currentIndex = ["clean", "mixed", "singing"].indexOf(values.content_mode || "clean")
        watchCheck.checked = Boolean(rule.watch_enabled)
        batchScroll.contentY = 0
    }

    function saveRule() {
        root.bridge.batchRules.saveRule({
            "batch_id": root.editingBatchId,
            "input_root": batchInput.text,
            "output_root": batchOutput.text,
            "model": batchModel.currentValue,
            "preset_name": batchPresetName.text.length > 0 ? batchPresetName.text : "custom",
            "recursive": true,
            "watch": watchCheck.checked,
            "preset": {
                "pitch": batchPitch.value,
                "f0": ["rmvpe", "fcpe", "pm"][batchF0.currentIndex],
                "index_rate": batchIndexRate.value,
                "rms_mix_rate": batchRmsMix.value,
                "protect": batchProtect.value,
                "content_mode": ["clean", "mixed", "singing"][batchMode.currentIndex]
            }
        })
        root.editingBatchId = ""
    }

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
                        columns: width >= 680 ? 2 : 1
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
                        onActivated: root.applyRecommendations()
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 3 : 1
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.preset_name") }
                            AppTextField { id: batchPresetName; Layout.fillWidth: true; text: "custom" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.mode") }
                            AppComboBox {
                                id: batchMode
                                Layout.fillWidth: true
                                model: [root.bridge.text("mode.clean"), root.bridge.text("mode.mixed"), root.bridge.text("mode.singing")]
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.f0") }
                            AppComboBox { id: batchF0; Layout.fillWidth: true; model: ["RMVPE", "FCPE", "PM"] }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.pitch") }
                            AppSlider { id: batchPitch; Layout.fillWidth: true; from: -36; to: 36; value: 0; stepSize: 1; showPositiveSign: true; accessibleName: root.bridge.text("field.pitch") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.index_rate") }
                            AppSlider { id: batchIndexRate; Layout.fillWidth: true; from: 0; to: 1; value: 0.72; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text("field.index_rate") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.rms_mix") }
                            AppSlider { id: batchRmsMix; Layout.fillWidth: true; from: 0; to: 1; value: 0.25; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text("field.rms_mix") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.protect") }
                            AppSlider { id: batchProtect; Layout.fillWidth: true; from: 0; to: 0.5; value: 0.33; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text("field.protect") }
                        }
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
                            text: root.editingBatchId.length > 0
                                ? root.bridge.text("action.save_changes")
                                : root.bridge.text("action.create_batch")
                            kind: "primary"
                            enabled: batchInput.text.length > 0 && batchOutput.text.length > 0 && batchModel.currentIndex >= 0
                            onClicked: root.saveRule()
                        }
                        AppButton {
                            visible: root.editingBatchId.length > 0
                            text: root.bridge.text("action.cancel")
                            onClicked: root.editingBatchId = ""
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
                    AppCheckBox {
                        text: root.bridge.text("batch.show_archived")
                        checked: root.showArchived
                        onToggled: root.showArchived = checked
                    }
                    Repeater {
                        model: root.visibleBatches
                        delegate: Rectangle {
                            id: batchRule
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 126
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
                                        text: batchRule.modelData.state === "archived"
                                            ? root.bridge.text("batch.state.archived")
                                            : batchRule.modelData.watch_enabled
                                            ? root.bridge.text("batch.state.watching")
                                            : root.bridge.text("batch.state.paused")
                                        tone: batchRule.modelData.state === "archived"
                                            ? "warning" : batchRule.modelData.watch_enabled ? "success" : "neutral"
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: root.bridge.text("action.run_batch")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-run:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.run(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: root.bridge.text("action.edit")
                                        onClicked: root.editRule(batchRule.modelData)
                                    }
                                    AppButton {
                                        compact: true
                                        text: batchRule.modelData.state === "archived"
                                            ? root.bridge.text("action.restore")
                                            : root.bridge.text("action.archive_rule")
                                        onClicked: root.bridge.batchRules.setArchived(
                                            batchRule.modelData.id,
                                            batchRule.modelData.state !== "archived"
                                        )
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                            && Number(batchRule.modelData.item_counts.failed || 0)
                                            + Number(batchRule.modelData.item_counts.cancelled || 0)
                                            + Number(batchRule.modelData.item_counts.interrupted || 0) > 0
                                        text: root.bridge.text("action.retry_failed")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-retry:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.retry(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
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
                                Label {
                                    Layout.fillWidth: true
                                    text: root.bridge.text("field.pitch") + " "
                                        + Number((batchRule.modelData.preset || {}).pitch || 0)
                                        + " · " + String((batchRule.modelData.preset || {}).f0 || "rmvpe").toUpperCase()
                                        + " · " + root.bridge.text("field.index_rate") + " "
                                        + Number((batchRule.modelData.preset || {}).index_rate || 0.72).toFixed(2)
                                    color: root.theme.textMuted
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
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
