import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Button {
    id: control

    property string glyph: ""
    property string accessibleName: ""
    property string kind: "secondary"

    Theme { id: theme }

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitWidth: theme.controlHeight
    implicitHeight: theme.controlHeight
    leftPadding: 0
    rightPadding: 0
    Accessible.name: accessibleName

    contentItem: Text {
        text: control.glyph
        color: control.enabled ? (control.kind === "primary" ? theme.accentInk : theme.text) : theme.textDim
        font.family: "Segoe Fluent Icons"
        font.pixelSize: 18
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        renderType: Text.NativeRendering
    }

    background: Rectangle {
        radius: theme.radiusSmall
        color: {
            if (!control.enabled) return theme.field
            if (control.kind === "primary") return control.down ? theme.accentPressed : (control.hovered ? theme.accentHover : theme.accent)
            if (control.kind === "quiet") return control.hovered ? theme.surfaceHover : "transparent"
            return control.down ? theme.field : (control.hovered ? theme.surfaceHover : theme.surfaceRaised)
        }
        border.width: control.activeFocus ? 2 : (control.kind === "quiet" ? 0 : 1)
        border.color: control.activeFocus ? theme.info : (control.kind === "primary" ? theme.accent : theme.border)
    }

    Basic.ToolTip.visible: control.hovered
    Basic.ToolTip.text: control.accessibleName
    Basic.ToolTip.delay: 350
}
