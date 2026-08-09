import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia

ApplicationWindow {
    id: root
    width: 1280
    height: 800
    visible: true
    title: bridge.text("app.title")
    color: "#10131a"

    property var models: []
    property var tasks: []
    property var speakers: []
    property var selectedSpeakers: []
    property var previewOutputs: []
    property var presets: []
    property real pendingAudioPosition: -1
    property bool resumeAudioAfterSwitch: false
    property int currentPage: 0

    Connections {
        target: bridge
        function onModelsChanged() { root.models = JSON.parse(bridge.modelsJson) }
        function onTasksChanged() { root.tasks = JSON.parse(bridge.tasksJson) }
        function onSpeakersChanged() {
            root.speakers = JSON.parse(bridge.speakersJson)
            root.selectedSpeakers = []
        }
        function onPreviewOutputsChanged() { root.previewOutputs = JSON.parse(bridge.previewOutputsJson) }
        function onPresetsChanged() { root.presets = JSON.parse(bridge.presetsJson) }
    }

    FileDialog {
        id: inputDialog
        title: bridge.text("field.input")
        onAccepted: inputField.text = selectedFile
    }
    FileDialog {
        id: outputDialog
        title: bridge.text("field.output")
        fileMode: FileDialog.SaveFile
        onAccepted: outputField.text = selectedFile
    }
    FolderDialog {
        id: inputFolderDialog
        onAccepted: batchInput.text = selectedFolder
    }
    FolderDialog {
        id: outputFolderDialog
        onAccepted: batchOutput.text = selectedFolder
    }
    FolderDialog {
        id: modelRootDialog
        onAccepted: bridge.scanModelRoot(selectedFolder)
    }
    FileDialog {
        id: localModelDialog
        nameFilters: ["RVC model (*.pth)"]
        onAccepted: localModelPath.text = selectedFile
    }
    FileDialog {
        id: localIndexDialog
        nameFilters: ["RVC index (*.index)"]
        onAccepted: localIndexPath.text = selectedFile
    }
    FileDialog {
        id: diagnosticDialog
        fileMode: FileDialog.SaveFile
        currentFolder: bridge.dataRootUrl
        nameFilters: ["JSON (*.json)"]
        onAccepted: bridge.exportDiagnostics(selectedFile)
    }

    component NavButton: Button {
        Layout.fillWidth: true
        flat: true
        font.pixelSize: 15
        leftPadding: 18
        contentItem: Text {
            text: parent.text
            color: parent.highlighted ? "#10131a" : "#c8d0dd"
            font: parent.font
            horizontalAlignment: Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
        }
    }

    component SectionTitle: Label {
        font.pixelSize: 26
        font.bold: true
        color: "#f3f5fa"
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 230
            Layout.minimumWidth: 230
            Layout.fillHeight: true
            color: "#171b25"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8
                Label { text: "VoxWeave"; color: "#8ec5ff"; font.pixelSize: 24; font.bold: true; Layout.bottomMargin: 24 }
                Repeater {
                    model: ["nav.convert", "nav.models", "nav.batch", "nav.tasks", "nav.settings"]
                    delegate: NavButton {
                        required property int index
                        required property string modelData
                        text: bridge.text(modelData)
                        highlighted: root.currentPage === index
                        onClicked: root.currentPage = index
                    }
                }
                Item { Layout.fillHeight: true }
                Label { text: bridge.status; color: "#9aa5b8"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                ComboBox {
                    model: ["简体中文", "English"]
                    currentIndex: bridge.language === "zh-CN" ? 0 : 1
                    onActivated: bridge.language = currentIndex === 0 ? "zh-CN" : "en"
                    Layout.fillWidth: true
                }
            }
        }

        StackLayout {
            currentIndex: root.currentPage
            Layout.fillWidth: true
            Layout.fillHeight: true

            ScrollView {
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: 16
                    anchors.margins: 30
                    SectionTitle { text: bridge.text("nav.convert") }
                    Label { text: bridge.text("field.input"); color: "#aab3c2" }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField { id: inputField; Layout.fillWidth: true; placeholderText: "WAV / FLAC / MP3 / MP4 / MKV" }
                        Button { text: bridge.text("action.choose"); onClicked: inputDialog.open() }
                    }
                    Label { text: bridge.text("field.output"); color: "#aab3c2" }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField { id: outputField; Layout.fillWidth: true; placeholderText: "output.wav / output.mp4" }
                        Button { text: bridge.text("action.choose"); onClicked: outputDialog.open() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: bridge.text("field.model"); color: "#aab3c2" }
                            ComboBox {
                                id: modelCombo
                                Layout.fillWidth: true
                                model: root.models
                                textRole: "display_name"
                                valueRole: "id"
                                onCurrentIndexChanged: {
                                    if (currentIndex < 0 || !root.models[currentIndex]) return
                                    var values = root.models[currentIndex].recommended
                                    pitchBox.value = values.pitch
                                    f0Combo.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
                                    indexRate.text = String(values.index_rate)
                                    rmsMix.text = String(values.rms_mix_rate)
                                    protect.text = String(values.protect)
                                    bridge.refreshPresets(currentValue)
                                }
                            }
                        }
                        ColumnLayout {
                            Label { text: bridge.text("field.pitch"); color: "#aab3c2" }
                            SpinBox { id: pitchBox; from: -24; to: 24; value: 9 }
                        }
                        ColumnLayout {
                            Label { text: bridge.text("field.mode"); color: "#aab3c2" }
                            ComboBox { id: modeCombo; model: [bridge.text("mode.clean"), bridge.text("mode.mixed"), bridge.text("mode.singing")] }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Label { text: bridge.text("field.f0"); color: "#aab3c2" }
                            ComboBox { id: f0Combo; model: ["RMVPE", "FCPE", "PM"] }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: bridge.text("field.index_rate"); color: "#aab3c2" }
                            TextField {
                                id: indexRate
                                text: "0.72"
                                validator: DoubleValidator { bottom: 0; top: 1; decimals: 2 }
                                Layout.fillWidth: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: bridge.text("field.rms_mix"); color: "#aab3c2" }
                            TextField {
                                id: rmsMix
                                text: "0.25"
                                validator: DoubleValidator { bottom: 0; top: 1; decimals: 2 }
                                Layout.fillWidth: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: bridge.text("field.protect"); color: "#aab3c2" }
                            TextField {
                                id: protect
                                text: "0.33"
                                validator: DoubleValidator { bottom: 0; top: 0.5; decimals: 2 }
                                Layout.fillWidth: true
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: bridge.text("field.preset"); color: "#aab3c2" }
                        ComboBox {
                            id: presetCombo
                            Layout.fillWidth: true
                            model: root.presets
                            textRole: "name"
                            onActivated: {
                                var values = root.presets[currentIndex].parameters
                                pitchBox.value = values.pitch
                                f0Combo.currentIndex = ["rmvpe", "fcpe", "pm"].indexOf(values.f0)
                                indexRate.text = String(values.index_rate)
                                rmsMix.text = String(values.rms_mix_rate)
                                protect.text = String(values.protect)
                                modeCombo.currentIndex = ["clean", "mixed", "singing"].indexOf(values.content_mode)
                            }
                        }
                        TextField { id: presetName; placeholderText: bridge.text("field.preset_name"); Layout.preferredWidth: 190 }
                        Button {
                            text: bridge.text("action.save_preset")
                            enabled: presetName.text.length > 0 && modelCombo.currentIndex >= 0
                            onClicked: bridge.savePreset(modelCombo.currentValue, presetName.text, pitchBox.value, f0Combo.currentText.toLowerCase(), parseFloat(indexRate.text), parseFloat(rmsMix.text), parseFloat(protect.text), ["clean", "mixed", "singing"][modeCombo.currentIndex])
                        }
                    }
                    RowLayout {
                        Button {
                            text: bridge.text("action.analyze")
                            enabled: inputField.text.length > 0 && modeCombo.currentIndex !== 2
                            onClicked: bridge.analyze(inputField.text, ["clean", "mixed", "singing"][modeCombo.currentIndex])
                        }
                        Button {
                            text: bridge.text("action.preview")
                            enabled: inputField.text.length > 0 && modelCombo.currentIndex >= 0
                            onClicked: bridge.preview(inputField.text, modelCombo.currentValue, pitchBox.value, f0Combo.currentText.toLowerCase(), parseFloat(indexRate.text), parseFloat(rmsMix.text), parseFloat(protect.text), ["clean", "mixed", "singing"][modeCombo.currentIndex])
                        }
                        Button {
                            text: bridge.text("action.convert")
                            highlighted: true
                            enabled: inputField.text.length > 0 && outputField.text.length > 0 && modelCombo.currentIndex >= 0
                            onClicked: bridge.convert(inputField.text, outputField.text, modelCombo.currentValue, pitchBox.value, f0Combo.currentText.toLowerCase(), parseFloat(indexRate.text), parseFloat(rmsMix.text), parseFloat(protect.text), ["clean", "mixed", "singing"][modeCombo.currentIndex], JSON.stringify(root.selectedSpeakers), overlapCombo.currentIndex === 0 ? "skip" : "convert")
                        }
                    }
                    Label {
                        visible: root.speakers.length > 0
                        text: bridge.text("field.speakers")
                        color: "#aab3c2"
                    }
                    Flow {
                        Layout.fillWidth: true
                        visible: root.speakers.length > 0
                        spacing: 12
                        Repeater {
                            model: root.speakers
                            delegate: CheckBox {
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
                            delegate: Button {
                                required property var modelData
                                visible: !!modelData.sample_audio
                                text: modelData.id + " " + bridge.text("action.listen")
                                onClicked: bridge.selectAudio(modelData.sample_audio)
                            }
                        }
                    }
                    ComboBox {
                        id: overlapCombo
                        visible: root.speakers.length > 0
                        model: [bridge.text("overlap.skip"), bridge.text("overlap.convert")]
                        currentIndex: 1
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 110
                        radius: 10
                        color: "#1b2130"
                        MediaPlayer {
                            id: player
                            objectName: "resultPlayer"
                            source: bridge.resultAudio
                            audioOutput: AudioOutput {}
                            onMediaStatusChanged: {
                                if (mediaStatus === MediaPlayer.LoadedMedia && root.pendingAudioPosition >= 0) {
                                    setPosition(root.pendingAudioPosition)
                                    root.pendingAudioPosition = -1
                                    if (root.resumeAudioAfterSwitch) play()
                                }
                            }
                        }
                        RowLayout {
                            anchors.centerIn: parent
                            Button { text: player.playbackState === MediaPlayer.PlayingState ? "❚❚" : "▶"; enabled: bridge.resultAudio.length > 0; onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play() }
                            Label { text: bridge.resultAudio.length > 0 ? bridge.resultAudio : bridge.text("status.no_audio"); color: "#c8d0dd"; elide: Text.ElideMiddle; Layout.preferredWidth: 700 }
                        }
                    }
                    Label { visible: root.previewOutputs.length > 0; text: bridge.text("preview.compare"); color: "#aab3c2" }
                    Flow {
                        Layout.fillWidth: true
                        visible: root.previewOutputs.length > 0
                        spacing: 10
                        Repeater {
                            model: root.previewOutputs
                            delegate: Button {
                                required property var modelData
                                text: "Pitch " + (modelData.parameters.pitch >= 0 ? "+" : "") + modelData.parameters.pitch
                                onClicked: {
                                    root.pendingAudioPosition = player.position
                                    root.resumeAudioAfterSwitch = player.playbackState === MediaPlayer.PlayingState
                                    bridge.selectAudio(modelData.output_path)
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                spacing: 12
                anchors.margins: 30
                SectionTitle { text: bridge.text("nav.models") }
                RowLayout {
                    Button { text: bridge.text("action.scan"); onClicked: bridge.scanModels() }
                    Button { text: bridge.text("action.scan_external"); onClicked: modelRootDialog.open() }
                    Button { text: bridge.text("action.refresh"); onClicked: bridge.refreshModels() }
                    Label { text: root.models.length + " models"; color: "#9aa5b8" }
                }
                GroupBox {
                    title: bridge.text("models.local_import")
                    Layout.fillWidth: true
                    ColumnLayout {
                        anchors.fill: parent
                        RowLayout {
                            Layout.fillWidth: true
                            TextField { id: localModelPath; Layout.fillWidth: true; placeholderText: "model.pth" }
                            Button { text: bridge.text("action.choose"); onClicked: localModelDialog.open() }
                            TextField { id: localIndexPath; Layout.fillWidth: true; placeholderText: "optional model.index" }
                            Button { text: bridge.text("action.choose"); onClicked: localIndexDialog.open() }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField { id: localModelId; Layout.fillWidth: true; placeholderText: "stable.id (optional)" }
                            TextField { id: localDisplayName; Layout.fillWidth: true; placeholderText: "Display name" }
                            TextField { id: localLicense; Layout.fillWidth: true; placeholderText: "SPDX license (optional)" }
                            TextField { id: localSource; Layout.fillWidth: true; placeholderText: "https:// source (optional)" }
                            Button {
                                text: bridge.text("action.import")
                                enabled: localModelPath.text.length > 0
                                onClicked: bridge.importLocalModel(localModelPath.text, localIndexPath.text, localModelId.text, localDisplayName.text, localLicense.text, localSource.text)
                            }
                        }
                    }
                }
                GroupBox {
                    title: bridge.text("models.url_import")
                    Layout.fillWidth: true
                    ColumnLayout {
                        anchors.fill: parent
                        RowLayout {
                            Layout.fillWidth: true
                            TextField { id: urlModel; Layout.fillWidth: true; placeholderText: "https://…/model.pth" }
                            TextField { id: urlSource; Layout.fillWidth: true; placeholderText: "Source page (optional)" }
                            TextField { id: urlSize; Layout.preferredWidth: 150; placeholderText: "bytes"; validator: IntValidator { bottom: 1 } }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField { id: urlModelId; Layout.fillWidth: true; placeholderText: "stable.id" }
                            TextField { id: urlDisplayName; Layout.fillWidth: true; placeholderText: "Display name" }
                            TextField { id: urlLicense; Layout.fillWidth: true; placeholderText: "SPDX license" }
                            TextField { id: urlSha; Layout.fillWidth: true; placeholderText: "SHA-256" }
                            Button {
                                text: bridge.text("action.import")
                                enabled: urlModel.text.length > 0 && urlModelId.text.length > 0 && urlDisplayName.text.length > 0 && urlLicense.text.length > 0 && urlSha.text.length === 64 && urlSize.text.length > 0
                                onClicked: bridge.importUrlModel(urlModel.text, urlModelId.text, urlDisplayName.text, urlLicense.text, urlSha.text, parseInt(urlSize.text), urlSource.text)
                            }
                        }
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: root.models
                    spacing: 8
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width
                        height: 92
                        radius: 8
                        color: "#1b2130"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            ColumnLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.display_name; color: "white"; font.pixelSize: 17; font.bold: true }
                                Label { text: modelData.id + "  ·  " + modelData.rvc_version + "  ·  " + modelData.sample_rate + " Hz"; color: "#9aa5b8" }
                                Label { text: modelData.model_sha256; color: "#6f7b8e"; font.family: "Consolas"; elide: Text.ElideMiddle; Layout.fillWidth: true }
                            }
                            Label { text: modelData.license_spdx || bridge.text("models.license_unknown"); color: modelData.license_spdx ? "#7bd88f" : "#e6b86a" }
                        }
                    }
                }
            }

            ColumnLayout {
                spacing: 14
                anchors.margins: 30
                SectionTitle { text: bridge.text("nav.batch") }
                Label { text: bridge.text("field.input_dir"); color: "#aab3c2" }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: batchInput; Layout.fillWidth: true }
                    Button { text: bridge.text("action.choose"); onClicked: inputFolderDialog.open() }
                }
                Label { text: bridge.text("field.output_dir"); color: "#aab3c2" }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: batchOutput; Layout.fillWidth: true }
                    Button { text: bridge.text("action.choose"); onClicked: outputFolderDialog.open() }
                }
                ComboBox { id: batchModel; Layout.fillWidth: true; model: root.models; textRole: "display_name"; valueRole: "id" }
                CheckBox { id: watchCheck; text: bridge.text("batch.watch") }
                Button { text: bridge.text("action.create_batch"); highlighted: true; onClicked: bridge.createBatch(batchInput.text, batchOutput.text, batchModel.currentValue, watchCheck.checked) }
                Item { Layout.fillHeight: true }
            }

            ColumnLayout {
                spacing: 12
                anchors.margins: 30
                SectionTitle { text: bridge.text("nav.tasks") }
                Button { text: bridge.text("action.refresh"); onClicked: bridge.refreshTasks() }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: root.tasks
                    spacing: 6
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width
                        height: 76
                        color: "#1b2130"
                        radius: 8
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            ColumnLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.operation; color: "white"; font.bold: true }
                                Label { text: modelData.id; color: "#7f899a"; font.family: "Consolas" }
                            }
                            ProgressBar { value: modelData.progress; Layout.preferredWidth: 220 }
                            Label { text: modelData.state; color: modelData.state === "completed" ? "#7bd88f" : modelData.state === "failed" ? "#ff7c7c" : "#8ec5ff" }
                            Button {
                                visible: !["completed", "failed", "cancelled", "interrupted"].includes(modelData.state)
                                text: bridge.text("action.cancel")
                                onClicked: bridge.cancelTask(modelData.id)
                            }
                            Button {
                                visible: ["failed", "cancelled", "interrupted"].includes(modelData.state)
                                text: bridge.text("action.retry")
                                onClicked: bridge.retryTask(modelData.id)
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                spacing: 14
                anchors.margins: 30
                SectionTitle { text: bridge.text("nav.settings") }
                Label { text: bridge.text("settings.local_only"); color: "#b8c1cf"; font.pixelSize: 16 }
                Label { text: bridge.text("settings.license"); color: "#8ec5ff"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label { text: "Data root: " + bridge.dataRoot; color: "#8e9aae"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Button { text: bridge.text("action.inspect"); onClicked: bridge.inspectRuntime() }
                Button { text: bridge.text("action.export_diagnostics"); onClicked: diagnosticDialog.open() }
                Label { text: bridge.diagnosticPath; visible: bridge.diagnosticPath.length > 0; color: "#7bd88f"; elide: Text.ElideMiddle; Layout.fillWidth: true }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; TextArea { text: bridge.runtimeJson; readOnly: true; font.family: "Consolas"; color: "#cbd4e1"; wrapMode: Text.WrapAnywhere } }
            }
        }
    }
}
