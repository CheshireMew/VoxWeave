import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: header

    default property alias actions: actionLayout.data
    property string title: ""

    Theme { id: theme }

    spacing: 10

    Rectangle {
        Layout.preferredWidth: 3
        Layout.preferredHeight: 28
        radius: 1
        color: theme.accent
    }

    Label {
        text: header.title
        color: theme.text
        font.family: theme.uiFont
        font.pixelSize: 21
        font.weight: Font.DemiBold
    }
    Item { Layout.fillWidth: true }

    RowLayout {
        id: actionLayout
        spacing: 6
    }
}
