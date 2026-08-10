import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Button {
    id: control

    property string iconName: "convert"
    property bool selected: false

    Theme { id: theme }

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: 44
    implicitWidth: 44
    leftPadding: 0
    rightPadding: 0

    contentItem: NavIcon {
        kind: control.iconName
        color: control.selected ? theme.accent : (control.hovered ? theme.text : theme.textMuted)
    }

    background: Rectangle {
        radius: theme.radiusMedium
        color: control.selected ? theme.surfaceRaised : (control.hovered ? theme.surface : "transparent")
        border.width: control.activeFocus ? 1 : 0
        border.color: theme.info

        Rectangle {
            width: 3
            height: 22
            radius: 2
            anchors.left: parent.left
            anchors.leftMargin: 1
            anchors.verticalCenter: parent.verticalCenter
            color: theme.accent
            visible: control.selected
        }

        Behavior on color { ColorAnimation { duration: 110 } }
    }

    Basic.ToolTip.visible: control.hovered
    Basic.ToolTip.text: control.text
    Basic.ToolTip.delay: 350
}
