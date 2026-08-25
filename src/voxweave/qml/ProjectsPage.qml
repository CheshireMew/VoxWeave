pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtMultimedia

Item {
    id: root
    objectName: "projectsPage"
    required property var bridge
    required property var theme
    property var models: []
    property var project: root.bridge.projects.current
    property bool creating: false
    property string comparisonA: ""
    property string comparisonB: ""

    AudioOutput { id: previewOutput }
    MediaPlayer {
        id: previewPlayer
        audioOutput: previewOutput
        source: root.bridge.projects.previewSource
        onSourceChanged: {
            if (source.toString().length > 0) play()
        }
    }
    AudioOutput { id: resultComparisonOutput }
    MediaPlayer { id: resultComparisonPlayer; audioOutput: resultComparisonOutput }

    function modelChoices() {
        return [{"id": "", "localized_name": root.bridge.text(
            root.bridge.language, "projects.inherit_model")}].concat(root.models)
    }

    function modelIndex(modelId) {
        var choices = root.modelChoices()
        for (var index = 0; index < choices.length; ++index) {
            if (String(choices[index].id || "") === String(modelId || "")) return index
        }
        return 0
    }

    function formatTime(value) {
        return Number(value || 0).toFixed(2) + " s"
    }

    function loadProjectProcessingChain() {
        var document = root.project && root.project.document
            ? root.project.document : ({})
        var parameters = document.default_parameters || ({})
        var chain = parameters.processing_chain || ({})
        projectNoiseReduction.value = Number(chain.noise_reduction_db || 0)
        projectDereverb.value = Number(chain.dereverb_strength || 0) * 100
        projectHighpass.value = Number(chain.highpass_hz || 0)
        projectLowEq.value = Number(chain.low_eq_db || 0)
        projectPresenceEq.value = Number(chain.presence_eq_db || 0)
        projectCompressor.checked = Boolean(chain.compressor)
        projectDeesser.checked = Boolean(chain.deesser)
        projectTargetLoudness.checked = chain.target_lufs !== null
            && chain.target_lufs !== undefined
        projectTargetLufs.value = Number(chain.target_lufs === null
            || chain.target_lufs === undefined ? -16 : chain.target_lufs)
        projectLimiter.value = Number(chain.limiter_dbfs === null
            || chain.limiter_dbfs === undefined ? -1 : chain.limiter_dbfs)
        projectTrimSilence.checked = Boolean(chain.trim_silence)
    }

    function projectProcessingChain() {
        return {
            "noise_reduction_db": Number(projectNoiseReduction.value),
            "dereverb_strength": Number(projectDereverb.value) / 100.0,
            "highpass_hz": Math.round(Number(projectHighpass.value)),
            "low_eq_db": Number(projectLowEq.value),
            "presence_eq_db": Number(projectPresenceEq.value),
            "compressor": Boolean(projectCompressor.checked),
            "deesser": Boolean(projectDeesser.checked),
            "target_lufs": projectTargetLoudness.checked
                ? Number(projectTargetLufs.value) : null,
            "limiter_dbfs": Number(projectLimiter.value),
            "trim_silence": Boolean(projectTrimSilence.checked)
        }
    }

    FileDialog {
        id: projectInputDialog
        title: root.bridge.text(root.bridge.language, "field.input")
        nameFilters: [root.bridge.text(root.bridge.language, "filter.media")
            + " (" + root.bridge.mediaFileFilter + ")"]
        onAccepted: createInput.text = root.bridge.media.localPath(selectedFile)
    }

    FileDialog {
        id: projectOutputDialog
        title: root.bridge.text(root.bridge.language, "field.output")
        fileMode: FileDialog.SaveFile
        options: FileDialog.DontConfirmOverwrite
        nameFilters: [
            root.bridge.text(root.bridge.language, "filter.audio")
                + " (" + root.bridge.audioFileFilter + ")",
            root.bridge.text(root.bridge.language, "filter.video")
                + " (" + root.bridge.videoFileFilter + ")"
        ]
        onAccepted: {
            if (root.creating) createOutput.text = root.bridge.media.localPath(selectedFile)
            else editOutput.text = root.bridge.media.localPath(selectedFile)
        }
    }

    Connections {
        target: root.bridge.projects
        function onCurrentChanged() {
            root.project = root.bridge.projects.current
            if (root.project && root.project.id) {
                root.creating = false
                editName.text = String(root.project.name || "")
                editOutput.text = String(root.project.output_path || "")
                editMode.currentIndex = ["clean", "mixed", "singing"].indexOf(
                    String(root.project.content_mode || "clean"))
                root.loadProjectProcessingChain()
                waveform.requestPaint()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.projects")
            AppButton {
                text: root.bridge.text(root.bridge.language, "projects.new")
                onClicked: {
                    root.creating = true
                    root.bridge.projects.closeProject()
                }
            }
            AppIconButton {
                glyph: "\uE72C"
                accessibleName: root.bridge.text(root.bridge.language, "action.refresh")
                onClicked: root.bridge.projects.refresh()
            }
        }

        Label {
            Layout.fillWidth: true
            text: root.bridge.text(root.bridge.language, "projects.subtitle")
            color: root.theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            AppPanel {
                Layout.preferredWidth: 220
                Layout.fillHeight: true

                SectionHeader {
                    Layout.fillWidth: true
                    title: root.bridge.text(root.bridge.language, "projects.list")
                    badgeText: String(root.bridge.projects.items.length)
                }

                AppScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth

                    ColumnLayout {
                        width: parent.width
                        spacing: 6

                        Repeater {
                            model: root.bridge.projects.items
                            delegate: AppButton {
                                id: projectButton
                                required property var modelData
                                Layout.fillWidth: true
                                kind: root.project && root.project.id === modelData.id
                                    ? "secondary" : "quiet"
                                text: modelData.name
                                    + (modelData.state === "archived" ? " · "
                                        + root.bridge.text(root.bridge.language,
                                            "projects.archived") : "")
                                onClicked: root.bridge.projects.openProject(modelData.id)
                            }
                        }

                        EmptyState {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 150
                            visible: !root.bridge.projects.loading
                                && root.bridge.projects.items.length === 0
                            title: root.bridge.text(root.bridge.language,
                                "projects.empty.title")
                            detail: root.bridge.text(root.bridge.language,
                                "projects.empty.detail")
                        }
                    }
                }
            }

            AppScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true

                ColumnLayout {
                    width: parent.width
                    spacing: 10

                    AppPanel {
                        Layout.fillWidth: true
                        visible: root.creating || !(root.project && root.project.id)

                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language,
                                "projects.create_title")
                        }
                        FieldLabel { text: root.bridge.text(root.bridge.language, "field.name") }
                        AppTextField {
                            id: createName
                            Layout.fillWidth: true
                            placeholderText: root.bridge.text(root.bridge.language,
                                "projects.name_placeholder")
                        }
                        FieldLabel { text: root.bridge.text(root.bridge.language, "field.input") }
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField { id: createInput; Layout.fillWidth: true }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.choose")
                                onClicked: projectInputDialog.open()
                            }
                        }
                        FieldLabel { text: root.bridge.text(root.bridge.language, "field.output") }
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField { id: createOutput; Layout.fillWidth: true }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.choose")
                                onClicked: projectOutputDialog.open()
                            }
                        }
                        FieldLabel { text: root.bridge.text(root.bridge.language, "field.mode") }
                        AppComboBox {
                            id: createMode
                            Layout.fillWidth: true
                            model: [
                                root.bridge.text(root.bridge.language, "mode.clean"),
                                root.bridge.text(root.bridge.language, "mode.mixed"),
                                root.bridge.text(root.bridge.language, "mode.singing")
                            ]
                        }
                        AppButton {
                            Layout.alignment: Qt.AlignRight
                            kind: "primary"
                            text: root.bridge.text(root.bridge.language, "projects.create")
                            enabled: createName.text.trim().length > 0
                                && createInput.text.trim().length > 0
                            onClicked: root.bridge.projects.createProject({
                                "name": createName.text.trim(),
                                "input": createInput.text,
                                "output": createOutput.text,
                                "content_mode": ["clean", "mixed", "singing"][
                                    createMode.currentIndex]
                            })
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        visible: root.project && root.project.id

                        RowLayout {
                            Layout.fillWidth: true
                            SectionHeader {
                                Layout.fillWidth: true
                                title: root.bridge.text(root.bridge.language,
                                    "projects.editor")
                                badgeText: root.bridge.projects.dirty
                                    ? root.bridge.text(root.bridge.language, "projects.unsaved")
                                    : "v" + String(root.project.revision || 0)
                                badgeTone: root.bridge.projects.dirty ? "warning" : "neutral"
                            }
                            AppButton {
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.undo")
                                enabled: root.bridge.projects.canUndo
                                onClicked: root.bridge.projects.undo()
                            }
                            AppButton {
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.redo")
                                enabled: root.bridge.projects.canRedo
                                onClicked: root.bridge.projects.redo()
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 650 ? 2 : 1
                            columnSpacing: 10
                            rowSpacing: 8
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.name") }
                                AppTextField { id: editName; Layout.fillWidth: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.mode") }
                                AppComboBox {
                                    id: editMode
                                    Layout.fillWidth: true
                                    model: [
                                        root.bridge.text(root.bridge.language, "mode.clean"),
                                        root.bridge.text(root.bridge.language, "mode.mixed"),
                                        root.bridge.text(root.bridge.language, "mode.singing")
                                    ]
                                }
                            }
                        }
                        FieldLabel { text: root.bridge.text(root.bridge.language, "field.output") }
                        RowLayout {
                            Layout.fillWidth: true
                            AppTextField { id: editOutput; Layout.fillWidth: true }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.choose")
                                onClicked: projectOutputDialog.open()
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "action.save_changes")
                                onClicked: root.bridge.projects.saveProject({
                                    "name": editName.text.trim(),
                                    "output": editOutput.text,
                                    "content_mode": ["clean", "mixed", "singing"][
                                        editMode.currentIndex]
                                })
                            }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "projects.analyze")
                                kind: "secondary"
                                enabled: !root.bridge.activity.busyKeys.includes(
                                    "project-analyze:" + String(root.project.id || ""))
                                onClicked: root.bridge.projects.analyzeProject({
                                    "name": editName.text.trim(),
                                    "output": editOutput.text,
                                    "content_mode": ["clean", "mixed", "singing"][
                                        editMode.currentIndex]
                                })
                            }
                            AppButton {
                                text: root.bridge.text(root.bridge.language, "projects.render")
                                kind: "primary"
                                enabled: (root.project.document.segments || []).length > 0
                                    && editOutput.text.trim().length > 0
                                    && !root.bridge.activity.busyKeys.includes(
                                        "project-run:" + String(root.project.id || ""))
                                onClicked: root.bridge.projects.renderProject({
                                    "name": editName.text.trim(),
                                    "output": editOutput.text,
                                    "content_mode": ["clean", "mixed", "singing"][
                                        editMode.currentIndex]
                                })
                            }
                            Item { Layout.fillWidth: true }
                            AppButton {
                                compact: true
                                text: root.project.state === "archived"
                                    ? root.bridge.text(root.bridge.language, "action.restore")
                                    : root.bridge.text(root.bridge.language,
                                        "projects.archive")
                                onClicked: root.bridge.projects.setArchived(
                                    root.project.id, root.project.state !== "archived")
                            }
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        visible: root.project && root.project.id
                            && (root.project.document.segments || []).length > 0

                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language,
                                "projects.timeline")
                            badgeText: String((root.project.document.segments || []).length)
                        }

                        FieldLabel {
                            text: root.bridge.text(root.bridge.language,
                                "projects.default_model")
                        }
                        AppComboBox {
                            id: defaultModel
                            Layout.fillWidth: true
                            model: root.modelChoices()
                            textRole: "localized_name"
                            valueRole: "id"
                            currentIndex: root.modelIndex(
                                (root.project.document || {}).default_model || "")
                            onActivated: root.bridge.projects.setDefaultModel(currentValue)
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: root.theme.border
                        }
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language,
                                "section.processing_chain")
                        }
                        GridLayout {
                            objectName: "projectProcessingChain"
                            Layout.fillWidth: true
                            columns: width > 760 ? 4 : 2
                            columnSpacing: 9
                            rowSpacing: 7
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.noise_reduction") }
                                AppSlider { id: projectNoiseReduction; Layout.fillWidth: true; from: 0; to: 30; value: 0; stepSize: 1; suffix: " dB" }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: "Dereverb" }
                                AppSlider { id: projectDereverb; Layout.fillWidth: true; from: 0; to: 100; value: 0; stepSize: 5; suffix: "%" }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.highpass") }
                                AppSlider { id: projectHighpass; Layout.fillWidth: true; from: 0; to: 400; value: 0; stepSize: 10; suffix: " Hz" }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.low_eq") }
                                AppSlider { id: projectLowEq; Layout.fillWidth: true; from: -12; to: 12; value: 0; stepSize: 1; suffix: " dB"; showPositiveSign: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.presence_eq") }
                                AppSlider { id: projectPresenceEq; Layout.fillWidth: true; from: -12; to: 12; value: 0; stepSize: 1; suffix: " dB"; showPositiveSign: true }
                            }
                            AppCheckBox { id: projectCompressor; text: root.bridge.text(root.bridge.language, "field.compressor") }
                            AppCheckBox { id: projectDeesser; text: root.bridge.text(root.bridge.language, "field.deesser") }
                            AppCheckBox { id: projectTrimSilence; text: root.bridge.text(root.bridge.language, "field.trim_silence") }
                            ColumnLayout {
                                Layout.fillWidth: true
                                FieldLabel { text: root.bridge.text(root.bridge.language, "field.limiter") }
                                AppSlider { id: projectLimiter; Layout.fillWidth: true; from: -3; to: -0.1; value: -1; stepSize: 0.1; decimals: 1; suffix: " dBFS" }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppCheckBox { id: projectTargetLoudness; text: root.bridge.text(root.bridge.language, "field.target_loudness") }
                                AppSlider { id: projectTargetLufs; Layout.fillWidth: true; from: -24; to: -9; value: -16; stepSize: 1; suffix: " LUFS"; enabled: projectTargetLoudness.checked }
                            }
                            AppButton {
                                objectName: "projectProcessingApplyButton"
                                text: root.bridge.text(root.bridge.language,
                                    "projects.apply_processing")
                                enabled: root.project.state !== "archived"
                                onClicked: root.bridge.projects.setDefaultProcessingChain(
                                    root.projectProcessingChain())
                            }
                        }

                        Canvas {
                            id: waveform
                            Layout.fillWidth: true
                            Layout.preferredHeight: 96
                            antialiasing: true

                            onPaint: {
                                var context = getContext("2d")
                                context.reset()
                                context.fillStyle = root.theme.field
                                context.fillRect(0, 0, width, height)
                                var peaks = (root.project.document || {}).waveform_peaks || []
                                context.fillStyle = root.theme.accent
                                var middle = height / 2
                                for (var index = 0; index < peaks.length; ++index) {
                                    var x = index * width / Math.max(1, peaks.length)
                                    var bar = Math.max(1, Number(peaks[index]) * (height - 16))
                                    context.fillRect(x, middle - bar / 2,
                                        Math.max(1, width / Math.max(1, peaks.length) - 1), bar)
                                }
                                var duration = Number((root.project.document || {}).duration_seconds || 0)
                                if (duration > 0) {
                                    var segments = root.project.document.segments || []
                                    for (var item = 0; item < segments.length; ++item) {
                                        var segment = segments[item]
                                        context.fillStyle = segment.enabled
                                            ? "rgba(237,178,79,0.30)" : "rgba(120,120,120,0.18)"
                                        var start = Number(segment.start_seconds) / duration * width
                                        var end = Number(segment.end_seconds) / duration * width
                                        context.fillRect(start, 0, Math.max(1, end - start), height)
                                    }
                                }
                            }
                        }

                        Repeater {
                            model: (root.project.document || {}).segments || []
                            delegate: Rectangle {
                                id: segmentRow
                                required property var modelData
                                required property int index
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
                                        AppCheckBox {
                                            text: segmentRow.modelData.speaker + " · "
                                                + segmentRow.modelData.id
                                            checked: segmentRow.modelData.enabled
                                            onClicked: root.bridge.projects.setSegmentEnabled(
                                                segmentRow.modelData.id, checked)
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: root.formatTime(segmentRow.modelData.start_seconds)
                                                + " — "
                                                + root.formatTime(segmentRow.modelData.end_seconds)
                                            color: root.theme.textDim
                                            horizontalAlignment: Text.AlignRight
                                        }
                                        AppButton {
                                            compact: true
                                            text: previewPlayer.playbackState === MediaPlayer.PlayingState
                                                ? root.bridge.text(root.bridge.language, "action.pause")
                                                : root.bridge.text(root.bridge.language, "action.listen")
                                            enabled: !root.bridge.activity.busyKeys.includes(
                                                "project-preview:" + String(root.project.id || "")
                                                + ":" + segmentRow.modelData.id)
                                            onClicked: {
                                                if (previewPlayer.playbackState
                                                        === MediaPlayer.PlayingState) {
                                                    previewPlayer.pause()
                                                } else {
                                                    root.bridge.projects.previewSegment(
                                                        segmentRow.modelData.id)
                                                }
                                            }
                                        }
                                        AppButton {
                                            compact: true
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.split")
                                            onClicked: root.bridge.projects.splitSegment(
                                                segmentRow.modelData.id)
                                        }
                                        AppButton {
                                            compact: true
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.merge_next")
                                            enabled: segmentRow.index + 1
                                                < root.project.document.segments.length
                                            onClicked: root.bridge.projects.mergeWithNext(
                                                segmentRow.modelData.id)
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        AppComboBox {
                                            Layout.fillWidth: true
                                            model: root.modelChoices()
                                            textRole: "localized_name"
                                            valueRole: "id"
                                            currentIndex: root.modelIndex(
                                                segmentRow.modelData.model || "")
                                            onActivated: root.bridge.projects.setSegmentModel(
                                                segmentRow.modelData.id, currentValue)
                                        }
                                        AppSlider {
                                            Layout.preferredWidth: 220
                                            from: -36
                                            to: 36
                                            stepSize: 1
                                            showPositiveSign: true
                                            value: Number((segmentRow.modelData.parameters || {}).pitch || 0)
                                            accessibleName: root.bridge.text(root.bridge.language,
                                                "field.pitch")
                                            onUserEdited: root.bridge.projects.setSegmentPitch(
                                                segmentRow.modelData.id, Math.round(value))
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        FieldLabel {
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.start")
                                        }
                                        AppTextField {
                                            id: segmentStart
                                            Layout.preferredWidth: 90
                                            text: Number(segmentRow.modelData.start_seconds).toFixed(3)
                                            validator: DoubleValidator { bottom: 0 }
                                        }
                                        FieldLabel {
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.end")
                                        }
                                        AppTextField {
                                            id: segmentEnd
                                            Layout.preferredWidth: 90
                                            text: Number(segmentRow.modelData.end_seconds).toFixed(3)
                                            validator: DoubleValidator { bottom: 0 }
                                        }
                                        AppButton {
                                            compact: true
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.apply_bounds")
                                            onClicked: root.bridge.projects.setSegmentBounds(
                                                segmentRow.modelData.id,
                                                Number(segmentStart.text), Number(segmentEnd.text))
                                        }
                                        Item { Layout.fillWidth: true }
                                        StatusPill {
                                            visible: segmentRow.modelData.overlap !== false
                                            text: root.bridge.text(root.bridge.language,
                                                "projects.overlap")
                                            tone: "warning"
                                        }
                                    }
                                }
                            }
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        visible: root.project && root.project.id
                            && root.bridge.projects.results.length > 0
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language,
                                "projects.results")
                            badgeText: String(root.bridge.projects.results.length)
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: "A: " + (root.comparisonA || "not selected")
                                color: root.theme.textMuted
                                elide: Text.ElideMiddle
                            }
                            AppButton {
                                compact: true
                                text: "Play A"
                                enabled: root.comparisonA.length > 0
                                onClicked: {
                                    resultComparisonPlayer.source = root.comparisonA
                                    resultComparisonPlayer.play()
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: "B: " + (root.comparisonB || "not selected")
                                color: root.theme.textMuted
                                elide: Text.ElideMiddle
                            }
                            AppButton {
                                compact: true
                                text: "Play B"
                                enabled: root.comparisonB.length > 0
                                onClicked: {
                                    resultComparisonPlayer.source = root.comparisonB
                                    resultComparisonPlayer.play()
                                }
                            }
                        }
                        Repeater {
                            model: root.bridge.projects.results
                            delegate: ColumnLayout {
                                id: resultVersion
                                required property var modelData
                                Layout.fillWidth: true
                                RowLayout {
                                    Layout.fillWidth: true
                                    AppTextField {
                                        id: resultLabel
                                        Layout.fillWidth: true
                                        text: resultVersion.modelData.label
                                        placeholderText: root.bridge.text(
                                            root.bridge.language, "projects.result_label")
                                    }
                                    AppCheckBox {
                                        id: resultFavorite
                                        text: root.bridge.text(root.bridge.language,
                                            "projects.result_favorite")
                                        checked: resultVersion.modelData.favorite
                                    }
                                    AppButton {
                                        compact: true
                                        text: root.bridge.text(root.bridge.language,
                                            "action.save_changes")
                                        onClicked: root.bridge.projects.updateResult(
                                            resultVersion.modelData.id,
                                            resultLabel.text,
                                            resultFavorite.checked)
                                    }
                                    AppButton {
                                        compact: true
                                        text: root.bridge.text(root.bridge.language,
                                            "action.open_result")
                                        onClicked: root.bridge.projects.openResult(
                                            resultVersion.modelData.output_path)
                                    }
                                    AppButton {
                                        compact: true
                                        text: "A"
                                        onClicked: root.comparisonA = resultVersion.modelData.output_path
                                    }
                                    AppButton {
                                        compact: true
                                        text: "B"
                                        onClicked: root.comparisonB = resultVersion.modelData.output_path
                                    }
                                    AppButton {
                                        compact: true
                                        text: root.bridge.activity.busyKeys.includes(
                                            "result-rerun:" + resultVersion.modelData.id)
                                            ? "Rerunning…" : "Rerun exact config"
                                        enabled: !root.bridge.activity.busyKeys.includes(
                                            "result-rerun:" + resultVersion.modelData.id)
                                        onClicked: root.bridge.projects.rerunResult(
                                            resultVersion.modelData.id)
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: resultVersion.modelData.output_path + " · "
                                        + resultVersion.modelData.created_at
                                        + " · generation " + resultVersion.modelData.generation
                                        + (resultVersion.modelData.parent_id
                                            ? " · parent " + resultVersion.modelData.parent_id : "")
                                    color: root.theme.textDim
                                    font.pixelSize: 10
                                    elide: Text.ElideMiddle
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: Object.keys(resultVersion.modelData.differences || {}).length > 0
                                    text: "Changes from parent: "
                                        + resultVersion.modelData.differences_text
                                    color: root.theme.textMuted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: root.theme.border
                                }
                            }
                        }
                    }

                    AppPanel {
                        Layout.fillWidth: true
                        visible: root.project && root.project.id
                            && root.bridge.projects.history.length > 1
                        SectionHeader {
                            Layout.fillWidth: true
                            title: root.bridge.text(root.bridge.language,
                                "projects.history")
                        }
                        Repeater {
                            model: root.bridge.projects.history
                            delegate: RowLayout {
                                id: revisionRow
                                required property var modelData
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    text: "v" + revisionRow.modelData.revision + " · "
                                        + revisionRow.modelData.created_at
                                    color: root.theme.textMuted
                                    elide: Text.ElideRight
                                }
                                AppButton {
                                    compact: true
                                    text: root.bridge.text(root.bridge.language,
                                        "projects.restore_revision")
                                    enabled: revisionRow.modelData.revision
                                        !== root.project.revision
                                    onClicked: root.bridge.projects.restoreRevision(
                                        revisionRow.modelData.revision)
                                }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true; Layout.minimumHeight: 2 }
                }
            }
        }
    }
}
