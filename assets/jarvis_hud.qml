import QtQuick

Rectangle {
    id: root
    width: 615
    height: 700
    color: "#00060a"

    property bool muted: false
    property bool speaking: false
    property string assistantState: "INITIALISING"
    property real cpu: 0
    property real memory: 0
    property real network: 0
    property real temperature: -1
    property real measuredFps: 0

    readonly property string stateText: muted ? "MICROPHONE MUTED"
        : speaking ? "VOICE SYNTHESIS ACTIVE"
        : assistantState === "THINKING" ? "COGNITIVE PROCESSING"
        : assistantState === "PROCESSING" ? "TASK PROCESSING"
        : assistantState === "LISTENING" ? "LISTENING FOR COMMAND"
        : assistantState === "STANDBY" ? "LOCAL WAKE WORD ONLY"
        : assistantState
    readonly property color stateColor: muted ? "#ff3366"
        : speaking ? "#00d4ff"
        : (assistantState === "THINKING" || assistantState === "PROCESSING") ? "#ffcc00"
        : assistantState === "LISTENING" ? "#00ff88" : "#00d4ff"

    gradient: Gradient {
        GradientStop { position: 0.0; color: "#000307" }
        GradientStop { position: 0.5; color: "#000d16" }
        GradientStop { position: 1.0; color: "#000204" }
    }

    FrameAnimation {
        running: true
        onTriggered: root.measuredFps = smoothFrameTime > 0 ? 1 / smoothFrameTime : 0
    }

    Repeater {
        model: Math.ceil(root.width / 18)
        Rectangle { x: index * 18; width: 1; height: root.height; color: "#061c25"; opacity: 0.55 }
    }
    Repeater {
        model: Math.ceil(root.height / 18)
        Rectangle { y: index * 18; width: root.width; height: 1; color: "#061c25"; opacity: 0.55 }
    }

    Item {
        id: orbit
        width: Math.min(root.width * 0.88, root.height * 0.78)
        height: width
        anchors.centerIn: parent

        Repeater {
            model: [0.48, 0.41, 0.33, 0.22]
            Rectangle {
                required property real modelData
                width: orbit.width * modelData * 2
                height: width
                radius: width / 2
                anchors.centerIn: parent
                color: "transparent"
                border.width: 1
                border.color: root.muted ? "#9c2849" : (index === 0 ? "#137f96" : "#086276")
                opacity: 0.75
            }
        }

        Repeater {
            model: 45
            Rectangle {
                property real angle: index * 8 * Math.PI / 180
                property real radiusValue: orbit.width * 0.49
                width: index % 4 === 0 ? 18 : 11
                height: 1
                x: orbit.width / 2 + Math.cos(angle) * radiusValue - width / 2
                y: orbit.height / 2 + Math.sin(angle) * radiusValue
                rotation: index * 8 + 90
                color: root.muted ? "#ff3366" : "#17b9d3"
                opacity: 0.8
            }
        }

        Repeater {
            model: 4
            Item {
                id: ringItem
                required property int index
                anchors.fill: parent
                property real ringRadius: orbit.width * [0.45, 0.385, 0.30, 0.19][index]
                property int segmentCount: [24, 18, 14, 10][index]
                Repeater {
                    model: parent.segmentCount
                    Rectangle {
                        property real angle: index * 2 * Math.PI / ringItem.segmentCount
                        width: 30 + (index % 4) * 7
                        height: ringItem.index < 2 ? 4 : 3
                        x: orbit.width / 2 + Math.cos(angle) * ringItem.ringRadius - width / 2
                        y: orbit.height / 2 + Math.sin(angle) * ringItem.ringRadius
                        rotation: angle * 180 / Math.PI + 90
                        color: root.muted ? "#ff3366" : (index % 5 === 0 ? "#8ffcff" : "#00d4ff")
                        opacity: 0.86
                    }
                }
                RotationAnimator on rotation {
                    from: 0
                    to: index % 2 ? -360 : 360
                    duration: root.speaking
                        ? [28000, 38000, 23000, 48000][index]
                        : [65000, 90000, 52000, 110000][index]
                    loops: Animation.Infinite
                    running: true
                }
            }
        }

        Repeater {
            model: 3
            Rectangle {
                width: orbit.width * (0.10 + index * 0.18)
                height: width
                radius: width / 2
                anchors.centerIn: parent
                color: "transparent"
                border.width: 1
                border.color: root.muted ? "#ff3366" : "#00d4ff"
                SequentialAnimation on scale {
                    loops: Animation.Infinite
                    PauseAnimation { duration: index * 900 }
                    NumberAnimation { from: 0.4; to: 2.6; duration: root.speaking ? 2600 : 4500; easing.type: Easing.OutCubic }
                }
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    PauseAnimation { duration: index * 900 }
                    NumberAnimation { from: root.speaking ? 0.8 : 0.45; to: 0; duration: root.speaking ? 2600 : 4500 }
                }
            }
        }

        Rectangle { anchors.centerIn: parent; width: orbit.width * 0.72; height: 1; color: "#08758c"; opacity: 0.5 }
        Rectangle { anchors.centerIn: parent; width: 1; height: orbit.height * 0.72; color: "#08758c"; opacity: 0.5 }

        Repeater {
            model: 6
            Rectangle {
                property real angle: (index * 60) * Math.PI / 180
                width: orbit.width * 0.30
                height: 1
                x: orbit.width / 2 + Math.cos(angle) * orbit.width * 0.16
                y: orbit.height / 2 + Math.sin(angle) * orbit.height * 0.16
                rotation: index * 60
                transformOrigin: Item.Left
                color: "#08758c"
                opacity: 0.52
            }
        }

        Text {
            anchors.centerIn: parent
            text: "JARVIS"
            color: "#d8f8ff"
            font.family: "Courier New"
            font.bold: true
            font.pixelSize: Math.max(34, orbit.width * 0.088)
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height / 2 + 32
            text: root.stateText
            color: root.stateColor
            font.family: "Courier New"
            font.bold: true
            font.pixelSize: Math.max(10, orbit.width * 0.022)
        }

        Item {
            id: waveform
            width: 32 * 5
            height: 40
            anchors.horizontalCenter: parent.horizontalCenter
            y: orbit.height * 0.78
            Repeater {
                model: 32
                Rectangle {
                    property real tallHeight: 6 + Math.abs(Math.sin(index * 0.58)) * (root.speaking ? 32 : 25)
                    width: 3
                    height: root.muted ? 2 : tallHeight
                    x: index * 5
                    y: waveform.height - height
                    color: root.muted ? "#ff3366" : (index % 5 === 0 ? "#8ffcff" : "#00d4ff")
                    SequentialAnimation on height {
                        running: !root.muted
                        loops: Animation.Infinite
                        NumberAnimation { to: 3; duration: root.speaking ? 130 + index * 3 : 350 + index * 7; easing.type: Easing.InOutSine }
                        NumberAnimation { to: tallHeight; duration: root.speaking ? 130 + index * 3 : 350 + index * 7; easing.type: Easing.InOutSine }
                    }
                }
            }
        }

        Text { x: orbit.width * 0.13; y: orbit.height * 0.25; text: "CPU " + root.cpu.toFixed(0); color: "#3a8a9a"; font.family: "Courier New"; font.pixelSize: 9 }
        Text { x: orbit.width * 0.76; y: orbit.height * 0.25; text: "MEM " + root.memory.toFixed(0); color: "#3a8a9a"; font.family: "Courier New"; font.pixelSize: 9 }
        Text { x: orbit.width * 0.13; y: orbit.height * 0.72; text: "NET " + root.network.toFixed(0); color: "#3a8a9a"; font.family: "Courier New"; font.pixelSize: 9 }
        Text { x: orbit.width * 0.76; y: orbit.height * 0.72; text: "TMP " + (root.temperature < 0 ? "N/A" : root.temperature.toFixed(0)); color: "#3a8a9a"; font.family: "Courier New"; font.pixelSize: 9 }
    }

    Text {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 10
        text: "FPS " + root.measuredFps.toFixed(1)
        color: root.measuredFps >= 90 ? "#00ff88" : "#ffcc00"
        font.family: "Courier New"
        font.bold: true
        font.pixelSize: 10
    }
    Text {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: 26
        anchors.rightMargin: 10
        text: "GPU QT QUICK"
        color: "#3a8a9a"
        font.family: "Courier New"
        font.pixelSize: 9
    }
}
