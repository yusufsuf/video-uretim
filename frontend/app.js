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
let currentJobId   = null;
let pollInterval   = null;
let lastGenerationInputs = null; // captured at generation start, shown after result
let lastDebugPayload    = null; // actual API body, returned from backend after generation
let currentWizardStep = 1;
let generationStarted = false;

// ─── Defile State ────────────────────────────────────────────────
let videoMode = "defile";          // "defile" | "studio"
let defileOutfits = [];           // [{front_url, side_url, back_url, name}]
let defileShotConfigs = [{ duration: 5, prompt: "" }]; // global shot list [{duration}]
let defileBgUrl = null;
let defileBgExtraUrls = [];
let defileAspectRatio = "9:16";
let defileStartFrameFile = null;
let defileStartFrameUrl = null;
let defileShotArc = null;           // null = random, or arc ID string
let defileShotArcs = [];            // fetched from /api/defile/shot-arcs

// ── Defile Shot Arc Picker ──────────────────────────────────────────
async function fetchDefileShotArcs() {
    try {
        const resp = await fetch(`${API_BASE}/api/defile/shot-arcs`, {
            headers: getAuthHeaders(),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        defileShotArcs = data.arcs || [];
        renderDefileArcPicker();
    } catch (e) {
        console.warn("Shot arcs fetch failed:", e);
    }
}

function renderDefileArcPicker() {
    const grid = document.getElementById("defile-arc-grid");
    if (!grid) return;

    // Auto/random card + fetched arcs
    const cards = [
        `<button class="defile-arc-card${defileShotArc === null ? ' active' : ''}"
            data-arc="auto" onclick="selectDefileShotArc(null)">
            <span class="defile-arc-icon">🎲</span>
            <span class="defile-arc-name">Otomatik</span>
        </button>`,
        ...defileShotArcs.map(a => `
            <button class="defile-arc-card${defileShotArc === a.id ? ' active' : ''}"
                data-arc="${a.id}" onclick="selectDefileShotArc('${a.id}')">
                <span class="defile-arc-name">${a.name}</span>
            </button>
        `),
    ];
    grid.innerHTML = cards.join("");
}

function selectDefileShotArc(arcId) {
    defileShotArc = arcId;
    renderDefileArcPicker();

    const label = document.getElementById("defile-arc-selected-label");
    const beatsBox = document.getElementById("defile-arc-beats");

    if (!arcId) {
        if (label) label.textContent = "🎲 Otomatik";
        if (beatsBox) beatsBox.style.display = "none";
        return;
    }

    const arc = defileShotArcs.find(a => a.id === arcId);
    if (!arc) return;

    if (label) label.textContent = arc.name;
    if (beatsBox) {
        beatsBox.style.display = "block";
        beatsBox.innerHTML = arc.beats
            .map((b, i) => `<div style="margin-bottom:4px"><strong style="color:var(--text-primary)">${i + 1}.</strong> ${b}</div>`)
            .join("");
    }
}

window.selectDefileShotArc = selectDefileShotArc;

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
        <div style="background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:8px;padding:8px 10px">
            <div style="display:flex;align-items:center;gap:8px">
                <span style="font-size:0.72rem;font-weight:600;color:var(--text-muted);min-width:52px">Sahne ${idx + 1}</span>
                <input type="range" class="shot-dur-slider" style="flex:1" min="${DEFILE_MIN_SHOT}" max="${DEFILE_MAX_SHOT}" value="${cfg.duration}"
                    oninput="updateDefileShotDuration(${idx}, this.value)">
                <span style="font-size:0.72rem;font-weight:600;color:var(--text-primary);min-width:24px;text-align:right">${cfg.duration}s</span>
                ${defileShotConfigs.length > 1
                    ? `<button onclick="removeDefileShot(${idx})" style="background:none;border:none;cursor:pointer;color:var(--text-muted);font-size:0.8rem;padding:2px 4px;line-height:1" title="Sahneyi kaldır">✕</button>`
                    : ""}
            </div>
            <textarea class="defile-shot-prompt" data-idx="${idx}" rows="2"
                placeholder="Prompt (boş bırakırsan AI üretir)"
                style="width:100%;margin-top:6px;font-size:0.72rem;padding:6px 8px;border:1px solid var(--border-subtle);border-radius:6px;background:var(--bg-primary);color:var(--text-primary);resize:vertical;font-family:inherit"
                oninput="updateDefileShotPrompt(${idx}, this.value)">${cfg.prompt || ""}</textarea>
        </div>
    `).join("");
}

function updateDefileShotDuration(idx, val) {
    defileShotConfigs[idx].duration = parseInt(val);
    renderDefileShotDesigner();
}

function updateDefileShotPrompt(idx, val) {
    defileShotConfigs[idx].prompt = val;
}

function addDefileShot() {
    if (defileShotConfigs.length >= DEFILE_MAX_SHOTS) return;
    const remaining = DEFILE_MAX_TOTAL - _defileTotalDuration();
    const dur = Math.max(DEFILE_MIN_SHOT, Math.min(DEFILE_MAX_SHOT, remaining > 0 ? remaining : DEFILE_MIN_SHOT));
    defileShotConfigs.push({ duration: dur, prompt: "" });
    renderDefileShotDesigner();
}

function removeDefileShot(idx) {
    if (defileShotConfigs.length <= 1) return;
    defileShotConfigs.splice(idx, 1);
    renderDefileShotDesigner();
}

// ─── Aspect Ratio (shared) ──────────────────────────────────────────
let selectedAspectRatio = "9:16";



// ─── Aspect Ratio Cards ──────────────────────────────────────────────
document.querySelectorAll(".ratio-card").forEach(card => {
    card.addEventListener("click", () => {
        document.querySelectorAll(".ratio-card").forEach(c => c.classList.remove("active"));
        card.classList.add("active");
        selectedAspectRatio = card.dataset.ratio;
    });
});

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
    defileShotConfigs = configs.map(c => ({ duration: Number(c.duration) || 5, prompt: c.prompt || "" }));
    renderDefileShotDesigner();
    toggleDefileJsonLoad(false);
}


// ─── Wizard Management ──────────────────────────────────────────────
function openWizard() {
    // "Yeni Video" button now opens Studio mode by default
    openStudio();
}

function closeWizard() {
    if (generationStarted && currentJobId && pollInterval) {
        if (!confirm("Üretim devam ediyor. Yine de kapatmak istiyor musunuz?")) return;
        clearInterval(pollInterval);
        pollInterval = null;
        generationStarted = false;
    }
    wizardModal.style.display = "none";
    document.body.style.overflow = "";
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

    closeLibraryPicker();
}

// Expose library picker functions globally (used by inline onclick in index.html)
window.openLibraryPicker = openLibraryPicker;
window.selectLibraryItem = selectLibraryItem;

// ─── Defile Mode ─────────────────────────────────────────────────
function _hideAllSteps() {
    ["step-3", "step-defile", "step-studio-1", "step-studio-2"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = "none";
    });
}

function openDefile() {
    wizardModal.style.display = "flex";
    document.body.style.overflow = "hidden";
    // Skip choice screen — go directly to NB2 defile
    openDefileNB2();
}

function openDefileNB2() {
    videoMode = "defile";
    defileOutfits = [];
    defileShotConfigs = [{ duration: 5, prompt: "" }];
    defileShotArc = null;
    defileBgUrl = null;
    defileBgExtraUrls = [];
    defileAspectRatio = "9:16";
    defileStartFrameFile = null;
    defileStartFrameUrl = null;
    // Fetch arcs if not already loaded
    if (defileShotArcs.length === 0) fetchDefileShotArcs();
    else renderDefileArcPicker();

    const titleEl = document.getElementById("wizard-title");
    if (titleEl) titleEl.textContent = "Defile — Nano Banana Pro";

    _hideAllSteps();
    document.getElementById("step-defile").style.display = "block";

    const footer = document.getElementById("wizard-footer");
    const backBtn = document.getElementById("wizard-back-btn");
    const nextBtn = document.getElementById("wizard-next-btn");
    if (backBtn) { backBtn.style.display = "inline-flex"; backBtn.textContent = "← Geri"; }
    if (nextBtn) { nextBtn.textContent = "Defile Üret"; nextBtn.disabled = true; }
    if (footer) footer.style.display = "flex";

    // Start frame upload
    _setupDefileStartFrame();

    renderDefileGrid();
    renderDefileShotDesigner();
}


function renderDefileGrid() {
    const list = document.getElementById("defile-elements-list");
    const addBtns = document.getElementById("defile-add-element-btns");
    if (!list) return;

    if (defileOutfits.length === 0) {
        list.innerHTML = `
            <div class="studio-element-empty">
                <div class="studio-element-empty-icon">◈</div>
                <div class="studio-element-empty-text">Henüz element seçilmedi</div>
            </div>`;
    } else {
        const catLabels = { character: "Karakter", costume: "Kostüm", scene: "Mekan", style: "Stil", effect: "Efekt", other: "Diğer", element: "Element", background: "Arka Plan" };
        list.innerHTML = defileOutfits.map((el, idx) => `
            <div class="studio-selected-card" style="margin-bottom:8px">
                <img src="${el.front_url}" alt="${el.name}" class="studio-element-thumb">
                <div class="studio-element-info">
                    <div class="studio-element-name-label">${el.name || `Element ${idx + 1}`}</div>
                    <div class="studio-element-token-badge">${catLabels[el.category] || el.category || "Element"} · ${(el.extra_urls || []).length + 1} görsel</div>
                </div>
                <button class="studio-change-btn" onclick="removeDefileOutfit(${idx})">✕ Çıkar</button>
            </div>`).join("");
    }

    if (addBtns) addBtns.style.display = "block";

    const nextBtn = document.getElementById("wizard-next-btn");
    if (nextBtn) nextBtn.disabled = defileOutfits.length === 0;

    // Re-attach pick/create buttons (use onclick to avoid stacking)
    const pickBtn = document.getElementById("defile-add-outfit-btn");
    if (pickBtn) pickBtn.onclick = openDefileOutfitPicker;
    const createBtn = document.getElementById("defile-create-btn");
    if (createBtn) createBtn.onclick = openStudioCreateModalForDefile;
}

function removeDefileOutfit(idx) {
    defileOutfits.splice(idx, 1);
    renderDefileGrid();
}

function openStudioCreateModalForDefile() {
    // Reuse studio create modal, but on save add to defile instead
    window._defileCreateMode = true;
    openStudioCreateModal();
}

// updateDefileShots removed — replaced by renderDefileShotDesigner

function clearDefileBg() {
    defileBgUrl = null;
    defileBgExtraUrls = [];
    const preview = document.getElementById("defile-bg-preview");
    if (preview) preview.style.display = "none";
}

function _setupDefileStartFrame() {
    const zone = document.getElementById("defile-start-frame-zone");
    const input = document.getElementById("defile-start-frame-input");
    if (!zone || !input) return;
    // Clone to remove old listeners
    const newZone = zone.cloneNode(true);
    zone.parentNode.replaceChild(newZone, zone);
    const newInput = newZone.querySelector("#defile-start-frame-input");
    newZone.addEventListener("click", () => newInput?.click());
    newInput?.addEventListener("change", () => {
        const f = newInput.files?.[0];
        if (!f) return;
        defileStartFrameFile = f;
        defileStartFrameUrl = URL.createObjectURL(f);
        const preview = document.getElementById("defile-start-frame-preview");
        const img = document.getElementById("defile-start-frame-img");
        const nameEl = document.getElementById("defile-start-frame-name");
        if (img) img.src = defileStartFrameUrl;
        if (nameEl) nameEl.textContent = f.name;
        if (preview) preview.style.display = "block";
        newZone.style.display = "none";
    });
    // Reset UI
    const preview = document.getElementById("defile-start-frame-preview");
    if (preview) preview.style.display = "none";
    newZone.style.display = "";
}

function clearDefileStartFrame() {
    defileStartFrameFile = null;
    defileStartFrameUrl = null;
    const preview = document.getElementById("defile-start-frame-preview");
    const zone = document.getElementById("defile-start-frame-zone");
    if (preview) preview.style.display = "none";
    if (zone) zone.style.display = "";
}

// Defile library picker — multi-select outfit
let _defilePickerMode = false;

function openDefileOutfitPicker() {
    _defilePickerMode = true;
    _libPickerTarget = "defile-outfit";
    _libPickerActiveTab = "";

    const modal   = document.getElementById("lib-picker-modal");
    const title   = document.getElementById("lib-picker-title");
    const tabs    = document.getElementById("lib-picker-tabs");
    const grid    = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = "Element Seç";
    const catTabs = [
        { cat: "",         label: "Tümü" },
        { cat: "character", label: "Karakter" },
        { cat: "costume",  label: "Kostüm" },
        { cat: "scene",    label: "Mekan" },
        { cat: "style",    label: "Stil" },
        { cat: "effect",   label: "Efekt" },
        { cat: "other",    label: "Diğer" },
    ];
    tabs.innerHTML = catTabs.map((t, i) =>
        `<button class="lib-picker-tab${i === 0 ? " active" : ""}" data-cat="${t.cat}">${t.label}</button>`
    ).join("");
    tabs.querySelectorAll(".lib-picker-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            tabs.querySelectorAll(".lib-picker-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _libPickerActiveTab = btn.dataset.cat;
            _fetchAndRenderDefileOutfitLibrary(grid, btn.dataset.cat);
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

    _fetchAndRenderDefileOutfitLibrary(grid, "");
}

async function _fetchAndRenderDefileOutfitLibrary(grid, category = "") {
    grid.innerHTML = `<div class="lib-picker-loading">Yükleniyor...</div>`;
    try {
        const url = category ? `/library/items?category=${category}` : `/library/items`;
        const resp = await fetch(url, { headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();
        if (!items.length) {
            grid.innerHTML = `<div class="lib-picker-empty">Kütüphanede element yok.<br><a href="/library" target="_blank">Kütüphaneye git →</a></div>`;
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
                    // Add — include all extra_urls for Kling element creation + NB2
                    const extras = item.extra_urls || [];
                    defileOutfits.push({
                        front_url: item.image_url,
                        side_url: extras[0] || null,
                        back_url: extras[1] || null,
                        extra_urls: extras,
                        name: item.name,
                        category: item.category || "costume",
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
    _libPickerActiveTab = "scene";

    const modal   = document.getElementById("lib-picker-modal");
    const title   = document.getElementById("lib-picker-title");
    const tabs    = document.getElementById("lib-picker-tabs");
    const grid    = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = "Pist Arka Planı Seç";
    tabs.innerHTML = `<button class="lib-picker-tab active" data-cat="scene">Mekanlar</button>`;

    modal.style.display = "flex";
    document.body.style.overflow = "hidden";
    closeBtn.onclick = () => closeLibraryPicker();
    modal.onclick = (e) => { if (e.target === modal) closeLibraryPicker(); };

    _fetchAndRenderLibrary("scene", grid);
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
    document.getElementById("input-summary-panel")?.classList.remove("active");
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

    const defileProvider = document.getElementById("defile-provider-select")?.value || "fal";
    const defileKlingModel = document.getElementById("defile-model-select")?.value || "kling-v3";

    // Upload start frame if provided
    let startFrameUploadUrl = null;
    if (defileStartFrameFile) {
        try {
            updateProgress(5, "Başlangıç karesi yükleniyor...");
            const sfFormData = new FormData();
            sfFormData.append("file", defileStartFrameFile);
            const sfResp = await fetch(`${API_BASE}/api/upload-temp`, { method: "POST", body: sfFormData, headers: getAuthHeaders() });
            if (sfResp.ok) {
                const sfData = await sfResp.json();
                startFrameUploadUrl = sfData.url;
            }
        } catch (e) {
            console.warn("Start frame upload failed, continuing with NB2:", e);
        }
    }

    const payload = {
        outfits: defileOutfits,
        shot_configs: defileShotConfigs,
        runway_background_url: defileBgUrl || null,
        runway_background_extra_urls: defileBgExtraUrls.length > 0 ? defileBgExtraUrls : null,
        start_frame_url: startFrameUploadUrl,
        aspect_ratio: defileAspectRatio,
        generate_audio: document.getElementById("defile-audio-toggle")?.checked ?? true,
        provider: defileProvider,
        kling_model: defileKlingModel,
        shot_arc: defileShotArc,
    };

    // Capture inputs for post-generation summary
    lastGenerationInputs = {
        mod: startFrameUploadUrl ? "Defile (Direkt)" : "Defile (NB2)",
        provider: defileProvider === "kling" ? "Kling Direct" : "fal.ai",
        kiyafetSayisi: defileOutfits.length,
        cekimSayisi: defileShotConfigs.length,
        toplamSure: _defileTotalDuration() + "s",
        aspect: defileAspectRatio,
        ses: (document.getElementById("defile-audio-toggle")?.checked ? "Açık" : "Kapalı"),
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
window.updateDefileShotPrompt    = updateDefileShotPrompt;
window.addDefileShot             = addDefileShot;
window.removeDefileShot          = removeDefileShot;
window.clearDefileBg             = clearDefileBg;
window.clearDefileStartFrame     = clearDefileStartFrame;
window.confirmDefileOutfits      = confirmDefileOutfits;

// ─── Wizard Events ──────────────────────────────────────────────────
document.getElementById("open-wizard-btn")?.addEventListener("click", openWizard);
document.getElementById("nav-defile")?.addEventListener("click", openDefile);
document.getElementById("card-defile")?.addEventListener("click", openDefile);
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
    if (videoMode === "studio") {
        if (studioStep === 1) {
            _studioGoToStep(2);
        } else {
            startStudioGeneration();
        }
        return;
    }
});

wizardBackBtn?.addEventListener("click", () => {
    // Studio: adım 2 → adım 1
    if (videoMode === "studio" && studioStep === 2) {
        _studioGoToStep(1);
        return;
    }
    // Defile → close wizard
    if (videoMode === "defile" &&
        document.getElementById("step-defile")?.style.display === "block") {
        closeWizard();
        return;
    }
});

document.getElementById("defile-nb2-card")?.addEventListener("click", openDefileNB2);
document.getElementById("nav-studio")?.addEventListener("click", openStudio);
document.getElementById("card-studio")?.addEventListener("click", openStudio);


// ─── Studio Mode ──────────────────────────────────────────────────

let studioStep = 1;           // 1 | 2
let studioElements = [];      // [{id, name, image_url, extra_urls}, ...] max 4
let studioStartFile = null;

const STUDIO_MAX_ELEMENTS = 4;
let studioShots = [
    { description: "", duration: 5 },
    { description: "", duration: 5 },
];
let studioAspectRatio = "9:16";
let studioInputMode = "shots";  // "shots" | "text"

const STUDIO_MAX_SHOTS = 5;

// ── Studio Text Mode ─────────────────────────────────────────────────
function toggleStudioInputMode(forceMode) {
    studioInputMode = forceMode || (studioInputMode === "shots" ? "text" : "shots");
    const shotsPanel = document.getElementById("studio-shots-mode");
    const textPanel  = document.getElementById("studio-text-mode-panel");
    const toggleBtn  = document.getElementById("studio-text-mode-btn");
    if (!shotsPanel || !textPanel) return;
    if (studioInputMode === "text") {
        shotsPanel.style.display = "none";
        textPanel.style.display  = "block";
        if (toggleBtn) toggleBtn.textContent = "← Sahne Modu";
    } else {
        shotsPanel.style.display = "block";
        textPanel.style.display  = "none";
        if (toggleBtn) toggleBtn.textContent = "Metin Modu";
    }
}

async function parseStudioScenario() {
    const text = (document.getElementById("studio-scenario-text")?.value || "").trim();
    if (!text) { alert("Lütfen bir senaryo metni girin."); return; }

    const shotCount = parseInt(document.getElementById("studio-parse-shot-count")?.value || "4");
    const totalDur = parseInt(document.getElementById("studio-parse-duration")?.value || "15");

    const btn = document.getElementById("studio-parse-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Analiz ediliyor..."; }

    try {
        const resp = await fetch(`${API_BASE}/api/studio/parse-scenario`, {
            method: "POST",
            headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({ text, shot_count: shotCount, total_duration: totalDur }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        studioShots = (data.shots || []).map(s => ({
            description: s.description || "",
            duration: parseInt(s.duration) || 5,
        }));
        renderStudioShots();
        toggleStudioInputMode("shots");
    } catch (err) {
        alert("Senaryo dönüştürme hatası: " + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Sahnelere Dönüştür"; }
    }
}

window.toggleStudioInputMode = toggleStudioInputMode;
window.parseStudioScenario   = parseStudioScenario;

function _studioTotalDur() {
    return studioShots.reduce((s, sh) => s + sh.duration, 0);
}

function openStudio() {
    videoMode = "studio";
    studioStep = 1;
    studioElements = [];
    studioStartFile = null;
    studioShots = [{ description: "", duration: 5 }, { description: "", duration: 5 }];
    studioAspectRatio = "9:16";
    studioInputMode = "shots";

    const titleEl = document.getElementById("wizard-title");
    if (titleEl) titleEl.textContent = "Stüdyo";
    const stepLabel = document.getElementById("wizard-step-label");
    if (stepLabel) stepLabel.textContent = "Adım 1 / 2";

    _hideAllSteps();
    document.getElementById("step-studio-1").style.display = "block";

    const backBtn = document.getElementById("wizard-back-btn");
    const nextBtn = document.getElementById("wizard-next-btn");
    const footer  = document.getElementById("wizard-footer");
    if (backBtn) backBtn.style.display = "none";
    if (nextBtn) { nextBtn.textContent = "Devam →"; nextBtn.disabled = true; }
    if (footer)  footer.style.display = "flex";

    _renderStudioElementsState();
    _setupStudioStartZone();
    renderStudioShots();

    // Ratio cards
    document.querySelectorAll("#studio-ratio-cards .ratio-card").forEach(card => {
        card.addEventListener("click", () => {
            document.querySelectorAll("#studio-ratio-cards .ratio-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            studioAspectRatio = card.dataset.ratio;
        });
    });

    wizardModal.style.display = "flex";
    document.body.style.overflow = "hidden";
}

function _studioGoToStep(step) {
    studioStep = step;
    _hideAllSteps();
    document.getElementById(`step-studio-${step}`).style.display = "block";

    const stepLabel = document.getElementById("wizard-step-label");
    if (stepLabel) stepLabel.textContent = `Adım ${step} / 2`;

    const backBtn = document.getElementById("wizard-back-btn");
    const nextBtn = document.getElementById("wizard-next-btn");
    const footer  = document.getElementById("wizard-footer");
    if (footer) footer.style.display = "flex";
    if (backBtn) backBtn.style.display = step === 1 ? "none" : "inline-flex";

    if (step === 2) {
        if (nextBtn) { nextBtn.textContent = "Video Üret"; nextBtn.disabled = false; }
        _setupStudioStartZone();
        renderStudioShots();
        // Ensure correct panel visibility on step entry
        toggleStudioInputMode(studioInputMode);
    } else {
        if (nextBtn) { nextBtn.textContent = "Devam →"; nextBtn.disabled = studioElements.length === 0; }
        _renderStudioElementsState();
    }
}

function _renderStudioElementsState() {
    const list    = document.getElementById("studio-elements-list");
    const addBtns = document.getElementById("studio-add-element-btns");
    if (!list) return;

    if (studioElements.length === 0) {
        list.innerHTML = `
            <div class="studio-element-empty">
                <div class="studio-element-empty-icon">◈</div>
                <div class="studio-element-empty-text">Henüz element seçilmedi</div>
            </div>`;
    } else {
        list.innerHTML = studioElements.map((el, idx) => `
            <div class="studio-selected-card" style="margin-bottom:8px">
                <img src="${el.image_url}" alt="${el.name}" class="studio-element-thumb">
                <div class="studio-element-info">
                    <div class="studio-element-name-label">${el.name}</div>
                    <div class="studio-element-token-badge">@Element${idx + 1} → @${el.name}</div>
                </div>
                <button class="studio-change-btn" onclick="removeStudioElement(${idx})">✕ Çıkar</button>
            </div>`).join("");
    }

    if (addBtns) addBtns.style.display = studioElements.length >= STUDIO_MAX_ELEMENTS ? "none" : "block";

    const nextBtn = document.getElementById("wizard-next-btn");
    if (nextBtn && studioStep === 1) nextBtn.disabled = studioElements.length === 0;

    // Re-attach pick/create buttons
    document.getElementById("studio-pick-btn")?.addEventListener("click", openStudioElementPicker);
    document.getElementById("studio-create-btn")?.addEventListener("click", openStudioCreateModal);
}

function removeStudioElement(idx) {
    studioElements.splice(idx, 1);
    _renderStudioElementsState();
    renderStudioShots();
}
window.removeStudioElement = removeStudioElement;

function _setupStudioStartZone() {
    const zone  = document.getElementById("studio-start-zone");
    const input = document.getElementById("studio-start-input");
    if (!zone || !input) return;

    const newZone = zone.cloneNode(true);
    zone.parentNode.replaceChild(newZone, zone);
    const newInput = newZone.querySelector("input[type=file]");

    newZone.addEventListener("click", () => newInput?.click());
    newInput?.addEventListener("change", () => {
        const f = newInput.files?.[0];
        if (!f) return;
        studioStartFile = f;
        const label = newZone.querySelector(".upload-label");
        if (label) label.textContent = f.name.length > 20 ? f.name.slice(0, 18) + "…" : f.name;
        newZone.classList.add("has-file");
    });
}

function renderStudioShots() {
    const container = document.getElementById("studio-shots-container");
    const durLabel  = document.getElementById("studio-total-dur-label");
    if (!container) return;
    if (durLabel) durLabel.textContent = `${_studioTotalDur()}s toplam`;

    const addBtn = document.getElementById("studio-add-shot-btn");
    if (addBtn) addBtn.style.display = studioShots.length >= STUDIO_MAX_SHOTS ? "none" : "inline-flex";

    const elTokens = studioElements.length > 0 ? studioElements.map(e => `@${e.name}`).join(", ") : "@Element";
    const placeholder = `${elTokens} ile bu çekimi tanımlayın (örn. ${studioElements[0] ? "@" + studioElements[0].name : "@Element"} yavaşça kameraya doğru yürüyor) veya ✦ AI'ya bırakın`;

    container.innerHTML = studioShots.map((sh, idx) => `
        <div class="studio-shot-card">
            <div class="studio-shot-header">
                <span class="studio-shot-num">Çekim ${idx + 1}</span>
                <div style="display:flex;align-items:center;gap:6px">
                    <span class="studio-shot-dur-label">${sh.duration}s</span>
                    ${studioShots.length > 1 ? `<button class="shot-remove-btn" onclick="removeStudioShot(${idx})">✕</button>` : ""}
                </div>
            </div>
            <textarea class="form-input studio-shot-desc"
                id="studio-shot-desc-${idx}"
                placeholder="${placeholder}"
                oninput="updateStudioShotDesc(${idx}, this.value)">${sh.description || ""}</textarea>
            <div class="shot-dur-row" style="margin-top:6px">
                <div class="shot-dur-labels">
                    <span style="font-size:0.72rem;color:var(--text-secondary)">Süre</span>
                    <span class="studio-shot-dur-label">${sh.duration}s</span>
                </div>
                <input type="range" class="shot-dur-slider" min="3" max="10" value="${sh.duration}"
                       oninput="updateStudioShotDur(${idx}, this.value, this.closest('.studio-shot-card'))">
            </div>
        </div>
    `).join("");
}

function updateStudioShotDesc(idx, val) {
    studioShots[idx].description = val;
}

function updateStudioShotDur(idx, val, card) {
    studioShots[idx].duration = parseInt(val);
    if (card) card.querySelectorAll(".studio-shot-dur-label").forEach(el => el.textContent = val + "s");
    const durLabel = document.getElementById("studio-total-dur-label");
    if (durLabel) durLabel.textContent = `${_studioTotalDur()}s toplam`;
}

function removeStudioShot(idx) {
    if (studioShots.length <= 1) return;
    studioShots.splice(idx, 1);
    renderStudioShots();
}

function addStudioShot() {
    if (studioShots.length >= STUDIO_MAX_SHOTS) return;
    studioShots.push({ description: "", duration: 5 });
    renderStudioShots();
}

window.removeStudioShot       = removeStudioShot;
window.updateStudioShotDesc   = updateStudioShotDesc;
window.updateStudioShotDur    = updateStudioShotDur;

// ── Element Picker ───────────────────────────────────────────────

function openStudioElementPicker() {
    const modal  = document.getElementById("lib-picker-modal");
    const title  = document.getElementById("lib-picker-title");
    const tabs   = document.getElementById("lib-picker-tabs");
    const grid   = document.getElementById("lib-picker-grid");
    const closeBtn = document.getElementById("lib-picker-close");

    title.textContent = "Element Seç";
    tabs.innerHTML = `<button class="lib-picker-tab active" data-cat="element">Elementler</button>`;

    modal.style.display = "flex";
    document.body.style.overflow = "hidden";

    closeBtn.onclick = () => closeLibraryPicker();
    modal.onclick = (e) => { if (e.target === modal) closeLibraryPicker(); };

    _fetchAndRenderStudioElements(grid);
}

async function _fetchAndRenderStudioElements(grid) {
    grid.innerHTML = `<div class="lib-picker-loading">Yükleniyor...</div>`;
    try {
        const resp = await fetch("/library/items", { headers: getAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();
        if (!items.length) {
            grid.innerHTML = `<div class="lib-picker-empty">Henüz element oluşturulmadı.<br><button onclick="closeLibraryPicker();openStudioCreateModal();" style="margin-top:10px;padding:6px 14px;background:var(--accent);color:var(--btn-text);border:none;border-radius:6px;cursor:pointer;font-size:0.78rem">+ Yeni Element Oluştur</button></div>`;
            return;
        }

        grid.innerHTML = items.map(item => {
            const extras = item.extra_urls || [];
            const badge = extras.length > 0 ? `<div class="lib-picker-extras-badge">+${extras.length}</div>` : "";
            return `
                <div class="lib-picker-item" data-id="${item.id}">
                    <img src="${item.image_url}" alt="${item.name}" loading="lazy">
                    <div class="lib-picker-item-name">${item.name}</div>
                    ${badge}
                </div>`;
        }).join("");

        const itemMap = Object.fromEntries(items.map(it => [it.id, it]));
        grid.querySelectorAll(".lib-picker-item").forEach(el => {
            el.addEventListener("click", () => {
                const item = itemMap[el.dataset.id];
                if (!item) return;
                if (studioElements.find(e => e.id === item.id)) { alert("Bu element zaten seçili."); return; }
                if (studioElements.length >= STUDIO_MAX_ELEMENTS) { alert("En fazla 4 element seçebilirsiniz."); return; }
                studioElements.push(item);
                closeLibraryPicker();
                _renderStudioElementsState();
                renderStudioShots();
                const nextBtn = document.getElementById("wizard-next-btn");
                if (nextBtn) nextBtn.disabled = false;
            });
        });
    } catch (err) {
        grid.innerHTML = `<div class="lib-picker-empty">Yüklenemedi: ${err.message}</div>`;
    }
}

// ── Element Oluştur Modal ────────────────────────────────────────

let _createFrontFile  = null;
let _createAngle1File = null;
let _createAngle2File = null;
let _createVideoFile  = null;
let _createMode = "image"; // "image" | "video"

function _setCreateMode(mode) {
    _createMode = mode;
    const imageBox = document.getElementById("create-uploads-image");
    const videoBox = document.getElementById("create-uploads-video");
    const imgBtn   = document.getElementById("create-mode-image");
    const vidBtn   = document.getElementById("create-mode-video");
    const hint     = document.getElementById("create-mode-hint");
    if (mode === "video") {
        if (imageBox) imageBox.style.display = "none";
        if (videoBox) videoBox.style.display = "";
        imgBtn?.classList.remove("active");
        vidBtn?.classList.add("active");
        if (hint) hint.innerHTML = "Tek <b>.mp4/.mov</b> yükleyin (3–8s, 9:16 veya 16:9). Kling native <b>video_refer</b> element oluşturur — element yalnızca <b>kling-video-o3+</b> ile kullanılır. İşlem ~2-3 dakika sürebilir.";
    } else {
        if (imageBox) imageBox.style.display = "";
        if (videoBox) videoBox.style.display = "none";
        vidBtn?.classList.remove("active");
        imgBtn?.classList.add("active");
        if (hint) hint.innerHTML = "Bir ana görsel yükleyin. Daha iyi tutarlılık için farklı açılardan ek görseller ekleyebilirsiniz (opsiyonel).";
    }
    _updateCreateSaveBtn();
}

function openStudioCreateModal() {
    _createFrontFile = _createAngle1File = _createAngle2File = null;
    _createVideoFile = null;
    _createMode = "image";
    const modal = document.getElementById("studio-create-modal");
    if (!modal) return;
    modal.style.display = "flex";
    document.body.style.overflow = "hidden";

    // Reset fields
    const nameInput = document.getElementById("create-elem-name");
    if (nameInput) nameInput.value = "";
    const fabricSelect = document.getElementById("create-elem-fabric");
    if (fabricSelect) fabricSelect.value = "";
    const descTextarea = document.getElementById("create-elem-description");
    if (descTextarea) descTextarea.value = "";

    // Show category selector for defile mode, hide for studio mode
    const catSelect = document.getElementById("create-elem-category");
    if (catSelect) {
        if (window._defileCreateMode) {
            catSelect.style.display = "";
            catSelect.value = "costume";
        } else {
            catSelect.style.display = "none";
            catSelect.value = "element";
        }
    }

    _setCreateMode("image");

    // Setup upload zones (image + video)
    [
        ["create-front-zone",  "create-front-input",  f => { _createFrontFile  = f; _updateCreateSaveBtn(); }],
        ["create-angle1-zone", "create-angle1-input", f => { _createAngle1File = f; }],
        ["create-angle2-zone", "create-angle2-input", f => { _createAngle2File = f; }],
        ["create-video-zone",  "create-video-input",  f => { _createVideoFile  = f; _updateCreateSaveBtn(); }],
    ].forEach(([zoneId, inputId, onFile]) => {
        const zone = document.getElementById(zoneId);
        const inp  = document.getElementById(inputId);
        if (!zone || !inp) return;
        const newZone = zone.cloneNode(true);
        zone.parentNode.replaceChild(newZone, zone);
        const newInp = newZone.querySelector("input[type=file]");
        newZone.addEventListener("click", () => newInp?.click());
        newInp?.addEventListener("change", () => {
            const f = newInp.files?.[0];
            if (!f) return;
            onFile(f);
            const lbl = newZone.querySelector(".upload-label");
            if (lbl) lbl.textContent = f.name.length > 14 ? f.name.slice(0, 12) + "…" : f.name;
            newZone.classList.add("has-file");
        });
    });

    document.getElementById("create-mode-image")?.addEventListener("click", () => _setCreateMode("image"));
    document.getElementById("create-mode-video")?.addEventListener("click", () => _setCreateMode("video"));
    document.getElementById("create-elem-name")?.addEventListener("input", _updateCreateSaveBtn);
    document.getElementById("studio-create-close")?.addEventListener("click", closeStudioCreateModal);
    document.getElementById("studio-create-save-btn")?.addEventListener("click", saveStudioElement);
    document.getElementById("create-elem-auto-btn")?.addEventListener("click", autoDescribeElement);
    modal.addEventListener("click", e => { if (e.target === modal) closeStudioCreateModal(); });
}

async function autoDescribeElement() {
    const btn   = document.getElementById("create-elem-auto-btn");
    const label = document.getElementById("create-elem-auto-label");
    const icon  = document.getElementById("create-elem-auto-icon");
    const desc  = document.getElementById("create-elem-description");
    if (!btn || !desc) return;

    if (!_createFrontFile) {
        alert("Önce ana görseli yükleyin.");
        return;
    }
    if (_createMode === "video") {
        alert("Auto yalnızca fotoğraf modunda çalışır. Ana görseli fotoğraf olarak yükleyin.");
        return;
    }

    const origLabel = label ? label.textContent : "Auto";
    btn.disabled = true;
    if (label) label.textContent = "Analiz ediliyor...";
    if (icon)  icon.textContent  = "⏳";

    try {
        const fd = new FormData();
        fd.append("file", _createFrontFile);
        const resp = await fetch("/library/describe-image", {
            method: "POST",
            headers: getAuthHeaders(),
            body: fd,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const text = (data.description || "").trim();
        if (!text) throw new Error("boş açıklama");
        desc.value = text;
        desc.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (err) {
        alert("Otomatik açıklama üretilemedi: " + err.message);
    } finally {
        btn.disabled = false;
        if (label) label.textContent = origLabel;
        if (icon)  icon.textContent  = "✦";
    }
}

function _updateCreateSaveBtn() {
    const btn = document.getElementById("studio-create-save-btn");
    const name = (document.getElementById("create-elem-name")?.value || "").trim();
    const primary = _createMode === "video" ? _createVideoFile : _createFrontFile;
    if (btn) btn.disabled = !(primary && name);
}

function closeStudioCreateModal() {
    const modal = document.getElementById("studio-create-modal");
    if (modal) modal.style.display = "none";
    document.body.style.overflow = "";
    window._defileCreateMode = false;
}

async function saveStudioElement() {
    const btn  = document.getElementById("studio-create-save-btn");
    const name = (document.getElementById("create-elem-name")?.value || "").trim();
    const isVideo = _createMode === "video";
    const primary = isVideo ? _createVideoFile : _createFrontFile;
    if (!primary || !name) return;

    if (btn) {
        btn.disabled = true;
        btn.textContent = isVideo ? "Video element oluşturuluyor (~2-3 dk)..." : "Kaydediliyor...";
    }

    const catSelect = document.getElementById("create-elem-category");
    const category = catSelect ? catSelect.value : "element";
    const fabric = (document.getElementById("create-elem-fabric")?.value || "").trim();
    const description = (document.getElementById("create-elem-description")?.value || "").trim();

    const fd = new FormData();
    fd.append("name",     name);
    fd.append("category", category);
    if (fabric)      fd.append("fabric", fabric);
    if (description) fd.append("description", description);
    fd.append("file",     primary);
    if (!isVideo) {
        if (_createAngle1File) fd.append("file2", _createAngle1File);
        if (_createAngle2File) fd.append("file3", _createAngle2File);
    }

    try {
        const resp = await fetch("/library/items", { method: "POST", headers: getAuthHeaders(), body: fd });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const item = await resp.json();

        if (window._defileCreateMode) {
            // Add to defile outfits instead of studio elements
            window._defileCreateMode = false;
            defileOutfits.push({
                front_url: item.image_url,
                side_url: (item.extra_urls || [])[0] || null,
                back_url: (item.extra_urls || [])[1] || null,
                extra_urls: item.extra_urls || [],
                name: item.name,
                category: item.category,
            });
            closeStudioCreateModal();
            renderDefileGrid();
        } else {
            if (studioElements.length < STUDIO_MAX_ELEMENTS) studioElements.push(item);
            closeStudioCreateModal();
            _renderStudioElementsState();
            renderStudioShots();
        }
        const nextBtn = document.getElementById("wizard-next-btn");
        if (nextBtn) nextBtn.disabled = false;
    } catch (err) {
        alert("Element kaydedilemedi: " + err.message);
        if (btn) { btn.disabled = false; btn.textContent = "Kaydet ve Seç"; }
    }
}

// ── AI Çekim Öner ────────────────────────────────────────────────

document.getElementById("studio-add-shot-btn")?.addEventListener("click", addStudioShot);

document.getElementById("studio-ai-shots-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("studio-ai-shots-btn");
    if (!btn || studioElements.length === 0) return;
    btn.disabled = true;
    btn.textContent = "✦ Üretiliyor...";

    const fd = new FormData();
    fd.append("element_image_url", studioElements[0].image_url);
    fd.append("elements_json", JSON.stringify(studioElements.map(e => ({ name: e.name, front_url: e.image_url, extra_urls: e.extra_urls || [] }))));
    fd.append("shot_count", String(studioShots.length));
    if (studioStartFile) fd.append("start_frame", studioStartFile);

    try {
        const resp = await fetch("/api/studio/ai-shots", { method: "POST", headers: getAuthHeaders(), body: fd });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const shots = data.shots || [];
        shots.forEach((s, i) => {
            if (i < studioShots.length) {
                studioShots[i].description = s.description;
                studioShots[i].duration    = s.duration || 5;
            }
        });
        renderStudioShots();
    } catch (err) {
        alert("AI çekim üretilemedi: " + err.message);
    }

    btn.disabled = false;
    btn.textContent = "✦ AI Çekim Öner";
});

// ── Studio Generation ────────────────────────────────────────────

async function startStudioGeneration() {
    if (studioElements.length === 0) { alert("Önce bir element seçin."); return; }

    hideError();
    _hideAllSteps();
    document.getElementById("step-3").style.display = "block";

    resultSec.classList.remove("active");
    document.getElementById("input-summary-panel")?.classList.remove("active");
    progressSec.classList.add("active");
    generationStarted = true;
    wizardFooter.style.display = "none";
    step4Title.textContent = "Video Üretiliyor...";
    step4Sub.textContent = `Stüdyo modu: ${studioShots.length} çekim, ${_studioTotalDur()}s toplam. Lütfen bekleyin.`;
    resetSteps();
    updateProgress(0, "Başlatılıyor...");

    // Capture inputs for post-generation summary
    const providerVal = document.getElementById("studio-provider-select")?.value || "fal";
    lastGenerationInputs = {
        mod: "Stüdyo",
        provider: providerVal === "kling" ? "Kling Direct" : "fal.ai",
        elementler: studioElements.map(e => e.name).join(", ") || "—",
        aspect: studioAspectRatio || "9:16",
        cekimSayisi: studioShots.length,
        toplamSure: _studioTotalDur() + "s",
        ses: (document.getElementById("studio-audio-toggle")?.checked ? "Açık" : "Kapalı"),
    };

    // Replace @ElementName tokens with @ElementN (fal.ai positional tokens)
    let resolvedShots = studioShots.map(s => ({ ...s, description: s.description || "" }));
    studioElements.forEach((el, idx) => {
        const re = new RegExp(`@${el.name}`.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
        resolvedShots = resolvedShots.map(s => ({ ...s, description: s.description.replace(re, `@Element${idx + 1}`) }));
    });

    // Build shots JSON (ShotConfig format)
    const shotsJson = JSON.stringify(resolvedShots.map(s => ({
        camera_move: "",
        duration: s.duration,
        description: s.description,
        camera_angle: "",
        shot_size: "",
    })));

    // Build elements JSON for backend (multi-element support)
    const elementsJson = JSON.stringify(studioElements.map(el => ({
        front_url: el.image_url,
        extra_urls: el.extra_urls || [],
        name: el.name,
    })));

    const formData = new FormData();
    formData.append("generation_mode",   "studio");
    formData.append("elements_json",     elementsJson);
    // Keep first element in legacy fields for backward compat
    formData.append("library_front_url", studioElements[0].image_url);
    const extras = studioElements[0].extra_urls || [];
    if (extras[0]) formData.append("library_side_url", extras[0]);
    if (extras[1]) formData.append("library_back_url", extras[1]);
    if (studioStartFile) formData.append("ozel_start_frame", studioStartFile);
    formData.append("shots",          shotsJson);
    formData.append("aspect_ratio",   studioAspectRatio);
    formData.append("generate_audio", document.getElementById("studio-audio-toggle")?.checked ? "true" : "false");
    formData.append("provider", document.getElementById("studio-provider-select")?.value || "fal");
    formData.append("kling_model", document.getElementById("studio-model-select")?.value || "kling-v3");
    // Required dummy front_image field (pipeline uses library_front_url when provided)
    formData.append("front_image", new Blob([], { type: "image/jpeg" }), "placeholder.jpg");

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

        if (job.analysis)      showAnalysis(job.analysis);
        if (job.scene_prompt)  showPrompt(job.scene_prompt);
        if (job.debug_payload) lastDebugPayload = job.debug_payload;

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

    // Show input summary panel
    _showInputSummary();

    loadRecentVideos();
}

function _showInputSummary() {
    const panel  = document.getElementById("input-summary-panel");
    const grid   = document.getElementById("input-summary-grid");
    const toggle = document.getElementById("input-summary-toggle");
    const body   = document.getElementById("input-summary-body");
    if (!panel || !grid || !lastGenerationInputs) return;

    const labelMap = {
        mod:           "Mod",
        provider:      "Motor",
        lokasyon:      "Lokasyon",
        elementler:    "Elementler",
        kiyafetSayisi: "Kıyafet",
        aspect:        "Oran",
        cekimSayisi:   "Çekim",
        toplamSure:    "Süre",
        ses:           "Ses",
        mood:          "Mood",
    };
    grid.innerHTML = Object.entries(lastGenerationInputs)
        .map(([k, v]) => `<div class="analysis-item"><div class="label">${labelMap[k] || k}</div><div class="value">${v}</div></div>`)
        .join("");

    // Show raw API payload if available
    let payloadEl = document.getElementById("input-summary-payload");
    if (!payloadEl) {
        payloadEl = document.createElement("div");
        payloadEl.id = "input-summary-payload";
        payloadEl.style.cssText = "margin-top:12px";
        body.appendChild(payloadEl);
    }
    if (lastDebugPayload) {
        payloadEl.innerHTML = `<div style="font-size:0.65rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:var(--accent-warm);margin-bottom:6px">API Payload</div><pre class="debug-payload-pre">${JSON.stringify(lastDebugPayload, null, 2)}</pre>`;
    } else {
        payloadEl.innerHTML = "";
    }

    panel.classList.add("active");

    // Toggle collapse behaviour
    toggle.onclick = () => {
        const isOpen = body.classList.toggle("open");
        toggle.classList.toggle("open", isOpen);
    };
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
    document.getElementById("input-summary-panel")?.classList.remove("active");
    document.getElementById("input-summary-body")?.classList.remove("open");
    document.getElementById("input-summary-toggle")?.classList.remove("open");
    lastGenerationInputs = null;
    lastDebugPayload     = null;
    analysisPanel.classList.remove("active");
    promptPanel.classList.remove("active");
    currentJobId = null;
    generationStarted = false;
    pollInterval = null;
    // Reset defile state
    videoMode = "defile";
    defileOutfits = [];
    defileShotConfigs = [{ duration: 5, prompt: "" }];
    defileBgUrl = null;
    defileStartFrameFile = null;
    defileStartFrameUrl = null;
    step4Title.textContent = "Video Üretmeye Hazır";
    step4Sub.textContent = "Ayarlarınız kaydedildi. Üretimi başlatın.";
    currentWizardStep = 1;
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

// ─── Pending Order Auto-Apply ──────────────────────────────────────────────
// If admin opened this page from "Studio'da Uygula", pre-fill studio shots.
(function applyPendingOrder() {
    const raw = localStorage.getItem("pendingOrderShots");
    const code = localStorage.getItem("pendingOrderCode");
    if (!raw) return;
    try {
        const shots = JSON.parse(raw);
        if (!Array.isArray(shots) || shots.length === 0) return;
        localStorage.removeItem("pendingOrderShots");
        localStorage.removeItem("pendingOrderCode");
        // Small delay so the page fully initialises before opening studio
        setTimeout(() => {
            openStudio();
            studioShots = shots.map(s => ({
                description: s.description || "",
                duration: parseInt(s.duration) || 5,
            }));
            renderStudioShots();
            // Show a banner so admin knows which order was applied
            if (code) {
                const banner = document.createElement("div");
                banner.style.cssText = "position:fixed;top:70px;left:50%;transform:translateX(-50%);background:#1d4ed8;color:#fff;padding:8px 20px;border-radius:8px;font-size:0.82rem;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.4)";
                banner.textContent = `Sipariş kodu ${code} uygulandı — ${shots.length} sahne yüklendi.`;
                document.body.appendChild(banner);
                setTimeout(() => banner.remove(), 4000);
            }
        }, 300);
    } catch (e) {
        console.warn("pendingOrderShots parse error:", e);
    }
})();

// Re-apply carousel translate on resize
window.addEventListener("resize", () => {
    shots.forEach((_, idx) => _applyCamTranslate(idx));
});

// ─── Kling Model Selectors ─────────────────────────────────────────────────
function toggleDefileModelSelect() {
    const provider = document.getElementById("defile-provider-select")?.value;
    const wrapper = document.getElementById("defile-model-wrapper");
    if (wrapper) wrapper.style.display = provider === "kling" ? "" : "none";
}

function toggleStudioModelSelect() {
    const provider = document.getElementById("studio-provider-select")?.value;
    const wrapper = document.getElementById("studio-model-wrapper");
    if (wrapper) wrapper.style.display = provider === "kling" ? "flex" : "none";
}

// ─── Kling Prompt Composer Modal ───────────────────────────────────────────
(function () {
    const modal = document.getElementById("kling-prompt-modal");
    if (!modal) return;

    const openBtn = document.getElementById("open-kling-prompt-btn");
    const closeBtn = document.getElementById("kling-prompt-close");
    const generateBtn = document.getElementById("kp-generate-btn");
    const copyAllBtn = document.getElementById("kp-copy-all-btn");
    const output = document.getElementById("kp-output");
    const errorBox = document.getElementById("kp-error");
    const tagsInput = document.getElementById("kp-element-tags");
    const nShotsSel = document.getElementById("kp-n-shots");
    const totalDurInput = document.getElementById("kp-total-duration");
    const arcSel = document.getElementById("kp-arc-tone");
    const noteInput = document.getElementById("kp-director-note");
    const includeNegChk = document.getElementById("kp-include-negative");
    const sfZone = document.getElementById("kp-start-frame-zone");
    const sfInput = document.getElementById("kp-start-frame-input");
    const sfLabel = document.getElementById("kp-start-frame-label");
    const sfHint = document.getElementById("kp-start-frame-hint");
    const sfPreview = document.getElementById("kp-start-frame-preview");

    // Technique picker + continuation
    const techWrap = document.getElementById("kp-shot-techniques-wrap");
    const techRow = document.getElementById("kp-shot-techniques");
    const techOverlay = document.getElementById("kp-tech-overlay");
    const techGrid = document.getElementById("kp-tech-grid");
    const techCloseBtn = document.getElementById("kp-tech-close");
    const techClearBtn = document.getElementById("kp-tech-clear");
    const techShotIdx = document.getElementById("kp-tech-shot-idx");
    const contToggle = document.getElementById("kp-continuation-toggle");
    const contBox = document.getElementById("kp-continuation-box");
    const prevPromptInput = document.getElementById("kp-previous-prompt");

    let currentMode = "multi_shot";
    let startFrameUrl = null;
    let startFrameUploading = false;
    let techniques = [];  // loaded once from backend
    let shotTechniques = [];  // aligned to n_shots; null = AI picks
    let activePickerShot = -1;

    function open() {
        modal.style.display = "flex";
        errorBox.style.display = "none";
        output.style.display = "none";
        copyAllBtn.style.display = "none";
        loadTechniques();
        rebuildShotChips();
        updateTechniqueVisibility();
    }
    function close() { modal.style.display = "none"; }

    sfZone?.addEventListener("click", (e) => {
        if (e.target === sfInput) return;
        sfInput?.click();
    });
    sfInput?.addEventListener("change", async () => {
        const file = sfInput.files?.[0];
        if (!file) return;
        errorBox.style.display = "none";
        startFrameUploading = true;
        startFrameUrl = null;
        sfHint.textContent = "Yükleniyor…";
        const reader = new FileReader();
        reader.onload = (ev) => {
            sfPreview.src = ev.target.result;
            sfPreview.style.display = "block";
        };
        reader.readAsDataURL(file);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const resp = await fetch("/api/upload-temp", {
                method: "POST",
                body: fd,
                headers: getAuthHeaders(),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            startFrameUrl = data.url;
            sfLabel.textContent = "✓ " + file.name;
            sfHint.textContent = "Hazır — GPT bu görseli analiz edip prompt'u ona göre yazacak";
        } catch (e) {
            showError("Start frame yüklenemedi: " + (e.message || e));
            sfHint.textContent = "Yükleme başarısız — tekrar deneyin";
            startFrameUrl = null;
        } finally {
            startFrameUploading = false;
        }
    });

    openBtn?.addEventListener("click", open);
    closeBtn?.addEventListener("click", close);
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

    // Mode toggle
    document.querySelectorAll(".kp-mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".kp-mode-btn").forEach((b) => {
                b.classList.remove("active");
                b.querySelector("div").style.color = "#9aa0a6";
                b.firstChild.textContent = b.firstChild.textContent.replace(/^[✓]\s*/, "");
            });
            btn.classList.add("active");
            currentMode = btn.dataset.mode;
            const label = btn.firstChild;
            if (!label.textContent.startsWith("✓")) {
                label.textContent = "✓ " + label.textContent.trim();
            }
            updateTechniqueVisibility();
        });
    });

    // n_shots change → rebuild technique chips
    nShotsSel?.addEventListener("change", rebuildShotChips);

    // Continuation toggle
    contToggle?.addEventListener("change", () => {
        contBox.style.display = contToggle.checked ? "block" : "none";
    });

    // Technique picker wiring
    techCloseBtn?.addEventListener("click", closeTechniquePicker);
    techOverlay?.addEventListener("click", (e) => {
        if (e.target === techOverlay) closeTechniquePicker();
    });
    techClearBtn?.addEventListener("click", () => {
        if (activePickerShot >= 0) {
            shotTechniques[activePickerShot] = null;
            rebuildShotChips();
        }
        closeTechniquePicker();
    });

    function updateTechniqueVisibility() {
        if (!techWrap) return;
        techWrap.style.display = currentMode === "custom_multi_shot" ? "block" : "none";
    }

    async function loadTechniques() {
        if (techniques.length > 0) return;
        try {
            const resp = await fetch("/api/kling-prompt/techniques", { headers: getAuthHeaders() });
            if (!resp.ok) return;
            const data = await resp.json();
            techniques = data.techniques || [];
        } catch { /* silent — picker just won't show items */ }
    }

    function rebuildShotChips() {
        if (!techRow) return;
        const n = parseInt(nShotsSel.value, 10) || 1;
        // resize shotTechniques preserving existing picks
        while (shotTechniques.length < n) shotTechniques.push(null);
        if (shotTechniques.length > n) shotTechniques = shotTechniques.slice(0, n);

        techRow.innerHTML = "";
        for (let i = 0; i < n; i++) {
            const tech = shotTechniques[i] ? techniques.find((t) => t.id === shotTechniques[i]) : null;
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "wizard-btn-ghost";
            chip.style.cssText = "font-size:11px;padding:6px 10px;display:flex;align-items:center;gap:6px;" +
                (tech ? "background:rgba(99,102,241,0.18);border-color:rgba(99,102,241,0.5);color:#c7d2fe" : "");
            chip.innerHTML = `<span style="font-weight:700">Shot ${i + 1}</span>` +
                `<span style="color:${tech ? '#c7d2fe' : '#6b7280'}">${tech ? escapeHtml(tech.tr_label) : 'AI seçsin'}</span>`;
            chip.addEventListener("click", () => openTechniquePicker(i));
            techRow.appendChild(chip);
        }
    }

    function openTechniquePicker(shotIdx) {
        activePickerShot = shotIdx;
        techShotIdx.textContent = String(shotIdx + 1);
        techGrid.innerHTML = "";
        const current = shotTechniques[shotIdx];
        techniques.forEach((t) => {
            const card = document.createElement("button");
            card.type = "button";
            const active = t.id === current;
            card.style.cssText = "text-align:left;padding:10px 12px;background:" +
                (active ? "rgba(99,102,241,0.18)" : "rgba(255,255,255,0.03)") +
                ";border:1px solid " + (active ? "rgba(99,102,241,0.6)" : "rgba(255,255,255,0.08)") +
                ";border-radius:8px;cursor:pointer;transition:all 0.15s;color:#e5e7eb";
            card.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:3px;color:${active ? '#c7d2fe' : '#f3f4f6'}">${escapeHtml(t.tr_label)}</div>` +
                `<div style="font-size:11px;color:#9aa0a6;line-height:1.45">${escapeHtml(t.tr_desc)}</div>`;
            card.addEventListener("mouseenter", () => {
                if (!active) card.style.background = "rgba(99,102,241,0.08)";
            });
            card.addEventListener("mouseleave", () => {
                if (!active) card.style.background = "rgba(255,255,255,0.03)";
            });
            card.addEventListener("click", () => {
                shotTechniques[shotIdx] = t.id;
                rebuildShotChips();
                closeTechniquePicker();
            });
            techGrid.appendChild(card);
        });
        techOverlay.style.display = "flex";
    }

    function closeTechniquePicker() {
        techOverlay.style.display = "none";
        activePickerShot = -1;
    }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.style.display = "block";
    }

    async function copyText(text, btn) {
        try {
            await navigator.clipboard.writeText(text);
            const orig = btn.textContent;
            btn.textContent = "✓ Kopyalandı";
            setTimeout(() => { btn.textContent = orig; }, 1400);
        } catch {
            showError("Panoya kopyalanamadı.");
        }
    }

    function negativeCardHtml(neg) {
        return `
          <div style="background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:10px;padding:12px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
              <div style="font-size:11px;font-weight:700;color:#fca5a5;text-transform:uppercase;letter-spacing:0.5px">Negative Prompt</div>
              <button class="wizard-btn-ghost kp-copy-neg" style="font-size:11px;padding:4px 10px">📋 Kopyala</button>
            </div>
            <div class="kp-neg-prompt" style="font-size:12.5px;line-height:1.55;color:#fecaca">${escapeHtml(neg)}</div>
          </div>
        `;
    }

    function renderCustom(data) {
        const includeNeg = !!includeNegChk?.checked;
        const parts = [];
        data.shots.forEach((s) => {
            parts.push(`
              <div class="kp-shot-card" style="background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.2);border-radius:10px;padding:12px;margin-bottom:10px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                  <div style="font-size:12px;font-weight:700;color:#a5b4fc">Shot ${s.shot_no} · ${s.time_range} · ${s.camera_type}</div>
                  <button class="wizard-btn-ghost kp-copy-shot" data-shot="${s.shot_no}" style="font-size:11px;padding:4px 10px">📋 Kopyala</button>
                </div>
                <div class="kp-shot-prompt" style="font-size:12.5px;line-height:1.55;color:#e5e7eb;white-space:pre-wrap">${escapeHtml(s.prompt)}</div>
              </div>
            `);
        });
        if (includeNeg) parts.push(negativeCardHtml(data.negative_prompt));
        output.innerHTML = parts.join("");
        output.style.display = "block";

        output.querySelectorAll(".kp-copy-shot").forEach((btn) => {
            btn.addEventListener("click", () => {
                const n = parseInt(btn.dataset.shot, 10);
                const shot = data.shots.find((s) => s.shot_no === n);
                if (shot) copyText(shot.prompt, btn);
            });
        });
        output.querySelector(".kp-copy-neg")?.addEventListener("click", (e) => {
            copyText(data.negative_prompt, e.currentTarget);
        });

        copyAllBtn.style.display = "inline-flex";
        copyAllBtn.onclick = () => {
            let all = data.shots.map((s) => `Shot ${s.shot_no} (${s.time_range}, ${s.camera_type}):\n${s.prompt}`).join("\n\n");
            if (includeNeg) all += `\n\nNegative Prompt:\n${data.negative_prompt}`;
            copyText(all, copyAllBtn);
        };
    }

    function renderMulti(data) {
        const includeNeg = !!includeNegChk?.checked;
        let html = `
          <div style="background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.2);border-radius:10px;padding:12px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <div style="font-size:12px;font-weight:700;color:#a5b4fc">Multi-Shot Prompt (tek paragraf)</div>
              <button class="wizard-btn-ghost kp-copy-main" style="font-size:11px;padding:4px 10px">📋 Kopyala</button>
            </div>
            <div class="kp-main-prompt" style="font-size:12.5px;line-height:1.6;color:#e5e7eb;white-space:pre-wrap">${escapeHtml(data.prompt)}</div>
          </div>
        `;
        if (includeNeg) html += negativeCardHtml(data.negative_prompt);
        output.innerHTML = html;
        output.style.display = "block";

        output.querySelector(".kp-copy-main")?.addEventListener("click", (e) => copyText(data.prompt, e.currentTarget));
        output.querySelector(".kp-copy-neg")?.addEventListener("click", (e) => copyText(data.negative_prompt, e.currentTarget));

        copyAllBtn.style.display = "inline-flex";
        copyAllBtn.onclick = () => {
            let all = data.prompt;
            if (includeNeg) all += `\n\nNegative Prompt:\n${data.negative_prompt}`;
            copyText(all, copyAllBtn);
        };
    }

    function escapeHtml(s) {
        return (s || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
    }

    generateBtn?.addEventListener("click", async () => {
        errorBox.style.display = "none";
        output.style.display = "none";
        copyAllBtn.style.display = "none";

        if (startFrameUploading) {
            showError("Start frame hâlâ yükleniyor, birkaç saniye bekleyin.");
            return;
        }
        if (!startFrameUrl) {
            showError("Başlangıç karesi zorunlu — lütfen bir görsel yükleyin.");
            return;
        }

        const totalDur = parseInt(totalDurInput.value, 10);
        if (!Number.isFinite(totalDur) || totalDur < 3 || totalDur > 60) {
            showError("Toplam süre 3-60 saniye arasında olmalı.");
            return;
        }

        const tags = tagsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
        const nShots = parseInt(nShotsSel.value, 10);
        const body = {
            start_frame_url: startFrameUrl,
            element_tags: tags,
            n_shots: nShots,
            total_duration: totalDur,
            arc_tone: arcSel.value,
            mode: currentMode,
            director_note: noteInput.value.trim() || null,
        };

        if (currentMode === "custom_multi_shot") {
            const picks = shotTechniques.slice(0, nShots);
            while (picks.length < nShots) picks.push(null);
            if (picks.some((p) => p)) body.shot_techniques = picks;
        }

        if (contToggle?.checked) {
            const prev = (prevPromptInput?.value || "").trim();
            if (prev) body.previous_prompt = prev;
        }

        const orig = generateBtn.textContent;
        generateBtn.disabled = true;
        generateBtn.textContent = "Üretiliyor…";
        try {
            const resp = await fetch("/api/kling-prompt/compose", {
                method: "POST",
                headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            if (data.mode === "multi_shot") {
                renderMulti(data);
            } else {
                renderCustom(data);
            }
        } catch (e) {
            showError(e.message || "Prompt üretilemedi.");
        } finally {
            generateBtn.disabled = false;
            generateBtn.textContent = orig;
        }
    });
})();

// ─── Photo Montage Modal (4 photos → side-by-side) ─────────────────────────
(function () {
    const modal = document.getElementById("photo-montage-modal");
    if (!modal) return;

    const openBtn = document.getElementById("open-photo-montage-btn");
    const closeBtn = document.getElementById("photo-montage-close");
    const zone = document.getElementById("pm-zone");
    const input = document.getElementById("pm-input");
    const previews = document.getElementById("pm-previews");
    const combineBtn = document.getElementById("pm-combine-btn");
    const clearBtn = document.getElementById("pm-clear-btn");
    const downloadBtn = document.getElementById("pm-download-btn");
    const errorBox = document.getElementById("pm-error");
    const resultWrap = document.getElementById("pm-result-wrap");
    const resultImg = document.getElementById("pm-result-img");
    const resultMeta = document.getElementById("pm-result-meta");

    const MAX = 4;
    let files = [];  // File[] — upload order

    function open() {
        modal.style.display = "flex";
    }
    function close() { modal.style.display = "none"; }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.style.display = "block";
    }
    function clearError() {
        errorBox.style.display = "none";
        errorBox.textContent = "";
    }

    function renderPreviews() {
        if (files.length === 0) {
            previews.style.display = "none";
            previews.innerHTML = "";
            combineBtn.disabled = true;
            clearBtn.style.display = "none";
            return;
        }
        previews.style.display = "grid";
        previews.innerHTML = "";
        files.forEach((f, idx) => {
            const card = document.createElement("div");
            card.style.cssText = "position:relative;background:#0b0e14;border:1px solid rgba(14,165,233,0.25);border-radius:8px;overflow:hidden;aspect-ratio:3/4;display:flex;align-items:center;justify-content:center";
            const img = document.createElement("img");
            img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
            const reader = new FileReader();
            reader.onload = (ev) => { img.src = ev.target.result; };
            reader.readAsDataURL(f);
            card.appendChild(img);

            const badge = document.createElement("div");
            badge.textContent = String(idx + 1);
            badge.style.cssText = "position:absolute;top:4px;left:4px;background:rgba(14,165,233,0.9);color:#fff;font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px";
            card.appendChild(badge);

            const rm = document.createElement("button");
            rm.type = "button";
            rm.textContent = "✕";
            rm.style.cssText = "position:absolute;top:4px;right:4px;background:rgba(0,0,0,0.6);color:#fff;border:none;width:22px;height:22px;border-radius:4px;cursor:pointer;font-size:12px;line-height:1";
            rm.addEventListener("click", () => {
                files.splice(idx, 1);
                renderPreviews();
            });
            card.appendChild(rm);

            previews.appendChild(card);
        });
        combineBtn.disabled = false;
        clearBtn.style.display = "inline-flex";
    }

    function addFiles(newFiles) {
        clearError();
        const remaining = MAX - files.length;
        if (remaining <= 0) {
            showError(`En fazla ${MAX} fotoğraf ekleyebilirsiniz.`);
            return;
        }
        const toAdd = Array.from(newFiles).slice(0, remaining);
        files = files.concat(toAdd);
        if (newFiles.length > remaining) {
            showError(`Sadece ilk ${remaining} fotoğraf eklendi (maks ${MAX}).`);
        }
        renderPreviews();
    }

    zone?.addEventListener("click", (e) => {
        if (e.target === input) return;
        input?.click();
    });
    input?.addEventListener("change", () => {
        if (input.files && input.files.length) {
            addFiles(input.files);
            input.value = "";
        }
    });

    openBtn?.addEventListener("click", open);
    closeBtn?.addEventListener("click", close);
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

    clearBtn?.addEventListener("click", () => {
        files = [];
        resultWrap.style.display = "none";
        downloadBtn.style.display = "none";
        clearError();
        renderPreviews();
    });

    combineBtn?.addEventListener("click", async () => {
        if (files.length === 0) return;
        clearError();
        resultWrap.style.display = "none";
        downloadBtn.style.display = "none";

        const fd = new FormData();
        files.forEach((f) => fd.append("files", f));

        const origText = combineBtn.textContent;
        combineBtn.disabled = true;
        combineBtn.textContent = "Birleştiriliyor…";
        try {
            const resp = await fetch("/api/photo-montage", {
                method: "POST",
                body: fd,
                headers: getAuthHeaders(),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            resultImg.src = data.url;
            resultMeta.textContent = `${data.count} fotoğraf · ${data.width}×${data.height} px`;
            resultWrap.style.display = "block";
            downloadBtn.href = data.url;
            downloadBtn.style.display = "inline-flex";
        } catch (e) {
            showError(e.message || "Birleştirme başarısız.");
        } finally {
            combineBtn.disabled = false;
            combineBtn.textContent = origText;
        }
    });
})();

// ─── Seedance 2.0 Prompt Composer Modal ────────────────────────────────────
(function () {
    const modal = document.getElementById("seedance-prompt-modal");
    if (!modal) return;

    const openBtn = document.getElementById("open-seedance-prompt-btn");
    const closeBtn = document.getElementById("seedance-prompt-close");
    const generateBtn = document.getElementById("sd-generate-btn");
    const copyCombinedBtn = document.getElementById("sd-copy-combined-btn");
    const output = document.getElementById("sd-output");
    const errorBox = document.getElementById("sd-error");

    // Quota bar
    const quotaUsedEl = document.getElementById("sd-quota-used");
    const quotaRemainingEl = document.getElementById("sd-quota-remaining");

    // Start frame bucket
    const sfZone = document.getElementById("sd-sf-zone");
    const sfInput = document.getElementById("sd-sf-input");
    const sfLabel = document.getElementById("sd-sf-label");
    const sfHint = document.getElementById("sd-sf-hint");
    const sfPreview = document.getElementById("sd-sf-preview");

    // Character bucket
    const charZone = document.getElementById("sd-char-zone");
    const charInput = document.getElementById("sd-char-input");
    const charPreviews = document.getElementById("sd-char-previews");

    // Location bucket
    const locZone = document.getElementById("sd-loc-zone");
    const locInput = document.getElementById("sd-loc-input");
    const locPreviews = document.getElementById("sd-loc-previews");

    // Render mode
    const renderBtns = document.querySelectorAll(".sd-render-btn");
    const shotTechWrap = document.getElementById("sd-shot-techniques-wrap");
    const shotTechRow = document.getElementById("sd-shot-techniques");

    // Form inputs
    const nShotsSel = document.getElementById("sd-n-shots");
    const totalDurInput = document.getElementById("sd-total-duration");
    const aspectSel = document.getElementById("sd-aspect");
    const filmLookSel = document.getElementById("sd-film-look");
    const arcSel = document.getElementById("sd-arc-tone");
    const dirNoteInput = document.getElementById("sd-director-note");
    const silentChk = document.getElementById("sd-silent");
    const contToggle = document.getElementById("sd-continuation-toggle");
    const contBox = document.getElementById("sd-continuation-box");
    const prevPromptInput = document.getElementById("sd-previous-prompt");

    const MAX_TOTAL = 9;
    let startFrameUrl = null;
    let startFrameUploading = false;
    let charItems = [];  // [{url, name, uploading}]
    let locItems = [];
    let currentRenderMode = "numbered_shots";
    let techniques = [];
    let shotTechniques = [];
    let activePickerShot = -1;

    // Reuse Kling technique picker overlay (same overlay element)
    const techOverlay = document.getElementById("kp-tech-overlay");
    const techGrid = document.getElementById("kp-tech-grid");
    const techShotIdx = document.getElementById("kp-tech-shot-idx");

    function open() {
        modal.style.display = "flex";
        errorBox.style.display = "none";
        output.style.display = "none";
        copyCombinedBtn.style.display = "none";
        loadTechniques();
        rebuildShotChips();
        updateQuotaBar();
        updateRenderVisibility();
    }
    function close() { modal.style.display = "none"; }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.style.display = "block";
    }
    function clearError() { errorBox.style.display = "none"; }

    function escapeHtml(s) {
        return (s || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
    }

    function quotaUsed() {
        return (startFrameUrl ? 1 : 0) + charItems.length + locItems.length;
    }
    function quotaRemaining() {
        return MAX_TOTAL - quotaUsed();
    }
    function updateQuotaBar() {
        const used = quotaUsed();
        const remaining = MAX_TOTAL - used;
        quotaUsedEl.textContent = String(used);
        quotaRemainingEl.textContent = String(remaining);
        quotaRemainingEl.style.color = remaining === 0 ? "#f87171" : (remaining <= 2 ? "#fbbf24" : "#4ade80");
    }

    async function uploadFile(file) {
        const fd = new FormData();
        fd.append("file", file);
        const resp = await fetch("/api/upload-temp", {
            method: "POST",
            body: fd,
            headers: getAuthHeaders(),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        return data.url;
    }

    // Start frame handler
    sfZone?.addEventListener("click", (e) => {
        if (e.target === sfInput) return;
        sfInput?.click();
    });
    sfInput?.addEventListener("change", async () => {
        const file = sfInput.files?.[0];
        if (!file) return;
        clearError();
        startFrameUploading = true;
        startFrameUrl = null;
        updateQuotaBar();
        sfHint.textContent = "Yükleniyor…";
        const reader = new FileReader();
        reader.onload = (ev) => {
            sfPreview.src = ev.target.result;
            sfPreview.style.display = "block";
        };
        reader.readAsDataURL(file);
        try {
            const url = await uploadFile(file);
            startFrameUrl = url;
            sfLabel.textContent = "✓ " + file.name;
            sfHint.textContent = "Hazır — @image1 olarak numaralandırıldı";
            updateQuotaBar();
        } catch (e) {
            showError("Start frame yüklenemedi: " + (e.message || e));
            sfHint.textContent = "Yükleme başarısız — tekrar deneyin";
            startFrameUrl = null;
            updateQuotaBar();
        } finally {
            startFrameUploading = false;
        }
    });

    // Bucket renderer (shared for character & location)
    function renderBucket(previewsEl, items, bucketStartOffset) {
        if (items.length === 0) {
            previewsEl.style.display = "none";
            previewsEl.innerHTML = "";
            return;
        }
        previewsEl.style.display = "grid";
        previewsEl.innerHTML = "";
        items.forEach((item, idx) => {
            const card = document.createElement("div");
            card.style.cssText = "position:relative;background:#0b0e14;border:1px solid rgba(249,115,22,0.25);border-radius:6px;overflow:hidden;aspect-ratio:3/4";
            const img = document.createElement("img");
            img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
            img.src = item.thumbUrl || item.url;
            card.appendChild(img);

            const badge = document.createElement("div");
            badge.textContent = `@image${bucketStartOffset + idx}`;
            badge.style.cssText = "position:absolute;bottom:3px;left:3px;background:rgba(249,115,22,0.9);color:#fff;font-size:10px;font-weight:700;padding:2px 5px;border-radius:3px";
            card.appendChild(badge);

            const rm = document.createElement("button");
            rm.type = "button";
            rm.textContent = "✕";
            rm.style.cssText = "position:absolute;top:3px;right:3px;background:rgba(0,0,0,0.7);color:#fff;border:none;width:20px;height:20px;border-radius:3px;cursor:pointer;font-size:11px;line-height:1";
            rm.addEventListener("click", () => {
                const target = items === charItems ? charItems : locItems;
                target.splice(idx, 1);
                rerenderBuckets();
                updateQuotaBar();
            });
            card.appendChild(rm);

            if (item.uploading) {
                const overlay = document.createElement("div");
                overlay.textContent = "Yükleniyor…";
                overlay.style.cssText = "position:absolute;inset:0;background:rgba(0,0,0,0.6);color:#fff;font-size:10px;display:flex;align-items:center;justify-content:center";
                card.appendChild(overlay);
            }
            previewsEl.appendChild(card);
        });
    }

    function rerenderBuckets() {
        // @image numbering: start=1, character=2..(1+C), location=(2+C)..(1+C+L)
        const charStart = 2;
        const locStart = 2 + charItems.length;
        renderBucket(charPreviews, charItems, charStart);
        renderBucket(locPreviews, locItems, locStart);
    }

    async function addToBucket(files, target) {
        clearError();
        const remaining = quotaRemaining();
        if (remaining <= 0) {
            showError(`9 görsel limitine ulaşıldı — önce bir görsel silin.`);
            return;
        }
        const filesToAdd = Array.from(files).slice(0, remaining);
        if (files.length > remaining) {
            showError(`Sadece ilk ${remaining} görsel eklendi (toplam 9 limit).`);
        }

        // Preview with local blob first
        const startIndex = target.length;
        filesToAdd.forEach((f) => {
            target.push({
                url: null,
                thumbUrl: URL.createObjectURL(f),
                name: f.name,
                uploading: true,
            });
        });
        rerenderBuckets();
        updateQuotaBar();

        // Upload in parallel
        await Promise.all(filesToAdd.map(async (f, i) => {
            const slot = startIndex + i;
            try {
                const url = await uploadFile(f);
                if (target[slot]) {
                    target[slot].url = url;
                    target[slot].uploading = false;
                }
            } catch (e) {
                showError(`${f.name} yüklenemedi: ${e.message || e}`);
                // Remove failed upload from target
                if (target[slot]) {
                    target.splice(slot, 1);
                }
            }
        }));
        rerenderBuckets();
    }

    // Character bucket wiring
    charZone?.addEventListener("click", (e) => {
        if (e.target === charInput) return;
        charInput?.click();
    });
    charInput?.addEventListener("change", async () => {
        if (charInput.files && charInput.files.length) {
            await addToBucket(charInput.files, charItems);
            charInput.value = "";
        }
    });

    // Location bucket wiring
    locZone?.addEventListener("click", (e) => {
        if (e.target === locInput) return;
        locInput?.click();
    });
    locInput?.addEventListener("change", async () => {
        if (locInput.files && locInput.files.length) {
            await addToBucket(locInput.files, locItems);
            locInput.value = "";
        }
    });

    openBtn?.addEventListener("click", open);
    closeBtn?.addEventListener("click", close);
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

    // Render mode toggle
    renderBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            renderBtns.forEach((b) => {
                b.classList.remove("active");
                b.firstChild.textContent = b.firstChild.textContent.replace(/^[✓]\s*/, "");
            });
            btn.classList.add("active");
            currentRenderMode = btn.dataset.mode;
            const label = btn.firstChild;
            if (!label.textContent.startsWith("✓")) {
                label.textContent = "✓ " + label.textContent.trim();
            }
            updateRenderVisibility();
        });
    });

    function updateRenderVisibility() {
        if (!shotTechWrap) return;
        // Shot techniques only for numbered_shots mode
        shotTechWrap.style.display = currentRenderMode === "numbered_shots" ? "block" : "none";
    }

    // n_shots change → rebuild technique chips
    nShotsSel?.addEventListener("change", rebuildShotChips);

    // Continuation toggle
    contToggle?.addEventListener("change", () => {
        contBox.style.display = contToggle.checked ? "block" : "none";
    });

    // Technique library (shared with Kling)
    async function loadTechniques() {
        if (techniques.length > 0) return;
        try {
            const resp = await fetch("/api/kling-prompt/techniques", { headers: getAuthHeaders() });
            if (!resp.ok) return;
            const data = await resp.json();
            techniques = data.techniques || [];
        } catch { /* silent */ }
    }

    function rebuildShotChips() {
        if (!shotTechRow) return;
        const n = parseInt(nShotsSel.value, 10) || 1;
        while (shotTechniques.length < n) shotTechniques.push(null);
        if (shotTechniques.length > n) shotTechniques = shotTechniques.slice(0, n);

        shotTechRow.innerHTML = "";
        for (let i = 0; i < n; i++) {
            const tech = shotTechniques[i] ? techniques.find((t) => t.id === shotTechniques[i]) : null;
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "wizard-btn-ghost";
            chip.style.cssText = "font-size:11px;padding:6px 10px;display:flex;align-items:center;gap:6px;" +
                (tech ? "background:rgba(249,115,22,0.18);border-color:rgba(249,115,22,0.5);color:#fed7aa" : "");
            chip.innerHTML = `<span style="font-weight:700">Shot ${i + 1}</span>` +
                `<span style="color:${tech ? '#fed7aa' : '#6b7280'}">${tech ? escapeHtml(tech.tr_label) : 'AI seçsin'}</span>`;
            chip.addEventListener("click", () => openTechniquePicker(i));
            shotTechRow.appendChild(chip);
        }
    }

    function openTechniquePicker(shotIdx) {
        activePickerShot = shotIdx;
        techShotIdx.textContent = String(shotIdx + 1);
        techGrid.innerHTML = "";
        const current = shotTechniques[shotIdx];
        techniques.forEach((t) => {
            const card = document.createElement("button");
            card.type = "button";
            const active = t.id === current;
            card.style.cssText = "text-align:left;padding:10px 12px;background:" +
                (active ? "rgba(249,115,22,0.18)" : "rgba(255,255,255,0.03)") +
                ";border:1px solid " + (active ? "rgba(249,115,22,0.6)" : "rgba(255,255,255,0.08)") +
                ";border-radius:8px;cursor:pointer;transition:all 0.15s;color:#e5e7eb";
            card.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:3px;color:${active ? '#fed7aa' : '#f3f4f6'}">${escapeHtml(t.tr_label)}</div>` +
                `<div style="font-size:11px;color:#9aa0a6;line-height:1.45">${escapeHtml(t.tr_desc)}</div>`;
            card.addEventListener("click", () => {
                shotTechniques[shotIdx] = t.id;
                rebuildShotChips();
                closeTechniquePicker();
            });
            techGrid.appendChild(card);
        });
        techOverlay.style.display = "flex";
        // Override tech-clear to set shot-technique null for this modal's state
        const clearBtn = document.getElementById("kp-tech-clear");
        if (clearBtn) {
            clearBtn.onclick = () => {
                if (activePickerShot >= 0) {
                    shotTechniques[activePickerShot] = null;
                    rebuildShotChips();
                }
                closeTechniquePicker();
            };
        }
    }

    function closeTechniquePicker() {
        techOverlay.style.display = "none";
        activePickerShot = -1;
    }

    async function copyText(text, btn) {
        try {
            await navigator.clipboard.writeText(text);
            const orig = btn.textContent;
            btn.textContent = "✓ Kopyalandı";
            setTimeout(() => { btn.textContent = orig; }, 1400);
        } catch {
            showError("Panoya kopyalanamadı.");
        }
    }

    function renderOutput(data) {
        const parts = [];

        // Duration-adjusted warning (when backend clamped up due to Seedance min-per-shot)
        const meta = data.meta || {};
        if (meta.duration_adjusted && meta.requested_duration && meta.total_duration) {
            parts.push(`
              <div style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.3);border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:12px;color:#fbbf24;line-height:1.5">
                ⚠️ <b>Süre ayarlandı:</b> ${meta.requested_duration}s istediniz ama ${meta.n_shots} shot için Seedance'ın minimum shot-başı süresi (4s) nedeniyle toplam <b>${meta.total_duration}s</b>'ye çıkarıldı.
                Tam ${meta.requested_duration}s istiyorsanız render modunu <b>Timed Segments</b>'a alın (tek kamera, beat süresi sınırsız) veya shot sayısını düşürün.
              </div>
            `);
        }

        // Combined prompt card (most useful — one-click copy)
        parts.push(`
          <div style="background:rgba(249,115,22,0.08);border:1px solid rgba(249,115,22,0.3);border-radius:10px;padding:12px;margin-bottom:12px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <div style="font-size:11px;font-weight:700;color:#fdba74;text-transform:uppercase;letter-spacing:0.5px">📦 Tam Prompt (Seedance'a yapıştır)</div>
              <button class="wizard-btn-ghost sd-copy-combined-inner" style="font-size:11px;padding:4px 10px">📋 Kopyala</button>
            </div>
            <pre style="font-size:12px;line-height:1.5;color:#e5e7eb;white-space:pre-wrap;word-break:break-word;margin:0;font-family:ui-monospace,Menlo,Consolas,monospace">${escapeHtml(data.combined_prompt)}</pre>
          </div>
        `);

        // Per-shot breakdown
        if (data.shots && data.shots.length) {
            parts.push(`<div style="font-size:11px;color:#9aa0a6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Per-shot döküm</div>`);
            data.shots.forEach((s) => {
                parts.push(`
                  <div class="sd-shot-card" style="background:rgba(249,115,22,0.04);border:1px solid rgba(249,115,22,0.18);border-radius:10px;padding:12px;margin-bottom:8px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                      <div style="font-size:12px;font-weight:700;color:#fdba74">Shot ${s.shot_no} · ${s.time_range} · ${escapeHtml(s.camera_type)}</div>
                      <button class="wizard-btn-ghost sd-copy-shot" data-shot="${s.shot_no}" style="font-size:11px;padding:4px 10px">📋 Kopyala</button>
                    </div>
                    <div class="sd-shot-prompt" style="font-size:12px;line-height:1.5;color:#e5e7eb;white-space:pre-wrap">${escapeHtml(s.prompt)}</div>
                  </div>
                `);
            });
        }

        output.innerHTML = parts.join("");
        output.style.display = "block";

        output.querySelector(".sd-copy-combined-inner")?.addEventListener("click", (e) => {
            copyText(data.combined_prompt, e.currentTarget);
        });
        output.querySelectorAll(".sd-copy-shot").forEach((btn) => {
            btn.addEventListener("click", () => {
                const n = parseInt(btn.dataset.shot, 10);
                const shot = data.shots.find((s) => s.shot_no === n);
                if (shot) copyText(shot.prompt, btn);
            });
        });

        copyCombinedBtn.style.display = "inline-flex";
        copyCombinedBtn.onclick = () => copyText(data.combined_prompt, copyCombinedBtn);
    }

    generateBtn?.addEventListener("click", async () => {
        clearError();
        output.style.display = "none";
        copyCombinedBtn.style.display = "none";

        if (startFrameUploading) {
            showError("Start frame hâlâ yükleniyor, birkaç saniye bekleyin.");
            return;
        }
        if (!startFrameUrl) {
            showError("Başlangıç karesi zorunlu — lütfen bir görsel yükleyin.");
            return;
        }
        if (charItems.some((it) => it.uploading) || locItems.some((it) => it.uploading)) {
            showError("Bazı görseller hâlâ yükleniyor, bitmesini bekleyin.");
            return;
        }

        const totalDur = parseInt(totalDurInput.value, 10);
        if (!Number.isFinite(totalDur) || totalDur < 4 || totalDur > 90) {
            showError("Toplam süre 4-90 saniye arasında olmalı.");
            return;
        }

        const nShots = parseInt(nShotsSel.value, 10);
        const body = {
            start_frame_url: startFrameUrl,
            character_urls: charItems.filter((it) => it.url).map((it) => it.url),
            location_urls: locItems.filter((it) => it.url).map((it) => it.url),
            n_shots: nShots,
            total_duration: totalDur,
            aspect_ratio: aspectSel.value,
            arc_tone: arcSel.value,
            render_mode: currentRenderMode,
            film_look: filmLookSel.value,
            silent: !!silentChk.checked,
            director_note: dirNoteInput.value.trim() || null,
        };

        if (currentRenderMode === "numbered_shots") {
            const picks = shotTechniques.slice(0, nShots);
            while (picks.length < nShots) picks.push(null);
            if (picks.some((p) => p)) body.shot_techniques = picks;
        }

        if (contToggle?.checked) {
            const prev = (prevPromptInput?.value || "").trim();
            if (prev) body.previous_prompt = prev;
        }

        const origText = generateBtn.textContent;
        generateBtn.disabled = true;
        generateBtn.textContent = "Üretiliyor…";
        try {
            const resp = await fetch("/api/seedance-prompt/compose", {
                method: "POST",
                headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            renderOutput(data);
        } catch (e) {
            showError(e.message || "Prompt üretilemedi.");
        } finally {
            generateBtn.disabled = false;
            generateBtn.textContent = origText;
        }
    });
})();
