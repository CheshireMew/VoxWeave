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
    property var allModels: []
    property var batches: []
    property string editingBatchId: ""
    property bool showArchived: false
    readonly property var visibleBatches: root.batches.filter(function(rule) {
        return root.showArchived || rule.state !== "archived"
    })
    readonly property int activeBatchCount: root.batches.filter(function(rule) {
        return rule.state !== "archived"
    }).length
    ListModel { id: batchVariantsModel }

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

    function modelName(modelId) {
        for (var index = 0; index < root.allModels.length; ++index)
            if (String(root.allModels[index].id) === String(modelId))
                return String(root.allModels[index].localized_name || modelId)
        return String(modelId)
    }

    function resetForm() {
        root.editingBatchId = ""
        batchInput.text = ""
        batchOutput.text = ""
        batchModel.currentIndex = root.models.length > 0 ? 0 : -1
        batchPresetName.text = "custom"
        batchMode.currentIndex = 0
        batchF0.currentIndex = 0
        batchPitch.value = 0
        batchIndexRate.value = 0.72
        batchRmsMix.value = 0.25
        batchProtect.value = 0.33
        watchCheck.checked = false
        recursiveCheck.checked = true
        preserveStructure.checked = true
        namingTemplate.text = "{stem}_{source_ext}_{model}_{preset}_{variant}_{hash}"
        collisionPolicy.currentIndex = 0
        outputFormat.currentIndex = 0
        includeGlobs.text = ""
        excludeGlobs.text = ""
        root.applyProcessingChain({})
        root.applyRecommendations()
        batchVariantsModel.clear()
        if (batchModel.currentIndex >= 0) batchVariantsModel.append({
            "name": "variant-1",
            "model_id": String(batchModel.currentValue),
            "extensions": "",
            "include_globs": "",
            "exclude_globs": ""
        })
    }

    function applyProcessingChain(chain) {
        var values = chain || ({})
        batchNoiseReduction.value = Number(values.noise_reduction_db || 0)
        batchDereverb.value = Number(values.dereverb_strength || 0) * 100
        batchHighpass.value = Number(values.highpass_hz || 0)
        batchLowEq.value = Number(values.low_eq_db || 0)
        batchPresenceEq.value = Number(values.presence_eq_db || 0)
        batchCompressor.checked = Boolean(values.compressor)
        batchDeesser.checked = Boolean(values.deesser)
        batchTrimSilence.checked = Boolean(values.trim_silence)
        batchTargetLoudness.checked = values.target_lufs !== null
            && values.target_lufs !== undefined
        batchTargetLufs.value = Number(values.target_lufs === null
            || values.target_lufs === undefined ? -16 : values.target_lufs)
        batchLimiter.value = Number(values.limiter_dbfs === null
            || values.limiter_dbfs === undefined ? -1 : values.limiter_dbfs)
    }

    function processingChain() {
        return {
            "noise_reduction_db": Number(batchNoiseReduction.value),
            "dereverb_strength": Number(batchDereverb.value) / 100.0,
            "highpass_hz": Math.round(Number(batchHighpass.value)),
            "low_eq_db": Number(batchLowEq.value),
            "presence_eq_db": Number(batchPresenceEq.value),
            "compressor": Boolean(batchCompressor.checked),
            "deesser": Boolean(batchDeesser.checked),
            "target_lufs": batchTargetLoudness.checked
                ? Number(batchTargetLufs.value) : null,
            "limiter_dbfs": Number(batchLimiter.value),
            "trim_silence": Boolean(batchTrimSilence.checked)
        }
    }

    function itemCountText(counts) {
        var values = counts || ({})
        return root.bridge.text(root.bridge.language, "batch.counts")
            .replace("{queued}", Number(values.queued || 0))
            .replace("{running}", Number(values.running || 0))
            .replace("{completed}", Number(values.completed || 0))
            .replace("{failed}", Number(values.failed || 0)
                + Number(values.cancelled || 0) + Number(values.interrupted || 0))
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
        recursiveCheck.checked = Boolean(rule.recursive)
        preserveStructure.checked = Boolean(rule.preserve_structure)
        namingTemplate.text = String(rule.naming_template
            || "{stem}_{source_ext}_{model}_{preset}_{hash}")
        collisionPolicy.currentIndex = ["skip", "version", "overwrite"].indexOf(
            rule.collision_policy || "skip")
        outputFormat.currentIndex = ["auto", "wav", "flac", "mp3"].indexOf(
            rule.output_format || "auto")
        includeGlobs.text = (rule.include_globs || []).join(", ")
        excludeGlobs.text = (rule.exclude_globs || []).join(", ")
        root.applyProcessingChain(values.processing_chain)
        batchVariantsModel.clear()
        var variants = rule.variants || []
        for (var variantIndex = 0; variantIndex < variants.length; ++variantIndex) {
            var variant = variants[variantIndex]
            batchVariantsModel.append({
                "name": String(variant.name || "variant-" + (variantIndex + 1)),
                "model_id": String(variant.model_id || rule.model_id),
                "extensions": (variant.extensions || []).join(", "),
                "include_globs": (variant.include_globs || []).join(", "),
                "exclude_globs": (variant.exclude_globs || []).join(", ")
            })
        }
        if (batchVariantsModel.count === 0) batchVariantsModel.append({
            "name": "variant-1", "model_id": String(rule.model_id),
            "extensions": "", "include_globs": "", "exclude_globs": ""
        })
        batchScroll.contentY = 0
    }

    function splitValues(value) {
        return String(value || "").split(",").map(function(item) {
            return item.trim()
        }).filter(function(item) { return item.length > 0 })
    }

    function variantPreset() {
        return {
            "pitch": batchPitch.value,
            "f0": ["rmvpe", "fcpe", "pm"][batchF0.currentIndex],
            "index_rate": batchIndexRate.value,
            "rms_mix_rate": batchRmsMix.value,
            "protect": batchProtect.value,
            "content_mode": ["clean", "mixed", "singing"][batchMode.currentIndex],
            "processing_chain": root.processingChain()
        }
    }

    function variantsPayload() {
        var result = []
        for (var index = 0; index < batchVariantsModel.count; ++index) {
            var item = batchVariantsModel.get(index)
            result.push({
                "name": String(item.name),
                "model": String(item.model_id),
                "preset_name": batchPresetName.text.length > 0
                    ? batchPresetName.text : "custom",
                "preset": root.variantPreset(),
                "output_format": ["auto", "wav", "flac", "mp3"][outputFormat.currentIndex],
                "extensions": root.splitValues(item.extensions),
                "include_globs": root.splitValues(item.include_globs),
                "exclude_globs": root.splitValues(item.exclude_globs)
            })
        }
        return result
    }

    function retryVariant(item, rule) {
        if (root.editingBatchId === String(rule.id)) return root.variantsPayload()[0]
        var stored = item.variant || (rule.variants || [])[0] || ({})
        return {
            "name": String(stored.name || item.variant_name || "retry"),
            "model": String(stored.model_id || rule.model_id),
            "preset_name": String(stored.preset_name || rule.preset_name || "custom"),
            "preset": stored.preset || rule.preset || ({}),
            "output_format": String(stored.output_format || rule.output_format || "auto"),
            "extensions": stored.extensions || [],
            "include_globs": stored.include_globs || [],
            "exclude_globs": stored.exclude_globs || []
        }
    }

    function saveRule() {
        root.bridge.batchRules.saveRule({
            "batch_id": root.editingBatchId,
            "input_root": batchInput.text,
            "output_root": batchOutput.text,
            "variants": root.variantsPayload(),
            "preset_name": batchPresetName.text.length > 0 ? batchPresetName.text : "custom",
            "recursive": recursiveCheck.checked,
            "watch": watchCheck.checked,
            "naming_template": namingTemplate.text,
            "preserve_structure": preserveStructure.checked,
            "collision_policy": ["skip", "version", "overwrite"][collisionPolicy.currentIndex],
            "output_format": ["auto", "wav", "flac", "mp3"][outputFormat.currentIndex],
            "include_globs": includeGlobs.text.split(",").map(function(value) {
                return value.trim()
            }).filter(function(value) { return value.length > 0 }),
            "exclude_globs": excludeGlobs.text.split(",").map(function(value) {
                return value.trim()
            }).filter(function(value) { return value.length > 0 }),
            "preset": root.variantPreset()
        })
    }

FolderDialog {
    id: inputFolderDialog
    onAccepted: batchInput.text = root.bridge.media.localPath(selectedFolder)
}
FolderDialog {
    id: outputFolderDialog
    onAccepted: batchOutput.text = root.bridge.media.localPath(selectedFolder)
}

    Connections {
        target: root.bridge.batchRules
        function onRuleSaved(_batchId) { root.resetForm() }
    }

    objectName: "batchPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.batch")
            StatusPill { text: root.activeBatchCount + " " + root.bridge.text(root.bridge.language, "label.batch_rules"); tone: root.activeBatchCount > 0 ? "info" : "neutral" }
            AppIconButton {
                glyph: "\uE72C"
                accessibleName: root.bridge.text(root.bridge.language, "action.refresh")
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
                        title: root.bridge.text(root.bridge.language, "section.batch_rule")
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 2 : 1
                        columnSpacing: 10
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.input_dir") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField { id: batchInput; Layout.fillWidth: true; placeholderText: root.bridge.text(root.bridge.language, "placeholder.input_dir") }
                                AppButton { text: root.bridge.text(root.bridge.language, "action.choose"); onClicked: inputFolderDialog.open() }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.output_dir") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField { id: batchOutput; Layout.fillWidth: true; placeholderText: root.bridge.text(root.bridge.language, "placeholder.output_dir") }
                                AppButton { text: root.bridge.text(root.bridge.language, "action.choose"); onClicked: outputFolderDialog.open() }
                            }
                        }
                    }

                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.batch_output_rules")
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 2 : 1
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.naming_template") }
                            AppTextField {
                                id: namingTemplate
                                Layout.fillWidth: true
                                text: "{stem}_{source_ext}_{model}_{preset}_{variant}_{hash}"
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.output_format") }
                            AppComboBox {
                                id: outputFormat
                                Layout.fillWidth: true
                                model: ["Auto", "WAV", "FLAC", "MP3"]
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.collision_policy") }
                            AppComboBox {
                                id: collisionPolicy
                                Layout.fillWidth: true
                                model: [
                                    root.bridge.text(root.bridge.language, "batch.collision.skip"),
                                    root.bridge.text(root.bridge.language, "batch.collision.version"),
                                    root.bridge.text(root.bridge.language, "batch.collision.overwrite")
                                ]
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.include_globs") }
                            AppTextField { id: includeGlobs; Layout.fillWidth: true; placeholderText: "*.wav, podcast/**/*.mp3" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.exclude_globs") }
                            AppTextField { id: excludeGlobs; Layout.fillWidth: true; placeholderText: "draft/**, *_old.wav" }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppCheckBox {
                                id: recursiveCheck
                                text: root.bridge.text(root.bridge.language, "batch.recursive")
                                checked: true
                            }
                            AppCheckBox {
                                id: preserveStructure
                                text: root.bridge.text(root.bridge.language, "batch.preserve_structure")
                                checked: true
                            }
                        }
                    }

                    FieldLabel { text: root.bridge.text(root.bridge.language, "field.model") }
                    AppComboBox {
                        id: batchModel
                        Layout.fillWidth: true
                        model: root.models
                        textRole: "localized_name"
                        valueRole: "id"
                        emptyText: root.bridge.text(root.bridge.language, "empty.models.short")
                        enabled: root.models.length > 0
                        onActivated: root.applyRecommendations()
                    }
                    AppButton {
                        Layout.fillWidth: true
                        text: "Add selected model as output variant"
                        enabled: batchModel.currentIndex >= 0
                        onClicked: batchVariantsModel.append({
                            "name": "variant-" + (batchVariantsModel.count + 1),
                            "model_id": String(batchModel.currentValue),
                            "extensions": "",
                            "include_globs": "",
                            "exclude_globs": ""
                        })
                    }
                    Repeater {
                        model: batchVariantsModel
                        delegate: AppPanel {
                            id: variantRow
                            required property int index
                            required property string name
                            required property string model_id
                            required property string extensions
                            required property string include_globs
                            required property string exclude_globs
                            Layout.fillWidth: true
                            GridLayout {
                                Layout.fillWidth: true
                                columns: width >= 760 ? 3 : 1
                                AppTextField {
                                    Layout.fillWidth: true
                                    text: variantRow.name
                                    placeholderText: "Variant name"
                                    onEditingFinished: batchVariantsModel.setProperty(
                                        variantRow.index, "name", text.trim())
                                }
                                AppComboBox {
                                    Layout.fillWidth: true
                                    model: root.models
                                    textRole: "localized_name"
                                    valueRole: "id"
                                    currentIndex: root.modelIndex(variantRow.model_id)
                                    onActivated: batchVariantsModel.setProperty(
                                        variantRow.index, "model_id", String(currentValue))
                                }
                                AppButton {
                                    text: "Remove variant"
                                    enabled: batchVariantsModel.count > 1
                                    onClicked: batchVariantsModel.remove(variantRow.index)
                                }
                                AppTextField {
                                    Layout.fillWidth: true
                                    text: variantRow.extensions
                                    placeholderText: "Extensions, e.g. .wav, .mp3"
                                    onEditingFinished: batchVariantsModel.setProperty(
                                        variantRow.index, "extensions", text)
                                }
                                AppTextField {
                                    Layout.fillWidth: true
                                    text: variantRow.include_globs
                                    placeholderText: "Include globs for this model"
                                    onEditingFinished: batchVariantsModel.setProperty(
                                        variantRow.index, "include_globs", text)
                                }
                                AppTextField {
                                    Layout.fillWidth: true
                                    text: variantRow.exclude_globs
                                    placeholderText: "Exclude globs for this model"
                                    onEditingFinished: batchVariantsModel.setProperty(
                                        variantRow.index, "exclude_globs", text)
                                }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 3 : 1
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.preset_name") }
                            AppTextField { id: batchPresetName; Layout.fillWidth: true; text: "custom" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.mode") }
                            AppComboBox {
                                id: batchMode
                                Layout.fillWidth: true
                                model: [root.bridge.text(root.bridge.language, "mode.clean"), root.bridge.text(root.bridge.language, "mode.mixed"), root.bridge.text(root.bridge.language, "mode.singing")]
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.f0") }
                            AppComboBox { id: batchF0; Layout.fillWidth: true; model: ["RMVPE", "FCPE", "PM"] }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.pitch") }
                            AppSlider { id: batchPitch; Layout.fillWidth: true; from: -36; to: 36; value: 0; stepSize: 1; showPositiveSign: true; accessibleName: root.bridge.text(root.bridge.language, "field.pitch") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.index_rate") }
                            AppSlider { id: batchIndexRate; Layout.fillWidth: true; from: 0; to: 1; value: 0.72; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text(root.bridge.language, "field.index_rate") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.rms_mix") }
                            AppSlider { id: batchRmsMix; Layout.fillWidth: true; from: 0; to: 1; value: 0.25; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text(root.bridge.language, "field.rms_mix") }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.protect") }
                            AppSlider { id: batchProtect; Layout.fillWidth: true; from: 0; to: 0.5; value: 0.33; stepSize: 0.01; decimals: 2; accessibleName: root.bridge.text(root.bridge.language, "field.protect") }
                        }
                    }

                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.processing_chain")
                    }
                    GridLayout {
                        objectName: "batchProcessingChain"
                        Layout.fillWidth: true
                        columns: width > 760 ? 4 : 2
                        columnSpacing: 9
                        rowSpacing: 7
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.noise_reduction") }
                            AppSlider { id: batchNoiseReduction; Layout.fillWidth: true; from: 0; to: 30; value: 0; stepSize: 1; suffix: " dB" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: "Dereverb" }
                            AppSlider { id: batchDereverb; Layout.fillWidth: true; from: 0; to: 100; value: 0; stepSize: 5; suffix: "%" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.highpass") }
                            AppSlider { id: batchHighpass; Layout.fillWidth: true; from: 0; to: 400; value: 0; stepSize: 10; suffix: " Hz" }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.low_eq") }
                            AppSlider { id: batchLowEq; Layout.fillWidth: true; from: -12; to: 12; value: 0; stepSize: 1; suffix: " dB"; showPositiveSign: true }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.presence_eq") }
                            AppSlider { id: batchPresenceEq; Layout.fillWidth: true; from: -12; to: 12; value: 0; stepSize: 1; suffix: " dB"; showPositiveSign: true }
                        }
                        AppCheckBox { id: batchCompressor; text: root.bridge.text(root.bridge.language, "field.compressor") }
                        AppCheckBox { id: batchDeesser; text: root.bridge.text(root.bridge.language, "field.deesser") }
                        AppCheckBox { id: batchTrimSilence; text: root.bridge.text(root.bridge.language, "field.trim_silence") }
                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.limiter") }
                            AppSlider { id: batchLimiter; Layout.fillWidth: true; from: -3; to: -0.1; value: -1; stepSize: 0.1; decimals: 1; suffix: " dBFS" }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppCheckBox { id: batchTargetLoudness; text: root.bridge.text(root.bridge.language, "field.target_loudness") }
                            AppSlider { id: batchTargetLufs; Layout.fillWidth: true; from: -24; to: -9; value: -16; stepSize: 1; suffix: " LUFS"; enabled: batchTargetLoudness.checked }
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
                                Label { text: root.bridge.text(root.bridge.language, "batch.watch"); color: root.theme.text; font.family: root.theme.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Label {
                                    Layout.fillWidth: true
                                    text: root.bridge.text(root.bridge.language, "batch.watch.detail")
                                    color: root.theme.textDim
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 11
                                    wrapMode: Text.Wrap
                                }
                            }
                            AppCheckBox { id: watchCheck; text: root.bridge.text(root.bridge.language, "action.enable") }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text(root.bridge.language, "hint.batch")
                            color: root.theme.textDim
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            text: root.editingBatchId.length > 0
                                ? root.bridge.text(root.bridge.language, "action.save_changes")
                                : root.bridge.text(root.bridge.language, "action.create_batch")
                            kind: "primary"
                            enabled: batchInput.text.length > 0 && batchOutput.text.length > 0 && batchModel.currentIndex >= 0
                            onClicked: root.saveRule()
                        }
                        AppButton {
                            visible: root.editingBatchId.length > 0
                            text: root.bridge.text(root.bridge.language, "action.cancel")
                            onClicked: root.resetForm()
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    visible: root.batches.length > 0 || root.bridge.batchRules.loading
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.batch_rules")
                    }
                    AppCheckBox {
                        text: root.bridge.text(root.bridge.language, "batch.show_archived")
                        checked: root.showArchived
                        onToggled: root.showArchived = checked
                    }
                    Repeater {
                        model: root.visibleBatches
                        delegate: Rectangle {
                            id: batchRule
                            required property var modelData
                            property var failedItems: (modelData.items || []).filter(function(item) {
                                return ["failed", "cancelled", "interrupted"].includes(item.state)
                            })
                            Layout.fillWidth: true
                            Layout.preferredHeight: 146 + batchRule.failedItems.length * 34
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
                                        text: root.modelName(batchRule.modelData.model_id) + " · " + batchRule.modelData.preset_name
                                        color: root.theme.text
                                        font.family: root.theme.uiFont
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    StatusPill {
                                        text: batchRule.modelData.state === "archived"
                                            ? root.bridge.text(root.bridge.language, "batch.state.archived")
                                            : batchRule.modelData.watch_enabled
                                            ? root.bridge.text(root.bridge.language, "batch.state.watching")
                                            : root.bridge.text(root.bridge.language, "batch.state.paused")
                                        tone: batchRule.modelData.state === "archived"
                                            ? "warning" : batchRule.modelData.watch_enabled ? "success" : "neutral"
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: root.bridge.text(root.bridge.language, "action.run_batch")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-run:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.run(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: root.bridge.text(root.bridge.language, "action.plan_batch")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-plan:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.plan(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: root.bridge.text(root.bridge.language, "action.edit")
                                        onClicked: root.editRule(batchRule.modelData)
                                    }
                                    AppButton {
                                        compact: true
                                        text: batchRule.modelData.state === "archived"
                                            ? root.bridge.text(root.bridge.language, "action.restore")
                                            : root.bridge.text(root.bridge.language, "action.archive_rule")
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
                                        text: root.bridge.text(root.bridge.language, "action.retry_failed")
                                        enabled: !root.bridge.activity.busyKeys.includes("batch-retry:" + batchRule.modelData.id)
                                        onClicked: root.bridge.batchRules.retry(batchRule.modelData.id)
                                    }
                                    AppButton {
                                        compact: true
                                        visible: batchRule.modelData.state !== "archived"
                                        text: batchRule.modelData.watch_enabled
                                            ? root.bridge.text(root.bridge.language, "action.pause_watch")
                                            : root.bridge.text(root.bridge.language, "action.resume_watch")
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
                                    text: root.bridge.text(root.bridge.language, "field.pitch") + " "
                                        + Number((batchRule.modelData.preset || {}).pitch || 0)
                                        + " · " + String((batchRule.modelData.preset || {}).f0 || "rmvpe").toUpperCase()
                                        + " · " + root.bridge.text(root.bridge.language, "field.index_rate") + " "
                                        + Number((batchRule.modelData.preset || {}).index_rate || 0.72).toFixed(2)
                                    color: root.theme.textMuted
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: root.itemCountText(batchRule.modelData.item_counts)
                                    color: root.theme.textMuted
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                                Repeater {
                                    model: batchRule.failedItems
                                    delegate: RowLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.source_path + " · "
                                                + (modelData.error || modelData.state)
                                            color: root.theme.danger
                                            font.pixelSize: 10
                                            elide: Text.ElideMiddle
                                        }
                                        AppButton {
                                            compact: true
                                            text: root.editingBatchId === String(batchRule.modelData.id)
                                                ? "Retry with edited settings" : "Retry item"
                                            onClicked: root.bridge.batchRules.retryItem(
                                                modelData.id,
                                                root.retryVariant(modelData, batchRule.modelData))
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: root.bridge.batchRules.loading
                        text: root.bridge.text(root.bridge.language, "batch.loading")
                        color: root.theme.textMuted
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !root.bridge.batchRules.loading
                            && root.batches.length > 0 && root.visibleBatches.length === 0
                        text: root.bridge.text(root.bridge.language, "batch.filtered_empty")
                        color: root.theme.textMuted
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                EmptyState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 150
                    visible: !root.bridge.batchRules.loading && root.batches.length === 0
                    title: root.bridge.text(root.bridge.language, "batch.empty.title")
                    detail: root.bridge.text(root.bridge.language, "batch.empty.detail")
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.batch_behavior")
                        badgeText: root.bridge.text(root.bridge.language, "badge.non_destructive")
                        badgeTone: "success"
                    }
                    Label {
                        Layout.fillWidth: true
                        text: root.bridge.text(root.bridge.language, "batch.behavior.detail")
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
