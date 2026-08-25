pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: panel
    required property var bridge
    required property var theme
    required property var mainWindow

    width: 360
    height: 84
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    title: "VoxWeave Mini"

    Rectangle {
        anchors.fill: parent
        radius: 14
        color: panel.theme.surfaceRaised
        border.color: panel.theme.border
        border.width: 1

        RowLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 7

            Rectangle {
                Layout.preferredWidth: 10
                Layout.preferredHeight: 10
                radius: 5
                color: panel.bridge.realtime.status.state === "running"
                    ? panel.theme.success : panel.theme.textDim
            }

            AppButton {
                compact: true
                text: ["starting", "running", "stopping"].includes(
                    panel.bridge.realtime.status.state) ? "Stop" : "Start"
                onClicked: panel.bridge.realtime.toggleStartStop()
            }
            AppButton {
                compact: true
                text: "Bypass"
                checked: Boolean((panel.bridge.realtime.status.metrics || {}).bypass)
                onClicked: panel.bridge.realtime.toggleBypass()
            }
            AppButton {
                compact: true
                text: "Mute"
                checked: Boolean((panel.bridge.realtime.status.metrics || {}).muted)
                onClicked: panel.bridge.realtime.toggleMute()
            }
            AppButton {
                compact: true
                text: Boolean((panel.bridge.realtime.status.metrics || {}).recording)
                    ? "Rec ●" : "Rec"
                onClicked: panel.bridge.realtime.toggleRecording()
            }
            Basic.Label {
                Layout.fillWidth: true
                text: Boolean((panel.bridge.realtime.status.metrics || {}).push_to_talk_enabled)
                    ? (Boolean((panel.bridge.realtime.status.metrics || {}).push_to_talk_pressed)
                        ? "PTT ON" : "PTT") : ""
                color: Boolean((panel.bridge.realtime.status.metrics || {}).push_to_talk_pressed)
                    ? panel.theme.success : panel.theme.textMuted
                font.pixelSize: 11
            }
            AppButton {
                compact: true
                text: "Open"
                onClicked: {
                    panel.mainWindow.show()
                    panel.mainWindow.raise()
                    panel.mainWindow.requestActivate()
                }
            }
            AppButton { compact: true; text: "×"; onClicked: panel.hide() }
        }
    }
}
