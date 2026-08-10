import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Button {
    id: control

    property bool selected: false

    Theme { id: theme }

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitWidth: Math.max(150, label.implicitWidth + 32)
    implicitHeight: 40
    leftPadding: 16
    rightPadding: 16
    font.family: theme.uiFont
    font.pixelSize: 13
    font.weight: selected ? Font.DemiBold : Font.Medium

    contentItem: Text {
        id: label
        text: control.text
        color: control.selected ? theme.text : theme.textMuted
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        color: control.selected ? theme.surfaceRaised : (control.hovered ? theme.surface : "transparent")
        radius: theme.radiusSmall
        border.width: control.activeFocus ? 1 : 0
        border.color: theme.info

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            height: 2
            radius: 1
            color: theme.accent
            visible: control.selected
        }
    }
}
