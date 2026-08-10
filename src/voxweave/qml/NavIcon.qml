import QtQuick

Item {
    id: icon

    property string kind: "convert"
    property color color: "white"
    readonly property string glyph: {
        if (kind === "convert") return "\uE8B1"
        if (kind === "realtime") return "\uE720"
        if (kind === "models") return "\uE8F1"
        if (kind === "batch") return "\uE8B7"
        if (kind === "tasks") return "\uE9D5"
        return "\uE713"
    }

    implicitWidth: 22
    implicitHeight: 22

    Text {
        anchors.centerIn: parent
        text: icon.glyph
        color: icon.color
        font.family: "Segoe Fluent Icons"
        font.pixelSize: 21
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        renderType: Text.NativeRendering
    }
}
