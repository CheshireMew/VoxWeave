pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Controls.Basic as Basic
import QtMultimedia

Item {
    id: root
    required property var bridge
    required property var theme
    property bool pageActive: false
    property var readyModels: []
    property var speakers: []
    property var selectedSpeakers: []
    property var previewOutputs: []
    property var presets: []
    property var pendingPreset: null
    property real pendingAudioPosition: -1
    property bool resumeAudioAfterSwitch: false
    property bool playbackPending: false
    property bool outputAuto: true
    property string requestedModelId: ""
    readonly property var inputValidation: root.bridge.media.validateInput(inputField.text)
    readonly property var pathValidation: root.bridge.media.validateConversion(
        inputField.text, outputField.text
    )

    function selectRequestedModel() {
        if (!root.requestedModelId) return
        for (var index = 0; index < root.readyModels.length; ++index) {
            if (String(root.readyModels[index].id) === root.requestedModelId) {
                modelCombo.currentIndex = index
                return
            }
        }
    }

    function parametersEdited() {
        root.bridge.media.invalidateResults()
    }

    function formatDuration(milliseconds) {
        if (!isFinite(milliseconds) || milliseconds < 0) return "0:00"
        var seconds = Math.floor(milliseconds / 1000)
        var minutes = Math.floor(seconds / 60)
        return minutes + ":" + String(seconds % 60).padStart(2, "0")
    }

    function setInput(value) {
        inputField.text = value
        if (root.outputAuto || outputField.text.length === 0)
            outputField.text = root.bridge.media.suggestOutput(value)
        root.outputAuto = true
    }

    function contentMode(index) {
        return ["clean", "mixed", "singing"][index]
    }

    function applyPreset(preset) {
        if (!preset) return
        root.parametersEdited()
        var values = preset.parameters
        pitchSlider.value = Number(values.pitch)
        f0Combo.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
        indexRateSlider.value = Number(values.index_rate)
        rmsMixSlider.value = Number(values.rms_mix_rate)
        protectSlider.value = Number(values.protect)
        modeCombo.currentIndex = ["clean", "mixed", "singing"].indexOf(values.content_mode)
    }

    onSpeakersChanged: root.selectedSpeakers = []
    onRequestedModelIdChanged: root.selectRequestedModel()
    onReadyModelsChanged: root.selectRequestedModel()

    Connections {
        target: root.bridge.media
        function onPlaybackRequested() {
            root.playbackPending = true
            if (player.mediaStatus === MediaPlayer.LoadedMedia
                    || player.mediaStatus === MediaPlayer.BufferedMedia) {
                player.play()
                root.playbackPending = false
            }
        }
    }

FileDialog {
    id: inputDialog
    title: root.bridge.text(root.bridge.language, "field.input")
    nameFilters: [
        root.bridge.text(root.bridge.language, "filter.media") + " (" + root.bridge.mediaFileFilter + ")"
    ]
    onAccepted: root.setInput(root.bridge.media.localPath(selectedFile))
}
FileDialog {
    id: outputDialog
    title: root.bridge.text(root.bridge.language, "field.output")
    fileMode: FileDialog.SaveFile
    options: FileDialog.DontConfirmOverwrite
    nameFilters: [
        root.bridge.text(root.bridge.language, "filter.audio") + " (" + root.bridge.audioFileFilter + ")",
        root.bridge.text(root.bridge.language, "filter.video") + " (" + root.bridge.videoFileFilter + ")"
    ]
    onAccepted: {
        root.outputAuto = false
        outputField.text = root.bridge.media.localPath(selectedFile)
        root.parametersEdited()
    }
}
Basic.Dialog {
    id: presetConfirmation
    modal: true
    anchors.centerIn: parent
    width: Math.min(420, root.width - 48)
    height: 190
    title: root.bridge.text(root.bridge.language, "preset.reconfirm.title")
    standardButtons: Basic.Dialog.Ok | Basic.Dialog.Cancel
    contentItem: Label {
        text: root.bridge.text(root.bridge.language, "preset.reconfirm.detail")
        color: root.theme.text
        font.family: root.theme.uiFont
        wrapMode: Text.Wrap
    }
    onAccepted: {
        root.applyPreset(root.pendingPreset)
        root.pendingPreset = null
    }
    onRejected: {
        root.pendingPreset = null
        presetCombo.currentIndex = -1
    }
}

    objectName: "conversionPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.convert")
            StatusPill {
                text: root.readyModels.length + " " + root.bridge.text(root.bridge.language, "label.models")
                tone: root.readyModels.length > 0 ? "success" : "warning"
            }
        }

        AppScrollView {
            id: convertScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            ColumnLayout {
                width: convertScroll.availableWidth
                spacing: 10

                AppPanel {
                    id: sourcePanel
                    Layout.fillWidth: true
                    overlay: Component {
                        DropArea {
                            anchors.fill: parent
                            onDropped: function(drop) {
                                if (drop.urls && drop.urls.length > 0) {
                                    root.setInput(drop.urls[0])
                                    drop.acceptProposedAction()
                                }
                            }
                        }
                    }
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.source")
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 680 ? 2 : 1
                        columnSpacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.input") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField {
                                    id: inputField
                                    objectName: "inputField"
                                    Layout.fillWidth: true
                                    placeholderText: root.bridge.text(root.bridge.language, "placeholder.input_media")
                                    Accessible.name: root.bridge.text(root.bridge.language, "field.input")
                                    onTextChanged: {
                                        root.bridge.media.invalidateAnalysis()
                                        root.bridge.media.invalidateResults()
                                        if (root.outputAuto)
                                            outputField.text = root.bridge.media.suggestOutput(text)
                                    }
                                }
                                AppButton { text: root.bridge.text(root.bridge.language, "action.choose"); onClicked: inputDialog.open() }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.output") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField {
                                    id: outputField
                                    objectName: "outputField"
                                    Layout.fillWidth: true
                                    placeholderText: root.bridge.text(root.bridge.language, "placeholder.output_media")
                                    Accessible.name: root.bridge.text(root.bridge.language, "field.output")
                                    onTextEdited: {
                                        root.outputAuto = false
                                        root.parametersEdited()
                                    }
                                }
                                AppButton { text: root.bridge.text(root.bridge.language, "action.choose"); onClicked: outputDialog.open() }
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: inputField.text.length > 0 && !root.pathValidation.valid
                        text: root.bridge.text(root.bridge.language, "validation." + root.pathValidation.code)
                        color: root.theme.warning
                        font.family: root.theme.uiFont
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        visible: inputField.text.length > 0 && !root.pathValidation.valid
                            && String(root.pathValidation.suggestion || "").length > 0
                        text: root.bridge.text(root.bridge.language, "action.use_suggested_output")
                        onClicked: {
                            root.outputAuto = true
                            outputField.text = String(root.pathValidation.suggestion)
                        }
                    }

                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.voice")
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width > 760 ? 3 : 2
                        columnSpacing: 9
                        rowSpacing: 7

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.model") }
                            AppComboBox {
                                id: modelCombo
                                objectName: "modelSelector"
                                Layout.fillWidth: true
                                model: root.readyModels
                                textRole: "localized_name"
                                valueRole: "id"
                                emptyText: root.bridge.text(root.bridge.language, "empty.models.short")
                                enabled: root.readyModels.length > 0
                                onCurrentIndexChanged: {
                                    if (currentIndex < 0 || !root.readyModels[currentIndex]) return
                                    root.parametersEdited()
                                    var values = root.readyModels[currentIndex].recommended
                                    pitchSlider.value = Number(values.pitch)
                                    f0Combo.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
                                    indexRateSlider.value = Number(values.index_rate)
                                    rmsMixSlider.value = Number(values.rms_mix_rate)
                                    protectSlider.value = Number(values.protect)
                                    root.bridge.media.refreshPresets(currentValue)
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.mode") }
                            AppComboBox {
                                id: modeCombo
                                Layout.fillWidth: true
                                model: [root.bridge.text(root.bridge.language, "mode.clean"), root.bridge.text(root.bridge.language, "mode.mixed"), root.bridge.text(root.bridge.language, "mode.singing")]
                                onCurrentIndexChanged: {
                                    root.bridge.media.invalidateAnalysis()
                                    root.parametersEdited()
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.pitch") }
                            AppSlider {
                                id: pitchSlider
                                objectName: "conversionPitchSlider"
                                Layout.fillWidth: true
                                from: -24
                                to: 24
                                value: 9
                                stepSize: 1
                                showPositiveSign: true
                                accessibleName: root.bridge.text(root.bridge.language, "field.pitch")
                                onUserEdited: root.parametersEdited()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.f0") }
                            AppComboBox {
                                id: f0Combo
                                Layout.fillWidth: true
                                model: ["RMVPE", "FCPE", "PM"]
                                onActivated: root.parametersEdited()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.index_rate") }
                            AppSlider {
                                id: indexRateSlider
                                objectName: "conversionIndexRateSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: 0.72
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text(root.bridge.language, "field.index_rate")
                                onUserEdited: root.parametersEdited()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.rms_mix") }
                            AppSlider {
                                id: rmsMixSlider
                                objectName: "conversionRmsMixSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: 0.25
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text(root.bridge.language, "field.rms_mix")
                                onUserEdited: root.parametersEdited()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text(root.bridge.language, "field.protect") }
                            AppSlider {
                                id: protectSlider
                                objectName: "conversionProtectSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 0.5
                                value: 0.33
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text(root.bridge.language, "field.protect")
                                onUserEdited: root.parametersEdited()
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    FieldLabel { text: root.bridge.text(root.bridge.language, "field.preset") }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        AppComboBox {
                            id: presetCombo
                            Layout.fillWidth: true
                            model: root.presets
                            textRole: "name"
                            onActivated: {
                                var preset = root.presets[currentIndex]
                                if (preset.needs_reconfirmation) {
                                    root.pendingPreset = preset
                                    presetConfirmation.open()
                                } else {
                                    root.applyPreset(preset)
                                }
                            }
                        }
                        AppTextField {
                            id: presetName
                            Layout.preferredWidth: 200
                            placeholderText: root.bridge.text(root.bridge.language, "field.preset_name")
                        }
                        AppButton {
                            text: root.bridge.text(root.bridge.language, "action.save_preset")
                            enabled: presetName.text.length > 0 && modelCombo.currentIndex >= 0
                            onClicked: root.bridge.media.savePreset({
                                "model": modelCombo.currentValue,
                                "name": presetName.text,
                                "pitch": pitchSlider.value,
                                "f0": f0Combo.currentText.toLowerCase(),
                                "index_rate": indexRateSlider.value,
                                "rms_mix_rate": rmsMixSlider.value,
                                "protect": protectSlider.value,
                                "content_mode": root.contentMode(modeCombo.currentIndex)
                            })
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Label {
                            text: root.bridge.text(root.bridge.language, "preview.variants")
                            color: root.theme.textDim
                            font.pixelSize: 11
                        }
                        AppSpinBox {
                            id: previewCount
                            from: 1
                            to: 4
                            value: 2
                            Accessible.name: root.bridge.text(root.bridge.language, "preview.variants")
                            onValueModified: root.parametersEdited()
                        }
                        Label {
                            text: root.bridge.text(root.bridge.language, "preview.pitch_step")
                            color: root.theme.textDim
                            font.pixelSize: 11
                        }
                        AppSpinBox {
                            id: previewPitchStep
                            from: -12
                            to: 12
                            value: 3
                            Accessible.name: root.bridge.text(root.bridge.language, "preview.pitch_step")
                            onValueModified: root.parametersEdited()
                        }
                        Item { Layout.fillWidth: true }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 0
                        columns: width >= 700 ? 4 : 1
                        rowSpacing: 6
                        columnSpacing: 6
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text(root.bridge.language, "hint.preview")
                            color: root.theme.textDim
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            Layout.fillWidth: true
                            text: root.bridge.activity.busyKeys.includes("analysis")
                                ? root.bridge.text(root.bridge.language, "task.state.running") : root.bridge.text(root.bridge.language, "action.analyze")
                            enabled: inputField.text.length > 0 && modeCombo.currentIndex !== 2
                                && root.inputValidation.valid
                                && !root.bridge.activity.busyKeys.includes("analysis")
                            onClicked: root.bridge.media.analyze(inputField.text, root.contentMode(modeCombo.currentIndex))
                        }
                        AppButton {
                            Layout.fillWidth: true
                            text: root.bridge.activity.busyKeys.includes("preview")
                                ? root.bridge.text(root.bridge.language, "task.state.running") : root.bridge.text(root.bridge.language, "action.preview")
                            enabled: root.inputValidation.valid && modelCombo.currentIndex >= 0
                                && !root.bridge.activity.busyKeys.includes("preview")
                            onClicked: root.bridge.media.previewWithOptions({
                                "input": inputField.text,
                                "model": modelCombo.currentValue,
                                "pitch": pitchSlider.value,
                                "f0": f0Combo.currentText.toLowerCase(),
                                "index_rate": indexRateSlider.value,
                                "rms_mix_rate": rmsMixSlider.value,
                                "protect": protectSlider.value,
                                "content_mode": root.contentMode(modeCombo.currentIndex),
                                "variant_count": previewCount.value,
                                "pitch_step": previewPitchStep.value
                            })
                        }
                        AppButton {
                            objectName: "convertButton"
                            Layout.fillWidth: true
                            text: root.bridge.activity.busyKeys.includes("conversion")
                                ? root.bridge.text(root.bridge.language, "task.state.running") : root.bridge.text(root.bridge.language, "action.convert")
                            kind: "primary"
                            enabled: root.pathValidation.valid
                                && modelCombo.currentIndex >= 0
                                && !root.bridge.activity.busyKeys.includes("conversion")
                            onClicked: root.bridge.media.convert({
                                "input": inputField.text,
                                "output": outputField.text,
                                "model": modelCombo.currentValue,
                                "pitch": pitchSlider.value,
                                "f0": f0Combo.currentText.toLowerCase(),
                                "index_rate": indexRateSlider.value,
                                "rms_mix_rate": rmsMixSlider.value,
                                "protect": protectSlider.value,
                                "content_mode": root.contentMode(modeCombo.currentIndex),
                                "selected_speakers": root.selectedSpeakers,
                                "overlap_policy": overlapCombo.currentIndex === 0 ? "skip" : "convert"
                            })
                        }
                    }
                }

                AppPanel {
                    visible: root.speakers.length > 0
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.speakers")
                        badgeText: root.speakers.length + " " + root.bridge.text(root.bridge.language, "label.speakers")
                        badgeTone: "info"
                    }
                    Flow {
                        Layout.fillWidth: true
                        Layout.preferredHeight: childrenRect.height
                        spacing: 6
                        Repeater {
                            model: root.speakers
                            delegate: AppCheckBox {
                                required property var modelData
                                text: modelData.id + " · " + Number(modelData.duration_seconds).toFixed(1) + "s"
                                onToggled: {
                                    var values = root.selectedSpeakers.slice()
                                    var position = values.indexOf(modelData.id)
                                    if (checked && position < 0) values.push(modelData.id)
                                    if (!checked && position >= 0) values.splice(position, 1)
                                    root.selectedSpeakers = values
                                    root.parametersEdited()
                                }
                            }
                        }
                        Repeater {
                            model: root.speakers
                            delegate: AppButton {
                                required property var modelData
                                compact: true
                                visible: !!modelData.sample_audio
                                text: modelData.id + " · " + root.bridge.text(root.bridge.language, "action.listen")
                                onClicked: root.bridge.media.selectAudio(modelData.sample_audio, true)
                            }
                        }
                    }
                    AppComboBox {
                        id: overlapCombo
                        Layout.fillWidth: true
                        model: [root.bridge.text(root.bridge.language, "overlap.skip"), root.bridge.text(root.bridge.language, "overlap.convert")]
                        currentIndex: 1
                        onActivated: root.parametersEdited()
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text(root.bridge.language, "section.output")
                        badgeText: root.bridge.media.resultPath.length > 0 ? root.bridge.text(root.bridge.language, "badge.ready") : root.bridge.text(root.bridge.language, "badge.waiting")
                        badgeTone: root.bridge.media.resultPath.length > 0 ? "success" : "neutral"
                    }

                    MediaPlayer {
                        id: player
                        objectName: "resultPlayer"
                        source: root.pageActive ? root.bridge.media.resultAudio : ""
                        audioOutput: AudioOutput {
                            id: audioOutput
                            volume: volumeSlider.value
                        }
                        onMediaStatusChanged: {
                            if ((mediaStatus === MediaPlayer.LoadedMedia
                                    || mediaStatus === MediaPlayer.BufferedMedia)
                                    && root.pendingAudioPosition >= 0) {
                                setPosition(root.pendingAudioPosition)
                                root.pendingAudioPosition = -1
                                if (root.resumeAudioAfterSwitch) play()
                            }
                            if ((mediaStatus === MediaPlayer.LoadedMedia
                                    || mediaStatus === MediaPlayer.BufferedMedia)
                                    && root.playbackPending) {
                                play()
                                root.playbackPending = false
                            }
                            if (mediaStatus === MediaPlayer.InvalidMedia) {
                                root.playbackPending = false
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: root.bridge.media.resultAudio.length > 0 ? 104 : 60
                        radius: root.theme.radiusMedium
                        color: root.theme.field
                        border.color: root.theme.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 9

                            AppButton {
                                square: true
                                text: player.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                                kind: root.bridge.media.resultAudio.length > 0 ? "primary" : "secondary"
                                enabled: root.bridge.media.resultAudio.length > 0
                                Accessible.name: player.playbackState === MediaPlayer.PlayingState ? root.bridge.text(root.bridge.language, "action.pause") : root.bridge.text(root.bridge.language, "action.play")
                                onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play()
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Label {
                                    text: root.bridge.media.resultAudio.length > 0 ? root.bridge.text(root.bridge.language, "label.current_output") : root.bridge.text(root.bridge.language, "status.no_audio")
                                    color: root.theme.text
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: root.bridge.media.resultAudio.length > 0
                                    text: root.bridge.media.resultAudioPath
                                    color: root.theme.textDim
                                    font.family: root.theme.monoFont
                                    font.pixelSize: 10
                                    elide: Text.ElideMiddle
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: root.bridge.media.resultAudio.length > 0
                                    spacing: 6
                                    Label {
                                        text: root.formatDuration(player.position)
                                        color: root.theme.textDim
                                        font.family: root.theme.monoFont
                                        font.pixelSize: 10
                                    }
                                    Basic.Slider {
                                        Layout.fillWidth: true
                                        from: 0
                                        to: Math.max(1, player.duration)
                                        value: player.position
                                        enabled: player.seekable
                                        Accessible.name: root.bridge.text(root.bridge.language, "player.position")
                                        onMoved: player.setPosition(value)
                                    }
                                    Label {
                                        text: root.formatDuration(player.duration)
                                        color: root.theme.textDim
                                        font.family: root.theme.monoFont
                                        font.pixelSize: 10
                                    }
                                    Label {
                                        text: root.bridge.text(root.bridge.language, "player.volume")
                                        color: root.theme.textDim
                                        font.pixelSize: 10
                                    }
                                    Basic.Slider {
                                        id: volumeSlider
                                        Layout.preferredWidth: 72
                                        from: 0
                                        to: 1
                                        value: 0.8
                                        Accessible.name: root.bridge.text(root.bridge.language, "player.volume")
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.bridge.media.resultPath.length > 0
                        spacing: 6
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.media.resultPath
                            color: root.theme.textDim
                            font.family: root.theme.monoFont
                            font.pixelSize: 10
                            elide: Text.ElideMiddle
                        }
                        AppButton {
                            compact: true
                            text: root.bridge.text(root.bridge.language, "action.open_result")
                            onClicked: root.bridge.media.openResult()
                        }
                        AppButton {
                            compact: true
                            text: root.bridge.text(root.bridge.language, "action.open_folder")
                            onClicked: root.bridge.media.openResultFolder()
                        }
                    }

                    RowLayout {
                        visible: root.previewOutputs.length > 0
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text(root.bridge.language, "preview.compare")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: root.previewOutputs
                            delegate: AppButton {
                                required property var modelData
                                compact: true
                                text: root.bridge.text(root.bridge.language, "label.pitch") + " " + (modelData.parameters.pitch >= 0 ? "+" : "") + modelData.parameters.pitch
                                onClicked: {
                                    root.pendingAudioPosition = player.position
                                    root.resumeAudioAfterSwitch = player.playbackState === MediaPlayer.PlayingState
                                    root.bridge.media.selectAudio(modelData.output_path, true)
                                }
                            }
                        }
                    }
                }

                Item { Layout.preferredHeight: 2 }
            }
        }
    }
}
