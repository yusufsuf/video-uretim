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
let wfOutfits = [];           // [{front_url, side_url, back_url, extra_urls, name, id}]
let wfSelectedOutfits = [];   // multi-select: array of selected outfits
let wfShotConfigs = [{ duration: 5 }, { duration: 5 }, { duration: 5 }];
let wfBgUrl = null;
let wfBgExtraUrls = [];
// Per-outfit data: wfOutfitData[i] = { outfit, shots: [...], scene_frame_url }
let wfOutfitData = [];
let wfJobId = null;
let wfPollInterval = null;

// ─── Init ──────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    loadOutfits();
    renderShotConfigs();

    document.getElementById("wf-btn-scenario").addEventListener("click", generateScenario);
    document.getElementById("wf-btn-approve-scenario").addEventListener("click", approveScenario);
    document.getElementById("wf-btn-approve-scene").addEventListener("click", approveScene);
    document.getElementById("wf-bg-pick-btn").addEventListener("click", () => openWfLibPicker("background"));
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
                card.classList.toggle("selected");
                // Rebuild selected list
                wfSelectedOutfits = [];
                grid.querySelectorAll(".wf-outfit-card.selected").forEach(c => {
                    wfSelectedOutfits.push(wfOutfits[parseInt(c.dataset.idx)]);
                });
                document.getElementById("wf-btn-scenario").disabled = wfSelectedOutfits.length === 0;
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
    if (!wfSelectedOutfits.length) return;

    const btn = document.getElementById("wf-btn-scenario");
    btn.disabled = true;
    btn.innerHTML = `<span class="wf-spinner"></span>Senaryo üretiliyor (0/${wfSelectedOutfits.length})...`;

    wfOutfitData = [];

    try {
        for (let i = 0; i < wfSelectedOutfits.length; i++) {
            btn.innerHTML = `<span class="wf-spinner"></span>Senaryo üretiliyor (${i + 1}/${wfSelectedOutfits.length})...`;

            const resp = await fetch(`${API}/api/workflow/scenario`, {
                method: "POST",
                headers: { ...authHeaders(), "Content-Type": "application/json" },
                body: JSON.stringify({
                    outfit: wfSelectedOutfits[i],
                    shot_configs: wfShotConfigs,
                    background_url: wfBgUrl,
                    aspect_ratio: document.getElementById("wf-aspect").value,
                    director_note: document.getElementById("wf-director-note").value.trim() || null,
                }),
            });

            if (!resp.ok) throw new Error(`HTTP ${resp.status} (kıyafet ${i + 1})`);
            const data = await resp.json();

            wfOutfitData.push({
                outfit: wfSelectedOutfits[i],
                shots: data.shots,
                scene_frame_url: data.scene_frame_url,
            });
        }

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
    let html = "";
    wfOutfitData.forEach((od, oi) => {
        html += `<div style="font-weight:600;font-size:0.82rem;margin-top:${oi > 0 ? 16 : 0}px;margin-bottom:8px;color:var(--text-primary)">${od.outfit.name || `Kıyafet ${oi + 1}`}</div>`;
        od.shots.forEach((shot, si) => {
            html += `
                <div class="wf-scenario-card" data-outfit="${oi}" data-shot="${si}">
                    <div class="wf-shot-label">Sahne ${si + 1} (${shot.duration}s)</div>
                    <div class="wf-shot-prompt">${shot.prompt}</div>
                    <textarea class="wf-scenario-edit" data-outfit="${oi}" data-shot="${si}">${shot.prompt}</textarea>
                </div>`;
        });
    });
    list.innerHTML = html;
}

window.wfEditScenario = () => {
    document.querySelectorAll(".wf-scenario-card").forEach(card => {
        card.classList.toggle("editing");
    });
    document.querySelectorAll(".wf-scenario-edit").forEach(ta => {
        ta.addEventListener("input", () => {
            const oi = parseInt(ta.dataset.outfit);
            const si = parseInt(ta.dataset.shot);
            wfOutfitData[oi].shots[si].prompt = ta.value;
        });
    });
};

window.wfRegenerateScenario = () => {
    generateScenario();
};

// ─── Step 3: Approve Scenario → Generate Scene Frames ──────────────
async function approveScenario() {
    const btn = document.getElementById("wf-btn-approve-scenario");
    btn.disabled = true;

    try {
        for (let i = 0; i < wfOutfitData.length; i++) {
            btn.innerHTML = `<span class="wf-spinner"></span>Sahne karesi oluşturuluyor (${i + 1}/${wfOutfitData.length})...`;

            const resp = await fetch(`${API}/api/workflow/scene-frame`, {
                method: "POST",
                headers: { ...authHeaders(), "Content-Type": "application/json" },
                body: JSON.stringify({
                    outfit: wfOutfitData[i].outfit,
                    background_url: wfBgUrl,
                    background_extra_urls: wfBgExtraUrls,
                    aspect_ratio: document.getElementById("wf-aspect").value,
                }),
            });

            if (!resp.ok) throw new Error(`HTTP ${resp.status} (kıyafet ${i + 1})`);
            const data = await resp.json();
            wfOutfitData[i].scene_frame_url = data.scene_frame_url;
        }

        renderSceneFrames();
        activateStep(3);
    } catch (err) {
        alert("Sahne karesi oluşturulamadı: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Onayla ve Devam Et";
    }
}

function renderSceneFrames() {
    const preview = document.getElementById("wf-scene-preview");
    preview.innerHTML = wfOutfitData.map((od, i) => `
        <div style="margin-bottom:12px">
            <div style="font-weight:600;font-size:0.78rem;margin-bottom:6px;color:var(--text-primary)">${od.outfit.name || `Kıyafet ${i + 1}`}</div>
            <img src="${od.scene_frame_url}" alt="Sahne karesi ${i + 1}" style="max-width:100%;border-radius:8px;border:1px solid var(--border-subtle)">
        </div>
    `).join("");
}

window.wfRegenerateScene = () => {
    approveScenario();
};

// ─── Step 4: Approve Scene → Generate Video ────────────────────────
async function approveScene() {
    const btn = document.getElementById("wf-btn-approve-scene");
    btn.disabled = true;
    btn.textContent = "Video başlatılıyor...";

    // Build per-outfit payload
    const outfitPayloads = wfOutfitData.map(od => ({
        outfit: od.outfit,
        scene_frame_url: od.scene_frame_url,
        shots: od.shots,
    }));

    try {
        const resp = await fetch(`${API}/api/workflow/generate`, {
            method: "POST",
            headers: { ...authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({
                outfits: outfitPayloads,
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


// ─── Library Picker ───────────────────────────────────────────────
let _wfLibTarget = null; // "background" | "outfit"

async function openWfLibPicker(target) {
    _wfLibTarget = target;
    const modal = document.getElementById("wf-lib-modal");
    const title = document.getElementById("wf-lib-title");
    const grid  = document.getElementById("wf-lib-grid");
    const close = document.getElementById("wf-lib-close");

    title.textContent = target === "background" ? "Arka Plan Seç" : "Kıyafet Seç";
    modal.style.display = "flex";

    close.onclick = () => { modal.style.display = "none"; };
    modal.onclick = (e) => { if (e.target === modal) modal.style.display = "none"; };

    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);font-size:0.8rem;padding:32px">Yükleniyor...</div>`;

    try {
        const resp = await fetch(`/library/items?category=${target}`, { headers: authHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();

        if (!items.length) {
            grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);font-size:0.8rem;padding:32px">Bu kategoride öğe yok. <a href="/library" target="_blank">Kütüphaneye git →</a></div>`;
            return;
        }

        grid.innerHTML = items.map(it => `
            <div class="wf-lib-item" data-id="${it.id}" style="cursor:pointer;border:2px solid transparent;border-radius:8px;overflow:hidden;transition:border-color 0.15s">
                <img src="${it.image_url}" alt="${it.name}" loading="lazy" style="width:100%;aspect-ratio:3/4;object-fit:cover;display:block">
                <div style="font-size:0.68rem;padding:4px 6px;text-align:center;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${it.name}</div>
            </div>
        `).join("");

        const itemMap = Object.fromEntries(items.map(it => [it.id, it]));
        grid.querySelectorAll(".wf-lib-item").forEach(el => {
            el.addEventListener("click", () => {
                const item = itemMap[el.dataset.id];
                if (item) selectWfLibItem(item);
            });
        });
    } catch (err) {
        grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);font-size:0.8rem;padding:32px">Yüklenemedi: ${err.message}</div>`;
    }
}

function selectWfLibItem(item) {
    if (_wfLibTarget === "background") {
        wfBgUrl = item.image_url;
        wfBgExtraUrls = item.extra_urls || [];
        const preview = document.getElementById("wf-bg-preview");
        const img = document.getElementById("wf-bg-img");
        if (img) img.src = item.image_url;
        if (preview) preview.style.display = "flex";
    }
    document.getElementById("wf-lib-modal").style.display = "none";
}
