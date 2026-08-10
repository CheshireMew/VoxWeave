import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

RowLayout {
    id: control

    property alias from: slider.from
    property alias to: slider.to
    property alias value: slider.value
    property alias stepSize: slider.stepSize
    property int decimals: 0
    property string suffix: ""
    property bool showPositiveSign: false
    property string accessibleName: ""

    function formattedValue() {
        var number = Number(slider.value)
        var sign = control.showPositiveSign && number > 0 ? "+" : ""
        return sign + number.toFixed(control.decimals) + control.suffix
    }

    Theme { id: theme }

    spacing: 8
    implicitWidth: 180
    implicitHeight: theme.controlHeight
    opacity: enabled ? 1 : 0.52

    Basic.Slider {
        id: slider
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
        implicitHeight: theme.controlHeight
        focusPolicy: Qt.StrongFocus
        snapMode: Basic.Slider.SnapAlways
        live: true
        Accessible.name: control.accessibleName

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.availableWidth
            height: 6
            radius: 3
            color: theme.field
            border.width: 1
            border.color: slider.activeFocus ? theme.borderStrong : theme.border

            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: parent.radius
                color: theme.accent
            }
        }

        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: 17
            height: 17
            radius: width / 2
            color: slider.pressed ? theme.accentPressed : (slider.hovered ? theme.accentHover : theme.accent)
            border.width: 2
            border.color: theme.canvas
        }
    }

    Rectangle {
        Layout.preferredWidth: control.decimals > 0 ? 54 : 48
        Layout.preferredHeight: 30
        Layout.alignment: Qt.AlignVCenter
        radius: theme.radiusSmall
        color: theme.field
        border.width: 1
        border.color: theme.border

        Basic.Label {
            anchors.fill: parent
            text: control.formattedValue()
            color: theme.text
            font.family: theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }
}
