import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.CheckBox {
    id: control

    Theme { id: theme }

    hoverEnabled: true
    spacing: 10
    font.family: theme.uiFont
    font.pixelSize: 13
    implicitHeight: 34

    indicator: Rectangle {
        implicitWidth: 20
        implicitHeight: 20
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: 4
        color: control.checked ? theme.accent : theme.field
        border.width: control.activeFocus ? 2 : 1
        border.color: control.checked ? theme.accent : (control.hovered ? theme.borderStrong : theme.border)

        Text {
            anchors.centerIn: parent
            text: "✓"
            visible: control.checked
            color: theme.accentInk
            font.family: theme.uiFont
            font.pixelSize: 14
            font.weight: Font.Bold
        }
    }

    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        color: control.enabled ? theme.textMuted : theme.textDim
        font: control.font
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }
}
