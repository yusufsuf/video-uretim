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
let uploadFile = null;

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
        grid.innerHTML = uploadCard + items.map(item => `
            <div class="lib-item">
                <img src="${item.image_url}" alt="${item.name}" loading="lazy">
                <div class="lib-item-overlay">
                    <div class="lib-item-name">${item.name}</div>
                    <div class="lib-item-cat">${catLabels[item.category] || item.category}</div>
                </div>
                <button class="lib-item-del" onclick="deleteItem('${item.id}', event)" title="Sil">✕</button>
            </div>
        `).join("");
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
    uploadFile = null;
    document.getElementById("upload-name").value = "";
    document.getElementById("upload-category").value = "character";
    document.getElementById("confirm-upload-btn").disabled = true;
    resetDropZone();
    document.getElementById("upload-modal").style.display = "flex";
}

function closeUploadModal() {
    document.getElementById("upload-modal").style.display = "none";
}

function resetDropZone() {
    const zone = document.getElementById("upload-drop-zone");
    zone.innerHTML = `
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom:6px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17,8 12,3 7,8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <div>Görsel seç veya sürükle</div>
        <div style="font-size:0.65rem;margin-top:4px;color:var(--text-muted)">JPG, PNG, WebP</div>
        <input type="file" id="upload-file-input" accept="image/*" style="display:none">
    `;
    setupDropZone();
}

function setupDropZone() {
    const zone = document.getElementById("upload-drop-zone");
    const input = document.getElementById("upload-file-input");

    zone.addEventListener("click", () => input.click());
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", e => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
    });
    input.addEventListener("change", () => {
        if (input.files[0]) selectFile(input.files[0]);
    });
}

function selectFile(file) {
    uploadFile = file;
    const zone = document.getElementById("upload-drop-zone");
    const url = URL.createObjectURL(file);
    zone.innerHTML = `<img src="${url}" class="preview" style="max-height:120px;border-radius:8px;object-fit:contain">
        <div style="font-size:0.68rem;margin-top:6px;color:var(--text-secondary)">${file.name}</div>`;
    document.getElementById("confirm-upload-btn").disabled = false;
}

document.getElementById("open-upload-btn")?.addEventListener("click", openUploadModal);
document.getElementById("cancel-upload-btn")?.addEventListener("click", closeUploadModal);
document.getElementById("upload-modal")?.addEventListener("click", e => {
    if (e.target === document.getElementById("upload-modal")) closeUploadModal();
});

document.getElementById("confirm-upload-btn")?.addEventListener("click", async () => {
    if (!uploadFile) return;
    const name = document.getElementById("upload-name").value.trim() || uploadFile.name;
    const category = document.getElementById("upload-category").value;
    const btn = document.getElementById("confirm-upload-btn");
    btn.disabled = true;
    btn.textContent = "Yükleniyor...";

    const formData = new FormData();
    formData.append("file", uploadFile);
    formData.append("name", name);
    formData.append("category", category);

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

// ─── Init ─────────────────────────────────────────────────────────
setupDropZone();
loadItems("");
