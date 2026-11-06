import * as tf from "@tensorflow/tfjs-core";
import * as tfconv from "@tensorflow/tfjs-converter";
import "@tensorflow/tfjs-backend-cpu";
import "@tensorflow/tfjs-backend-webgl";
import * as blazeface from "@tensorflow-models/blazeface";

let model = null;
let modelPromise = null;
const MODEL_CACHE_URL = "indexeddb://hris-attendance-blazeface-v3";
const MODEL_CONFIG = {
    maxFaces: 2,
    inputWidth: 128,
    inputHeight: 128,
    iouThreshold: 0.3,
    scoreThreshold: 0.5,
};

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
        return await loadCachedModel(modelUrl);
    } catch (error) {
        if (backend !== "webgl") {
            throw error;
        }

        // Some browsers expose WebGL but cannot compile this model. Retry locally on CPU.
        await tf.setBackend("cpu");
        await tf.ready();
        return loadCachedModel(modelUrl);
    }
}

async function removeCachedModel() {
    if (tf.io && typeof tf.io.removeModel === "function") {
        try {
            await tf.io.removeModel(MODEL_CACHE_URL);
        } catch (error) {
            // A missing or unsupported IndexedDB cache is safe to ignore.
        }
    }
}

async function loadCachedModel(modelUrl) {
    let graphModel;

    try {
        graphModel = await tfconv.loadGraphModel(MODEL_CACHE_URL);
    } catch (cacheError) {
        await removeCachedModel();
        graphModel = await tfconv.loadGraphModel(modelUrl);

        // Persist the graph and weights for the next portal visit. IndexedDB is
        // an optimization only; private browsing and older browsers may reject it.
        try {
            await graphModel.save(MODEL_CACHE_URL);
        } catch (saveError) {
            // Continue with the in-memory model when persistent storage is unavailable.
        }
    }

    return new blazeface.BlazeFaceModel(
        graphModel,
        MODEL_CONFIG.inputWidth,
        MODEL_CONFIG.inputHeight,
        MODEL_CONFIG.maxFaces,
        MODEL_CONFIG.iouThreshold,
        MODEL_CONFIG.scoreThreshold,
    );
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
