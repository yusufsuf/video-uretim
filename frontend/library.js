/**
 * Library management page — upload, browse, and delete visual reference items.
 */

const API = "";

function getAuthHeaders() {
    const token = localStorage.getItem("auth_token");
    return token ? { "Authorization": "Bearer " + token } : {};
}

// ─── State ────────────────────────────────────────────────────────
let currentCategory = "";
let uploadFiles = [null, null, null, null]; // [primary, extra1, extra2, extra3]

// All categories use the same 4-slot layout (element-style)
const DEFAULT_SLOTS = [
    { label: "Ön Görünüm",  required: true },
    { label: "Açı 2", required: false },
    { label: "Açı 3", required: false },
    { label: "Açı 4", required: false },
];

const SLOT_DEFS = {
    character:  DEFAULT_SLOTS,
    costume:    DEFAULT_SLOTS,
    scene:      DEFAULT_SLOTS,
    style:      DEFAULT_SLOTS,
    effect:     DEFAULT_SLOTS,
    other:      DEFAULT_SLOTS,
    // Legacy
    background: DEFAULT_SLOTS,
    element:    DEFAULT_SLOTS,
};

const SLOT_HINTS = {
    character:  "Ön görünüm zorunlu, farklı açılar opsiyonel — daha iyi tutarlılık için çoklu açı önerilir",
    costume:    "Ön görünüm zorunlu, farklı açılar opsiyonel — daha iyi tutarlılık için çoklu açı önerilir",
    scene:      "Ana görsel zorunlu, farklı açılar veya varyasyonlar opsiyonel",
    style:      "Stil referans görseli zorunlu, ek görseller opsiyonel",
    effect:     "Efekt görseli zorunlu, ek referanslar opsiyonel",
    other:      "Ana görsel zorunlu, ek görseller opsiyonel",
    background: "Ana görsel zorunlu, ek görseller opsiyonel",
    element:    "Ön görünüm zorunlu, farklı açılar opsiyonel",
};

// Kategoriye göre video element yükleme izni — Kling video_refer yalnızca
// insan/kıyafet figürlerinde anlamlı (sahne/stil/efekt değil).
const VIDEO_ENABLED_CATEGORIES = new Set(["character", "costume", "element"]);
let uploadMode = "image"; // "image" | "video" — element/costume/character için toggle

// ─── Load items ──────────────────────────────────────────────────
async function loadItems(category) {
    currentCategory = category;
    const grid = document.getElementById("lib-grid");
    grid.innerHTML = `<div class="lib-empty">Yükleniyor...</div>`;

    // Legacy mapping: "character" tab also shows old "element" items
    const url = category
        ? `${API}/library/items?category=${category}`
        : `${API}/library/items`;

    try {
        const res = await fetch(url, { headers: getAuthHeaders() });
        if (res.status === 401) { window.location.href = "/login"; return; }
        const items = await res.json();
        renderGrid(items);
    } catch {
        grid.innerHTML = `<div class="lib-empty">Yüklenemedi.</div>`;
    }
}

function renderGrid(items) {
    const grid = document.getElementById("lib-grid");
    const catLabels = {
        character: "Karakter", costume: "Kostüm", scene: "Mekan",
        style: "Stil", effect: "Efekt", other: "Diğer",
        background: "Arka Plan", element: "Element",
    };

    const uploadCard = `
        <button class="lib-upload-card" id="upload-card-btn">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14"/></svg>
            <span>Görsel Ekle</span>
        </button>`;

    if (items.length === 0) {
        grid.innerHTML = uploadCard + `<div class="lib-empty" style="grid-column:2/-1">Henüz görsel eklenmedi.</div>`;
    } else {
        grid.innerHTML = uploadCard + items.map(item => {
            const extras = item.extra_urls || [];
            const isVideo = /\.(mp4|mov)(\?|$)/i.test(item.image_url || "");
            let thumbsHtml = "";

            const primaryTag = isVideo
                ? `<video src="${item.image_url}" class="primary" muted playsinline preload="metadata" style="width:100%;height:100%;object-fit:cover"></video>`
                : `<img src="${item.image_url}" alt="${item.name}" loading="lazy" style="width:100%;height:100%;object-fit:cover">`;

            if (extras.length > 0) {
                // Side-by-side: front + extras column
                thumbsHtml = `
                    <div class="lib-item-views">
                        ${primaryTag}
                        <div class="extras">
                            ${extras.slice(0, 3).map(u => `<img src="${u}" alt="">`).join("")}
                        </div>
                    </div>`;
            } else {
                thumbsHtml = primaryTag;
            }

            return `
                <div class="lib-item">
                    ${thumbsHtml}
                    <div class="lib-item-overlay">
                        <div class="lib-item-name">${item.name}</div>
                        <div class="lib-item-cat">${catLabels[item.category] || item.category}${extras.length > 0 ? ` · ${extras.length + 1} görsel` : ""}</div>
                    </div>
                    <button class="lib-item-del" onclick="deleteItem('${item.id}', event)" title="Sil">✕</button>
                </div>`;
        }).join("");
    }

    document.getElementById("upload-card-btn")?.addEventListener("click", openUploadModal);
}

// ─── Delete ───────────────────────────────────────────────────────
async function deleteItem(id, e) {
    e.stopPropagation();
    if (!confirm("Bu görseli silmek istediğinize emin misiniz?")) return;
    try {
        await fetch(`${API}/library/items/${id}`, {
            method: "DELETE",
            headers: getAuthHeaders(),
        });
        loadItems(currentCategory);
    } catch {
        alert("Silme başarısız.");
    }
}
window.deleteItem = deleteItem;

// ─── Upload Modal ────────────────────────────────────────────────
function openUploadModal() {
    uploadFiles = [null, null, null, null];
    uploadMode = "image";
    document.getElementById("upload-name").value = "";
    const catSel = document.getElementById("upload-category");
    catSel.value = currentCategory || "character";
    document.getElementById("confirm-upload-btn").disabled = true;
    renderUploadSlots(catSel.value);
    document.getElementById("upload-modal").style.display = "flex";
}

function closeUploadModal() {
    document.getElementById("upload-modal").style.display = "none";
}

// ─── Upload Slots ─────────────────────────────────────────────────
function renderUploadSlots(category) {
    const videoAllowed = VIDEO_ENABLED_CATEGORIES.has(category);
    if (!videoAllowed) uploadMode = "image"; // video-only toggle sıfırla

    const container = document.getElementById("upload-slots-container");

    // Toggle: sadece video izinli kategorilerde gösterilir
    const toggleHtml = videoAllowed ? `
        <div class="upload-mode-toggle" style="display:flex;gap:6px;margin-bottom:10px;justify-content:center">
            <button type="button" class="wizard-btn-ghost upload-mode-btn ${uploadMode === "image" ? "active" : ""}" data-mode="image" style="flex:1;padding:6px 10px;font-size:0.72rem">Fotoğraf (3–4 görsel)</button>
            <button type="button" class="wizard-btn-ghost upload-mode-btn ${uploadMode === "video" ? "active" : ""}" data-mode="video" style="flex:1;padding:6px 10px;font-size:0.72rem">Video (.mp4/.mov, 3–8s)</button>
        </div>` : "";

    if (uploadMode === "video") {
        container.innerHTML = `
            ${toggleHtml}
            <div class="upload-slots-grid cols-1">
                <div class="upload-slot" id="slot-0" data-index="0">
                    <div class="upload-slot-icon">🎬</div>
                    <div class="upload-slot-label">Video Dosyası <span class="slot-required">*</span></div>
                    <input type="file" accept="video/mp4,video/quicktime,.mp4,.mov" class="slot-file-input" style="display:none">
                </div>
            </div>
        `;
    } else {
        const defs = SLOT_DEFS[category] || SLOT_DEFS.style;
        const cols = defs.length === 1 ? "cols-1" : defs.length === 2 ? "cols-2" : defs.length === 4 ? "cols-2" : "cols-3";
        container.innerHTML = `
            ${toggleHtml}
            <div class="upload-slots-grid ${cols}">
                ${defs.map((def, i) => `
                    <div class="upload-slot" id="slot-${i}" data-index="${i}">
                        <div class="upload-slot-icon">+</div>
                        <div class="upload-slot-label">${def.label}${def.required ? ' <span class="slot-required">*</span>' : ""}</div>
                        <input type="file" accept="image/*" class="slot-file-input" style="display:none">
                    </div>
                `).join("")}
            </div>
        `;
    }

    // Hint text
    const hint = document.getElementById("upload-hint");
    if (hint) {
        hint.textContent = uploadMode === "video"
            ? "Tek .mp4/.mov · 3–8 saniye · 9:16 veya 16:9 · max 200MB — element oluşturma ~2-3 dakika sürebilir"
            : (SLOT_HINTS[category] || "");
    }

    // Toggle handlers
    container.querySelectorAll(".upload-mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const mode = btn.dataset.mode;
            if (mode === uploadMode) return;
            uploadMode = mode;
            uploadFiles = [null, null, null, null];
            renderUploadSlots(category);
            updateConfirmBtn();
        });
    });

    // Slot handlers
    container.querySelectorAll(".upload-slot").forEach(slot => {
        const idx = parseInt(slot.dataset.index);
        const input = slot.querySelector(".slot-file-input");
        slot.addEventListener("click", (e) => {
            if (e.target.classList.contains("slot-clear-btn")) return;
            input.click();
        });
        input.addEventListener("change", () => {
            if (input.files[0]) handleSlotFile(idx, input.files[0]);
        });
    });
}

function handleSlotFile(idx, file) {
    uploadFiles[idx] = file;
    const slot = document.getElementById(`slot-${idx}`);
    if (!slot) return;

    const isVideoSlot = uploadMode === "video";
    const defs = SLOT_DEFS[document.getElementById("upload-category").value] || SLOT_DEFS.style;
    const label = isVideoSlot ? "Video" : (defs[idx]?.label || `Görsel ${idx + 1}`);
    const accept = isVideoSlot ? "video/mp4,video/quicktime,.mp4,.mov" : "image/*";

    slot.classList.add("has-file");
    if (isVideoSlot) {
        const objUrl = URL.createObjectURL(file);
        slot.innerHTML = `
            <video src="${objUrl}" class="slot-preview" muted playsinline style="width:100%;height:100%;object-fit:cover"></video>
            <div class="slot-name-bar">${label} · ${file.name.slice(0,24)}</div>
            <button class="slot-clear-btn" onclick="clearSlot(event,${idx})">✕</button>
            <input type="file" accept="${accept}" class="slot-file-input" style="display:none">
        `;
    } else {
        const objUrl = URL.createObjectURL(file);
        slot.innerHTML = `
            <img src="${objUrl}" class="slot-preview" alt="">
            <div class="slot-name-bar">${label}</div>
            <button class="slot-clear-btn" onclick="clearSlot(event,${idx})">✕</button>
            <input type="file" accept="${accept}" class="slot-file-input" style="display:none">
        `;
    }
    // Re-attach input handler
    const newInput = slot.querySelector(".slot-file-input");
    newInput.addEventListener("change", () => {
        if (newInput.files[0]) handleSlotFile(idx, newInput.files[0]);
    });
    slot.addEventListener("click", (e) => {
        if (e.target.classList.contains("slot-clear-btn")) return;
        newInput.click();
    });

    updateConfirmBtn();
}

function clearSlot(e, idx) {
    e.stopPropagation();
    uploadFiles[idx] = null;
    const slot = document.getElementById(`slot-${idx}`);
    if (!slot) return;
    const isVideoSlot = uploadMode === "video";
    const defs = SLOT_DEFS[document.getElementById("upload-category").value] || SLOT_DEFS.style;
    const def = defs[idx];
    const label = isVideoSlot ? "Video Dosyası" : (def?.label || `Görsel ${idx + 1}`);
    const required = isVideoSlot || def?.required;
    const icon = isVideoSlot ? "🎬" : "+";
    const accept = isVideoSlot ? "video/mp4,video/quicktime,.mp4,.mov" : "image/*";
    slot.classList.remove("has-file");
    slot.innerHTML = `
        <div class="upload-slot-icon">${icon}</div>
        <div class="upload-slot-label">${label}${required ? ' <span class="slot-required">*</span>' : ""}</div>
        <input type="file" accept="${accept}" class="slot-file-input" style="display:none">
    `;
    const input = slot.querySelector(".slot-file-input");
    input.addEventListener("change", () => {
        if (input.files[0]) handleSlotFile(idx, input.files[0]);
    });
    slot.addEventListener("click", (e2) => {
        if (e2.target.classList.contains("slot-clear-btn")) return;
        input.click();
    });
    updateConfirmBtn();
}
window.clearSlot = clearSlot;

function updateConfirmBtn() {
    // Enabled only when the primary (first) slot has a file
    document.getElementById("confirm-upload-btn").disabled = !uploadFiles[0];
}

// ─── Category change re-renders slots ────────────────────────────
document.getElementById("upload-category")?.addEventListener("change", (e) => {
    uploadFiles = [null, null, null, null];
    renderUploadSlots(e.target.value);
    updateConfirmBtn();
});

document.getElementById("open-upload-btn")?.addEventListener("click", openUploadModal);
document.getElementById("cancel-upload-btn")?.addEventListener("click", closeUploadModal);
document.getElementById("upload-modal")?.addEventListener("click", e => {
    if (e.target === document.getElementById("upload-modal")) closeUploadModal();
});

// ─── Upload Submit ────────────────────────────────────────────────
document.getElementById("confirm-upload-btn")?.addEventListener("click", async () => {
    if (!uploadFiles[0]) return;
    const name = document.getElementById("upload-name").value.trim() || uploadFiles[0].name;
    const category = document.getElementById("upload-category").value;
    const btn = document.getElementById("confirm-upload-btn");
    const isVideo = uploadMode === "video";
    btn.disabled = true;
    btn.textContent = isVideo ? "Element oluşturuluyor (~2-3 dk)..." : "Yükleniyor...";

    const formData = new FormData();
    formData.append("file",     uploadFiles[0]);
    formData.append("name",     name);
    formData.append("category", category);
    // Video modunda ekstra dosyalar gönderilmez — backend video tek kaynak alır.
    if (!isVideo) {
        if (uploadFiles[1]) formData.append("file2", uploadFiles[1]);
        if (uploadFiles[2]) formData.append("file3", uploadFiles[2]);
        if (uploadFiles[3]) formData.append("file4", uploadFiles[3]);
    }

    try {
        const res = await fetch(`${API}/library/items`, {
            method: "POST",
            body: formData,
            headers: getAuthHeaders(),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || "Yükleme başarısız.");
        } else {
            closeUploadModal();
            loadItems(currentCategory);
        }
    } catch {
        alert("Bağlantı hatası.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Yükle";
    }
});

// ─── Tabs ──────────────────────────────────────────────────────────
document.querySelectorAll(".lib-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".lib-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        loadItems(tab.dataset.cat || "");
    });
});

// ─── AI Venue Variants ───────────────────────────────────────────
document.getElementById("open-ai-venue-btn")?.addEventListener("click", () => {
    document.getElementById("ai-venue-name").value = "";
    document.getElementById("ai-venue-file").value = "";
    document.getElementById("ai-venue-file-label").textContent = "Fotoğraf seç...";
    document.getElementById("ai-venue-count").value = "2";
    document.getElementById("ai-venue-count-val").textContent = "2";
    document.getElementById("confirm-ai-venue-btn").disabled = true;
    document.getElementById("ai-venue-status").style.display = "none";
    document.getElementById("ai-venue-modal").style.display = "flex";
});

document.getElementById("cancel-ai-venue-btn")?.addEventListener("click", () => {
    document.getElementById("ai-venue-modal").style.display = "none";
});

document.getElementById("ai-venue-modal")?.addEventListener("click", e => {
    if (e.target === document.getElementById("ai-venue-modal"))
        document.getElementById("ai-venue-modal").style.display = "none";
});

document.getElementById("ai-venue-file")?.addEventListener("change", e => {
    const file = e.target.files[0];
    document.getElementById("ai-venue-file-label").textContent = file ? file.name : "Fotoğraf seç...";
    document.getElementById("confirm-ai-venue-btn").disabled = !file;
});

document.getElementById("ai-venue-count")?.addEventListener("input", e => {
    document.getElementById("ai-venue-count-val").textContent = e.target.value;
});

document.getElementById("confirm-ai-venue-btn")?.addEventListener("click", async () => {
    const name = document.getElementById("ai-venue-name").value.trim();
    const file = document.getElementById("ai-venue-file").files[0];
    const count = document.getElementById("ai-venue-count").value;

    if (!name) { alert("İsim girin."); return; }
    if (!file) { alert("Fotoğraf seçin."); return; }

    const btn = document.getElementById("confirm-ai-venue-btn");
    const cancelBtn = document.getElementById("cancel-ai-venue-btn");
    btn.disabled = true;
    btn.textContent = "Üretiliyor...";
    cancelBtn.disabled = true;
    document.getElementById("ai-venue-status").style.display = "block";

    try {
        const fd = new FormData();
        fd.append("name", name);
        fd.append("count", count);
        fd.append("file", file);

        const resp = await fetch(`${API}/library/generate-venue-variants`, {
            method: "POST",
            headers: getAuthHeaders(),
            body: fd,
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.detail || "Üretim başarısız.");
            return;
        }

        document.getElementById("ai-venue-modal").style.display = "none";

        // Switch to background tab and refresh
        document.querySelectorAll(".lib-tab").forEach(t => t.classList.remove("active"));
        document.querySelector('[data-cat="background"]')?.classList.add("active");
        loadItems("background");

    } catch (err) {
        alert("Bağlantı hatası: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Üret";
        cancelBtn.disabled = false;
        document.getElementById("ai-venue-status").style.display = "none";
    }
});

// ─── Init ─────────────────────────────────────────────────────────
loadItems("");
