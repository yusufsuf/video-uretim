/**
 * Fashion Video Automation – Client-side Logic
 * Handles file uploads, API calls, progress polling, and result display.
 */

const API_BASE = "";

// ─── Auth ──────────────────────────────────────────────────────────────────
function getAuthHeaders() {
    const token = localStorage.getItem("auth_token");
    return token ? { "Authorization": "Bearer " + token } : {};
}

// ─── Session Management ────────────────────────────────────────────────────
const INACTIVE_TIMEOUT_MS = 30 * 60 * 1000; // 30 dk hareketsizlik → çıkış
const REFRESH_BEFORE_MS   = 10 * 60 * 1000; // dolmadan 10 dk önce yenile
let _lastActivity = Date.now();

["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"].forEach(evt =>
    document.addEventListener(evt, () => { _lastActivity = Date.now(); }, { passive: true })
);

function _isUserActive() {
    return (Date.now() - _lastActivity) < INACTIVE_TIMEOUT_MS;
}

function _clearSession() {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_refresh_token");
    localStorage.removeItem("auth_expires_at");
}

async function _tryRefreshToken() {
    const refreshToken = localStorage.getItem("auth_refresh_token");
    if (!refreshToken) return false;
    try {
        const res = await fetch("/auth/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!res.ok) return false;
        const data = await res.json();
        localStorage.setItem("auth_token", data.access_token);
        localStorage.setItem("auth_refresh_token", data.refresh_token || refreshToken);
        localStorage.setItem("auth_expires_at", String(data.expires_at || ""));
        return true;
    } catch {
        return false;
    }
}

// Her 4 dakikada bir kontrol: aktifse yenile, pasifse süresi dolmuşsa çıkış
setInterval(async () => {
    const expiresAt = parseInt(localStorage.getItem("auth_expires_at") || "0", 10) * 1000;
    const timeLeft  = expiresAt - Date.now();
    if (_isUserActive()) {
        if (expiresAt > 0 && timeLeft < REFRESH_BEFORE_MS) await _tryRefreshToken();
    } else {
        if (expiresAt > 0 && timeLeft < 0) { _clearSession(); window.location.href = "/login"; }
    }
}, 4 * 60 * 1000);

async function handleAuthError(status) {
    if (status === 401) {
        if (_isUserActive()) {
            const refreshed = await _tryRefreshToken();
            if (refreshed) { window.location.reload(); return true; }
        }
        _clearSession();
        window.location.href = "/login";
        return true;
    }
    if (status === 403) {
        document.getElementById("pending-overlay").style.display = "flex";
        return true;
    }
    return false;
}

// Verify token on load and populate user chip
(async function initAuth() {
    const token = localStorage.getItem("auth_token");
    if (!token) { window.location.replace("/login"); return; }
    try {
        const res = await fetch("/auth/me", { headers: getAuthHeaders() });
        if (await handleAuthError(res.status)) return;
        const user = await res.json();
        const nameEl = document.querySelector(".user-name");
        const avatarEl = document.querySelector(".user-avatar");
        if (nameEl) nameEl.textContent = user.full_name || user.email;
        if (avatarEl) avatarEl.textContent = (user.full_name || user.email).slice(0, 2).toUpperCase();
    } catch {
        // network error — keep user on page, will fail gracefully on API calls
    }
})();

// ─── Tema Yönetimi ────────────────────────────────────────────────────
const THEME_KEY = "antigravity_theme";

function applyTheme(theme) {
    const html = document.documentElement;
    if (theme === "system") {
        html.removeAttribute("data-theme");
    } else {
        html.setAttribute("data-theme", theme);
    }
    const icons = { dark: "☾", light: "☀", system: "◐" };
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.querySelector(".theme-icon").textContent = icons[theme] || "◐";
    localStorage.setItem(THEME_KEY, theme);
}

function cycleTheme() {
    const current = localStorage.getItem(THEME_KEY) || "system";
    const next = { system: "dark", dark: "light", light: "system" };
    applyTheme(next[current] || "system");
}

applyTheme(localStorage.getItem(THEME_KEY) || "system");

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((localStorage.getItem(THEME_KEY) || "system") === "system") {
        const btn = document.getElementById("theme-toggle");
        if (btn) btn.querySelector(".theme-icon").textContent = "◐";
    }
});

document.getElementById("theme-toggle")?.addEventListener("click", cycleTheme);

// ─── DOM References ──────────────────────────────────────────────────
const frontZone    = document.getElementById("front-zone");
const sideZone     = document.getElementById("side-zone");
const backZone     = document.getElementById("back-zone");
const refimgZone   = document.getElementById("refimg-zone");
const videoZone    = document.getElementById("video-zone");
const frontInput   = document.getElementById("front-input");
const sideInput    = document.getElementById("side-input");
const backInput    = document.getElementById("back-input");
const refimgInput  = document.getElementById("refimg-input");
const videoInput   = document.getElementById("video-input");
const audioToggle      = document.getElementById("audio-toggle");
const watermarkInput   = document.getElementById("watermark-input");
const watermarkZone    = document.getElementById("watermark-zone");
const watermarkLabel   = document.getElementById("watermark-label");
const videoDescInput   = document.getElementById("video-description");
const progressSec  = document.getElementById("progress-section");
const progressBar  = document.getElementById("progress-bar");
const progressStat = document.getElementById("progress-status");
const progressPct  = document.getElementById("progress-percent");
const stepsTimeline    = document.getElementById("steps-timeline");
const analysisPanel    = document.getElementById("analysis-panel");
const analysisGrid     = document.getElementById("analysis-grid");
const promptPanel  = document.getElementById("prompt-panel");
const promptText   = document.getElementById("prompt-text");
const resultSec    = document.getElementById("result-section");
const resultVideo  = document.getElementById("result-video");
const downloadBtn  = document.getElementById("download-btn");
const newBtn       = document.getElementById("new-btn");
const errorMsg     = document.getElementById("error-message");
const errorText    = document.getElementById("error-text");

// Wizard elements
const wizardModal   = document.getElementById("wizard-modal");
const wizardFooter  = document.getElementById("wizard-footer");
const wizardNextBtn = document.getElementById("wizard-next-btn");
const wizardBackBtn = document.getElementById("wizard-back-btn");
const wizardStepLabel = document.getElementById("wizard-step-label");
const stepDots      = document.querySelectorAll("#step-dots .dot");
const step4Title    = document.getElementById("step4-title");
const step4Sub      = document.getElementById("step4-sub");

// ─── State ─────────────────────────────────────────────────────────
let frontFile  = null;
let sideFile   = null;
let backFile   = null;
let refimgFile = null;
let videoFile  = null;
let watermarkFile = null;
let currentJobId   = null;
let pollInterval   = null;
let currentWizardStep = 1;
const TOTAL_STEPS = 3;
let generationStarted = false;

// Library URL state
let libraryFrontUrl    = null;
let librarySideUrl     = null;
let libraryBackUrl     = null;
let libraryBgUrl       = null;
let libraryBgExtraUrls = [];   // extra background images for per-shot cycling
let libraryStyleUrl    = null;

// ─── Defile State ────────────────────────────────────────────────
let videoMode = "video";          // "video" | "defile" | "ozel"
let generationMode = "classic";   // "classic" | "multishot"
let defileOutfits = [];           // [{front_url, side_url, back_url, name}]
let defileShotConfigs = [{ duration: 5 }]; // global shot list [{duration}]
let defileBgUrl = null;
let defileBgExtraUrls = [];
let defileAspectRatio = "9:16";

// ── Defile Shot Designer ─────────────────────────────────────────────
const DEFILE_MAX_TOTAL = 15;
const DEFILE_MIN_SHOT  = 3;
const DEFILE_MAX_SHOT  = 10;
const DEFILE_MAX_SHOTS = 5;

function _defileTotalDuration() {
    return defileShotConfigs.reduce((s, c) => s + c.duration, 0);
}

function renderDefileShotDesigner() {
    const container = document.getElementById("defile-shot-rows");
    const totalLabel = document.getElementById("defile-total-dur-label");
    const addBtn = document.getElementById("defile-add-shot-btn");
    if (!container) return;

    const total = _defileTotalDuration();
    if (totalLabel) {
        totalLabel.textContent = `${total}s / ${DEFILE_MAX_TOTAL}s`;
        totalLabel.style.color = total > DEFILE_MAX_TOTAL ? "var(--error)" : "var(--text-secondary)";
    }
    if (addBtn) addBtn.style.display = defileShotConfigs.length >= DEFILE_MAX_SHOTS ? "none" : "block";

    container.innerHTML = defileShotConfigs.map((cfg, idx) => `
        <div style="display:flex;align-items:center;gap:8px;background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:8px;padding:8px 10px">
            <span style="font-size:0.72rem;font-weight:600;color:var(--text-muted);min-width:52px">Sahne ${idx + 1}</span>
            <input type="range" class="shot-dur-slider" style="flex:1" min="${DEFILE_MIN_SHOT}" max="${DEFILE_MAX_SHOT}" value="${cfg.duration}"
                oninput="updateDefileShotDuration(${idx}, this.value)">
            <span style="font-size:0.72rem;font-weight:600;color:var(--text-primary);min-width:24px;text-align:right">${cfg.duration}s</span>
            ${defileShotConfigs.length > 1
                ? `<button onclick="removeDefileShot(${idx})" style="background:none;border:none;cursor:pointer;color:var(--text-muted);font-size:0.8rem;padding:2px 4px;line-height:1" title="Sahneyi kaldır">✕</button>`
                : ""}
        </div>
    `).join("");
}

function updateDefileShotDuration(idx, val) {
    defileShotConfigs[idx].duration = parseInt(val);
    renderDefileShotDesigner();
}

function addDefileShot() {
    if (defileShotConfigs.length >= DEFILE_MAX_SHOTS) return;
    const remaining = DEFILE_MAX_TOTAL - _defileTotalDuration();
    const dur = Math.max(DEFILE_MIN_SHOT, Math.min(DEFILE_MAX_SHOT, remaining > 0 ? remaining : DEFILE_MIN_SHOT));
    defileShotConfigs.push({ duration: dur });
    renderDefileShotDesigner();
}

function removeDefileShot(idx) {
    if (defileShotConfigs.length <= 1) return;
    defileShotConfigs.splice(idx, 1);
    renderDefileShotDesigner();
}

// ─── Location State ─────────────────────────────────────────────────
let selectedLocation = "studio";

const LOCATIONS = [
    {
        value: "studio",
        label: "Stüdyo",
        labelTR: "Stüdyo",
        desc: "Profesyonel fotoğraf stüdyosu",
        gradient: "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)",
        accent: "#4cc9f0",
    },
    {
        value: "beach",
        label: "Sahil",
        labelTR: "Sahil",
        desc: "Altın saat, kumsalda çekim",
        gradient: "linear-gradient(135deg, #f4845f 0%, #f4a261 60%, #264653 100%)",
        accent: "#f4a261",
    },
    {
        value: "city_street",
        label: "Şehir Sokağı",
        labelTR: "Şehir",
        desc: "Urban mimari, gece ışıkları",
        gradient: "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)",
        accent: "#a78bfa",
    },
    {
        value: "garden",
        label: "Bahçe",
        labelTR: "Bahçe",
        desc: "Doğal ışık, yeşil ortam",
        gradient: "linear-gradient(135deg, #1b4332 0%, #52b788 60%, #95d5b2 100%)",
        accent: "#52b788",
    },
    {
        value: "rooftop",
        label: "Çatı Terası",
        labelTR: "Çatı",
        desc: "Şehir silueti, gün batımı",
        gradient: "linear-gradient(135deg, #ff6b6b 0%, #ee9ca7 40%, #4361ee 100%)",
        accent: "#ee9ca7",
    },
    {
        value: "runway",
        label: "Pist",
        labelTR: "Pist",
        desc: "Moda pistı, dramatik sahne",
        gradient: "linear-gradient(135deg, #f72585 0%, #7209b7 60%, #3a0ca3 100%)",
        accent: "#f72585",
    },
];

function toggleLocationPicker() {
    const panel  = document.getElementById("location-pick-panel");
    const toggle = document.getElementById("location-pick-toggle");
    const isOpen = panel.classList.contains("open");
    if (!isOpen) {
        renderLocationCards();
        panel.classList.add("open");
        toggle.classList.add("open");
    } else {
        panel.classList.remove("open");
        toggle.classList.remove("open");
    }
}

function renderLocationCards() {
    const grid = document.getElementById("location-grid");
    if (!grid) return;
    grid.innerHTML = LOCATIONS.map(loc => `
        <div class="location-card${selectedLocation === loc.value ? " active" : ""}"
             onclick="selectLocation('${loc.value}', '${loc.labelTR}')">
            <div class="location-card-bg" style="background:${loc.gradient}"></div>
            <div class="location-card-overlay"></div>
            <div class="location-card-info">
                <div class="location-card-name">${loc.label}</div>
                <div class="location-card-desc">${loc.desc}</div>
            </div>
            ${selectedLocation === loc.value
                ? `<div class="location-card-check" style="background:${loc.accent}">✓</div>`
                : ""}
        </div>
    `).join("");
}

function selectLocation(value, labelTR) {
    selectedLocation = value;
    const btn = document.getElementById("location-pick-label");
    if (btn) btn.textContent = labelTR;
    // Close panel
    document.getElementById("location-pick-panel")?.classList.remove("open");
    document.getElementById("location-pick-toggle")?.classList.remove("open");
    // Re-render to update active state (panel is hidden but stays in DOM)
    renderLocationCards();
}

// ─── Multishot State ────────────────────────────────────────────────
let shots = [
    { camera_move: "", duration: 5, description: "", camera_angle: "", shot_size: "" },
    { camera_move: "", duration: 5, description: "", camera_angle: "", shot_size: "" },
];
let selectedAspectRatio = "9:16";

const CAM_MOVES = [
    { value: "",          label: "Otomatik", animClass: "cam-anim-static" },
    { value: "dolly_in",  label: "Dolly In",  animClass: "cam-anim-dolly-in" },
    { value: "dolly_out", label: "Dolly Out", animClass: "cam-anim-dolly-out" },
    { value: "orbit",     label: "Orbit",     animClass: "cam-anim-orbit" },
    { value: "pan",       label: "Pan",       animClass: "cam-anim-pan" },
    { value: "tilt_up",   label: "Tilt Up",   animClass: "cam-anim-tilt" },
    { value: "tracking",  label: "Tracking",  animClass: "cam-anim-tracking" },
    { value: "crane",     label: "Crane",     animClass: "cam-anim-crane" },
    { value: "static",    label: "Static",    animClass: "cam-anim-static" },
];

const CAMERA_ANGLES = [
    { value: "",           label: "Otomatik" },
    { value: "eye_level",  label: "Eye Level" },
    { value: "low_angle",  label: "Low Angle" },
    { value: "high_angle", label: "High Angle" },
    { value: "profile",    label: "Profile" },
    { value: "rear",       label: "Rear Shot" },
    { value: "dutch",      label: "Dutch Angle" },
];

const SHOT_SIZES = [
    { value: "",                 label: "Otomatik" },
    { value: "wide",             label: "Wide" },
    { value: "medium_wide",      label: "Med. Wide" },
    { value: "medium",           label: "Medium" },
    { value: "close_up",         label: "Close-Up" },
    { value: "extreme_close_up", label: "Extreme CU" },
];

const CAM_PAGE = 4;
const CAM_MAX_OFFSET = CAM_MOVES.length - CAM_PAGE; // = 4
const camOffsets = {}; // per-card offset state: { shotIdx: offset }

function getTotalDuration() {
    return shots.reduce((sum, s) => sum + s.duration, 0);
}

function updateTotalDurationLabel() {
    const label = document.getElementById("total-duration-label");
    if (label) label.textContent = `• ${getTotalDuration()}sn toplam`;
}

function renderShots() {
    const container = document.getElementById("shots-container");
    if (!container) return;

    container.innerHTML = shots.map((shot, idx) => {
        if (camOffsets[idx] === undefined) camOffsets[idx] = 0;
        const offset = camOffsets[idx];
        return `
        <div class="shot-card">
            <div class="shot-card-header">
                <span class="shot-card-title">Sahne ${idx + 1} · <span class="shot-dur-label">${shot.duration}sn</span></span>
                ${shots.length > 1
                    ? `<button class="shot-remove-btn" onclick="removeShot(${idx})">✕</button>`
                    : ""}
            </div>
            <div class="cam-carousel">
                <button class="cam-nav-btn" id="cam-prev-${idx}"
                        onclick="shiftCamPage(${idx},-1)"
                        ${offset === 0 ? "disabled" : ""}>‹</button>
                <div class="cam-track-wrapper">
                    <div class="cam-track" id="cam-track-${idx}">
                        ${CAM_MOVES.map((cm, i) => `
                            <button class="cam-btn${shot.camera_move === cm.value ? " active" : ""}"
                                    id="cam-btn-${idx}-${i}"
                                    onclick="selectCamMove(${idx}, '${cm.value}', ${i})"
                                    title="${cm.label}">
                                <div class="cam-anim ${cm.animClass}"></div>
                                <span>${cm.label}</span>
                            </button>
                        `).join("")}
                    </div>
                </div>
                <button class="cam-nav-btn" id="cam-next-${idx}"
                        onclick="shiftCamPage(${idx},1)"
                        ${offset >= CAM_MAX_OFFSET ? "disabled" : ""}>›</button>
            </div>
            <div class="shot-params-row">
                <div class="shot-param">
                    <span class="shot-param-label">Açı</span>
                    <select class="shot-select" onchange="updateShotAngle(${idx}, this.value)">
                        ${CAMERA_ANGLES.map(a => `<option value="${a.value}"${shot.camera_angle === a.value ? " selected" : ""}>${a.label}</option>`).join("")}
                    </select>
                </div>
                <div class="shot-param">
                    <span class="shot-param-label">Çekim Boyu</span>
                    <select class="shot-select" onchange="updateShotSize(${idx}, this.value)">
                        ${SHOT_SIZES.map(s => `<option value="${s.value}"${shot.shot_size === s.value ? " selected" : ""}>${s.label}</option>`).join("")}
                    </select>
                </div>
            </div>
            <div class="shot-dur-row">
                <div class="shot-dur-labels">
                    <span>Süre</span>
                    <span class="shot-dur-label">${shot.duration}sn</span>
                </div>
                <input type="range" class="shot-dur-slider" min="3" max="10" value="${shot.duration}"
                       oninput="updateShotDuration(${idx}, this.value, this.closest('.shot-card'))">
            </div>
            <div class="shot-desc-row">
                <textarea class="form-input shot-desc"
                          id="shot-desc-${idx}"
                          placeholder="Kendi istediğinizi yazın (Türkçe olabilir) veya ✦ AI'ya bırakın"
                          oninput="updateShotDesc(${idx}, this.value)">${shot.description || ""}</textarea>
                <button class="shot-ai-btn" id="shot-ai-btn-${idx}"
                        onclick="refineShotDescription(${idx})" title="AI ile sinematik prompt oluştur">✦</button>
            </div>
        </div>
        `;
    }).join("");

    // Apply carousel translate for all cards after DOM is built
    shots.forEach((_, idx) => _applyCamTranslate(idx));

    updateTotalDurationLabel();
}

function _applyCamTranslate(idx) {
    const track = document.getElementById(`cam-track-${idx}`);
    if (!track) return;
    const wrapper = track.parentElement;
    const btnW = (wrapper.offsetWidth + 6) / CAM_PAGE; // +6 accounts for gap
    track.style.transform = `translateX(-${camOffsets[idx] * btnW}px)`;
}

function shiftCamPage(idx, dir) {
    camOffsets[idx] = Math.max(0, Math.min(CAM_MAX_OFFSET, (camOffsets[idx] || 0) + CAM_PAGE * dir));
    _applyCamTranslate(idx);
    const prev = document.getElementById(`cam-prev-${idx}`);
    const next = document.getElementById(`cam-next-${idx}`);
    if (prev) prev.disabled = camOffsets[idx] === 0;
    if (next) next.disabled = camOffsets[idx] >= CAM_MAX_OFFSET;
}

function selectCamMove(idx, move, btnIdx) {
    shots[idx].camera_move = move;
    // Update active class without full re-render
    CAM_MOVES.forEach((_, i) => {
        document.getElementById(`cam-btn-${idx}-${i}`)?.classList.remove("active");
    });
    if (btnIdx !== undefined) {
        document.getElementById(`cam-btn-${idx}-${btnIdx}`)?.classList.add("active");
    }
}

function updateShotDuration(idx, val, card) {
    shots[idx].duration = parseInt(val);
    if (card) {
        card.querySelectorAll(".shot-dur-label").forEach(el => el.textContent = val + "sn");
    }
    updateTotalDurationLabel();
}

function updateShotDesc(idx, val) {
    shots[idx].description = val;
}

function updateShotAngle(idx, val) {
    shots[idx].camera_angle = val;
}

function updateShotSize(idx, val) {
    shots[idx].shot_size = val;
}

function addShot() {
    shots.push({ camera_move: "", duration: 5, description: "", camera_angle: "", shot_size: "" });
    renderShots();
}

function removeShot(idx) {
    if (shots.length <= 1) return;
    shots.splice(idx, 1);
    renderShots();
}

async function refineShotDescription(idx) {
    const textarea = document.getElementById(`shot-desc-${idx}`);
    const btn      = document.getElementById(`shot-ai-btn-${idx}`);
    if (!textarea || !btn) return;

    const userText = textarea.value.trim();
    const shot = shots[idx];

    btn.disabled = true;
    btn.textContent = "…";

    // Resolve location context: library bg URL > uploaded refimg (as data URL) > none
    let locationImageUrl = libraryBgUrl || null;
    if (!locationImageUrl && refimgFile) {
        locationImageUrl = await new Promise(resolve => {
            const reader = new FileReader();
            reader.onload = e => resolve(e.target.result);
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(refimgFile);
        });
    }

    try {
        const resp = await fetch("/api/refine-shot", {
            method: "POST",
            headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({
                camera_move: shot.camera_move,
                camera_angle: shot.camera_angle || "eye_level",
                shot_size: shot.shot_size || "wide",
                duration: shot.duration,
                user_description: userText || "fashion model walks and poses naturally",
                location: selectedLocation,
                location_image_url: locationImageUrl,
            }),
        });
        if (resp.ok) {
            const data = await resp.json();
            textarea.value = data.description;
            shots[idx].description = data.description;
        }
    } catch {
        // Silent fail
    }

    btn.disabled = false;
    btn.textContent = "✦";
}

// Expose to global scope for inline onclick handlers
window.toggleLocationPicker = toggleLocationPicker;
window.selectLocation = selectLocation;
window.selectCamMove = selectCamMove;
window.updateShotDuration = updateShotDuration;
window.updateShotDesc = updateShotDesc;
window.updateShotAngle = updateShotAngle;
window.updateShotSize = updateShotSize;
window.removeShot = removeShot;
window.refineShotDescription = refineShotDescription;

// ─── Aspect Ratio Cards ──────────────────────────────────────────────
document.querySelectorAll(".ratio-card").forEach(card => {
    card.addEventListener("click", () => {
        document.querySelectorAll(".ratio-card").forEach(c => c.classList.remove("active"));
        card.classList.add("active");
        selectedAspectRatio = card.dataset.ratio;
    });
});

document.getElementById("add-shot-btn")?.addEventListener("click", addShot);

// ─── Generation Mode Toggle ──────────────────────────────────────────
// ── Order JSON type → shot params mapping ─────────────────────────
const ORDER_TYPE_MAP = {
    wide_full:     { camera_move: 'dolly_out',  camera_angle: 'eye_level',  shot_size: 'wide' },
    wide_ultra:    { camera_move: 'dolly_out',  camera_angle: 'eye_level',  shot_size: 'wide' },
    wide_overhead: { camera_move: 'crane',      camera_angle: 'high_angle', shot_size: 'wide' },
    wide_tele:     { camera_move: 'static',     camera_angle: 'eye_level',  shot_size: 'wide' },
    med_frontal:   { camera_move: 'dolly_in',   camera_angle: 'eye_level',  shot_size: 'medium' },
    med_low:       { camera_move: 'dolly_in',   camera_angle: 'low_angle',  shot_size: 'medium' },
    med_3q:        { camera_move: 'orbit',      camera_angle: 'profile',    shot_size: 'medium' },
    side_full:     { camera_move: 'tracking',   camera_angle: 'profile',    shot_size: 'medium_wide' },
    side_3q:       { camera_move: 'orbit',      camera_angle: 'profile',    shot_size: 'medium' },
    cu_jacket:     { camera_move: 'dolly_in',   camera_angle: 'eye_level',  shot_size: 'close_up' },
    cu_fabric:     { camera_move: 'static',     camera_angle: 'eye_level',  shot_size: 'close_up' },
    cu_collar:     { camera_move: 'dolly_in',   camera_angle: 'eye_level',  shot_size: 'close_up' },
    cu_hem:        { camera_move: 'tilt_up',    camera_angle: 'low_angle',  shot_size: 'close_up' },
    cu_belt:       { camera_move: 'static',     camera_angle: 'eye_level',  shot_size: 'close_up' },
    rear_full:     { camera_move: 'tracking',   camera_angle: 'rear',       shot_size: 'medium_wide' },
    rear_3q:       { camera_move: 'orbit',      camera_angle: 'rear',       shot_size: 'medium' },
    rear_low:      { camera_move: 'dolly_in',   camera_angle: 'low_angle',  shot_size: 'medium' },
    low_front:     { camera_move: 'dolly_in',   camera_angle: 'low_angle',  shot_size: 'medium' },
    low_power:     { camera_move: 'dolly_in',   camera_angle: 'low_angle',  shot_size: 'wide' },
    pivot_front:   { camera_move: 'orbit',      camera_angle: 'eye_level',  shot_size: 'medium' },
    pivot_side:    { camera_move: 'orbit',      camera_angle: 'profile',    shot_size: 'medium' },
    pivot_slow:    { camera_move: 'orbit',      camera_angle: 'eye_level',  shot_size: 'medium' },
    face_close:    { camera_move: 'dolly_in',   camera_angle: 'eye_level',  shot_size: 'close_up' },
    face_med:      { camera_move: 'static',     camera_angle: 'eye_level',  shot_size: 'medium' },
};

// ── JSON load helpers (regular video wizard) ───────────────────────
function toggleJsonLoad(show) {
    document.getElementById('json-load-area').style.display = show ? 'block' : 'none';
    document.getElementById('json-load-btn').style.display  = show ? 'none'  : 'inline-block';
    if (!show) document.getElementById('json-paste-input').value = '';
}

function applyJsonConfig() {
    let raw = document.getElementById('json-paste-input').value.trim();
    let configs;
    try {
        const parsed = JSON.parse(raw);
        configs = Array.isArray(parsed) ? parsed : (parsed.shot_configs || null);
        if (!Array.isArray(configs)) throw new Error('shot_configs dizisi bulunamadı');
    } catch (e) {
        alert('Geçersiz JSON: ' + e.message);
        return;
    }
    shots = configs.map(c => {
        const map = ORDER_TYPE_MAP[c.type] || {};
        return {
            camera_move:  map.camera_move  || 'dolly_in',
            camera_angle: map.camera_angle || 'eye_level',
            shot_size:    map.shot_size    || 'medium',
            duration:     Number(c.duration) || 5,
            description:  '',
        };
    });
    renderShots();
    updateTotalDurationLabel();
    toggleJsonLoad(false);
}

// ── JSON load helpers (defile wizard) ─────────────────────────────
function toggleDefileJsonLoad(show) {
    document.getElementById('defile-json-load-area').style.display = show ? 'block' : 'none';
    document.getElementById('defile-json-load-btn').style.display  = show ? 'none'  : 'inline-block';
    if (!show) document.getElementById('defile-json-paste-input').value = '';
}

function applyDefileJsonConfig() {
    let raw = document.getElementById('defile-json-paste-input').value.trim();
    let configs;
    try {
        const parsed = JSON.parse(raw);
        configs = Array.isArray(parsed) ? parsed : (parsed.shot_configs || null);
        if (!Array.isArray(configs)) throw new Error('shot_configs dizisi bulunamadı');
    } catch (e) {
        alert('Geçersiz JSON: ' + e.message);
        return;
    }
    defileShotConfigs = configs.map(c => ({ duration: Number(c.duration) || 5 }));
    renderDefileShotDesigner();
    toggleDefileJsonLoad(false);
}

function setGenerationMode(mode) {
    generationMode = mode;
    const btnClassic   = document.getElementById('mode-btn-classic');
    const btnMultishot = document.getElementById('mode-btn-multishot');
    const btnCustom    = document.getElementById('mode-btn-custom');
    const desc         = document.getElementById('mode-desc');
    const customArea   = document.getElementById('custom-prompt-area');
    if (!btnClassic) return;

    // Reset all buttons
    [btnClassic, btnMultishot, btnCustom].forEach(b => {
        if (!b) return;
        b.style.background = 'transparent';
        b.style.color      = 'var(--text-secondary)';
        b.style.boxShadow  = 'none';
    });

    // Activate selected
    const activeBtn = { classic: btnClassic, multishot: btnMultishot, custom: btnCustom }[mode];
    if (activeBtn) {
        activeBtn.style.background = '#fff';
        activeBtn.style.color      = '#0a0a0a';
        activeBtn.style.boxShadow  = '0 1px 4px rgba(0,0,0,0.15)';
    }

    const shotsDesigner = document.getElementById('shots-designer-section');
    const isCustom = mode === 'custom';
    if (shotsDesigner) shotsDesigner.style.display = isCustom ? 'none' : '';

    if (mode === 'classic') {
        if (desc) desc.textContent = 'Her sahne ayrı ayrı üretilip birleştirilir.';
        if (customArea) customArea.style.display = 'none';
    } else if (mode === 'multishot') {
        if (desc) desc.textContent = 'Tek NB2 + tek Kling çağrısıyla tüm sahneler üretilir.';
        if (customArea) customArea.style.display = 'none';
    } else {
        if (desc) desc.textContent = 'Yüklenen fotoğraf başlangıç karesi — analiz ve NB2 atlanır.';
        if (customArea) customArea.style.display = 'block';
    }

    _updateGenerateBtnCustomState();
}

function onCustomPromptInput() {
    _updateGenerateBtnCustomState();
}

function toggleCustomSceneOptions() {
    const panel = document.getElementById("custom-scene-options");
    const icon  = document.getElementById("custom-scene-toggle-icon");
    const btn   = document.getElementById("custom-scene-toggle");
    if (!panel) return;
    const open = panel.style.display === "none";
    panel.style.display = open ? "block" : "none";
    if (icon) icon.textContent = open ? "−" : "＋";
    if (btn)  btn.style.borderColor = open ? "var(--accent)" : "var(--border-subtle)";
}
window.toggleCustomSceneOptions = toggleCustomSceneOptions;

function _updateGenerateBtnCustomState() {
    const btn = document.getElementById('generate-btn');
    if (!btn) return;
    if (generationMode === 'custom') {
        btn.disabled = false;
        btn.title = '';
    } else {
        btn.disabled = false;
        btn.title = '';
    }
}

// ─── Wizard Management ──────────────────────────────────────────────
function openWizard() {
    videoMode = "video";
    const titleEl = document.getElementById("wizard-title");
    if (titleEl) titleEl.textContent = "Yeni Video Üret";
    // Hide defile step if it was showing
    const defileStep = document.getElementById("step-defile");
    if (defileStep) defileStep.style.display = "none";

    if (!generationStarted) {
        currentWizardStep = 1;
        showWizardStep(1);
        step4Title.textContent = "Video Üretmeye Hazır";
        step4Sub.textContent = "Ayarlarınız kaydedildi. Üretimi başlatın.";
    } else {
        showWizardStep(3);
    }
    wizardModal.style.display = "flex";
    document.body.style.overflow = "hidden";
}

function closeWizard() {
    if (generationStarted && currentJobId && pollInterval) {
        if (!confirm("Üretim devam ediyor. Yine de kapatmak istiyor musunuz?")) return;
        clearInterval(pollInterval);
        pollInterval = null;
        generationStarted = false;
    }
    videoMode = "video";
    wizardModal.style.display = "none";
    document.body.style.overflow = "";
}

function showWizardStep(step) {
    for (let i = 1; i <= TOTAL_STEPS; i++) {
        const el = document.getElementById(`step-${i}`);
        if (el) el.style.display = i === step ? "block" : "none";
    }
    wizardStepLabel.textContent = `Adım ${step} / ${TOTAL_STEPS}`;
    updateStepDots(step);
    updateWizardFooterButtons(step);

    // Render shots when step 2 becomes visible
    if (step === 2) renderShots();
}

function updateStepDots(step) {
    stepDots.forEach((dot, i) => {
        dot.classList.toggle("active", i === step - 1);
    });
}

function updateWizardFooterButtons(step) {
    wizardBackBtn.style.display = step === 1 ? "none" : "inline-flex";
    if (step === TOTAL_STEPS) {
        wizardNextBtn.textContent = "Video Üret";
        wizardNextBtn.disabled = false;
    } else {
        wizardNextBtn.textContent = "Devam →";
        wizardNextBtn.disabled = step === 1 && !frontFile;
    }
    wizardFooter.style.display = generationStarted ? "none" : "flex";
}

function updateNextBtn() {
    if (currentWizardStep === 1) {
        wizardNextBtn.disabled = !frontFile && !libraryFrontUrl;
    }
}

// ─── Library Picker ─────────────────────────────────────────────
let _libPickerTarget = null; // 'front' | 'background' | 'style'
let _libPickerActiveTab = null;

const LIB_TAB_MAP = {
    character:  [{ val: "character",  label: "Elbiseler" }],
    background: [{ val: "background", label: "Arka Planlar" }, { val: "style", label: "Stiller" }],
};

async function openLibraryPicker(targetZone, defaultCategory) {
    _libPickerTarget = targetZone;
    _libPickerActiveTab = defaultCategory;

    const modal   = document.getElementById("lib-picker-modal");
    const title   = document.getElementById("lib-picker-title");
    const tabs    = document.getElementById("lib-picker-tabs");
    const grid    = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = targetZone === "front" ? "Elbise Seç" : "Arka Plan / Stil Seç";

    // Render tabs
    const tabDefs = LIB_TAB_MAP[defaultCategory] || [{ val: defaultCategory, label: defaultCategory }];
    tabs.innerHTML = tabDefs.map(t =>
        `<button class="lib-picker-tab${t.val === defaultCategory ? " active" : ""}" data-cat="${t.val}">${t.label}</button>`
    ).join("");
    tabs.querySelectorAll(".lib-picker-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            tabs.querySelectorAll(".lib-picker-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _libPickerActiveTab = btn.dataset.cat;
            _fetchAndRenderLibrary(_libPickerActiveTab, grid);
        });
    });

    // Show modal
    modal.style.display = "flex";
    document.body.style.overflow = "hidden";

    // Close button
    closeBtn.onclick = () => closeLibraryPicker();

    // Click outside closes
    modal.onclick = (e) => { if (e.target === modal) closeLibraryPicker(); };

    // Load items
    await _fetchAndRenderLibrary(defaultCategory, grid);
}

function closeLibraryPicker() {
    const modal = document.getElementById("lib-picker-modal");
    modal.style.display = "none";
    document.body.style.overflow = "";
}

async function _fetchAndRenderLibrary(category, grid) {
    grid.innerHTML = `<div class="lib-picker-loading">Yükleniyor...</div>`;
    try {
        const resp = await fetch(`/library/items?category=${category}`, { headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();
        if (!items.length) {
            grid.innerHTML = `<div class="lib-picker-empty">Bu kategoride henüz öğe yok.<br><a href="/library" target="_blank">Kütüphaneye git →</a></div>`;
            return;
        }

        // Build HTML without inline onclick (inline JSON breaks HTML attribute parsing)
        grid.innerHTML = items.map(item => {
            const extras = item.extra_urls || [];
            const extrasBadge = extras.length > 0
                ? `<div class="lib-picker-extras-badge">+${extras.length}</div>` : "";
            return `
                <div class="lib-picker-item" data-id="${item.id}">
                    <img src="${item.image_url}" alt="${item.name}" loading="lazy">
                    <div class="lib-picker-item-name">${item.name}</div>
                    ${extrasBadge}
                </div>`;
        }).join("");

        // Attach click handlers via event listeners (safe with any item data)
        const itemMap = Object.fromEntries(items.map(it => [it.id, it]));
        grid.querySelectorAll(".lib-picker-item").forEach(el => {
            el.addEventListener("click", () => {
                const item = itemMap[el.dataset.id];
                if (item) selectLibraryItem(JSON.stringify(item));
            });
        });
    } catch (err) {
        grid.innerHTML = `<div class="lib-picker-empty">Yüklenemedi: ${err.message}</div>`;
    }
}

function selectLibraryItem(itemJson) {
    const item = JSON.parse(itemJson);
    const target = _libPickerTarget;

    if (target === "defile-bg") {
        defileBgUrl = item.image_url;
        defileBgExtraUrls = item.extra_urls || [];
        const preview = document.getElementById("defile-bg-preview");
        const img = document.getElementById("defile-bg-img");
        const name = document.getElementById("defile-bg-name");
        if (preview) preview.style.display = "block";
        if (img) img.src = item.image_url;
        if (name) name.textContent = item.name;
        closeLibraryPicker();
        return;
    }

    if (target === "front") {
        // Clear any uploaded file and use library URL
        libraryFrontUrl = item.image_url;
        frontFile = null;
        // Update front zone UI
        const zone = frontZone;
        zone.classList.add("has-file");
        zone.innerHTML = `
            <span class="badge">Ön</span>
            <button class="remove-btn" onclick="event.stopPropagation(); clearLibraryFront()">✕</button>
            <img src="${item.image_url}" class="preview-img" alt="${item.name}">
            <div class="upload-label">${item.name} <span style="font-size:0.65rem;opacity:0.6">(kütüphane)</span></div>
        `;

        // Auto-populate side and back zones from extra_urls
        const extras = item.extra_urls || [];
        if (extras.length >= 1) {
            librarySideUrl = extras[0];
            sideFile = null;
            sideZone.classList.add("has-file");
            sideZone.innerHTML = `
                <span class="badge muted">Yan</span>
                <button class="remove-btn" onclick="event.stopPropagation(); clearLibrarySide()">✕</button>
                <img src="${extras[0]}" class="preview-img" alt="Yan">
                <div class="upload-label">Yan görünüm <span style="font-size:0.65rem;opacity:0.6">(kütüphane)</span></div>
            `;
        }
        if (extras.length >= 2) {
            libraryBackUrl = extras[1];
            backFile = null;
            backZone.classList.add("has-file");
            backZone.innerHTML = `
                <span class="badge muted">Arka</span>
                <button class="remove-btn" onclick="event.stopPropagation(); clearLibraryBack()">✕</button>
                <img src="${extras[1]}" class="preview-img" alt="Arka">
                <div class="upload-label">Arka görünüm <span style="font-size:0.65rem;opacity:0.6">(kütüphane)</span></div>
            `;
        }
    } else if (item.category === "background") {
        libraryBgUrl = item.image_url;
        libraryBgExtraUrls = item.extra_urls || [];
        // Update refimg zone UI
        const zone = refimgZone;
        refimgFile = null;
        zone.classList.add("has-file");
        zone.innerHTML = `
            <span class="badge muted">Arka Plan</span>
            <button class="remove-btn" onclick="event.stopPropagation(); clearLibraryBg()">✕</button>
            <img src="${item.image_url}" class="preview-img" alt="${item.name}">
            <div class="upload-label">${item.name} <span style="font-size:0.65rem;opacity:0.6">(kütüphane)</span></div>
        `;
    } else if (item.category === "style") {
        libraryStyleUrl = item.image_url;
        const zone = refimgZone;
        refimgFile = null;
        zone.classList.add("has-file");
        zone.innerHTML = `
            <span class="badge muted">Stil</span>
            <button class="remove-btn" onclick="event.stopPropagation(); clearLibraryBg()">✕</button>
            <img src="${item.image_url}" class="preview-img" alt="${item.name}">
            <div class="upload-label">${item.name} <span style="font-size:0.65rem;opacity:0.6">(kütüphane)</span></div>
        `;
    }

    closeLibraryPicker();
    updateNextBtn();
}

function clearLibraryFront() {
    libraryFrontUrl = null;
    librarySideUrl  = null;
    libraryBackUrl  = null;
    removeFile("front");
    removeFile("side");
    removeFile("back");
}

function clearLibrarySide() {
    librarySideUrl = null;
    removeFile("side");
}

function clearLibraryBack() {
    libraryBackUrl = null;
    removeFile("back");
}

function clearLibraryBg() {
    libraryBgUrl       = null;
    libraryBgExtraUrls = [];
    libraryStyleUrl    = null;
    removeFile("refimg");
}

// Expose library picker functions globally (used by inline onclick in index.html)
window.openLibraryPicker = openLibraryPicker;
window.selectLibraryItem = selectLibraryItem;
window.clearLibraryFront = clearLibraryFront;
window.clearLibrarySide  = clearLibrarySide;
window.clearLibraryBack  = clearLibraryBack;
window.clearLibraryBg    = clearLibraryBg;

// ─── Defile Mode ─────────────────────────────────────────────────
function openDefile() {
    videoMode = "defile";
    defileOutfits = [];
    defileShotConfigs = [{ duration: 5 }];
    defileBgUrl = null;
    defileBgExtraUrls = [];
    defileAspectRatio = "9:16";

    const titleEl = document.getElementById("wizard-title");
    if (titleEl) titleEl.textContent = "Defile Modu";

    const stepLabel = document.getElementById("wizard-step-label");
    if (stepLabel) stepLabel.textContent = "";

    // Hide normal steps, show defile step
    for (let i = 1; i <= TOTAL_STEPS; i++) {
        const el = document.getElementById(`step-${i}`);
        if (el) el.style.display = "none";
    }
    document.getElementById("step-defile").style.display = "block";

    // Update footer
    const footer = document.getElementById("wizard-footer");
    const backBtn = document.getElementById("wizard-back-btn");
    const nextBtn = document.getElementById("wizard-next-btn");
    if (backBtn) backBtn.style.display = "none";
    if (nextBtn) {
        nextBtn.textContent = "Defile Üret";
        nextBtn.disabled = true;
    }
    if (footer) footer.style.display = "flex";

    // Reset dot indicators
    document.querySelectorAll("#step-dots .dot").forEach((d, i) => {
        d.classList.toggle("active", i === 0);
    });

    // Render initial defile grid + shot designer
    renderDefileGrid();
    renderDefileShotDesigner();

    wizardModal.style.display = "flex";
    document.body.style.overflow = "hidden";
}

// ─── Özel State ──────────────────────────────────────────────────
let ozelStartFile = null;
let ozelFrontFile = null;
let ozelBackFile  = null;
let ozelSideFile  = null;
let ozelAspectRatio = "9:16";

function openOzel() {
    videoMode = "ozel";
    ozelStartFile = null;
    ozelFrontFile = null;
    ozelBackFile  = null;
    ozelSideFile  = null;
    ozelAspectRatio = "9:16";

    const titleEl = document.getElementById("wizard-title");
    if (titleEl) titleEl.textContent = "Özel Video";

    const stepLabel = document.getElementById("wizard-step-label");
    if (stepLabel) stepLabel.textContent = "";

    for (let i = 1; i <= TOTAL_STEPS; i++) {
        const el = document.getElementById(`step-${i}`);
        if (el) el.style.display = "none";
    }
    document.getElementById("step-defile").style.display = "none";
    document.getElementById("step-ozel").style.display = "block";

    const backBtn = document.getElementById("wizard-back-btn");
    const nextBtn = document.getElementById("wizard-next-btn");
    if (backBtn) backBtn.style.display = "none";
    if (nextBtn) { nextBtn.textContent = "Video Üret"; nextBtn.disabled = true; }

    _setupOzelZone("ozel-start-zone", "ozel-start-input", f => { ozelStartFile = f; _checkOzelReady(); });
    _setupOzelZone("ozel-front-zone", "ozel-front-input", f => { ozelFrontFile = f; _checkOzelReady(); });
    _setupOzelZone("ozel-back-zone",  "ozel-back-input",  f => { ozelBackFile  = f; });
    _setupOzelZone("ozel-side-zone",  "ozel-side-input",  f => { ozelSideFile  = f; });

    document.querySelectorAll("#ozel-ratio-cards .ratio-card").forEach(card => {
        card.addEventListener("click", () => {
            document.querySelectorAll("#ozel-ratio-cards .ratio-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            ozelAspectRatio = card.dataset.ratio;
        });
    });

    wizardModal.style.display = "flex";
    document.body.style.overflow = "hidden";
}

function _setupOzelZone(zoneId, inputId, onFile) {
    const zone = document.getElementById(zoneId);
    if (!zone) return;
    const newZone = zone.cloneNode(true);
    zone.parentNode.replaceChild(newZone, zone);
    const newInput = newZone.querySelector("input[type=file]");
    newZone.addEventListener("click", () => newInput?.click());
    newInput?.addEventListener("change", () => {
        const f = newInput.files?.[0];
        if (!f) return;
        onFile(f);
        const label = newZone.querySelector(".upload-label");
        if (label) label.textContent = f.name.length > 18 ? f.name.slice(0, 16) + "…" : f.name;
        newZone.classList.add("has-file");
    });
}

function _checkOzelReady() {
    const nextBtn = document.getElementById("wizard-next-btn");
    if (nextBtn) nextBtn.disabled = !(ozelStartFile && ozelFrontFile);
}

async function startOzelGeneration() {
    hideError();
    resultSec.classList.remove("active");
    progressSec.classList.add("active");
    generationStarted = true;
    wizardFooter.style.display = "none";
    step4Title.textContent = "Video Üretiliyor...";
    step4Sub.textContent = "Bu işlem 1–3 dakika sürebilir, lütfen bekleyin.";
    resetSteps();
    updateProgress(0, "Başlatılıyor...");

    document.getElementById("step-ozel").style.display = "none";
    document.getElementById("step-3").style.display = "block";

    const formData = new FormData();
    formData.append("generation_mode", "ozel");
    formData.append("ozel_start_frame", ozelStartFile);
    formData.append("front_image", ozelFrontFile);
    if (ozelBackFile)  formData.append("back_image",  ozelBackFile);
    if (ozelSideFile)  formData.append("side_image",  ozelSideFile);

    const prompt = (document.getElementById("ozel-prompt-input")?.value || "").trim();
    if (prompt) formData.append("video_description", prompt);

    const sc = parseInt(document.getElementById("ozel-scene-count")?.value || "3");
    const td = parseInt(document.getElementById("ozel-total-duration")?.value || "15");
    if (sc >= 1 && sc <= 10)  formData.append("custom_scene_count",   String(sc));
    if (td >= 3 && td <= 120) formData.append("custom_total_duration", String(td));

    formData.append("aspect_ratio",   ozelAspectRatio);
    formData.append("generate_audio", document.getElementById("ozel-audio-toggle")?.checked ? "true" : "false");

    try {
        const resp = await fetch(`${API_BASE}/api/generate`, { method: "POST", body: formData, headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const job = await resp.json();
        currentJobId = job.job_id;
        startPolling();
    } catch (err) {
        showError(err.message);
        generationStarted = false;
        wizardFooter.style.display = "flex";
        wizardNextBtn.textContent = "Tekrar Dene";
        wizardNextBtn.disabled = false;
    }
}

function renderDefileGrid() {
    const grid = document.getElementById("defile-collection-grid");
    const emptyMsg = document.getElementById("defile-empty-msg");
    const countEl = document.getElementById("defile-outfit-count");
    if (!grid) return;

    if (countEl) countEl.textContent = `${defileOutfits.length} kıyafet seçildi`;

    if (defileOutfits.length === 0) {
        if (!emptyMsg) {
            grid.innerHTML = `<div class="defile-collection-empty" id="defile-empty-msg">Henüz kıyafet eklenmedi. Kütüphaneden seçin.</div>`;
        }
        document.getElementById("wizard-next-btn").disabled = true;
        return;
    }

    grid.innerHTML = defileOutfits.map((outfit, idx) => `
        <div class="defile-outfit-card">
            <img src="${outfit.front_url}" alt="${outfit.name || `Kıyafet ${idx + 1}`}">
            <div class="defile-outfit-card-overlay">
                <span>${outfit.name || `Kıyafet ${idx + 1}`}</span>
                <button class="defile-outfit-remove" onclick="removeDefileOutfit(${idx})">✕</button>
            </div>
        </div>
    `).join("");

    document.getElementById("wizard-next-btn").disabled = defileOutfits.length < 1;
}

function removeDefileOutfit(idx) {
    defileOutfits.splice(idx, 1);
    renderDefileGrid();
}

// updateDefileShots removed — replaced by renderDefileShotDesigner

function clearDefileBg() {
    defileBgUrl = null;
    defileBgExtraUrls = [];
    const preview = document.getElementById("defile-bg-preview");
    if (preview) preview.style.display = "none";
}

// Defile library picker — multi-select outfit
let _defilePickerMode = false;

function openDefileOutfitPicker() {
    _defilePickerMode = true;
    _libPickerTarget = "defile-outfit";
    _libPickerActiveTab = "character";

    const modal   = document.getElementById("lib-picker-modal");
    const title   = document.getElementById("lib-picker-title");
    const tabs    = document.getElementById("lib-picker-tabs");
    const grid    = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = "Kıyafet Seç";
    tabs.innerHTML = `<button class="lib-picker-tab active" data-cat="character">Elbiseler</button>`;
    tabs.querySelectorAll(".lib-picker-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            tabs.querySelectorAll(".lib-picker-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _libPickerActiveTab = btn.dataset.cat;
            _fetchAndRenderLibrary(_libPickerActiveTab, grid);
        });
    });

    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
    closeBtn.onclick = () => {
        _defilePickerMode = false;
        closeLibraryPicker();
    };
    modal.onclick = (e) => {
        if (e.target === modal) {
            _defilePickerMode = false;
            closeLibraryPicker();
        }
    };

    _fetchAndRenderDefileOutfitLibrary(grid);
}

async function _fetchAndRenderDefileOutfitLibrary(grid) {
    grid.innerHTML = `<div class="lib-picker-loading">Yükleniyor...</div>`;
    try {
        const resp = await fetch("/library/items?category=character", { headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();
        if (!items.length) {
            grid.innerHTML = `<div class="lib-picker-empty">Kütüphanede elbise yok.<br><a href="/library" target="_blank">Kütüphaneye git →</a></div>`;
            return;
        }

        grid.innerHTML = items.map(item => {
            const alreadyAdded = defileOutfits.some(o => o.front_url === item.image_url);
            const extras = item.extra_urls || [];
            const badge = extras.length > 0 ? `<div class="lib-picker-extras-badge">+${extras.length}</div>` : "";
            const selectedClass = alreadyAdded ? " defile-picker-selected" : "";
            return `
                <div class="lib-picker-item${selectedClass}" data-id="${item.id}">
                    <img src="${item.image_url}" alt="${item.name}" loading="lazy">
                    <div class="lib-picker-item-name">${item.name}</div>
                    ${badge}
                    ${alreadyAdded ? `<div class="defile-picker-check">✓</div>` : ""}
                </div>`;
        }).join("");

        const itemMap = Object.fromEntries(items.map(it => [it.id, it]));
        grid.querySelectorAll(".lib-picker-item").forEach(el => {
            el.addEventListener("click", () => {
                const item = itemMap[el.dataset.id];
                if (!item) return;
                const already = defileOutfits.findIndex(o => o.front_url === item.image_url);
                if (already >= 0) {
                    // Deselect
                    defileOutfits.splice(already, 1);
                    el.classList.remove("defile-picker-selected");
                    el.querySelector(".defile-picker-check")?.remove();
                } else {
                    // Add
                    const extras = item.extra_urls || [];
                    defileOutfits.push({
                        front_url: item.image_url,
                        side_url: extras[0] || null,
                        back_url: extras[1] || null,
                        name: item.name,
                    });
                    el.classList.add("defile-picker-selected");
                    const check = document.createElement("div");
                    check.className = "defile-picker-check";
                    check.textContent = "✓";
                    el.appendChild(check);
                }
                const countEl = document.getElementById("defile-outfit-count");
                if (countEl) countEl.textContent = `${defileOutfits.length} kıyafet seçildi`;
            });
        });

        // "Done" footer button
        const footer = grid.closest(".lib-picker")?.querySelector(".lib-picker-footer");
        if (footer) {
            footer.innerHTML = `
                <a href="/library" target="_blank" style="font-size:0.72rem;color:var(--text-secondary)">Kütüphaneyi Yönet →</a>
                <button class="wizard-btn-primary" style="font-size:0.78rem;padding:7px 18px" onclick="confirmDefileOutfits()">Tamam (${defileOutfits.length})</button>
            `;
        }
    } catch (err) {
        grid.innerHTML = `<div class="lib-picker-empty">Yüklenemedi: ${err.message}</div>`;
    }
}

function confirmDefileOutfits() {
    _defilePickerMode = false;
    closeLibraryPicker();
    renderDefileGrid();
    // Restore footer
    const footer = document.getElementById("lib-picker-modal")?.querySelector(".lib-picker-footer");
    if (footer) {
        footer.innerHTML = `<a href="/library" target="_blank" style="font-size:0.72rem;color:var(--text-secondary)">Kütüphaneyi Yönet →</a>`;
    }
}

function openDefileBgPicker() {
    _libPickerTarget = "defile-bg";
    _libPickerActiveTab = "background";

    const modal   = document.getElementById("lib-picker-modal");
    const title   = document.getElementById("lib-picker-title");
    const tabs    = document.getElementById("lib-picker-tabs");
    const grid    = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = "Pist Arka Planı Seç";
    tabs.innerHTML = `<button class="lib-picker-tab active" data-cat="background">Arka Planlar</button>`;

    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
    closeBtn.onclick = () => closeLibraryPicker();
    modal.onclick = (e) => { if (e.target === modal) closeLibraryPicker(); };

    _fetchAndRenderLibrary("background", grid);
}

// Defile ratio cards
document.querySelectorAll("#defile-ratio-cards .ratio-card").forEach(card => {
    card.addEventListener("click", () => {
        document.querySelectorAll("#defile-ratio-cards .ratio-card").forEach(c => c.classList.remove("active"));
        card.classList.add("active");
        defileAspectRatio = card.dataset.ratio;
    });
});

async function startDefileCollection() {
    hideError();
    resultSec.classList.remove("active");
    progressSec.classList.add("active");
    generationStarted = true;
    wizardFooter.style.display = "none";
    step4Title.textContent = "Defile Üretiliyor...";
    step4Sub.textContent = `${defileOutfits.length} kıyafet, ${defileShotConfigs.length} sahne (${_defileTotalDuration()}s). Lütfen bekleyin.`;
    resetSteps();
    updateProgress(0, "Defile başlatılıyor...");

    // Show step-3 (progress/result step)
    document.getElementById("step-defile").style.display = "none";
    document.getElementById("step-3").style.display = "block";

    const payload = {
        outfits: defileOutfits,
        shot_configs: defileShotConfigs,
        runway_background_url: defileBgUrl || null,
        runway_background_extra_urls: defileBgExtraUrls.length > 0 ? defileBgExtraUrls : null,
        aspect_ratio: defileAspectRatio,
        generate_audio: document.getElementById("defile-audio-toggle")?.checked ?? true,
    };

    try {
        const resp = await fetch(`${API_BASE}/api/defile/collection`, {
            method: "POST",
            headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const job = await resp.json();
        currentJobId = job.job_id;
        startPolling();
    } catch (err) {
        showError(err.message);
        generationStarted = false;
        wizardFooter.style.display = "flex";
        document.getElementById("wizard-next-btn").textContent = "Tekrar Dene";
        document.getElementById("wizard-next-btn").disabled = false;
    }
}

window.removeDefileOutfit        = removeDefileOutfit;
window.updateDefileShotDuration  = updateDefileShotDuration;
window.addDefileShot             = addDefileShot;
window.removeDefileShot          = removeDefileShot;
window.clearDefileBg             = clearDefileBg;
window.confirmDefileOutfits      = confirmDefileOutfits;

// ─── Wizard Events ──────────────────────────────────────────────────
document.getElementById("open-wizard-btn")?.addEventListener("click", openWizard);
document.getElementById("nav-new-video")?.addEventListener("click", openWizard);
document.getElementById("card-single-video")?.addEventListener("click", openWizard);
document.getElementById("nav-defile")?.addEventListener("click", openDefile);
document.getElementById("card-defile")?.addEventListener("click", openDefile);
document.getElementById("card-ozel")?.addEventListener("click", openOzel);
document.getElementById("wizard-close")?.addEventListener("click", closeWizard);
document.getElementById("defile-add-outfit-btn")?.addEventListener("click", openDefileOutfitPicker);
document.getElementById("defile-bg-btn")?.addEventListener("click", openDefileBgPicker);
document.getElementById("defile-add-shot-btn")?.addEventListener("click", addDefileShot);

wizardModal?.addEventListener("click", (e) => {
    if (e.target === wizardModal) closeWizard();
});

wizardNextBtn?.addEventListener("click", () => {
    if (videoMode === "defile") {
        startDefileCollection();
        return;
    }
    if (videoMode === "ozel") {
        startOzelGeneration();
        return;
    }
    if (currentWizardStep < TOTAL_STEPS) {
        currentWizardStep++;
        showWizardStep(currentWizardStep);
    } else {
        startGeneration();
    }
});

wizardBackBtn?.addEventListener("click", () => {
    if (currentWizardStep > 1 && !generationStarted) {
        currentWizardStep--;
        showWizardStep(currentWizardStep);
    }
});

// ─── Upload Zone Interactions ────────────────────────────────────
function setupUploadZone(zone, input, type) {
    zone.addEventListener("click", (e) => {
        if (e.target.classList.contains("remove-btn")) return;
        const currentInput = zone.querySelector("input[type=file]");
        if (currentInput) currentInput.click();
    });
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
    input.addEventListener("change", () => {
        if (input.files[0]) handleFileSelect(input.files[0], zone, type);
    });
}

function handleFileSelect(file, zone, type) {
    if (type === "front")  frontFile  = file;
    else if (type === "side")   sideFile   = file;
    else if (type === "back")   backFile   = file;
    else if (type === "refimg") refimgFile = file;
    else videoFile = file;

    zone.classList.add("has-file");
    const isVideo = type === "video";

    if (isVideo) {
        zone.innerHTML = `
            <button class="remove-btn" onclick="event.stopPropagation(); removeFile('${type}')">✕</button>
            <video src="${URL.createObjectURL(file)}" class="preview-img" muted autoplay loop style="max-height:120px;border-radius:12px;"></video>
            <div class="upload-label">${file.name}</div>
        `;
    } else {
        zone.innerHTML = `
            <span class="badge">${{ front: "Ön", side: "Yan", back: "Arka", refimg: "Ref" }[type] || type}</span>
            <button class="remove-btn" onclick="event.stopPropagation(); removeFile('${type}')">✕</button>
            <img src="${URL.createObjectURL(file)}" class="preview-img" alt="Preview">
            <div class="upload-label">${file.name}</div>
        `;
    }

    updateNextBtn();
}

function removeFile(type) {
    const zones  = { front: frontZone, side: sideZone, back: backZone, refimg: refimgZone, video: videoZone };
    const inputs = { front: frontInput, side: sideInput, back: backInput, refimg: refimgInput, video: videoInput };
    const zone = zones[type];
    const plusIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14"/></svg>';
    const imgIcon  = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 16l5-5 4 4 4-4 5 5"/></svg>';
    const vidIcon  = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5,3 19,12 5,21"/></svg>';

    if (type === "front")       { frontFile  = null; resetZone(zone, plusIcon, "Ön",              "JPG, PNG, WebP",        "Zorunlu"); }
    else if (type === "side")   { sideFile   = null; resetZone(zone, plusIcon, "Yan",             "Yan görünüm",           "Opsiyonel", true); }
    else if (type === "back")   { backFile   = null; resetZone(zone, plusIcon, "Arka",            "Arka görünüm",          "Opsiyonel", true); }
    else if (type === "refimg") { refimgFile = null; resetZone(zone, imgIcon,  "Mekan / Referans","Mekan veya stil referansı","Opsiyonel", true); }
    else                        { videoFile  = null; resetZone(zone, vidIcon,  "Referans Video",  "Hareket referansı MP4", "Opsiyonel", false, true); }

    zone.classList.remove("has-file");
    inputs[type].value = "";
    updateNextBtn();
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

// ─── Initialize Upload Zones ─────────────────────────────────────
setupUploadZone(frontZone,  frontInput,  "front");
setupUploadZone(sideZone,   sideInput,   "side");
setupUploadZone(backZone,   backInput,   "back");
setupUploadZone(refimgZone, refimgInput, "refimg");
setupUploadZone(videoZone,  videoInput,  "video");


// ─── Generate ────────────────────────────────────────────────────
async function startGeneration() {
    hideError();
    resultSec.classList.remove("active");
    progressSec.classList.add("active");
    generationStarted = true;
    wizardFooter.style.display = "none";
    step4Title.textContent = "Video Üretiliyor...";
    step4Sub.textContent = "Bu işlem 1–3 dakika sürebilir, lütfen bekleyin.";
    resetSteps();
    updateProgress(0, "Başlatılıyor...");

    const formData = new FormData();
    if (frontFile)          formData.append("front_image",            frontFile);
    if (sideFile)           formData.append("side_image",             sideFile);
    if (backFile)           formData.append("back_image",             backFile);
    if (refimgFile)         formData.append("reference_image",        refimgFile);
    if (videoFile)          formData.append("reference_video",        videoFile);
    if (libraryFrontUrl)    formData.append("library_front_url",      libraryFrontUrl);
    if (librarySideUrl)     formData.append("library_side_url",       librarySideUrl);
    if (libraryBackUrl)     formData.append("library_back_url",       libraryBackUrl);
    if (libraryBgUrl)       formData.append("library_background_url",        libraryBgUrl);
    if (libraryBgExtraUrls.length > 0) formData.append("library_background_extra_urls", JSON.stringify(libraryBgExtraUrls));
    if (libraryStyleUrl)    formData.append("library_style_url",      libraryStyleUrl);

    // Generation mode
    formData.append("generation_mode", generationMode);

    // Custom prompt (Özel mod)
    if (generationMode === 'custom') {
        const customPrompt = (document.getElementById('custom-prompt-input')?.value || '').trim();
        if (customPrompt) formData.append("video_description", customPrompt);

        // Optional scene count + duration override
        const scenePanel = document.getElementById("custom-scene-options");
        if (scenePanel && scenePanel.style.display !== "none") {
            const sc = parseInt(document.getElementById("custom-scene-count")?.value || "0");
            const td = parseInt(document.getElementById("custom-total-duration")?.value || "0");
            if (sc >= 1 && sc <= 10)  formData.append("custom_scene_count", String(sc));
            if (td >= 3 && td <= 120) formData.append("custom_total_duration", String(td));
        }
    }

    // Shots — serialize to JSON
    formData.append("shots",         JSON.stringify(shots));
    formData.append("duration",      String(getTotalDuration()));
    formData.append("scene_count",   String(shots.length));
    formData.append("aspect_ratio",  selectedAspectRatio);
    formData.append("generate_audio", audioToggle ? audioToggle.checked : true);
    formData.append("location",      selectedLocation);

    if (watermarkFile) formData.append("watermark_image", watermarkFile);
    if (videoDescInput && videoDescInput.value.trim()) {
        formData.append("video_description", videoDescInput.value.trim());
    }

    try {
        const resp = await fetch(`${API_BASE}/api/generate`, { method: "POST", body: formData, headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const job = await resp.json();
        currentJobId = job.job_id;
        startPolling();
    } catch (err) {
        showError(err.message);
        generationStarted = false;
        wizardFooter.style.display = "flex";
        wizardNextBtn.textContent = "Tekrar Dene";
        wizardNextBtn.disabled = false;
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
        const resp = await fetch(`${API_BASE}/api/status/${currentJobId}`, { headers: getAuthHeaders() });
        const job = await resp.json();

        updateProgress(job.progress, job.message);
        updateStepsTimeline(job.status);

        if (job.analysis)     showAnalysis(job.analysis);
        if (job.scene_prompt) showPrompt(job.scene_prompt);

        if (job.status === "completed") {
            clearInterval(pollInterval);
            pollInterval = null;
            showResult(job.result_url);
        } else if (job.status === "failed") {
            clearInterval(pollInterval);
            pollInterval = null;
            showError(job.message);
            generationStarted = false;
            wizardFooter.style.display = "flex";
            wizardNextBtn.textContent = "Tekrar Dene";
            wizardNextBtn.disabled = false;
        }
    } catch (err) {
        console.error("Poll error:", err);
    }
}

// ─── Progress UI ─────────────────────────────────────────────────
function updateProgress(percent, message) {
    const pct = percent ?? 0;
    progressBar.style.width = `${pct}%`;
    progressStat.textContent = message || "";
    progressPct.textContent = `${pct}%`;
}

const STEP_ORDER = ["analyzing", "generating_prompts", "generating_background", "generating_video"];

function resetSteps() {
    document.querySelectorAll(".step-item").forEach((el) => {
        el.classList.remove("active", "completed");
    });
    analysisPanel.classList.remove("active");
    promptPanel.classList.remove("active");
}

function updateStepsTimeline(currentStatus) {
    const idx = STEP_ORDER.indexOf(currentStatus);
    document.querySelectorAll(".step-item").forEach((el) => {
        const stepIdx = STEP_ORDER.indexOf(el.dataset.step);
        el.classList.remove("active", "completed");
        if (stepIdx < idx)      el.classList.add("completed");
        else if (stepIdx === idx) el.classList.add("active");
    });
}

// ─── Analysis Panel ──────────────────────────────────────────────
function showAnalysis(analysis) {
    if (analysisPanel.classList.contains("active")) return;
    const fields = {
        "Tür":    analysis.garment_type,
        "Renk":   analysis.color,
        "Desen":  analysis.pattern,
        "Kumaş":  analysis.fabric,
        "Kesim":  analysis.cut_style,
        "Uzunluk":analysis.length,
        "Detay":  analysis.details,
        "Mevsim": analysis.season,
        "Mood":   analysis.mood,
    };
    analysisGrid.innerHTML = Object.entries(fields)
        .map(([k, v]) => `<div class="analysis-item"><div class="label">${k}</div><div class="value">${v}</div></div>`)
        .join("");
    analysisPanel.classList.add("active");
}

// ─── Prompt Panel ────────────────────────────────────────────────
function showPrompt(scenePrompt) {
    if (promptPanel.classList.contains("active")) return;
    let html = `<div style="margin-bottom:8px;font-weight:600;font-style:normal;color:var(--text-primary);font-size:0.8rem;">${scenePrompt.scene_count} sahne • ${scenePrompt.total_duration}s</div>`;
    html += `<div style="margin-bottom:8px;font-size:0.75rem;color:var(--text-muted);">${scenePrompt.background_prompt || ""}</div>`;
    scenePrompt.scenes.forEach(s => {
        html += `<div style="margin-bottom:6px;padding:8px;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-subtle);font-size:0.7rem;line-height:1.6;"><strong>Sahne ${s.scene_number}</strong> (${s.duration}s) — ${(s.prompt || "").substring(0, 150)}...</div>`;
    });
    promptText.innerHTML = html;
    promptPanel.classList.add("active");
}

// ─── Result ──────────────────────────────────────────────────────
function showResult(url) {
    const videoUrl = `${API_BASE}${url}`;
    resultVideo.src = videoUrl;
    resultSec.classList.add("active");
    step4Title.textContent = "Video Hazır!";
    step4Sub.textContent = "Videonuzu izleyin, indirin veya paylaşın.";

    downloadBtn.onclick = () => {
        const a = document.createElement("a");
        a.href = videoUrl;
        a.download = "fashion_video.mp4";
        a.click();
    };

    loadRecentVideos();
}

// ─── WhatsApp Share ──────────────────────────────────────────────
document.getElementById("whatsapp-btn")?.addEventListener("click", () => {
    const videoUrl = resultVideo.src;
    if (!videoUrl) return;
    const fullUrl = new URL(videoUrl, window.location.origin).href;
    const text = encodeURIComponent(`🎬 Fashion Video AI ile oluşturduğum video:\n${fullUrl}`);
    window.open(`https://wa.me/?text=${text}`, "_blank");
});

// ─── New Video ───────────────────────────────────────────────────
newBtn?.addEventListener("click", () => {
    resultSec.classList.remove("active");
    progressSec.classList.remove("active");
    analysisPanel.classList.remove("active");
    promptPanel.classList.remove("active");
    removeFile("front");
    removeFile("back");
    removeFile("side");
    removeFile("refimg");
    removeFile("video");
    currentJobId = null;
    generationStarted = false;
    pollInterval = null;
    libraryFrontUrl = null;
    librarySideUrl  = null;
    libraryBackUrl  = null;
    libraryBgUrl       = null;
    libraryBgExtraUrls = [];
    libraryStyleUrl    = null;
    // Reset shots to default
    shots = [
        { camera_move: "", duration: 5, description: "", camera_angle: "", shot_size: "" },
        { camera_move: "", duration: 5, description: "", camera_angle: "", shot_size: "" },
    ];
    // Reset defile state
    videoMode = "video";
    defileOutfits = [];
    defileShotConfigs = [{ duration: 5 }];
    defileBgUrl = null;
    step4Title.textContent = "Video Üretmeye Hazır";
    step4Sub.textContent = "Ayarlarınız kaydedildi. Üretimi başlatın.";
    currentWizardStep = 1;
    showWizardStep(1);
    wizardFooter.style.display = "flex";
    closeWizard();
});

// ─── Error Handling ──────────────────────────────────────────────
function _trNetworkError(errMsg) {
    const m = (errMsg || "").toLowerCase();
    if (m.includes("http 401")) return "Oturum süresi doldu. Lütfen sayfayı yenileyip tekrar giriş yapın.";
    if (m.includes("http 403")) return "Bu işlem için yetkiniz yok.";
    if (m.includes("http 400")) return "Geçersiz istek. Lütfen girdiğiniz bilgileri kontrol edin.";
    if (m.includes("http 413")) return "Yüklenen dosya çok büyük. Lütfen daha küçük bir dosya seçin.";
    if (m.includes("http 429")) return "Çok fazla istek gönderildi. Lütfen birkaç dakika bekleyip tekrar deneyin.";
    if (m.includes("http 500") || m.includes("http 502") || m.includes("http 503"))
        return "Sunucu hatası oluştu. Lütfen biraz bekleyip tekrar deneyin.";
    if (m.includes("failed to fetch") || m.includes("network") || m.includes("load failed"))
        return "İnternet bağlantısı kesildi. Bağlantınızı kontrol edip tekrar deneyin.";
    if (m.includes("timeout") || m.includes("timed out"))
        return "Bağlantı zaman aşımına uğradı. Lütfen tekrar deneyin.";
    return null;
}

function showError(msg) {
    const translated = _trNetworkError(msg);
    errorText.textContent = translated || msg;
    errorMsg.classList.add("active");
}

function hideError() {
    errorMsg.classList.remove("active");
}

// ─── Watermark Upload ─────────────────────────────────────────────
watermarkZone?.addEventListener("click", () => watermarkInput && watermarkInput.click());
watermarkInput?.addEventListener("change", () => {
    if (watermarkInput.files[0]) {
        watermarkFile = watermarkInput.files[0];
        if (watermarkLabel) watermarkLabel.textContent = `✅ ${watermarkFile.name}`;
    }
});

// ─── Dashboard: Son Videolar ──────────────────────────────────────
async function loadRecentVideos() {
    const grid = document.getElementById("recent-videos-grid");
    if (!grid) return;
    try {
        const resp = await fetch("/api/gallery", { headers: getAuthHeaders() });
        const data = await resp.json();
        const items = (data.items || []).slice(0, 4);

        if (items.length === 0) {
            grid.innerHTML = `
                <div class="recent-empty">
                    <div class="recent-empty-icon">
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                    </div>
                    <div class="recent-empty-text">Henüz video üretilmedi</div>
                </div>`;
            return;
        }

        grid.innerHTML = items.map(item => {
            const isCompleted = item.status === "completed";
            const date = item.created_at
                ? new Date(item.created_at).toLocaleDateString("tr-TR")
                : "";
            const clickAttr = isCompleted && item.result_url
                ? `onclick="window.open('${item.result_url}', '_blank')"`
                : "";
            return `
                <div class="recent-item" ${clickAttr}>
                    ${isCompleted && item.result_url
                        ? `<video src="${item.result_url}" muted loop preload="metadata"
                            onmouseover="this.play()" onmouseout="this.pause();this.currentTime=0;"></video>`
                        : `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:0.75rem;">Hata</div>`
                    }
                    <div class="recent-item-overlay">
                        <div>${item.analysis_summary || (isCompleted ? "✓ Tamamlandı" : "✕ Hata")}</div>
                        ${date ? `<div class="recent-item-tag">${date}</div>` : ""}
                    </div>
                </div>`;
        }).join("");
    } catch (err) {
        console.error("Gallery load error:", err);
    }
}

loadRecentVideos();

// Re-apply carousel translate on resize
window.addEventListener("resize", () => {
    shots.forEach((_, idx) => _applyCamTranslate(idx));
});
