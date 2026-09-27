const path = require("path");

module.exports = {
    mode: "production",
    entry: path.resolve(__dirname, "static/src/js/attendanceFaceDetection.js"),
    output: {
        path: path.resolve(__dirname, "static/attendance/face_detection"),
        filename: "face_detection.bundle.js",
    },
    target: ["web", "es2017"],
    devtool: false,
    optimization: {
        minimize: true,
    },
    performance: {
        hints: false,
    },
};
