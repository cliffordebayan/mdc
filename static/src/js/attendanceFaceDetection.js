import * as tf from "@tensorflow/tfjs-core";
import "@tensorflow/tfjs-backend-cpu";
import "@tensorflow/tfjs-backend-webgl";
import * as blazeface from "@tensorflow-models/blazeface";

let model = null;
let modelPromise = null;

async function selectBackend() {
    for (const backend of ["webgl", "cpu"]) {
        try {
            const selected = await tf.setBackend(backend);
            if (selected !== false) {
                await tf.ready();
                if (tf.getBackend() === backend) {
                    return backend;
                }
            }
        } catch (error) {
            // Try the next local backend.
        }
    }
    throw new Error("No supported local face-detection backend is available.");
}

async function loadModel(modelUrl) {
    const backend = await selectBackend();
    try {
        return await blazeface.load({
            modelUrl,
            maxFaces: 2,
            inputWidth: 128,
            inputHeight: 128,
            scoreThreshold: 0.5,
        });
    } catch (error) {
        if (backend !== "webgl") {
            throw error;
        }

        // Some browsers expose WebGL but cannot compile this model. Retry locally on CPU.
        await tf.setBackend("cpu");
        await tf.ready();
        return blazeface.load({
            modelUrl,
            maxFaces: 2,
            inputWidth: 128,
            inputHeight: 128,
            scoreThreshold: 0.5,
        });
    }
}

async function load(modelUrl) {
    if (model) {
        return model;
    }
    if (!modelPromise) {
        modelPromise = (async () => {
            model = await loadModel(modelUrl);
            return model;
        })().catch((error) => {
            modelPromise = null;
            throw error;
        });
    }
    return modelPromise;
}

async function detect(videoElement) {
    if (!model) {
        throw new Error("Face detector is not loaded.");
    }
    // Keep annotations enabled so BlazeFace includes probability scores.
    return model.estimateFaces(videoElement, false, false, true);
}

function dispose() {
    if (model) {
        model.dispose();
    }
    model = null;
    modelPromise = null;
}

window.HRISFaceDetection = {
    load,
    detect,
    dispose,
    getBackend: () => tf.getBackend(),
};
