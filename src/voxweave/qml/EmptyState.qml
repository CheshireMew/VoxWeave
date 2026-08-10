import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: emptyState

    property string title: ""
    property string detail: ""

    Theme { id: theme }

    spacing: 8

    Label {
        Layout.alignment: Qt.AlignHCenter
        text: emptyState.title
        color: theme.text
        font.family: theme.uiFont
        font.pixelSize: 15
        font.weight: Font.DemiBold
    }
    Label {
        Layout.alignment: Qt.AlignHCenter
        Layout.maximumWidth: 380
        text: emptyState.detail
        color: theme.textDim
        font.family: theme.uiFont
        font.pixelSize: 12
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
    }
}
