/**
 * Workflow — Step-by-step video generation with approval gates.
 *
 * Steps:
 *   1. Select outfits + config
 *   2. Generate scenario (GPT) → user approves / edits / regenerates
 *   3. Generate scene frame (NB2) → user approves / regenerates
 *   4. Generate video (Kling/fal) → progress polling → result
 */

const API = "";

// ─── Auth ──────────────────────────────────────────────────────────
function authHeaders() {
    const t = localStorage.getItem("auth_token");
    return t ? { Authorization: "Bearer " + t } : {};
}

// ─── State ─────────────────────────────────────────────────────────
let wfOutfits = [];           // [{front_url, side_url, back_url, extra_urls, name, selected}]
let wfSelectedOutfit = null;  // single outfit for this workflow run
let wfShotConfigs = [{ duration: 5 }, { duration: 5 }, { duration: 5 }];
let wfBgUrl = null;
let wfBgExtraUrls = [];
let wfScenario = [];          // [{duration, prompt}]
let wfSceneFrameUrl = null;
let wfJobId = null;
let wfPollInterval = null;

// ─── Init ──────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    loadOutfits();
    renderShotConfigs();

    document.getElementById("wf-btn-scenario").addEventListener("click", generateScenario);
    document.getElementById("wf-btn-approve-scenario").addEventListener("click", approveScenario);
    document.getElementById("wf-btn-approve-scene").addEventListener("click", approveScene);
});

// ─── Step 1: Outfit Loading ────────────────────────────────────────
async function loadOutfits() {
    const grid = document.getElementById("wf-outfit-grid");
    const empty = document.getElementById("wf-outfit-empty");

    try {
        // Load both character and element library items
        const [charResp, elemResp] = await Promise.all([
            fetch("/library/items?category=character", { headers: authHeaders() }),
            fetch("/library/items?category=element", { headers: authHeaders() }),
        ]);
        const charItems = charResp.ok ? await charResp.json() : [];
        const elemItems = elemResp.ok ? await elemResp.json() : [];
        const allItems = [...charItems, ...elemItems];

        if (!allItems.length) {
            empty.textContent = "Kütüphanede kıyafet/element yok.";
            return;
        }

        empty.style.display = "none";
        wfOutfits = allItems.map(it => ({
            front_url: it.image_url,
            side_url: (it.extra_urls || [])[0] || null,
            back_url: (it.extra_urls || [])[1] || null,
            extra_urls: it.extra_urls || [],
            name: it.name,
            id: it.id,
        }));

        grid.innerHTML = wfOutfits.map((o, idx) => `
            <div class="wf-outfit-card" data-idx="${idx}">
                <img src="${o.front_url}" alt="${o.name}" loading="lazy">
                <div class="wf-outfit-name">${o.name}</div>
            </div>
        `).join("");

        grid.querySelectorAll(".wf-outfit-card").forEach(card => {
            card.addEventListener("click", () => {
                grid.querySelectorAll(".wf-outfit-card").forEach(c => c.classList.remove("selected"));
                card.classList.add("selected");
                wfSelectedOutfit = wfOutfits[parseInt(card.dataset.idx)];
                document.getElementById("wf-btn-scenario").disabled = false;
            });
        });
    } catch (err) {
        empty.textContent = "Yüklenemedi: " + err.message;
    }
}

// ─── Shot Config UI ────────────────────────────────────────────────
function renderShotConfigs() {
    const container = document.getElementById("wf-shot-configs");
    container.innerHTML = wfShotConfigs.map((cfg, idx) => `
        <div class="wf-shot-config">
            Sahne ${idx + 1}:
            <input type="number" min="3" max="10" value="${cfg.duration}"
                   onchange="wfUpdateShot(${idx}, this.value)">s
            ${wfShotConfigs.length > 1 ? `<button onclick="wfRemoveShot(${idx})" style="background:none;border:none;color:var(--accent-error);cursor:pointer;font-size:0.7rem">✕</button>` : ""}
        </div>
    `).join("");
}

window.wfUpdateShot = (idx, val) => {
    wfShotConfigs[idx].duration = Math.max(3, Math.min(10, parseInt(val) || 5));
    renderShotConfigs();
};

window.wfRemoveShot = (idx) => {
    if (wfShotConfigs.length <= 1) return;
    wfShotConfigs.splice(idx, 1);
    renderShotConfigs();
};

window.wfAddShot = () => {
    if (wfShotConfigs.length >= 6) return;
    wfShotConfigs.push({ duration: 5 });
    renderShotConfigs();
};

window.wfClearBg = () => {
    wfBgUrl = null;
    wfBgExtraUrls = [];
    document.getElementById("wf-bg-preview").style.display = "none";
};

// ─── Step Transitions ──────────────────────────────────────────────
function activateStep(num) {
    document.querySelectorAll(".wf-step").forEach((el, idx) => {
        el.classList.remove("active", "completed");
        const status = el.querySelector(".wf-step-status");
        if (idx + 1 < num) {
            el.classList.add("completed");
            status.textContent = "Tamamlandı";
        } else if (idx + 1 === num) {
            el.classList.add("active");
            status.textContent = "Aktif";
        } else {
            status.textContent = "Bekliyor";
        }
    });
}

// ─── Step 2: Generate Scenario ─────────────────────────────────────
async function generateScenario() {
    if (!wfSelectedOutfit) return;

    const btn = document.getElementById("wf-btn-scenario");
    btn.disabled = true;
    btn.innerHTML = `<span class="wf-spinner"></span>Senaryo üretiliyor...`;

    try {
        const resp = await fetch(`${API}/api/workflow/scenario`, {
            method: "POST",
            headers: { ...authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({
                outfit: wfSelectedOutfit,
                shot_configs: wfShotConfigs,
                background_url: wfBgUrl,
                aspect_ratio: document.getElementById("wf-aspect").value,
                director_note: document.getElementById("wf-director-note").value.trim() || null,
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        wfScenario = data.shots;
        wfSceneFrameUrl = data.scene_frame_url;
        renderScenario();
        activateStep(2);
    } catch (err) {
        alert("Senaryo üretimi başarısız: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Senaryo Üret";
    }
}

function renderScenario() {
    const list = document.getElementById("wf-scenario-list");
    list.innerHTML = wfScenario.map((shot, idx) => `
        <div class="wf-scenario-card" data-idx="${idx}">
            <div class="wf-shot-label">Sahne ${idx + 1} (${shot.duration}s)</div>
            <div class="wf-shot-prompt">${shot.prompt}</div>
            <textarea class="wf-scenario-edit" data-idx="${idx}">${shot.prompt}</textarea>
        </div>
    `).join("");
}

window.wfEditScenario = () => {
    document.querySelectorAll(".wf-scenario-card").forEach(card => {
        card.classList.toggle("editing");
    });
    // Save edited prompts back
    document.querySelectorAll(".wf-scenario-edit").forEach(ta => {
        ta.addEventListener("input", () => {
            wfScenario[parseInt(ta.dataset.idx)].prompt = ta.value;
        });
    });
};

window.wfRegenerateScenario = () => {
    generateScenario();
};

// ─── Step 3: Approve Scenario → Generate Scene Frame ───────────────
async function approveScenario() {
    const btn = document.getElementById("wf-btn-approve-scenario");
    btn.disabled = true;
    btn.innerHTML = `<span class="wf-spinner"></span>Sahne karesi oluşturuluyor...`;

    try {
        const resp = await fetch(`${API}/api/workflow/scene-frame`, {
            method: "POST",
            headers: { ...authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({
                outfit: wfSelectedOutfit,
                background_url: wfBgUrl,
                background_extra_urls: wfBgExtraUrls,
                aspect_ratio: document.getElementById("wf-aspect").value,
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        wfSceneFrameUrl = data.scene_frame_url;
        document.getElementById("wf-scene-img").src = wfSceneFrameUrl;
        activateStep(3);
    } catch (err) {
        alert("Sahne karesi oluşturulamadı: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Onayla ve Devam Et";
    }
}

window.wfRegenerateScene = () => {
    approveScenario();
};

// ─── Step 4: Approve Scene → Generate Video ────────────────────────
async function approveScene() {
    const btn = document.getElementById("wf-btn-approve-scene");
    btn.disabled = true;
    btn.textContent = "Video başlatılıyor...";

    try {
        const resp = await fetch(`${API}/api/workflow/generate`, {
            method: "POST",
            headers: { ...authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({
                outfit: wfSelectedOutfit,
                scene_frame_url: wfSceneFrameUrl,
                shots: wfScenario,
                aspect_ratio: document.getElementById("wf-aspect").value,
                generate_audio: document.getElementById("wf-audio").value === "true",
                provider: document.getElementById("wf-provider").value,
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        wfJobId = data.job_id;
        activateStep(4);
        startPolling();
    } catch (err) {
        alert("Video üretimi başlatılamadı: " + err.message);
        btn.disabled = false;
        btn.textContent = "Onayla ve Video Üret";
    }
}

function startPolling() {
    if (wfPollInterval) clearInterval(wfPollInterval);
    wfPollInterval = setInterval(pollStatus, 2000);
}

async function pollStatus() {
    if (!wfJobId) return;
    try {
        const resp = await fetch(`${API}/api/status/${wfJobId}`, { headers: authHeaders() });
        const job = await resp.json();

        const bar = document.getElementById("wf-progress-bar");
        const text = document.getElementById("wf-progress-text");
        bar.style.width = `${job.progress || 0}%`;
        text.textContent = job.message || "";

        if (job.status === "completed") {
            clearInterval(wfPollInterval);
            wfPollInterval = null;
            showResult(job.result_url);
        } else if (job.status === "failed") {
            clearInterval(wfPollInterval);
            wfPollInterval = null;
            text.textContent = "Hata: " + job.message;
            text.style.color = "var(--accent-error)";
        }
    } catch (err) {
        console.error("Poll error:", err);
    }
}

function showResult(url) {
    const fullUrl = `${API}${url}`;
    document.getElementById("wf-progress").style.display = "none";
    const resultEl = document.getElementById("wf-result");
    const video = document.getElementById("wf-result-video");
    video.src = fullUrl;
    resultEl.style.display = "block";

    document.getElementById("wf-download-btn").onclick = () => {
        const a = document.createElement("a");
        a.href = fullUrl;
        a.download = "workflow_video.mp4";
        a.click();
    };

    // Mark step completed
    document.getElementById("wf-step-4").classList.remove("active");
    document.getElementById("wf-step-4").classList.add("completed");
    document.getElementById("wf-step-4").querySelector(".wf-step-status").textContent = "Tamamlandı";
}
