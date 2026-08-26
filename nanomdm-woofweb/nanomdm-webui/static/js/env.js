function formatBytesEnv(n) {
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function formatMtimeEnv(epochSeconds) {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString("zh-TW", { hour12: false });
}

// ---------------------------------------------------------------------------
// 表單式 .env 欄位
// ---------------------------------------------------------------------------
function renderEnvFieldRow(field) {
  const tr = document.createElement("tr");
  tr.dataset.key = field.key;
  tr.innerHTML = `
    <td style="font-family:var(--mono); font-size:13px; word-break:break-all;">${escapeHtml(field.key)}</td>
    <td><input type="text" class="env-field-value" value="${escapeHtml(field.value)}" style="width:100%; font-family:var(--mono);"></td>
    <td>
      <button class="secondary env-field-save-btn" type="button" style="font-size:12px;">儲存</button>
      <button class="danger env-field-delete-btn" type="button" style="font-size:12px;">刪除</button>
    </td>
  `;
  return tr;
}

async function loadEnvFields() {
  const tbody = document.getElementById("env-fields-tbody");
  tbody.innerHTML = `<tr><td colspan="3">載入中...</td></tr>`;
  const res = await apiFetch("/api/env/fields");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="3" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  if (res.data.fields.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3">目前 .env 是空的,按右上角「新增變數」開始設定</td></tr>`;
    return;
  }
  res.data.fields.forEach((f) => tbody.appendChild(renderEnvFieldRow(f)));
}

async function saveEnvField(tr) {
  const key = tr.dataset.key;
  const value = tr.querySelector(".env-field-value").value;
  const btn = tr.querySelector(".env-field-save-btn");
  btn.disabled = true;
  btn.textContent = "儲存中...";

  const res = await apiFetchJSON("/api/env/fields/save", "POST", { key, value });

  btn.disabled = false;
  btn.textContent = "儲存";

  if (res.ok) {
    tr.querySelector(".env-field-value").style.background = "#e3f6e9";
    setTimeout(() => { tr.querySelector(".env-field-value").style.background = ""; }, 900);
    loadEnvBackups();
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function deleteEnvField(tr) {
  const key = tr.dataset.key;
  if (!confirm(`確定要刪除變數 ${key} 嗎?(會先自動備份)`)) return;
  const res = await apiFetchJSON("/api/env/fields/delete", "POST", { key });
  if (res.ok) {
    loadEnvFields();
    loadEnvBackups();
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function openAddFieldModal() {
  document.getElementById("env-new-key").value = "";
  document.getElementById("env-new-value").value = "";
  openModal("env-add-field-modal");
}

async function confirmAddField() {
  const key = document.getElementById("env-new-key").value.trim();
  const value = document.getElementById("env-new-value").value;
  if (!key) {
    alert("請輸入變數名稱");
    return;
  }
  const res = await apiFetchJSON("/api/env/fields/add", "POST", { key, value });
  if (res.ok) {
    closeModal("env-add-field-modal");
    loadEnvFields();
    loadEnvBackups();
  } else {
    alert("新增失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 備份記錄
// ---------------------------------------------------------------------------
async function loadEnvBackups() {
  const tbody = document.getElementById("env-backups-tbody");
  tbody.innerHTML = `<tr><td colspan="4">載入中...</td></tr>`;
  const res = await apiFetch("/api/env/backups");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:#d64545;">載入失敗</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  if (res.data.backups.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4">尚無備份記錄</td></tr>`;
    return;
  }
  res.data.backups.forEach((b) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(b.filename)}</td>
      <td>${escapeHtml(formatMtimeEnv(b.mtime))}</td>
      <td>${escapeHtml(formatBytesEnv(b.size))}</td>
      <td><a href="${apiUrl('/api/env/backups/download/' + encodeURIComponent(b.filename))}" class="secondary btn" style="font-size:12px; text-decoration:none; display:inline-block;">下載</a></td>
    `;
    tbody.appendChild(tr);
  });
}

async function backupEnvNow() {
  const btn = document.getElementById("env-backup-now-btn");
  btn.disabled = true;
  btn.textContent = "備份中...";
  const res = await apiFetchJSON("/api/env/backup", "POST");
  btn.disabled = false;
  btn.textContent = "立即備份";
  if (res.ok) {
    alert(res.data.message);
    loadEnvBackups();
  } else {
    alert("備份失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// devices.csv / groups.json (唯讀)
// ---------------------------------------------------------------------------
async function loadDevicesCsvRaw() {
  const res = await apiFetch("/api/devices-csv-raw");
  if (res.ok) {
    document.getElementById("devices-csv-content").value = res.data.content || "";
  }
}

async function loadGroupsJson() {
  const res = await apiFetch("/api/groups-json");
  if (res.ok) {
    document.getElementById("groups-json-content").value = res.data.content || "";
  }
}

// ---------------------------------------------------------------------------
// 品牌設定 (站台名稱 / LOGO)
// ---------------------------------------------------------------------------
async function loadSystemParams() {
  const res = await apiFetch("/api/system-params");
  if (!res.ok) return;
  const p = res.data.params;
  document.getElementById("param-asm-devices-interval").value = p.asm_devices_interval_minutes;
  document.getElementById("param-vpp-interval").value = p.vpp_interval_minutes;
  document.getElementById("param-devices-status-interval").value = p.devices_status_interval_minutes;
  document.getElementById("param-pending-retry-hours").value = p.pending_retry_threshold_hours;
}

async function saveSystemParams() {
  const payload = {
    asm_devices_interval_minutes: document.getElementById("param-asm-devices-interval").value,
    vpp_interval_minutes: document.getElementById("param-vpp-interval").value,
    devices_status_interval_minutes: document.getElementById("param-devices-status-interval").value,
    pending_retry_threshold_hours: document.getElementById("param-pending-retry-hours").value,
  };
  const res = await apiFetchJSON("/api/system-params", "POST", payload);
  if (res.ok) {
    alert("背景排程時間設定已儲存,下一輪排程就會套用新設定");
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function loadBrandingSiteLabel() {
  // 站台名稱已經在base.html的{{ branding.site_label }}裡渲染過,
  // 這裡直接把畫面上目前顯示的文字帶進輸入框當初始值,不用另外呼叫API查詢
  const brandTextEl = document.querySelector(".brand span");
  document.getElementById("branding-site-label-input").value = brandTextEl ? brandTextEl.textContent : "";
}

async function saveBrandingSiteLabel() {
  const input = document.getElementById("branding-site-label-input");
  const siteLabel = input.value.trim();
  if (!siteLabel) {
    alert("站台名稱不能是空的");
    return;
  }
  const res = await apiFetchJSON("/api/branding/save", "POST", { site_label: siteLabel });
  if (res.ok) {
    alert(res.data.message);
    const brandTextEl = document.querySelector(".brand span");
    if (brandTextEl) brandTextEl.textContent = siteLabel; // 立即反映在畫面左上角,不用重新整理頁面
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function handleBrandingLogoUpload(e) {
  const file = e.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  debugLog("REQUEST POST /api/branding/upload-logo", file.name);
  const resp = await fetch(apiUrl("/api/branding/upload-logo"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/branding/upload-logo", data, !resp.ok);

  e.target.value = "";

  if (data.ok) {
    alert(data.message);
    refreshBrandingLogoPreview();
  } else {
    alert("上傳失敗: " + (data.message || "未知錯誤"));
  }
}

async function resetBrandingLogo() {
  if (!confirm("確定要還原成預設圖片嗎?")) return;
  const res = await apiFetchJSON("/api/branding/reset-logo", "POST");
  if (res.ok) {
    alert(res.data.message);
    refreshBrandingLogoPreview();
  } else {
    alert("還原失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function refreshBrandingLogoPreview() {
  // 加上時間戳記強制瀏覽器重新抓圖,避免快取住舊圖片看不出更新效果
  const bust = `?t=${Date.now()}`;
  document.getElementById("branding-logo-preview").src = apiUrl("/api/logo-image") + bust;
  const headerLogo = document.querySelector(".brand-logo");
  if (headerLogo) headerLogo.src = apiUrl("/api/logo-image") + bust;
}

document.addEventListener("DOMContentLoaded", () => {
  loadEnvFields();
  loadEnvBackups();
  loadDevicesCsvRaw();
  loadGroupsJson();
  loadBrandingSiteLabel();
  loadSystemParams();

  document.getElementById("system-params-save-btn").addEventListener("click", saveSystemParams);

  document.getElementById("branding-save-label-btn").addEventListener("click", saveBrandingSiteLabel);
  document.getElementById("branding-upload-logo-btn").addEventListener("click", () => {
    document.getElementById("branding-logo-file-input").click();
  });
  document.getElementById("branding-logo-file-input").addEventListener("change", handleBrandingLogoUpload);
  document.getElementById("branding-reset-logo-btn").addEventListener("click", resetBrandingLogo);

  document.getElementById("env-backup-now-btn").addEventListener("click", backupEnvNow);
  document.getElementById("env-add-field-btn").addEventListener("click", openAddFieldModal);
  document.getElementById("env-add-field-confirm-btn").addEventListener("click", confirmAddField);
  document.getElementById("env-backups-reload-btn").addEventListener("click", loadEnvBackups);
  document.getElementById("groups-reload-btn").addEventListener("click", loadGroupsJson);

  document.getElementById("env-fields-tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || !tr.dataset.key) return;
    if (e.target.classList.contains("env-field-save-btn")) {
      saveEnvField(tr);
    } else if (e.target.classList.contains("env-field-delete-btn")) {
      deleteEnvField(tr);
    }
  });
});
