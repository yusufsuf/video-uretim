/**
 * Fashion Video Automation – Client-side Logic
 * Handles file uploads, API calls, progress polling, and result display.
 */

const API_BASE = "";

// ─── DOM References ──────────────────────────────────────────────
const frontZone = document.getElementById("front-zone");
const backZone = document.getElementById("back-zone");
const refimgZone = document.getElementById("refimg-zone");
const videoZone = document.getElementById("video-zone");
const frontInput = document.getElementById("front-input");
const backInput = document.getElementById("back-input");
const refimgInput = document.getElementById("refimg-input");
const videoInput = document.getElementById("video-input");
const locationSel = document.getElementById("location-select");
const cameraSel = document.getElementById("camera-select");
const actionSel = document.getElementById("action-select");
const moodSel = document.getElementById("mood-select");
const durationInput = document.getElementById("duration-input");
const sceneCountInput = document.getElementById("scene-count-input");
const videoDescInput = document.getElementById("video-description");
const customLocGrp = document.getElementById("custom-location-group");
const customLocIn = document.getElementById("custom-location");
const generateBtn = document.getElementById("generate-btn");
const progressSec = document.getElementById("progress-section");
const progressBar = document.getElementById("progress-bar");
const progressStat = document.getElementById("progress-status");
const progressPct = document.getElementById("progress-percent");
const stepsTimeline = document.getElementById("steps-timeline");
const analysisPanel = document.getElementById("analysis-panel");
const analysisGrid = document.getElementById("analysis-grid");
const promptPanel = document.getElementById("prompt-panel");
const promptText = document.getElementById("prompt-text");
const resultSec = document.getElementById("result-section");
const resultVideo = document.getElementById("result-video");
const downloadBtn = document.getElementById("download-btn");
const newBtn = document.getElementById("new-btn");
const errorMsg = document.getElementById("error-message");
const errorText = document.getElementById("error-text");

// ─── State ───────────────────────────────────────────────────────
let frontFile = null;
let backFile = null;
let refimgFile = null;
let videoFile = null;
let currentJobId = null;
let pollInterval = null;

// ─── Upload Zone Interactions ────────────────────────────────────
function setupUploadZone(zone, input, type) {
    // Drag & Drop
    zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("active");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("active"));
    zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("active");
        const file = e.dataTransfer.files[0];
        if (file) handleFileSelect(file, zone, type);
    });
    // Click input change
    input.addEventListener("change", () => {
        if (input.files[0]) handleFileSelect(input.files[0], zone, type);
    });
}

function handleFileSelect(file, zone, type) {
    if (type === "front") frontFile = file;
    else if (type === "back") backFile = file;
    else if (type === "refimg") refimgFile = file;
    else videoFile = file;

    // Show preview
    zone.classList.add("has-file");
    const isVideo = type === "video";

    if (isVideo) {
        zone.innerHTML = `
            <span class="badge">${type === "video" ? "Referans" : ""}</span>
            <button class="remove-btn" onclick="event.stopPropagation(); removeFile('${type}')">✕</button>
            <video src="${URL.createObjectURL(file)}" class="preview-img" muted autoplay loop style="max-height: 120px; border-radius: 12px;"></video>
            <div class="upload-label">${file.name}</div>
        `;
    } else {
        zone.innerHTML = `
            <span class="badge">${type === "front" ? "Ön" : "Arka"}</span>
            <button class="remove-btn" onclick="event.stopPropagation(); removeFile('${type}')">✕</button>
            <img src="${URL.createObjectURL(file)}" class="preview-img" alt="Preview">
            <div class="upload-label">${file.name}</div>
        `;
    }

    updateGenerateBtn();
}

function removeFile(type) {
    const zones = { front: frontZone, back: backZone, refimg: refimgZone, video: videoZone };
    const inputs = { front: frontInput, back: backInput, refimg: refimgInput, video: videoInput };
    const zone = zones[type];

    if (type === "front") { frontFile = null; resetZone(zone, "👗", "Ön Görünüm", "JPG, PNG veya WebP", "Zorunlu"); }
    else if (type === "back") { backFile = null; resetZone(zone, "👗", "Arka Görünüm", "Tutarlılık için önerilir", "Opsiyonel", true); }
    else if (type === "refimg") { refimgFile = null; resetZone(zone, "🖼️", "Mekan / Referans", "Mekan veya stil referansı", "Opsiyonel", true); }
    else { videoFile = null; resetZone(zone, "🎬", "Referans Video", "Hareket referansı MP4", "Opsiyonel", false, true); }

    zone.classList.remove("has-file");
    inputs[type].value = "";
    updateGenerateBtn();
}

function resetZone(zone, icon, label, hint, badge, isMuted = false, isVideo = false) {
    const badgeStyle = isMuted ? ' style="background: var(--text-muted);"' : '';
    // Determine type from zone id
    const type = zone.id.replace("-zone", "");
    const inputId = type + "-input";
    const acceptType = isVideo ? "video/*" : "image/*";
    zone.innerHTML = `
        <span class="badge"${badgeStyle}>${badge}</span>
        <div class="upload-icon">${icon}</div>
        <div class="upload-label">${label}</div>
        <div class="upload-hint">${hint}</div>
        <input type="file" id="${inputId}" accept="${acceptType}">
    `;
    // Re-bind the input
    const newInput = zone.querySelector("input[type=file]");
    newInput.addEventListener("change", () => {
        if (newInput.files[0]) handleFileSelect(newInput.files[0], zone, type);
    });
}

function updateGenerateBtn() {
    generateBtn.disabled = !frontFile;
}

// ─── Location Toggle ─────────────────────────────────────────────
locationSel.addEventListener("change", () => {
    customLocGrp.style.display = locationSel.value === "custom" ? "flex" : "none";
});

// ─── Generate ────────────────────────────────────────────────────
generateBtn.addEventListener("click", startGeneration);

async function startGeneration() {
    // Reset UI
    hideError();
    resultSec.classList.remove("active");
    progressSec.classList.add("active");
    generateBtn.disabled = true;
    resetSteps();
    updateProgress(0, "Başlatılıyor...");

    const formData = new FormData();
    formData.append("front_image", frontFile);
    if (backFile) formData.append("back_image", backFile);
    if (refimgFile) formData.append("reference_image", refimgFile);
    if (videoFile) formData.append("reference_video", videoFile);
    formData.append("location", locationSel.value);
    formData.append("duration", Math.max(3, Math.min(60, parseInt(durationInput.value) || 10)));
    formData.append("scene_count", Math.max(1, Math.min(10, parseInt(sceneCountInput.value) || 2)));
    if (videoDescInput.value.trim()) formData.append("video_description", videoDescInput.value.trim());
    if (locationSel.value === "custom") formData.append("custom_location", customLocIn.value);
    if (cameraSel.value) formData.append("camera_style", cameraSel.value);
    if (actionSel.value) formData.append("model_action", actionSel.value);
    if (moodSel.value) formData.append("mood", moodSel.value);

    try {
        const resp = await fetch(`${API_BASE}/api/generate`, { method: "POST", body: formData });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const job = await resp.json();
        currentJobId = job.job_id;
        startPolling();
    } catch (err) {
        showError(`Bağlantı hatası: ${err.message}`);
        generateBtn.disabled = false;
    }
}

// ─── Polling ─────────────────────────────────────────────────────
function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollStatus, 2000);
}

async function pollStatus() {
    if (!currentJobId) return;
    try {
        const resp = await fetch(`${API_BASE}/api/status/${currentJobId}`);
        const job = await resp.json();

        updateProgress(job.progress, job.message);
        updateSteps(job.status);

        // Show analysis when available
        if (job.analysis) showAnalysis(job.analysis);
        // Show prompt when available
        if (job.scene_prompt) showPrompt(job.scene_prompt);

        if (job.status === "completed") {
            clearInterval(pollInterval);
            showResult(job.result_url);
        } else if (job.status === "failed") {
            clearInterval(pollInterval);
            showError(job.message);
            generateBtn.disabled = false;
        }
    } catch (err) {
        console.error("Poll error:", err);
    }
}

// ─── Progress UI ─────────────────────────────────────────────────
function updateProgress(percent, message) {
    progressBar.style.width = `${percent}%`;
    progressStat.textContent = message;
    progressPct.textContent = `${percent}%`;
}

const STEP_ORDER = ["analyzing", "preprocessing", "generating_vto", "generating_video", "merging"];

function resetSteps() {
    document.querySelectorAll(".step-item").forEach((el) => {
        el.classList.remove("active", "completed");
    });
    analysisPanel.classList.remove("active");
    promptPanel.classList.remove("active");
}

function updateSteps(currentStatus) {
    const idx = STEP_ORDER.indexOf(currentStatus);
    document.querySelectorAll(".step-item").forEach((el) => {
        const stepIdx = STEP_ORDER.indexOf(el.dataset.step);
        el.classList.remove("active", "completed");
        if (stepIdx < idx) el.classList.add("completed");
        else if (stepIdx === idx) el.classList.add("active");
    });
}

// ─── Analysis Panel ──────────────────────────────────────────────
function showAnalysis(analysis) {
    if (analysisPanel.classList.contains("active")) return;
    const fields = {
        "Tür": analysis.garment_type,
        "Renk": analysis.color,
        "Desen": analysis.pattern,
        "Kumaş": analysis.fabric,
        "Kesim": analysis.cut_style,
        "Uzunluk": analysis.length,
        "Detay": analysis.details,
        "Mevsim": analysis.season,
        "Mood": analysis.mood,
    };
    analysisGrid.innerHTML = Object.entries(fields)
        .map(([k, v]) => `<div class="analysis-item"><div class="label">${k}</div><div class="value">${v}</div></div>`)
        .join("");
    analysisPanel.classList.add("active");
}

// ─── Prompt Panel ────────────────────────────────────────────────
function showPrompt(scenePrompt) {
    if (promptPanel.classList.contains("active")) return;
    // Multi-scene display
    let html = `<div style="margin-bottom:8px;font-weight:600;font-style:normal;color:var(--text-primary);font-size:0.8rem;">${scenePrompt.scene_count} sahne • ${scenePrompt.total_duration}s</div>`;
    html += `<div style="margin-bottom:8px;font-size:0.75rem;color:var(--text-muted);">${scenePrompt.background_prompt}</div>`;
    scenePrompt.scenes.forEach(s => {
        html += `<div style="margin-bottom:6px;padding:8px;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-subtle);font-size:0.7rem;line-height:1.6;"><strong>Sahne ${s.scene_number}</strong> (${s.duration_seconds}s) — ${s.full_scene_prompt.substring(0, 150)}...</div>`;
    });
    promptText.innerHTML = html;
    promptPanel.classList.add("active");
}

// ─── Result ──────────────────────────────────────────────────────
function showResult(url) {
    const videoUrl = `${API_BASE}${url}`;
    resultVideo.src = videoUrl;
    resultSec.classList.add("active");
    generateBtn.disabled = false;

    downloadBtn.onclick = () => {
        const a = document.createElement("a");
        a.href = videoUrl;
        a.download = "fashion_video.mp4";
        a.click();
    };
}

// ─── New Video ───────────────────────────────────────────────────
newBtn.addEventListener("click", () => {
    resultSec.classList.remove("active");
    progressSec.classList.remove("active");
    analysisPanel.classList.remove("active");
    promptPanel.classList.remove("active");
    removeFile("front");
    removeFile("back");
    removeFile("refimg");
    removeFile("video");
    currentJobId = null;
});

// ─── Error Handling ──────────────────────────────────────────────
function showError(msg) {
    errorText.textContent = msg;
    errorMsg.classList.add("active");
}

function hideError() {
    errorMsg.classList.remove("active");
}

// ─── Init ────────────────────────────────────────────────────────
setupUploadZone(frontZone, frontInput, "front");
setupUploadZone(backZone, backInput, "back");
setupUploadZone(refimgZone, refimgInput, "refimg");
setupUploadZone(videoZone, videoInput, "video");
