/**
 * Fashion Video Automation – Client-side Logic
 * Handles file uploads, API calls, progress polling, and result display.
 */

const API_BASE = "";

// ─── DOM References ──────────────────────────────────────────────────
const frontZone = document.getElementById("front-zone");
const sideZone = document.getElementById("side-zone");
const backZone = document.getElementById("back-zone");
const refimgZone = document.getElementById("refimg-zone");
const videoZone = document.getElementById("video-zone");
const frontInput = document.getElementById("front-input");
const sideInput = document.getElementById("side-input");
const backInput = document.getElementById("back-input");
const refimgInput = document.getElementById("refimg-input");
const videoInput = document.getElementById("video-input");
const locationSel = document.getElementById("location-select");
const moodSel = document.getElementById("mood-select");
const durationInput = document.getElementById("duration-input");
const sceneCountInput = document.getElementById("scene-count-input");
const aspectRatioSel = document.getElementById("aspect-ratio-select");
const audioToggle = document.getElementById("audio-toggle");
const watermarkInput = document.getElementById("watermark-input");
const watermarkZone = document.getElementById("watermark-zone");
const watermarkLabel = document.getElementById("watermark-label");
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

// ─── State ─────────────────────────────────────────────────────────
let frontFile = null;
let sideFile = null;
let backFile = null;
let refimgFile = null;
let videoFile = null;
let watermarkFile = null;
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
    else if (type === "side") sideFile = file;
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
            <span class="badge">${{ front: "On", side: "Yan", back: "Arka", refimg: "Ref" }[type] || type}</span>
            <button class="remove-btn" onclick="event.stopPropagation(); removeFile('${type}')">✕</button>
            <img src="${URL.createObjectURL(file)}" class="preview-img" alt="Preview">
            <div class="upload-label">${file.name}</div>
        `;
    }

    updateGenerateBtn();
}

function removeFile(type) {
    const zones = { front: frontZone, side: sideZone, back: backZone, refimg: refimgZone, video: videoZone };
    const inputs = { front: frontInput, side: sideInput, back: backInput, refimg: refimgInput, video: videoInput };
    const zone = zones[type];
    const plusIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14"/></svg>';
    const imgIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 16l5-5 4 4 4-4 5 5"/></svg>';
    const vidIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5,3 19,12 5,21"/></svg>';

    if (type === "front") { frontFile = null; resetZone(zone, plusIcon, "On", "JPG, PNG, WebP", "Zorunlu"); }
    else if (type === "side") { sideFile = null; resetZone(zone, plusIcon, "Yan", "Yan gorunum", "Opsiyonel", true); }
    else if (type === "back") { backFile = null; resetZone(zone, plusIcon, "Arka", "Arka gorunum", "Opsiyonel", true); }
    else if (type === "refimg") { refimgFile = null; resetZone(zone, imgIcon, "Mekan / Referans", "Mekan veya stil referansi", "Opsiyonel", true); }
    else { videoFile = null; resetZone(zone, vidIcon, "Referans Video", "Hareket referansi MP4", "Opsiyonel", false, true); }

    zone.classList.remove("has-file");
    inputs[type].value = "";
    updateGenerateBtn();
}

function resetZone(zone, icon, label, hint, badge, isMuted = false, isVideo = false) {
    const badgeClass = isMuted ? ' muted' : '';
    const type = zone.id.replace("-zone", "");
    const inputId = type + "-input";
    const acceptType = isVideo ? "video/*" : "image/*";
    zone.innerHTML = `
        <span class="badge${badgeClass}">${badge}</span>
        <div class="upload-icon">${icon}</div>
        <div class="upload-label">${label}</div>
        <div class="upload-hint">${hint}</div>
        <input type="file" id="${inputId}" accept="${acceptType}">
    `;
    const newInput = zone.querySelector("input[type=file]");
    newInput.addEventListener("change", () => {
        if (newInput.files[0]) handleFileSelect(newInput.files[0], zone, type);
    });
}

function updateGenerateBtn() {
    generateBtn.disabled = !frontFile;
}

// ─── Initialize Upload Zones ─────────────────────────────────────
setupUploadZone(frontZone, frontInput, "front");
setupUploadZone(sideZone, sideInput, "side");
setupUploadZone(backZone, backInput, "back");
setupUploadZone(refimgZone, refimgInput, "refimg");
setupUploadZone(videoZone, videoInput, "video");

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
    if (sideFile) formData.append("side_image", sideFile);
    if (backFile) formData.append("back_image", backFile);
    if (refimgFile) formData.append("reference_image", refimgFile);
    if (videoFile) formData.append("reference_video", videoFile);
    formData.append("location", locationSel.value);
    formData.append("aspect_ratio", aspectRatioSel.value);
    formData.append("generate_audio", audioToggle.checked);
    formData.append("duration", Math.max(3, Math.min(15, parseInt(durationInput.value) || 10)));
    formData.append("scene_count", Math.max(1, Math.min(6, parseInt(sceneCountInput.value) || 2)));
    if (watermarkFile) formData.append("watermark_image", watermarkFile);
    if (videoDescInput.value.trim()) formData.append("video_description", videoDescInput.value.trim());
    if (locationSel.value === "custom") formData.append("custom_location", customLocIn.value);
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

const STEP_ORDER = ["analyzing", "generating_prompts", "generating_background", "generating_video"];

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

// ─── WhatsApp Share ──────────────────────────────────────────────
const whatsappBtn = document.getElementById("whatsapp-btn");
whatsappBtn.addEventListener("click", () => {
    const videoUrl = resultVideo.src;
    if (!videoUrl) return;
    const fullUrl = new URL(videoUrl, window.location.origin).href;
    const text = encodeURIComponent(`🎬 Fashion Video AI ile oluşturduğum video:\n${fullUrl}`);
    window.open(`https://wa.me/?text=${text}`, "_blank");
});

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

// Watermark mini upload
watermarkZone.addEventListener("click", () => watermarkInput.click());
watermarkInput.addEventListener("change", () => {
    if (watermarkInput.files[0]) {
        watermarkFile = watermarkInput.files[0];
        watermarkLabel.textContent = `✅ ${watermarkFile.name}`;
    }
});

// ─── Prompt Templates (localStorage) ─────────────────────────────
const templateSelect = document.getElementById("template-select");
const saveTemplateBtn = document.getElementById("save-template-btn");
const loadTemplateBtn = document.getElementById("load-template-btn");
const deleteTemplateBtn = document.getElementById("delete-template-btn");

const TEMPLATE_KEY = "fashionvideo_templates";

function getTemplates() {
    try { return JSON.parse(localStorage.getItem(TEMPLATE_KEY) || "{}"); }
    catch { return {}; }
}

function saveTemplates(templates) {
    localStorage.setItem(TEMPLATE_KEY, JSON.stringify(templates));
}

function refreshTemplateList() {
    const templates = getTemplates();
    templateSelect.innerHTML = '<option value="">📋 Şablon Seç...</option>';
    Object.keys(templates).forEach(name => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        templateSelect.appendChild(opt);
    });
}

function getCurrentSettings() {
    return {
        location: locationSel.value,
        mood: moodSel.value,
        duration: durationInput.value,
        scene_count: sceneCountInput.value,
        aspect_ratio: aspectRatioSel.value,
        generate_audio: audioToggle.checked,
        video_description: videoDescInput.value,
        custom_location: customLocIn.value,
    };
}

function applySettings(s) {
    if (s.location) locationSel.value = s.location;
    if (s.mood) moodSel.value = s.mood;
    if (s.duration) durationInput.value = s.duration;
    if (s.scene_count) sceneCountInput.value = s.scene_count;
    if (s.aspect_ratio) aspectRatioSel.value = s.aspect_ratio;
    if (s.generate_audio !== undefined) audioToggle.checked = s.generate_audio;
    if (s.video_description) videoDescInput.value = s.video_description;
    if (s.custom_location) customLocIn.value = s.custom_location;
    // Toggle custom location visibility
    customLocGrp.style.display = locationSel.value === "custom" ? "flex" : "none";
}

saveTemplateBtn.addEventListener("click", () => {
    const name = prompt("Şablon adı girin:");
    if (!name || !name.trim()) return;
    const templates = getTemplates();
    templates[name.trim()] = getCurrentSettings();
    saveTemplates(templates);
    refreshTemplateList();
    templateSelect.value = name.trim();
});

loadTemplateBtn.addEventListener("click", () => {
    const name = templateSelect.value;
    if (!name) return;
    const templates = getTemplates();
    if (templates[name]) applySettings(templates[name]);
});

deleteTemplateBtn.addEventListener("click", () => {
    const name = templateSelect.value;
    if (!name) return;
    if (!confirm(`"${name}" şablonunu silmek istediğinize emin misiniz?`)) return;
    const templates = getTemplates();
    delete templates[name];
    saveTemplates(templates);
    refreshTemplateList();
});

refreshTemplateList();
