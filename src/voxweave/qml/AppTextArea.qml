import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.TextArea {
    id: control

    Theme { id: theme }

    selectByMouse: true
    leftPadding: 14
    rightPadding: 14
    topPadding: 12
    bottomPadding: 12
    color: theme.textMuted
    selectionColor: theme.accent
    selectedTextColor: theme.accentInk
    font.family: theme.monoFont
    font.pixelSize: 12
    wrapMode: TextEdit.WrapAnywhere

    background: Rectangle {
        radius: theme.radiusMedium
        color: theme.field
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? theme.accent : theme.border
    }
}
