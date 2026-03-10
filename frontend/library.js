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

const SLOT_DEFS = {
    character:  [
        { label: "Ön Görünüm",  required: true },
        { label: "Yan Görünüm", required: false },
        { label: "Arka Görünüm",required: false },
    ],
    background: [
        { label: "Görsel 1", required: true },
        { label: "Görsel 2", required: false },
        { label: "Görsel 3", required: false },
        { label: "Görsel 4", required: false },
    ],
    style: [
        { label: "Stil Görseli", required: true },
    ],
};

const SLOT_HINTS = {
    character:  "Ön görünüm zorunlu, yan ve arka opsiyonel — daha iyi tutarlılık için 3 açı önerilir",
    background: "1 zorunlu, en fazla 4 görsel yükleyebilirsiniz",
    style:      "Renk paleti, kompozisyon ve atmosfer referansı için tek görsel",
};

// ─── Load items ──────────────────────────────────────────────────
async function loadItems(category) {
    currentCategory = category;
    const grid = document.getElementById("lib-grid");
    grid.innerHTML = `<div class="lib-empty">Yükleniyor...</div>`;

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
    const catLabels = { character: "Elbise", background: "Arka Plan", style: "Stil" };

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
            let thumbsHtml = "";

            if (item.category === "character" && extras.length > 0) {
                // Side-by-side: front + side/back column
                thumbsHtml = `
                    <div class="lib-item-views">
                        <img src="${item.image_url}" class="primary" alt="">
                        <div class="extras">
                            ${extras.slice(0, 2).map(u => `<img src="${u}" alt="">`).join("")}
                        </div>
                    </div>`;
            } else if (item.category === "background" && extras.length > 0) {
                // 2×2 grid
                const all = [item.image_url, ...extras].slice(0, 4);
                thumbsHtml = `
                    <div class="lib-item-bg-grid">
                        ${all.map(u => `<img src="${u}" alt="">`).join("")}
                    </div>`;
            } else {
                thumbsHtml = `<img src="${item.image_url}" alt="${item.name}" loading="lazy" style="width:100%;height:100%;object-fit:cover">`;
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
    const defs = SLOT_DEFS[category] || SLOT_DEFS.style;
    const cols = defs.length === 1 ? "cols-1" : defs.length === 2 ? "cols-2" : defs.length === 4 ? "cols-2" : "cols-3";

    const container = document.getElementById("upload-slots-container");
    container.innerHTML = `
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

    // Hint text
    const hint = document.getElementById("upload-hint");
    if (hint) hint.textContent = SLOT_HINTS[category] || "";

    // Attach events
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

    const defs = SLOT_DEFS[document.getElementById("upload-category").value] || SLOT_DEFS.style;
    const label = defs[idx]?.label || `Görsel ${idx + 1}`;
    const objUrl = URL.createObjectURL(file);

    slot.classList.add("has-file");
    slot.innerHTML = `
        <img src="${objUrl}" class="slot-preview" alt="">
        <div class="slot-name-bar">${label}</div>
        <button class="slot-clear-btn" onclick="clearSlot(event,${idx})">✕</button>
        <input type="file" accept="image/*" class="slot-file-input" style="display:none">
    `;
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
    const defs = SLOT_DEFS[document.getElementById("upload-category").value] || SLOT_DEFS.style;
    const def = defs[idx];
    slot.classList.remove("has-file");
    slot.innerHTML = `
        <div class="upload-slot-icon">+</div>
        <div class="upload-slot-label">${def?.label || `Görsel ${idx + 1}`}${def?.required ? ' <span class="slot-required">*</span>' : ""}</div>
        <input type="file" accept="image/*" class="slot-file-input" style="display:none">
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
    btn.disabled = true;
    btn.textContent = "Yükleniyor...";

    const formData = new FormData();
    formData.append("file",     uploadFiles[0]);
    formData.append("name",     name);
    formData.append("category", category);
    if (uploadFiles[1]) formData.append("file2", uploadFiles[1]);
    if (uploadFiles[2]) formData.append("file3", uploadFiles[2]);
    if (uploadFiles[3]) formData.append("file4", uploadFiles[3]);

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
