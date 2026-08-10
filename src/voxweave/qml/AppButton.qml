import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Button {
    id: control

    property string kind: "secondary"
    property bool compact: false
    property bool square: false

    Theme { id: theme }

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: compact ? 34 : theme.controlHeight
    implicitWidth: square ? implicitHeight : Math.max(compact ? 72 : 92, label.implicitWidth + leftPadding + rightPadding)
    leftPadding: compact ? 12 : 17
    rightPadding: compact ? 12 : 17
    topPadding: 0
    bottomPadding: 0
    font.family: theme.uiFont
    font.pixelSize: compact ? 12 : 13
    font.weight: kind === "primary" ? Font.DemiBold : Font.Medium

    contentItem: Text {
        id: label
        text: control.text
        color: {
            if (!control.enabled) return theme.textDim
            if (control.kind === "primary") return theme.accentInk
            if (control.kind === "danger") return theme.danger
            return theme.text
        }
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: theme.radiusSmall
        color: {
            if (!control.enabled) return theme.field
            if (control.kind === "primary") {
                if (control.down) return theme.accentPressed
                return control.hovered ? theme.accentHover : theme.accent
            }
            if (control.kind === "quiet") return control.hovered ? theme.surfaceHover : "transparent"
            if (control.kind === "danger") return control.hovered ? theme.dangerWash : "transparent"
            if (control.down) return theme.field
            return control.hovered ? theme.surfaceHover : theme.surfaceRaised
        }
        border.width: control.activeFocus ? 2 : 1
        border.color: {
            if (control.activeFocus) return theme.info
            if (control.kind === "primary") return control.enabled ? theme.accent : theme.border
            if (control.kind === "danger") return theme.danger
            if (control.kind === "quiet") return "transparent"
            return control.hovered ? theme.borderStrong : theme.border
        }

        Behavior on color { ColorAnimation { duration: 110 } }
        Behavior on border.color { ColorAnimation { duration: 110 } }
    }
}
