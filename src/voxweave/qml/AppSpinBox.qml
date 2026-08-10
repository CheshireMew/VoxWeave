import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.SpinBox {
    id: control

    Theme { id: theme }

    editable: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: theme.controlHeight
    implicitWidth: 118

    contentItem: TextInput {
        z: 2
        text: control.displayText
        color: theme.text
        selectionColor: theme.accent
        selectedTextColor: theme.accentInk
        font.family: theme.uiFont
        font.pixelSize: 13
        horizontalAlignment: Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        leftPadding: 13
        rightPadding: 36
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: Qt.ImhFormattedNumbersOnly
    }

    up.indicator: Rectangle {
        x: control.width - width - 5
        y: 4
        width: 27
        height: (control.height - 9) / 2
        radius: 4
        color: control.up.pressed ? theme.accentWash : (control.up.hovered ? theme.surfaceHover : "transparent")
        Text {
            anchors.centerIn: parent
            text: "+"
            color: theme.textMuted
            font.pixelSize: 13
        }
    }

    down.indicator: Rectangle {
        x: control.width - width - 5
        y: control.height / 2
        width: 27
        height: (control.height - 9) / 2
        radius: 4
        color: control.down.pressed ? theme.accentWash : (control.down.hovered ? theme.surfaceHover : "transparent")
        Text {
            anchors.centerIn: parent
            text: "−"
            color: theme.textMuted
            font.pixelSize: 13
        }
    }

    background: Rectangle {
        radius: theme.radiusSmall
        color: theme.field
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? theme.accent : theme.border
    }
}
