import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.TextField {
    id: control

    Theme { id: theme }

    hoverEnabled: true
    selectByMouse: true
    implicitHeight: theme.controlHeight
    leftPadding: 13
    rightPadding: 13
    topPadding: 0
    bottomPadding: 0
    color: theme.text
    placeholderTextColor: theme.textDim
    selectionColor: theme.accent
    selectedTextColor: theme.accentInk
    font.family: theme.uiFont
    font.pixelSize: 13
    verticalAlignment: TextInput.AlignVCenter

    background: Rectangle {
        radius: theme.radiusSmall
        color: control.readOnly ? theme.surface : theme.field
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? theme.accent : (control.hovered ? theme.borderStrong : theme.border)
        Behavior on border.color { ColorAnimation { duration: 100 } }
    }
}
