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
    property var readyModels: []
    property var speakers: []
    property var selectedSpeakers: []
    property var previewOutputs: []
    property var presets: []
    property var pendingPreset: null
    property real pendingAudioPosition: -1
    property bool resumeAudioAfterSwitch: false
    property bool playbackPending: false

    function contentMode(index) {
        return ["clean", "mixed", "singing"][index]
    }

    function applyPreset(preset) {
        if (!preset) return
        var values = preset.parameters
        pitchSlider.value = Number(values.pitch)
        f0Combo.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
        indexRateSlider.value = Number(values.index_rate)
        rmsMixSlider.value = Number(values.rms_mix_rate)
        protectSlider.value = Number(values.protect)
        modeCombo.currentIndex = ["clean", "mixed", "singing"].indexOf(values.content_mode)
    }

    onSpeakersChanged: root.selectedSpeakers = []

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
    title: root.bridge.text("field.input")
    nameFilters: [
        root.bridge.text("filter.media") + " (*.wav *.flac *.mp3 *.m4a *.aac *.mp4 *.mkv *.mov *.webm)"
    ]
    onAccepted: inputField.text = selectedFile
}
FileDialog {
    id: outputDialog
    title: root.bridge.text("field.output")
    fileMode: FileDialog.SaveFile
    nameFilters: [
        root.bridge.text("filter.audio") + " (*.wav *.flac *.mp3 *.m4a *.aac)",
        root.bridge.text("filter.video") + " (*.mp4 *.mkv *.mov *.webm)"
    ]
    onAccepted: outputField.text = selectedFile
}
Basic.Dialog {
    id: presetConfirmation
    modal: true
    anchors.centerIn: parent
    width: Math.min(420, root.width - 48)
    title: root.bridge.text("preset.reconfirm.title")
    standardButtons: Basic.Dialog.Ok | Basic.Dialog.Cancel
    contentItem: Label {
        text: root.bridge.text("preset.reconfirm.detail")
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
            title: root.bridge.text("nav.convert")
            StatusPill {
                text: root.readyModels.length + " " + root.bridge.text("label.models")
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
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.source")
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            FieldLabel { text: root.bridge.text("field.input") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField {
                                    id: inputField
                                    objectName: "inputField"
                                    Layout.fillWidth: true
                                    placeholderText: root.bridge.text("placeholder.input_media")
                                    onTextChanged: root.bridge.media.invalidateAnalysis()
                                }
                                AppButton { text: root.bridge.text("action.choose"); onClicked: inputDialog.open() }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            FieldLabel { text: root.bridge.text("field.output") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                AppTextField {
                                    id: outputField
                                    objectName: "outputField"
                                    Layout.fillWidth: true
                                    placeholderText: root.bridge.text("placeholder.output_media")
                                }
                                AppButton { text: root.bridge.text("action.choose"); onClicked: outputDialog.open() }
                            }
                        }
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.voice")
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width > 760 ? 3 : 2
                        columnSpacing: 9
                        rowSpacing: 7

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.model") }
                            AppComboBox {
                                id: modelCombo
                                objectName: "modelSelector"
                                Layout.fillWidth: true
                                model: root.readyModels
                                textRole: "localized_name"
                                valueRole: "id"
                                emptyText: root.bridge.text("empty.models.short")
                                enabled: root.readyModels.length > 0
                                onCurrentIndexChanged: {
                                    if (currentIndex < 0 || !root.readyModels[currentIndex]) return
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
                            FieldLabel { text: root.bridge.text("field.mode") }
                            AppComboBox {
                                id: modeCombo
                                Layout.fillWidth: true
                                model: [root.bridge.text("mode.clean"), root.bridge.text("mode.mixed"), root.bridge.text("mode.singing")]
                                onCurrentIndexChanged: root.bridge.media.invalidateAnalysis()
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.pitch") }
                            AppSlider {
                                id: pitchSlider
                                objectName: "conversionPitchSlider"
                                Layout.fillWidth: true
                                from: -24
                                to: 24
                                value: 9
                                stepSize: 1
                                showPositiveSign: true
                                accessibleName: root.bridge.text("field.pitch")
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.f0") }
                            AppComboBox { id: f0Combo; Layout.fillWidth: true; model: ["RMVPE", "FCPE", "PM"] }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.index_rate") }
                            AppSlider {
                                id: indexRateSlider
                                objectName: "conversionIndexRateSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: 0.72
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text("field.index_rate")
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.rms_mix") }
                            AppSlider {
                                id: rmsMixSlider
                                objectName: "conversionRmsMixSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: 0.25
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text("field.rms_mix")
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            FieldLabel { text: root.bridge.text("field.protect") }
                            AppSlider {
                                id: protectSlider
                                objectName: "conversionProtectSlider"
                                Layout.fillWidth: true
                                from: 0
                                to: 0.5
                                value: 0.33
                                stepSize: 0.01
                                decimals: 2
                                accessibleName: root.bridge.text("field.protect")
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.theme.border }

                    FieldLabel { text: root.bridge.text("field.preset") }
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
                            placeholderText: root.bridge.text("field.preset_name")
                        }
                        AppButton {
                            text: root.bridge.text("action.save_preset")
                            enabled: presetName.text.length > 0 && modelCombo.currentIndex >= 0
                            onClicked: root.bridge.media.savePreset(modelCombo.currentValue, presetName.text, pitchSlider.value, f0Combo.currentText.toLowerCase(), indexRateSlider.value, rmsMixSlider.value, protectSlider.value, root.contentMode(modeCombo.currentIndex))
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 0
                        spacing: 6
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("hint.preview")
                            color: root.theme.textDim
                            font.family: root.theme.uiFont
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AppButton {
                            text: root.bridge.activity.busyKeys.includes("analysis")
                                ? root.bridge.text("task.state.running") : root.bridge.text("action.analyze")
                            enabled: inputField.text.length > 0 && modeCombo.currentIndex !== 2
                                && !root.bridge.activity.busyKeys.includes("analysis")
                            onClicked: root.bridge.media.analyze(inputField.text, root.contentMode(modeCombo.currentIndex))
                        }
                        AppButton {
                            text: root.bridge.activity.busyKeys.includes("preview")
                                ? root.bridge.text("task.state.running") : root.bridge.text("action.preview")
                            enabled: inputField.text.length > 0 && modelCombo.currentIndex >= 0
                                && !root.bridge.activity.busyKeys.includes("preview")
                            onClicked: root.bridge.media.preview(inputField.text, modelCombo.currentValue, pitchSlider.value, f0Combo.currentText.toLowerCase(), indexRateSlider.value, rmsMixSlider.value, protectSlider.value, root.contentMode(modeCombo.currentIndex))
                        }
                        AppButton {
                            objectName: "convertButton"
                            text: root.bridge.activity.busyKeys.includes("conversion")
                                ? root.bridge.text("task.state.running") : root.bridge.text("action.convert")
                            kind: "primary"
                            enabled: inputField.text.length > 0 && outputField.text.length > 0
                                && modelCombo.currentIndex >= 0
                                && !root.bridge.activity.busyKeys.includes("conversion")
                            onClicked: root.bridge.media.convert(inputField.text, outputField.text, modelCombo.currentValue, pitchSlider.value, f0Combo.currentText.toLowerCase(), indexRateSlider.value, rmsMixSlider.value, protectSlider.value, root.contentMode(modeCombo.currentIndex), root.selectedSpeakers, overlapCombo.currentIndex === 0 ? "skip" : "convert")
                        }
                    }
                }

                AppPanel {
                    visible: root.speakers.length > 0
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.speakers")
                        badgeText: root.speakers.length + " " + root.bridge.text("label.speakers")
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
                                }
                            }
                        }
                        Repeater {
                            model: root.speakers
                            delegate: AppButton {
                                required property var modelData
                                compact: true
                                visible: !!modelData.sample_audio
                                text: modelData.id + " · " + root.bridge.text("action.listen")
                                onClicked: root.bridge.media.selectAudio(modelData.sample_audio)
                            }
                        }
                    }
                    AppComboBox {
                        id: overlapCombo
                        Layout.fillWidth: true
                        model: [root.bridge.text("overlap.skip"), root.bridge.text("overlap.convert")]
                        currentIndex: 1
                    }
                }

                AppPanel {
                    Layout.fillWidth: true
                    SectionHeader {
                        Layout.fillWidth: true
                        title: root.bridge.text("section.output")
                        badgeText: root.bridge.media.resultAudio.length > 0 ? root.bridge.text("badge.ready") : root.bridge.text("badge.waiting")
                        badgeTone: root.bridge.media.resultAudio.length > 0 ? "success" : "neutral"
                    }

                    MediaPlayer {
                        id: player
                        objectName: "resultPlayer"
                        source: root.bridge.media.resultAudio
                        audioOutput: AudioOutput {}
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
                        Layout.preferredHeight: 60
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
                                Accessible.name: player.playbackState === MediaPlayer.PlayingState ? root.bridge.text("action.pause") : root.bridge.text("action.play")
                                onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play()
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Label {
                                    text: root.bridge.media.resultAudio.length > 0 ? root.bridge.text("label.current_output") : root.bridge.text("status.no_audio")
                                    color: root.theme.text
                                    font.family: root.theme.uiFont
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: root.bridge.media.resultAudio.length > 0
                                    text: root.bridge.media.resultAudio
                                    color: root.theme.textDim
                                    font.family: root.theme.monoFont
                                    font.pixelSize: 10
                                    elide: Text.ElideMiddle
                                }
                            }
                        }
                    }

                    RowLayout {
                        visible: root.previewOutputs.length > 0
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: root.bridge.text("preview.compare")
                            color: root.theme.textMuted
                            font.family: root.theme.uiFont
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: root.previewOutputs
                            delegate: AppButton {
                                required property var modelData
                                compact: true
                                text: root.bridge.text("label.pitch") + " " + (modelData.parameters.pitch >= 0 ? "+" : "") + modelData.parameters.pitch
                                onClicked: {
                                    root.pendingAudioPosition = player.position
                                    root.resumeAudioAfterSwitch = player.playbackState === MediaPlayer.PlayingState
                                    root.bridge.media.selectAudio(modelData.output_path)
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
