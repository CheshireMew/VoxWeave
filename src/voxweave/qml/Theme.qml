import QtQuick

QtObject {
    readonly property color canvas: "#0B0D10"
    readonly property color sidebar: "#101318"
    readonly property color surface: "#15191F"
    readonly property color surfaceRaised: "#1A2028"
    readonly property color surfaceHover: "#202732"
    readonly property color field: "#0F1318"
    readonly property color border: "#465362"
    readonly property color borderStrong: "#66798E"

    readonly property color text: "#F3F0E8"
    readonly property color textMuted: "#A8B0BC"
    readonly property color textDim: "#8E98A6"

    readonly property color accent: "#F0A45B"
    readonly property color accentHover: "#FFB66C"
    readonly property color accentPressed: "#D98A42"
    readonly property color accentInk: "#211207"
    readonly property color accentWash: "#322419"

    readonly property color info: "#79B7F2"
    readonly property color infoWash: "#172536"
    readonly property color success: "#62D0A3"
    readonly property color successWash: "#173029"
    readonly property color warning: "#EFC66A"
    readonly property color warningWash: "#332B19"
    readonly property color danger: "#F07D7D"
    readonly property color dangerWash: "#351D20"
    readonly property color windowCloseHover: "#C42B1C"

    readonly property string uiFont: Qt.platform.os === "windows" ? "Microsoft YaHei UI" : "sans-serif"
    readonly property string monoFont: Qt.platform.os === "windows" ? "Cascadia Mono" : "monospace"

    readonly property int radiusSmall: 4
    readonly property int radiusMedium: 6
    readonly property int radiusLarge: 8
    readonly property int controlHeight: 42
    readonly property int pageMargin: 16
    readonly property int panelPadding: 12
    readonly property int sidebarWidth: 64
    readonly property int titleBarHeight: 40
}
