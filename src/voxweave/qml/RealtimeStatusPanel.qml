pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppPanel {
    id: panel

    required property var bridge
    required property var theme
    required property var session
    readonly property var worker: session.worker || ({"state": "not_started"})
    readonly property var metrics: session.metrics || ({})

    objectName: "realtimeStatusPanel"

    function stateText(state) {
        return panel.bridge.text(panel.bridge.language, "realtime.state." + state)
    }

    function stateTone(state) {
        if (state === "running") return session.stage === "overloaded" ? "warning" : "success"
        if (state === "failed") return "danger"
        if (state === "interrupted") return "warning"
        if (state === "starting" || state === "stopping") return "info"
        return "neutral"
    }

    function workerTone(state) {
        if (state === "ready") return "success"
        if (state === "warming" || state === "starting") return "info"
        if (state === "failed") return "danger"
        return "neutral"
    }

    function meterValue(peak) {
        var value = Math.max(Number(peak || 0), 0.000001)
        var decibels = 20 * Math.log(value) / Math.LN10
        return Math.max(0, Math.min(1, (decibels + 60) / 60))
    }

    function meterDecibels(peak) {
        var value = Math.max(Number(peak || 0), 0.000001)
        return Math.max(-120, 20 * Math.log(value) / Math.LN10)
    }

    SectionHeader {
        Layout.fillWidth: true
        title: panel.bridge.text(panel.bridge.language, "section.realtime_status")
        badgeText: panel.stateText(panel.session.state || "idle")
        badgeTone: panel.stateTone(panel.session.state || "idle")
    }

    RowLayout {
        Layout.fillWidth: true
        Label { text: panel.bridge.text(panel.bridge.language, "realtime.worker.label"); color: panel.theme.textMuted; font.family: panel.theme.uiFont; font.pixelSize: 11 }
        Item { Layout.fillWidth: true }
        StatusPill {
            objectName: "realtimeWarmupStatus"
            text: panel.bridge.text(panel.bridge.language, "realtime.worker." + (panel.worker.state || "not_started"))
            tone: panel.workerTone(panel.worker.state || "not_started")
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: panel.session.state === "running"
        Label { text: panel.bridge.text(panel.bridge.language, "realtime.voice.label"); color: panel.theme.textMuted; font.family: panel.theme.uiFont; font.pixelSize: 11 }
        Item { Layout.fillWidth: true }
        StatusPill {
            objectName: "realtimeVadStatus"
            text: panel.metrics.test_mode && panel.metrics.rvc_inference_active
                ? panel.bridge.text(panel.bridge.language, "realtime.voice.recording")
                : panel.metrics.speech_detected
                ? panel.bridge.text(panel.bridge.language, "realtime.voice.detected")
                : panel.bridge.text(panel.bridge.language, "realtime.voice.waiting")
            tone: panel.metrics.speech_detected ? "info" : "neutral"
        }
        StatusPill {
            objectName: "realtimeVoiceStatus"
            text: panel.metrics.test_mode && panel.metrics.playback_active
                ? panel.bridge.text(panel.bridge.language, "realtime.voice.playing")
                : panel.metrics.test_mode && panel.metrics.rvc_inference_active
                ? panel.bridge.text(panel.bridge.language, "realtime.voice.processing")
                : panel.metrics.rvc_inference_active
                ? panel.bridge.text(panel.bridge.language, "realtime.voice.converting")
                : panel.bridge.text(panel.bridge.language, "realtime.voice.listening")
            tone: panel.metrics.playback_active || panel.metrics.rvc_inference_active
                ? "success" : "neutral"
        }
    }

    Label {
        objectName: "realtimeErrorSummary"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        visible: panel.session.state === "failed" && Boolean(panel.session.error)
        text: panel.bridge.summarizeError(panel.session.error || "")
        color: panel.theme.danger
        font.family: panel.theme.uiFont
        font.pixelSize: 11
        maximumLineCount: 2
        wrapMode: Text.WrapAnywhere
        elide: Text.ElideRight
    }

    GridLayout {
        Layout.fillWidth: true
        columns: 3
        columnSpacing: 8
        rowSpacing: 8

        Repeater {
            model: [
                {"label": "realtime.metric.latency", "value": panel.metrics.estimated_latency_ms, "suffix": " ms"},
                {"label": "realtime.metric.infer", "value": panel.metrics.infer_ms, "suffix": " ms"},
                {"label": "realtime.metric.xruns", "value": panel.metrics.xruns || 0, "suffix": ""}
            ]
            delegate: Rectangle {
                id: metricDelegate
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: 56
                radius: panel.theme.radiusSmall
                color: panel.theme.field
                Column {
                    anchors.centerIn: parent
                    spacing: 3
                    Label { anchors.horizontalCenter: parent.horizontalCenter; text: panel.bridge.text(panel.bridge.language, metricDelegate.modelData.label); color: panel.theme.textDim; font.pixelSize: 10 }
                    Label { anchors.horizontalCenter: parent.horizontalCenter; text: metricDelegate.modelData.value === undefined || metricDelegate.modelData.value === null ? "—" : String(metricDelegate.modelData.value) + metricDelegate.modelData.suffix; color: metricDelegate.modelData.label === "realtime.metric.xruns" && Number(metricDelegate.modelData.value) > 0 ? panel.theme.warning : panel.theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                }
            }
        }
    }

    GridLayout {
        Layout.fillWidth: true
        visible: panel.session.state === "running"
        columns: 2
        columnSpacing: 12

        ColumnLayout {
            Layout.fillWidth: true
            RowLayout {
                Layout.fillWidth: true
                Label { text: panel.bridge.text(panel.bridge.language, "realtime.level.input"); color: panel.theme.textDim; font.pixelSize: 10 }
                Item { Layout.fillWidth: true }
                Label { text: String(Number(panel.metrics.input_db || -120).toFixed(1)) + " dB"; color: panel.theme.textDim; font.pixelSize: 10 }
            }
            AppProgressBar { objectName: "realtimeInputLevel"; Layout.fillWidth: true; from: 0; to: 1; value: panel.meterValue(panel.metrics.peak_in) }
        }
        ColumnLayout {
            Layout.fillWidth: true
            RowLayout {
                Layout.fillWidth: true
                Label { text: panel.bridge.text(panel.bridge.language, "realtime.level.output"); color: panel.theme.textDim; font.pixelSize: 10 }
                Item { Layout.fillWidth: true }
                Label { text: panel.meterDecibels(panel.metrics.peak_out).toFixed(1) + " dB"; color: panel.theme.textDim; font.pixelSize: 10 }
            }
            AppProgressBar { objectName: "realtimeOutputLevel"; Layout.fillWidth: true; from: 0; to: 1; value: panel.meterValue(panel.metrics.peak_out) }
        }
    }

    Label {
        Layout.fillWidth: true
        visible: panel.session.stage === "overloaded"
        text: panel.bridge.text(panel.bridge.language, "realtime.overloaded")
        color: panel.theme.warning
        font.family: panel.theme.uiFont
        font.pixelSize: 11
        wrapMode: Text.Wrap
    }
}
