// Seedance 2.0 page — state + API calls

const MAX_REFS = 9;
const MAX_REF_VIDEOS = 3;
const MAX_SHOTS = 6;

const state = {
    startFileUrl: null,      // URL returned from /api/upload-temp
    refUrls: [],             // URLs returned from /api/upload-temp
    refVideoUrls: [],        // Reference video URLs
    shots: [{ prompt: "", duration: 10 }],
    aspect: "9:16",
    resolution: "1080p",
    audio: false,
    jobId: null,
    polling: false,
};

const authToken = () => localStorage.getItem("auth_token") || "";

async function api(path, { method = "GET", body = null, isJson = true } = {}) {
    const headers = { Authorization: `Bearer ${authToken()}` };
    let payload = body;
    if (isJson && body != null) {
        headers["Content-Type"] = "application/json";
        payload = JSON.stringify(body);
    }
    const resp = await fetch(path, { method, headers, body: payload });
    if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try {
            const j = await resp.json();
            if (j.detail) msg = j.detail;
        } catch (_) {}
        throw new Error(msg);
    }
    return resp.json();
}

async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/upload-temp", {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken()}` },
        body: fd,
    });
    if (!resp.ok) throw new Error(`Yükleme başarısız: ${resp.status}`);
    const j = await resp.json();
    return j.url;
}

// ─── Refs grid ──────────────────────────────────────────────────────

function renderRefs() {
    const grid = document.getElementById("sd-ref-grid");
    grid.innerHTML = "";

    state.refUrls.forEach((url, i) => {
        const slot = document.createElement("div");
        slot.className = "sd-ref-slot has-image";
        slot.innerHTML = `
            <img src="${url}" alt="ref">
            <button class="sd-ref-remove" title="Kaldır">✕</button>
        `;
        slot.querySelector(".sd-ref-remove").addEventListener("click", (e) => {
            e.stopPropagation();
            state.refUrls.splice(i, 1);
            renderRefs();
            updateSubmit();
        });
        grid.appendChild(slot);
    });

    if (state.refUrls.length < MAX_REFS) {
        const addSlot = document.createElement("div");
        addSlot.className = "sd-ref-slot";
        addSlot.innerHTML = `
            <div class="sd-ref-add">+</div>
            <div>Referans ekle</div>
            <div style="font-size:0.62rem;margin-top:2px">${state.refUrls.length}/${MAX_REFS}</div>
        `;
        addSlot.addEventListener("click", () => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*";
            input.addEventListener("change", async () => {
                const f = input.files?.[0];
                if (!f) return;
                try {
                    addSlot.innerHTML = '<div style="font-size:0.7rem">Yükleniyor…</div>';
                    const url = await uploadFile(f);
                    state.refUrls.push(url);
                    renderRefs();
                    updateSubmit();
                } catch (e) {
                    alert("Yükleme başarısız: " + e.message);
                    renderRefs();
                }
            });
            input.click();
        });
        grid.appendChild(addSlot);
    }
}

// ─── Reference videos grid ──────────────────────────────────────────

function renderRefVideos() {
    const grid = document.getElementById("sd-refvid-grid");
    if (!grid) return;
    grid.innerHTML = "";

    state.refVideoUrls.forEach((url, i) => {
        const slot = document.createElement("div");
        slot.className = "sd-ref-slot has-image";
        slot.innerHTML = `
            <video src="${url}" muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>
            <button class="sd-ref-remove" title="Kaldır">✕</button>
            <div style="position:absolute;bottom:4px;left:4px;background:rgba(0,0,0,0.6);color:#fff;font-size:0.55rem;padding:2px 5px;border-radius:3px;z-index:1">VIDEO</div>
        `;
        slot.querySelector(".sd-ref-remove").addEventListener("click", (e) => {
            e.stopPropagation();
            state.refVideoUrls.splice(i, 1);
            renderRefVideos();
            updateSubmit();
        });
        grid.appendChild(slot);
    });

    if (state.refVideoUrls.length < MAX_REF_VIDEOS) {
        const addSlot = document.createElement("div");
        addSlot.className = "sd-ref-slot";
        addSlot.innerHTML = `
            <div class="sd-ref-add">+</div>
            <div>Video ekle</div>
            <div style="font-size:0.62rem;margin-top:2px">${state.refVideoUrls.length}/${MAX_REF_VIDEOS}</div>
        `;
        addSlot.addEventListener("click", () => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "video/mp4,video/quicktime,.mp4,.mov";
            input.addEventListener("change", async () => {
                const f = input.files?.[0];
                if (!f) return;
                if (f.size > 50 * 1024 * 1024) {
                    alert("Video 50 MB sınırını aşıyor.");
                    return;
                }
                try {
                    addSlot.innerHTML = '<div style="font-size:0.7rem">Yükleniyor…</div>';
                    const url = await uploadFile(f);
                    state.refVideoUrls.push(url);
                    renderRefVideos();
                    updateSubmit();
                } catch (e) {
                    alert("Yükleme başarısız: " + e.message);
                    renderRefVideos();
                }
            });
            input.click();
        });
        grid.appendChild(addSlot);
    }
}

// ─── Shots ──────────────────────────────────────────────────────────

function renderShots() {
    const wrap = document.getElementById("sd-shots-wrap");
    wrap.innerHTML = "";
    state.shots.forEach((shot, idx) => {
        const row = document.createElement("div");
        row.className = "sd-shot-row";
        const canRemove = state.shots.length > 1;
        row.innerHTML = `
            <div class="sd-shot-head">
                <span class="sd-shot-label">Shot ${idx + 1}</span>
                <div class="sd-shot-actions">
                    <span class="sd-shot-dur">Süre <input type="number" min="4" max="15" value="${shot.duration}"> sn</span>
                    ${canRemove ? '<button class="sd-shot-remove" title="Sil">✕</button>' : ""}
                </div>
            </div>
            <textarea class="sd-shot-prompt" placeholder="Örn: Model beyaz elbisesiyle sağa döner, rüzgar saçlarını savurur, yumuşak akşam ışığı.">${shot.prompt}</textarea>
        `;
        row.querySelector(".sd-shot-prompt").addEventListener("input", (e) => {
            state.shots[idx].prompt = e.target.value;
            updateSubmit();
        });
        row.querySelector(".sd-shot-dur input").addEventListener("change", (e) => {
            let v = parseInt(e.target.value, 10) || 10;
            v = Math.max(4, Math.min(15, v));
            state.shots[idx].duration = v;
            e.target.value = v;
        });
        if (canRemove) {
            row.querySelector(".sd-shot-remove").addEventListener("click", () => {
                state.shots.splice(idx, 1);
                renderShots();
                updateSubmit();
            });
        }
        wrap.appendChild(row);
    });

    document.getElementById("sd-add-shot").style.display =
        state.shots.length >= MAX_SHOTS ? "none" : "";
}

// ─── Start frame ────────────────────────────────────────────────────

function bindStartFrame() {
    const zone = document.getElementById("sd-start-zone");
    const input = document.getElementById("sd-start-input");
    const preview = document.getElementById("sd-start-preview");
    const clearBtn = document.getElementById("sd-start-clear");

    zone.addEventListener("click", (e) => {
        if (e.target === clearBtn) return;
        input.click();
    });

    input.addEventListener("change", async () => {
        const f = input.files?.[0];
        if (!f) return;
        try {
            preview.src = URL.createObjectURL(f);
            preview.classList.add("visible");
            clearBtn.style.display = "inline-block";
            const url = await uploadFile(f);
            state.startFileUrl = url;
        } catch (e) {
            alert("Yükleme hatası: " + e.message);
            preview.classList.remove("visible");
            clearBtn.style.display = "none";
        }
    });

    clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        state.startFileUrl = null;
        preview.classList.remove("visible");
        preview.src = "";
        input.value = "";
        clearBtn.style.display = "none";
    });
}

// ─── Submit ─────────────────────────────────────────────────────────

function updateSubmit() {
    const btn = document.getElementById("sd-submit");
    const hasPrompt = state.shots.every((s) => (s.prompt || "").trim().length >= 2);
    btn.disabled = state.polling || !hasPrompt;
}

function showProgress(msg, pct) {
    const box = document.getElementById("sd-progress");
    box.classList.add("visible");
    document.getElementById("sd-progress-msg").textContent = msg;
    document.getElementById("sd-progress-fill").style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
}

function showResult(url) {
    const box = document.getElementById("sd-result");
    box.innerHTML = `
        <video src="${url}" controls autoplay playsinline></video>
        <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap">
            <a href="${url}" download style="padding:8px 14px;background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:8px;color:var(--text-primary);font-size:0.78rem;text-decoration:none;font-family:inherit">⬇ İndir</a>
            <a href="/gallery" style="padding:8px 14px;background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:8px;color:var(--text-primary);font-size:0.78rem;text-decoration:none;font-family:inherit">Galeriye Git</a>
        </div>
    `;
}

async function pollStatus() {
    if (!state.jobId) return;
    state.polling = true;
    updateSubmit();
    try {
        while (true) {
            const j = await api(`/api/seedance/status/${state.jobId}`);
            showProgress(j.message || "Üretiliyor…", j.progress || 0);
            if (j.status === "completed") {
                showProgress("Tamamlandı ✓", 100);
                if (j.result_url) showResult(j.result_url);
                break;
            }
            if (j.status === "failed") {
                showProgress("❌ " + (j.message || "Hata"), 0);
                break;
            }
            await new Promise((r) => setTimeout(r, 4000));
        }
    } catch (e) {
        showProgress("❌ " + e.message, 0);
    } finally {
        state.polling = false;
        updateSubmit();
    }
}

async function submit() {
    if (state.polling) return;

    const body = {
        shots: state.shots.map((s) => ({
            prompt: (s.prompt || "").trim(),
            duration: s.duration || 10,
        })),
        reference_image_urls: state.refUrls,
        reference_video_urls: state.refVideoUrls,
        start_frame_url: state.startFileUrl || null,
        aspect_ratio: state.aspect,
        resolution: state.resolution,
        generate_audio: state.audio,
    };

    try {
        showProgress("Kuyruğa alınıyor…", 2);
        const j = await api("/api/seedance/generate", { method: "POST", body });
        state.jobId = j.job_id;
        await pollStatus();
    } catch (e) {
        showProgress("❌ " + e.message, 0);
    }
}

// ─── Init ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    renderRefs();
    renderRefVideos();
    renderShots();
    bindStartFrame();

    document.getElementById("sd-add-shot").addEventListener("click", () => {
        if (state.shots.length >= MAX_SHOTS) return;
        state.shots.push({ prompt: "", duration: 10 });
        renderShots();
        updateSubmit();
    });

    document.getElementById("sd-aspect").addEventListener("change", (e) => {
        state.aspect = e.target.value;
    });
    document.getElementById("sd-resolution").addEventListener("change", (e) => {
        state.resolution = e.target.value;
    });
    document.getElementById("sd-audio").addEventListener("change", (e) => {
        state.audio = e.target.value === "true";
    });

    document.getElementById("sd-submit").addEventListener("click", submit);
    updateSubmit();
});
