let PROFILE_SCHEMA = null;   // { top_level_fields, payload_schema }
let currentFilename = null;  // 目前編輯中的檔名(null代表尚未開始)
let currentIsNew = false;
let currentIsProtected = false;
let currentUnmanagedPayloads = [];
let instanceCounter = 0; // 給多實例(wifi/webclip)區塊產生不重複的DOM id用

// ---------------------------------------------------------------------------
// 載入 schema (只需要載入一次)
// ---------------------------------------------------------------------------
async function loadSchema() {
  if (PROFILE_SCHEMA) return PROFILE_SCHEMA;
  const res = await apiFetch("/api/profiles/schema");
  if (res.ok) {
    PROFILE_SCHEMA = res.data;
  }
  return PROFILE_SCHEMA;
}

// ---------------------------------------------------------------------------
// 描述檔清單
// ---------------------------------------------------------------------------
function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function formatMtime(epochSeconds) {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString("zh-TW", { hour12: false });
}

async function loadProfilesList() {
  const container = document.getElementById("profiles-list-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/profiles");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  if (res.data.files.length === 0) {
    container.innerHTML = `<p style="color:#6b7280; font-size:13px;">目前沒有任何 .mobileconfig 檔案</p>`;
    return;
  }

  container.innerHTML = "";
  res.data.files.forEach((f) => {
    const div = document.createElement("div");
    const highlightStyle = f.is_protected ? "background:#fff7e6; border-left:3px solid #f0a500;" : "";
    div.style.cssText = `border-bottom:1px solid var(--border-color); padding:10px 10px; ${highlightStyle}`;
    const errBadge = f.parse_error ? `<span class="badge warn">解析失敗</span>` : "";
    const protectedBadge = f.is_protected ? `<span class="badge warn" style="margin-left:6px;">系統預設</span>` : "";
    div.innerHTML = `
      <div style="font-family:var(--mono); font-size:13px; font-weight:600;">${escapeHtml(f.filename)} ${errBadge}${protectedBadge}</div>
      <div style="font-size:12px; color:#6b7280; margin:3px 0;">${escapeHtml(f.display_name || "(無顯示名稱)")} · 配對群組: ${escapeHtml(f.assigned_group_label)}</div>
      <div style="font-size:11px; color:#9ca3af;">${f.payload_types.map(escapeHtml).join(", ") || "(無 payload)"}</div>
      <div style="font-size:11px; color:#9ca3af;">${formatBytes(f.size)} · ${formatMtime(f.mtime)}</div>
      <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
        <button class="secondary" data-action="edit-profile" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">編輯</button>
        <button class="secondary" data-action="duplicate-profile" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">再製</button>
        <button class="secondary" data-action="download-profile" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">下載</button>
        ${f.is_protected ? "" : `<button class="danger" data-action="delete-profile" data-filename="${escapeHtml(f.filename)}" type="button" style="font-size:12px;">刪除</button>`}
      </div>
    `;
    container.appendChild(div);
  });
}

async function deleteProfile(filename) {
  if (!confirm(`確定要刪除 ${filename} 嗎?這個動作無法復原。`)) return;
  const res = await apiFetchJSON("/api/profiles/delete", "POST", { filename });
  if (res.ok) {
    loadProfilesList();
    if (currentFilename === filename) {
      document.getElementById("editor-container").classList.add("hidden");
      document.getElementById("editor-title").textContent = "請從左側選擇一個描述檔,或按「新增描述檔」";
      currentFilename = null;
    }
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function downloadProfile(filename) {
  window.location.href = apiUrl(`/api/profiles/download/${encodeURIComponent(filename)}`);
}

// ---------------------------------------------------------------------------
// 再製
// ---------------------------------------------------------------------------
function openDuplicateModal(filename) {
  document.getElementById("profile-duplicate-new-filename").value = "";
  document.getElementById("profile-duplicate-new-filename").dataset.sourceFilename = filename;
  openModal("profile-duplicate-modal");
}

async function confirmDuplicateProfile() {
  const input = document.getElementById("profile-duplicate-new-filename");
  const sourceFilename = input.dataset.sourceFilename;
  const newFilename = input.value.trim();
  if (!newFilename.endsWith(".mobileconfig")) {
    alert("檔名必須以 .mobileconfig 結尾");
    return;
  }
  const res = await apiFetchJSON("/api/profiles/duplicate", "POST", {
    source_filename: sourceFilename, new_filename: newFilename,
  });
  if (res.ok) {
    closeModal("profile-duplicate-modal");
    loadProfilesList();
    alert(`已複製為 ${newFilename},請記得到編輯畫面把它指派給正確的群組`);
  } else {
    alert("複製失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 群組指派 (跟內容編輯分開的獨立動作,系統保護檔案不會顯示這區)
// ---------------------------------------------------------------------------
async function loadProfileAssignSection(filename, currentGroupLabel) {
  document.getElementById("profile-current-group-label").textContent = currentGroupLabel || "(尚未指派給任何群組)";
  const select = document.getElementById("profile-assign-group-select");
  select.innerHTML = `<option value="">(取消指派)</option>`;
  const res = await apiFetch(`/api/profiles/unpaired-groups?filename=${encodeURIComponent(filename)}`);
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

async function assignProfileToGroup() {
  const groupName = document.getElementById("profile-assign-group-select").value || null;
  const res = await apiFetchJSON("/api/profiles/assign", "POST", {
    filename: currentFilename, group_name: groupName,
  });
  if (res.ok) {
    alert(res.data.message);
    await loadProfileAssignSection(currentFilename, groupName);
    loadProfilesList();
  } else {
    alert("指派失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 編輯器渲染
// ---------------------------------------------------------------------------
function renderTopLevelFields(values) {
  const container = document.getElementById("top-level-fields-container");
  container.innerHTML = PROFILE_SCHEMA.top_level_fields.map((f) => renderField(f, values ? values[f.name] : undefined)).join("");
}

function renderInstanceBlock(schema, key, instanceId, fields) {
  const div = document.createElement("div");
  div.className = "payload-instance";
  div.dataset.instanceId = instanceId;
  div.style.cssText = "border:1px dashed var(--border-color); border-radius:6px; padding:10px 12px; margin-bottom:8px; position:relative;";
  div.innerHTML = `
    <button type="button" class="danger remove-instance-btn" style="position:absolute; top:8px; right:8px; font-size:11px; padding:3px 8px;">移除</button>
    ${schema.fields.map((f) => renderField(f, fields ? fields[f.name] : undefined)).join("")}
  `;
  return div;
}

function renderPayloadsSection(payloadsData) {
  const container = document.getElementById("payloads-container");
  container.innerHTML = "";

  Object.keys(PROFILE_SCHEMA.payload_schema).forEach((key) => {
    const schema = PROFILE_SCHEMA.payload_schema[key];
    const isSingular = schema.singular !== false;
    const existing = (payloadsData && payloadsData[key]) || (isSingular ? { enabled: false, fields: {} } : { enabled: false, instances: [] });

    const wrapper = document.createElement("div");
    wrapper.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:12px 14px; margin-bottom:12px;";

    if (isSingular) {
      const fieldsId = `payload-fields-${key}`;
      const payloadHelp = schema.help
        ? `<p style="color:#6b7280; font-size:12px; margin:6px 0 0 26px; line-height:1.5;">${escapeHtml(schema.help)}</p>`
        : "";
      wrapper.innerHTML = `
        <label style="display:flex; align-items:center; gap:8px; font-weight:600; cursor:pointer;">
          <input type="checkbox" class="payload-toggle" data-payload-key="${key}" ${existing.enabled ? "checked" : ""}>
          ${escapeHtml(schema.label)}
        </label>
        ${payloadHelp}
        <div id="${fieldsId}" class="payload-fields ${existing.enabled ? "" : "hidden"}" data-payload-key="${key}" style="margin-top:10px; padding-left:26px;">
          ${schema.fields.map((f) => renderField(f, existing.fields ? existing.fields[f.name] : undefined)).join("")}
        </div>
      `;
    } else {
      // 非singular(wifi/webclip):支援多組實例,每組有自己的移除按鈕,底下有「新增一組」按鈕
      wrapper.innerHTML = `
        <label style="display:flex; align-items:center; gap:8px; font-weight:600; cursor:pointer;">
          <input type="checkbox" class="payload-toggle" data-payload-key="${key}" ${existing.enabled ? "checked" : ""}>
          ${escapeHtml(schema.label)}
        </label>
        <div class="payload-fields ${existing.enabled ? "" : "hidden"}" data-payload-key="${key}" style="margin-top:10px; padding-left:26px;">
          <div class="instances-container" data-payload-key="${key}"></div>
          <button type="button" class="secondary add-instance-btn" data-payload-key="${key}" style="font-size:12px; margin-top:4px;">+ 新增一組${escapeHtml(schema.label)}</button>
        </div>
      `;
      container.appendChild(wrapper);

      const instancesContainer = wrapper.querySelector(".instances-container");
      const instances = existing.instances && existing.instances.length > 0 ? existing.instances : [{ fields: {} }];
      instances.forEach((inst) => {
        instanceCounter += 1;
        instancesContainer.appendChild(renderInstanceBlock(schema, key, instanceCounter, inst.fields));
      });
      return; // 已經 appendChild 過了,跳過下面共用的 appendChild
    }
    container.appendChild(wrapper);
  });

  container.querySelectorAll(".payload-toggle").forEach((cb) => {
    cb.addEventListener("change", () => {
      const fieldsDiv = container.querySelector(`.payload-fields[data-payload-key="${cb.dataset.payloadKey}"]`);
      fieldsDiv.classList.toggle("hidden", !cb.checked);
    });
  });

  container.querySelectorAll(".add-instance-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.payloadKey;
      const schema = PROFILE_SCHEMA.payload_schema[key];
      const instancesContainer = container.querySelector(`.instances-container[data-payload-key="${key}"]`);
      instanceCounter += 1;
      instancesContainer.appendChild(renderInstanceBlock(schema, key, instanceCounter, null));
    });
  });

  container.addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-instance-btn")) {
      const block = e.target.closest(".payload-instance");
      const instancesContainer = block.parentElement;
      // 至少保留一組,不能整個刪光(要移除整個payload類型請用上面的核取方塊取消勾選)
      if (instancesContainer.querySelectorAll(".payload-instance").length > 1) {
        block.remove();
      } else {
        alert("至少要保留一組;如果不需要這個 payload,請直接取消勾選上方的核取方塊");
      }
    }
  });
}

function collectFormData() {
  const topLevel = {};
  const topContainer = document.getElementById("top-level-fields-container");
  PROFILE_SCHEMA.top_level_fields.forEach((f) => {
    topLevel[f.name] = readFieldValue(topContainer, f);
  });

  const payloads = {};
  const payloadsContainer = document.getElementById("payloads-container");
  Object.keys(PROFILE_SCHEMA.payload_schema).forEach((key) => {
    const schema = PROFILE_SCHEMA.payload_schema[key];
    const isSingular = schema.singular !== false;
    const toggle = payloadsContainer.querySelector(`.payload-toggle[data-payload-key="${key}"]`);

    if (isSingular) {
      const fieldsDiv = payloadsContainer.querySelector(`.payload-fields[data-payload-key="${key}"]`);
      const fields = {};
      schema.fields.forEach((f) => { fields[f.name] = readFieldValue(fieldsDiv, f); });
      payloads[key] = { enabled: toggle.checked, fields };
    } else {
      const instancesContainer = payloadsContainer.querySelector(`.instances-container[data-payload-key="${key}"]`);
      const instances = Array.from(instancesContainer.querySelectorAll(".payload-instance")).map((block) => {
        const fields = {};
        schema.fields.forEach((f) => { fields[f.name] = readFieldValue(block, f); });
        return { fields };
      });
      payloads[key] = { enabled: toggle.checked, instances };
    }
  });

  return { top_level: topLevel, payloads };
}

function renderWarnings(warnings, errorMessage) {
  const container = document.getElementById("profile-warnings-container");
  let html = "";
  if (errorMessage) {
    html += `<div style="background:#fdeaea; color:#b42318; padding:10px 14px; border-radius:6px; font-size:13px; margin-bottom:8px;">❌ ${escapeHtml(errorMessage)}</div>`;
  }
  if (warnings && warnings.length > 0) {
    html += warnings.map((w) =>
      `<div style="background:#fdeee0; color:#b45309; padding:10px 14px; border-radius:6px; font-size:13px; margin-bottom:8px;">⚠️ ${escapeHtml(w)}</div>`
    ).join("");
  }
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 開啟編輯器(新增 / 編輯既有檔案)
// ---------------------------------------------------------------------------
function protectedNoticeText(filename) {
  if (filename === "enroll-template.mobileconfig") {
    return "⚠️ 這是系統預設的「精簡註冊描述檔」,裝置自動註冊(enroll-server.py)時會讀取這份檔案。內容應保持最精簡(通常只需要 SCEP + MDM)以加快註冊速度。可以編輯內容,但不能刪除、也不能指派給特定群組。";
  }
  if (filename === "baseline.mobileconfig") {
    return "⚠️ 這是系統預設的「初次註冊完成描述檔」,裝置完成註冊後(webhook-server.py)會推送這份檔案,通常包含 Wi-Fi、Web Clip、取用限制等完整設定。可以編輯內容,但不能刪除、也不能指派給特定群組。";
  }
  return "⚠️ 這是系統預設檔案,可以編輯內容,但不能刪除、也不能指派給特定群組。";
}

async function openNewProfile() {
  await loadSchema();
  currentFilename = null;
  currentIsNew = true;
  currentIsProtected = false;
  currentUnmanagedPayloads = [];

  document.getElementById("editor-title").textContent = "新增描述檔";
  document.getElementById("editor-container").classList.remove("hidden");
  document.getElementById("profile-protected-notice").classList.add("hidden");
  document.getElementById("profile-assign-section").classList.add("hidden");
  document.getElementById("profile-filename").value = "";
  document.getElementById("profile-filename").readOnly = false;
  document.getElementById("download-profile-btn").classList.add("hidden");
  document.getElementById("profile-warnings-container").innerHTML = "";

  renderTopLevelFields(null);
  renderPayloadsSection(null);
}

async function openEditProfile(filename) {
  await loadSchema();
  const listRes = await apiFetch("/api/profiles");
  const fileInfo = (listRes.ok ? listRes.data.files : []).find((f) => f.filename === filename) || {};

  const res = await apiFetch(`/api/profiles/${encodeURIComponent(filename)}`);
  if (!res.ok) {
    alert("讀取失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }

  currentFilename = filename;
  currentIsNew = false;
  currentIsProtected = !!fileInfo.is_protected;
  currentUnmanagedPayloads = res.data.unmanaged_payloads || [];

  document.getElementById("editor-title").textContent = `編輯 - ${filename}`;
  document.getElementById("editor-container").classList.remove("hidden");
  document.getElementById("profile-filename").value = filename;
  document.getElementById("profile-filename").readOnly = true;
  document.getElementById("download-profile-btn").classList.remove("hidden");
  document.getElementById("profile-warnings-container").innerHTML = "";

  const noticeEl = document.getElementById("profile-protected-notice");
  if (currentIsProtected) {
    noticeEl.textContent = protectedNoticeText(filename);
    noticeEl.classList.remove("hidden");
  } else {
    noticeEl.classList.add("hidden");
  }
  document.getElementById("profile-assign-section").classList.toggle("hidden", currentIsProtected);
  if (!currentIsProtected) {
    await loadProfileAssignSection(filename, fileInfo.assigned_group);
  }

  if (currentUnmanagedPayloads.length > 0) {
    document.getElementById("profile-warnings-container").innerHTML =
      `<div style="background:#e8eefc; color:#1e40af; padding:10px 14px; border-radius:6px; font-size:13px;">
        ℹ️ 這份檔案裡有 ${currentUnmanagedPayloads.length} 個此編輯器不認得的 payload 類型,存檔時會原樣保留不會被刪除。
      </div>`;
  }

  renderTopLevelFields(res.data.top_level);
  renderPayloadsSection(res.data.payloads);
}

async function validateProfile() {
  const formData = collectFormData();
  const res = await apiFetchJSON("/api/profiles/validate", "POST", {
    ...formData, unmanaged_payloads: currentUnmanagedPayloads,
  });
  if (res.ok && res.data.valid) {
    renderWarnings(res.data.warnings, null);
    if (!res.data.warnings || res.data.warnings.length === 0) {
      alert(`格式驗證通過 (檔案大小約 ${res.data.size} bytes)`);
    }
  } else {
    renderWarnings([], (res.data && res.data.message) || "驗證失敗");
  }
}

async function saveProfile() {
  const filename = document.getElementById("profile-filename").value.trim();
  if (!filename.endsWith(".mobileconfig")) {
    alert("檔名必須以 .mobileconfig 結尾");
    return;
  }

  const formData = collectFormData();
  const btn = document.getElementById("save-profile-btn");
  btn.disabled = true;
  btn.textContent = "儲存中...";

  const res = await apiFetchJSON("/api/profiles/save", "POST", {
    filename,
    is_new: currentIsNew,
    unmanaged_payloads: currentUnmanagedPayloads,
    ...formData,
  });

  btn.disabled = false;
  btn.textContent = "儲存";

  if (res.ok) {
    renderWarnings(res.data.warnings, null);
    const wasNew = currentIsNew;
    currentIsNew = false;
    currentFilename = filename;
    document.getElementById("profile-filename").readOnly = true;
    document.getElementById("download-profile-btn").classList.remove("hidden");
    document.getElementById("editor-title").textContent = `編輯 - ${filename}`;
    loadProfilesList();
    if (wasNew) {
      document.getElementById("profile-assign-section").classList.remove("hidden");
      await loadProfileAssignSection(filename, null);
      alert("已儲存。新建立的描述檔請先指派給群組,再重新編輯儲存一次即可套用推送。");
    } else if (!currentIsProtected) {
      const currentGroupLabel = document.getElementById("profile-current-group-label").textContent;
      const hasGroup = currentGroupLabel && currentGroupLabel !== "(尚未指派給任何群組)" && currentGroupLabel !== "-";
      if (hasGroup && confirm(`已儲存。是否要立即套用,推送給「${currentGroupLabel}」群組目前的所有裝置?\n\n過程會逐台顯示進度。`)) {
        await applyProfileToGroup(filename);
      } else if (!hasGroup) {
        alert("已儲存(這份描述檔還沒指派給任何群組,沒有可推送的對象)");
      }
    } else {
      alert("已儲存(系統預設檔案不會自動批次推送,enroll-template 會在下次裝置註冊時套用,baseline 會在裝置完成初次註冊時套用)");
    }
  } else {
    renderWarnings([], (res.data && res.data.message) || "儲存失敗");
  }
}

async function applyProfileToGroup(filename) {
  const progressContainer = document.getElementById("profile-apply-progress-container");
  progressContainer.innerHTML = "";

  const es = new EventSource(apiUrl(`/api/profiles/apply-to-group-stream?filename=${encodeURIComponent(filename)}`));
  es.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }

    const div = document.createElement("div");
    if (update.error) {
      div.style.cssText = "background:#fdeaea; color:#b42318; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;";
      div.textContent = `❌ ${update.error}`;
    } else if (update.done) {
      div.style.cssText = "background:#e3f6e9; color:#1c7c3f; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;";
      div.textContent = `✅ 完成,群組「${update.target_group}」共 ${update.success_count}/${update.total} 台裝置推送成功`;
    } else if (update.message) {
      div.style.cssText = "color:#6b7280; font-size:12px; margin-top:6px;";
      div.textContent = update.message;
    } else {
      div.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:6px 10px; font-size:12px; margin-top:4px;";
      div.textContent = `[${update.index}/${update.total}] ${update.serial_number}: ${update.ok ? "✅ 成功" : "⚠️ " + (update.message || "失敗")}`;
    }
    progressContainer.appendChild(div);
    progressContainer.scrollTop = progressContainer.scrollHeight;

    if (update.done || update.error) es.close();
  };
  es.onerror = () => es.close();
}

// ---------------------------------------------------------------------------
// 事件綁定
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadSchema();
  loadProfilesList();

  document.getElementById("new-profile-btn").addEventListener("click", openNewProfile);
  document.getElementById("validate-profile-btn").addEventListener("click", validateProfile);
  document.getElementById("save-profile-btn").addEventListener("click", saveProfile);
  document.getElementById("duplicate-profile-btn").addEventListener("click", () => openDuplicateModal(currentFilename));
  document.getElementById("profile-duplicate-confirm-btn").addEventListener("click", confirmDuplicateProfile);
  document.getElementById("profile-assign-btn").addEventListener("click", assignProfileToGroup);
  document.getElementById("download-profile-btn").addEventListener("click", () => {
    if (currentFilename) downloadProfile(currentFilename);
  });

  document.getElementById("profiles-list-container").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const filename = el.dataset.filename;
    const action = el.dataset.action;
    if (action === "edit-profile") openEditProfile(filename);
    else if (action === "download-profile") downloadProfile(filename);
    else if (action === "delete-profile") deleteProfile(filename);
    else if (action === "duplicate-profile") openDuplicateModal(filename);
  });
});
