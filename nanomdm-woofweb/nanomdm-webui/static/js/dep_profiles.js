let DEP_SCHEMA = null;
let currentDepFilename = null;
let currentDepIsNew = false;
let currentDepIsProtected = false;

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------
async function loadDepSchema() {
  if (DEP_SCHEMA) return DEP_SCHEMA;
  const res = await apiFetch("/api/dep-profiles/schema");
  if (res.ok) DEP_SCHEMA = res.data;
  return DEP_SCHEMA;
}

// ---------------------------------------------------------------------------
// 清單
// ---------------------------------------------------------------------------
async function loadDepProfilesList() {
  const container = document.getElementById("dep-profiles-list-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/dep-profiles");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  if (res.data.files.length === 0) {
    container.innerHTML = `<p style="color:#6b7280; font-size:13px;">目前沒有任何範本,按右上角「新增」建立第一個</p>`;
    return;
  }
  container.innerHTML = "";
  res.data.files.forEach((f) => {
    const div = document.createElement("div");
    const highlightStyle = f.is_protected ? "background:#fff7e6; border-left:3px solid #f0a500;" : "";
    div.style.cssText = `border-bottom:1px solid var(--border-color); padding:10px 10px; ${highlightStyle}`;
    const protectedBadge = f.is_protected ? `<span class="badge warn" style="margin-left:6px;">系統預設</span>` : "";
    const appliedLabel = f.last_applied_uuid
      ? `已套用 · ${escapeHtml(f.last_applied_at || "")}`
      : `<span style="color:#b45309;">尚未套用過</span>`;
    div.innerHTML = `
      <div style="font-family:var(--mono); font-size:13px; font-weight:600;">${escapeHtml(f.filename)}${protectedBadge}</div>
      <div style="font-size:12px; color:#6b7280;">${escapeHtml(f.profile_name || "(無名稱)")} · 配對群組: ${escapeHtml(f.assigned_group_label)}</div>
      <div style="font-size:11px; color:#9ca3af;">跳過 ${f.skip_count || 0} 個設定畫面 · ${appliedLabel}</div>
      <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
        <button class="secondary" data-action="edit-dep" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">編輯</button>
        <button class="secondary" data-action="duplicate-dep" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">再製</button>
        ${f.is_protected ? "" : `<button class="danger" data-action="delete-dep" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">刪除</button>`}
      </div>
    `;
    container.appendChild(div);
  });
}

async function deleteDepProfile(filename) {
  if (!confirm(`確定要刪除範本 ${filename} 嗎?(只會刪除本地範本檔案,不會影響已經套用到 Apple 的設定)`)) return;
  const res = await apiFetchJSON("/api/dep-profiles/delete", "POST", { filename });
  if (res.ok) {
    loadDepProfilesList();
    if (currentDepFilename === filename) {
      document.getElementById("dep-editor-container").classList.add("hidden");
      document.getElementById("dep-editor-title").textContent = "請從左側選擇一個範本,或按「新增」";
    }
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 再製
// ---------------------------------------------------------------------------
function openDuplicateModal(filename) {
  document.getElementById("dep-duplicate-new-filename").value = "";
  document.getElementById("dep-duplicate-new-filename").dataset.sourceFilename = filename;
  openModal("dep-duplicate-modal");
}

async function confirmDuplicateDepProfile() {
  const input = document.getElementById("dep-duplicate-new-filename");
  const sourceFilename = input.dataset.sourceFilename;
  const newFilename = input.value.trim();
  if (!newFilename.endsWith(".json")) {
    alert("檔名必須以 .json 結尾");
    return;
  }
  const res = await apiFetchJSON("/api/dep-profiles/duplicate", "POST", {
    source_filename: sourceFilename, new_filename: newFilename,
  });
  if (res.ok) {
    closeModal("dep-duplicate-modal");
    loadDepProfilesList();
    alert(`已複製為 ${newFilename},請記得到編輯畫面把它指派給正確的群組`);
  } else {
    alert("複製失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 群組指派 (跟內容編輯分開的獨立動作)
// ---------------------------------------------------------------------------
async function loadAssignSection(filename, currentGroupLabel) {
  document.getElementById("dep-current-group-label").textContent = currentGroupLabel || "(尚未指派給任何群組)";
  const select = document.getElementById("dep-assign-group-select");
  select.innerHTML = `<option value="">(取消指派)</option>`;
  const res = await apiFetch(`/api/dep-profiles/unpaired-groups?filename=${encodeURIComponent(filename)}`);
  if (res.ok) {
    res.data.groups.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g;
      opt.textContent = g;
      if (currentGroupLabel === g) opt.selected = true;
      select.appendChild(opt);
    });
  }
}

async function assignDepProfileToGroup() {
  const groupName = document.getElementById("dep-assign-group-select").value || null;
  const res = await apiFetchJSON("/api/dep-profiles/assign", "POST", {
    filename: currentDepFilename, group_name: groupName,
  });
  if (res.ok) {
    alert(res.data.message);
    await loadAssignSection(currentDepFilename, groupName);
    loadDepProfilesList();
  } else {
    alert("指派失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 編輯器
// ---------------------------------------------------------------------------
function renderDepFields(values) {
  const container = document.getElementById("dep-fields-container");
  container.innerHTML = DEP_SCHEMA.fields.map((f) => renderField(f, values ? values[f.name] : undefined)).join("");
}

function renderSkipItems(selected) {
  const container = document.getElementById("skip-items-container");
  const selectedSet = new Set(selected || []);
  const unverifiedSet = new Set(DEP_SCHEMA.unverified_skip_items || []);
  container.innerHTML = DEP_SCHEMA.skip_setup_items.map((key) => {
    const label = DEP_SCHEMA.skip_setup_item_labels[key] || key;
    const isUnverified = unverifiedSet.has(key);
    const badge = isUnverified
      ? `<span class="badge warn" title="尚未實際部署驗證,建議先在測試機上單獨確認有效">未驗證</span>`
      : "";
    return `
      <label style="display:flex; align-items:center; gap:6px; font-size:13px; padding:3px 0;">
        <input type="checkbox" data-skip-item="${escapeHtml(key)}" ${selectedSet.has(key) ? "checked" : ""}>
        ${escapeHtml(label)} <span style="color:#9ca3af; font-size:11px;">(${escapeHtml(key)})</span> ${badge}
      </label>
    `;
  }).join("");
}

function collectDepFormData() {
  const fieldsContainer = document.getElementById("dep-fields-container");
  const appleProfile = {};
  DEP_SCHEMA.fields.forEach((f) => {
    appleProfile[f.name] = readFieldValue(fieldsContainer, f);
  });
  appleProfile.skip_setup_items = Array.from(
    document.querySelectorAll("#skip-items-container [data-skip-item]:checked")
  ).map((el) => el.dataset.skipItem);
  appleProfile.anchor_certs = [];
  appleProfile.supervising_host_certs = [];

  return { apple_profile: appleProfile };
}

async function openNewDepProfile() {
  await loadDepSchema();
  currentDepFilename = null;
  currentDepIsNew = true;
  currentDepIsProtected = false;

  document.getElementById("dep-editor-title").textContent = "新增註冊 Profile";
  document.getElementById("dep-editor-container").classList.remove("hidden");
  document.getElementById("dep-protected-notice").classList.add("hidden");
  document.getElementById("dep-assign-section").classList.add("hidden");
  document.getElementById("dep-profile-filename").value = "";
  document.getElementById("dep-profile-filename").readOnly = false;
  document.getElementById("dep-status-container").innerHTML =
    "<em>新增後請先儲存,再到列表裡使用「指派」功能把它配對給群組(或到「群組一覽」新增群組時直接選用)</em>";
  document.getElementById("dep-apply-result-container").innerHTML = "";

  renderDepFields(null);
  renderSkipItems([]);
}

async function openEditDepProfile(filename) {
  await loadDepSchema();
  const listRes = await apiFetch("/api/dep-profiles");
  const fileInfo = (listRes.ok ? listRes.data.files : []).find((f) => f.filename === filename) || {};

  const res = await apiFetch(`/api/dep-profiles/${encodeURIComponent(filename)}`);
  if (!res.ok) {
    alert("讀取失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }

  currentDepFilename = filename;
  currentDepIsNew = false;
  currentDepIsProtected = !!fileInfo.is_protected;

  document.getElementById("dep-editor-title").textContent = `編輯 - ${filename}`;
  document.getElementById("dep-editor-container").classList.remove("hidden");
  document.getElementById("dep-profile-filename").value = filename;
  document.getElementById("dep-profile-filename").readOnly = true;
  document.getElementById("dep-apply-result-container").innerHTML = "";

  document.getElementById("dep-protected-notice").classList.toggle("hidden", !currentDepIsProtected);
  document.getElementById("dep-assign-section").classList.toggle("hidden", currentDepIsProtected);
  if (!currentDepIsProtected) {
    await loadAssignSection(filename, fileInfo.assigned_group);
  }

  const statusEl = document.getElementById("dep-status-container");
  statusEl.innerHTML = res.data.last_applied_uuid
    ? `目前已套用的 profile_uuid: <code>${escapeHtml(res.data.last_applied_uuid)}</code>(套用時間: ${escapeHtml(res.data.last_applied_at || "")})`
    : `<span style="color:#b45309;">這份範本還沒有實際套用過</span>`;

  renderDepFields(res.data.apple_profile);
  renderSkipItems(res.data.apple_profile.skip_setup_items || []);
}

async function saveDepProfile() {
  const filename = document.getElementById("dep-profile-filename").value.trim();
  if (!filename.endsWith(".json")) {
    alert("檔名必須以 .json 結尾");
    return;
  }
  const formData = collectDepFormData();
  const res = await apiFetchJSON("/api/dep-profiles/save", "POST", { filename, ...formData });
  if (res.ok) {
    const wasNew = currentDepIsNew;
    currentDepIsNew = false;
    currentDepFilename = filename;
    document.getElementById("dep-profile-filename").readOnly = true;
    document.getElementById("dep-editor-title").textContent = `編輯 - ${filename}`;
    loadDepProfilesList();
    if (wasNew) {
      document.getElementById("dep-assign-section").classList.remove("hidden");
      await loadAssignSection(filename, null);
    }

    if (wasNew) {
      alert("已儲存(僅本地範本,尚未套用到 Apple)。新建立的範本請先指派給群組,再按「套用」。");
    } else if (confirm("已儲存。是否要立即套用?\n\n套用後,這份範本目前配對的群組(或「預設」)底下的所有裝置都會被重新指派到新的 profile_uuid。")) {
      await applyDepProfile();
    }
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function renderApplyResult(result) {
  const container = document.getElementById("dep-apply-result-container");
  const steps = result.steps || {};
  let html = `<div style="background:#e3f6e9; color:#1c7c3f; padding:10px 14px; border-radius:6px; font-size:13px; margin-bottom:8px;">
    ✅ 套用成功,新的 profile_uuid: <code>${escapeHtml(result.new_profile_uuid)}</code>
  </div>`;

  Object.keys(steps).forEach((stepName) => {
    const stepLabel = {
      define: "1. 定義新 Profile", assign: "2. 指派給群組裝置", set_assigner: "2. 設定為預設 Assigner",
      verify: "3. 驗證套用結果", restart_depsyncer: "4. 重新啟動 depsyncer",
    }[stepName] || stepName;
    html += `
      <div style="border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-bottom:6px;">
        <strong style="font-size:12px;">${escapeHtml(stepLabel)}</strong>
        <pre style="white-space:pre-wrap; font-size:11px; margin:6px 0 0 0; color:#4b5563;">${escapeHtml(JSON.stringify(steps[stepName], null, 2))}</pre>
      </div>
    `;
  });
  container.innerHTML = html;
}

async function applyDepProfile() {
  if (currentDepIsNew || !currentDepFilename) {
    alert("請先儲存這份範本,才能套用");
    return;
  }
  if (!confirm(`即將實際呼叫 Apple DEP API 套用這份設定。\n\n這個動作無法復原(會產生新的 profile_uuid),確定要繼續嗎?`)) {
    return;
  }

  const btn = document.getElementById("dep-apply-btn");
  btn.disabled = true;
  btn.textContent = "套用中...(請耐心等候)";
  document.getElementById("dep-apply-result-container").innerHTML = "";

  const res = await apiFetchJSON("/api/dep-profiles/apply", "POST", { filename: currentDepFilename });

  btn.disabled = false;
  btn.textContent = "套用 (實際更新到 Apple/nanodep)";

  if (res.ok) {
    renderApplyResult(res.data.result);
    document.getElementById("dep-status-container").innerHTML =
      `目前已套用的 profile_uuid: <code>${escapeHtml(res.data.last_applied_uuid)}</code>(剛剛套用)`;
    loadDepProfilesList();
  } else {
    document.getElementById("dep-apply-result-container").innerHTML =
      `<div style="background:#fdeaea; color:#b42318; padding:10px 14px; border-radius:6px; font-size:13px;">
        ❌ 套用失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}
      </div>`;
  }
}

// ---------------------------------------------------------------------------
// 事件綁定
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadDepSchema();
  loadDepProfilesList();

  const urlParams = new URLSearchParams(window.location.search);
  const openFilename = urlParams.get("open");
  if (openFilename) {
    openEditDepProfile(openFilename);
  }

  document.getElementById("new-dep-profile-btn").addEventListener("click", openNewDepProfile);
  document.getElementById("dep-save-btn").addEventListener("click", saveDepProfile);
  document.getElementById("dep-apply-btn").addEventListener("click", applyDepProfile);
  document.getElementById("dep-duplicate-btn").addEventListener("click", () => openDuplicateModal(currentDepFilename));
  document.getElementById("dep-duplicate-confirm-btn").addEventListener("click", confirmDuplicateDepProfile);
  document.getElementById("dep-assign-btn").addEventListener("click", assignDepProfileToGroup);

  document.getElementById("dep-profiles-list-container").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const filename = el.dataset.filename;
    if (el.dataset.action === "edit-dep") openEditDepProfile(filename);
    else if (el.dataset.action === "delete-dep") deleteDepProfile(filename);
    else if (el.dataset.action === "duplicate-dep") openDuplicateModal(filename);
  });
});
