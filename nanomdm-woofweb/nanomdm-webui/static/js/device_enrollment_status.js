const NAME_PATTERN_ES = /^[^\x00-\x1f\x7f,"]{0,64}$/;
let groupNamesListES = [];
let allEnrollmentRows = [];

// 裝置註冊狀態的欄位定義,給排序功能用。操作欄位是存檔按鈕不是資料,不列進來。
const ENROLLMENT_TABLE_COLUMNS = [
  { key: "serial_number", label: "序號", type: "text" },
  { key: "wifi_mac", label: "WIFI MAC", type: "text" },
  { key: "model", label: "型號", type: "text" },
  { key: "device_name", label: "裝置名稱", type: "text" },
  { key: "group", label: "群組", type: "text" },
  { key: "profile_uuid", label: "DEP profile_uuid", type: "text" },
  { key: "profile_filename", label: "對應範本", type: "text" },
  { key: "enrollment_id", label: "MDM UUID", type: "text" },
  { key: "profile_status", label: "指派狀態", type: "text" },
];
const enrollmentSorter = createTableSorter();

function renderEnrollmentTableHeader() {
  const thead = document.getElementById("enrollment-status-thead");
  const cells = ENROLLMENT_TABLE_COLUMNS
    .map((col) => `<th style="cursor:pointer;" data-sort-key="${col.key}">${escapeHtml(col.label)}${enrollmentSorter.sortArrow(col.key)}</th>`)
    .join("");
  thead.innerHTML = `<tr>${cells}<th>操作</th></tr>`;
}
let enrollmentImportChanges = [];

function buildGroupOptionsHtmlES(currentGroup) {
  const names = new Set(groupNamesListES);
  if (currentGroup) names.add(currentGroup);
  let html = `<option value="">(未分類)</option>`;
  names.forEach((name) => {
    const selected = name === currentGroup ? "selected" : "";
    html += `<option value="${escapeHtml(name)}" ${selected}>${escapeHtml(name)}</option>`;
  });
  return html;
}

function statusBadgeES(status, pushTime, enrollmentId) {
  const map = { pushed: "ok", assigned: "warn", empty: "warn" };
  const cls = map[status] || "warn";

  let text;
  if (status === "pushed") {
    text = "已註冊成功";
  } else if (status === "assigned") {
    // 已經有MDM UUID代表這台裝置之前註冊過一次;重新指派新的profile後,
    // 要等裝置被清空、重新走一次Setup Assistant才會套用,跟「全新裝置尚未註冊過」是不同情境
    text = enrollmentId ? "(等待重新註冊)" : "已指派(尚未註冊)";
  } else {
    text = "尚未指派";
  }

  let html = `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
  if (status === "pushed" && pushTime) {
    html += `<span style="font-size:11px; color:#6b7280; margin-left:6px; white-space:nowrap;">${escapeHtml(formatPushTime(pushTime))}</span>`;
  }
  return html;
}

function formatPushTime(isoString) {
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return isoString;
  return d.toLocaleString("zh-TW", { hour12: false });
}

function renderEnrollmentRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.serial = row.serial_number;

  const modelText = [row.model, row.description, row.color].filter(Boolean).join(" / ");
  const wifiMacText = row.wifi_mac
    ? `<span style="font-family:var(--mono); white-space:nowrap;">${escapeHtml(row.wifi_mac)}</span>`
    : `<span style="color:#9ca3af; font-size:11px;">(尚無ASM快取資料)</span>`;
  const profileFilenameText = row.profile_filename || (row.profile_uuid ? "(不在本地範本清單中)" : "");
  const mdmUuidText = row.enrollment_id
    ? `<span style="font-family:var(--mono); font-size:11px; word-break:break-all;">${escapeHtml(row.enrollment_id)}</span>`
    : `<span style="color:#9ca3af;">尚未註冊</span>`;

  tr.innerHTML = `
    <td style="font-family:var(--mono); font-size:12px; word-break:break-all;">${escapeHtml(row.serial_number)}</td>
    <td style="font-size:12px;">${wifiMacText}</td>
    <td style="font-size:12px;">${escapeHtml(modelText)}</td>
    <td><input type="text" class="es-name-input" value="${escapeHtml(row.device_name)}" style="width:100%;"></td>
    <td><select class="es-group-select" style="width:100%;">${buildGroupOptionsHtmlES(row.group)}</select></td>
    <td style="font-family:var(--mono); font-size:10.5px; word-break:break-all;">${escapeHtml(row.profile_uuid || "無")}</td>
    <td style="font-size:12px;">${escapeHtml(profileFilenameText)}</td>
    <td>${mdmUuidText}</td>
    <td style="white-space:nowrap;">${statusBadgeES(row.profile_status, row.profile_push_time, row.enrollment_id)}</td>
    <td><button class="secondary es-save-btn" type="button" style="font-size:11px;">存檔</button></td>
  `;
  return tr;
}

function populateGroupFilterDropdown() {
  const select = document.getElementById("enrollment-filter-group");
  const current = select.value;
  select.innerHTML = `<option value="">(全部群組)</option><option value="__none__">(未分類)</option>`;
  groupNamesListES.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  select.value = current || "";
}

function applyEnrollmentFilters() {
  const groupFilter = document.getElementById("enrollment-filter-group").value;
  const searchText = document.getElementById("enrollment-filter-search").value.trim().toLowerCase();

  let filtered = allEnrollmentRows.filter((row) => {
    if (groupFilter === "__none__" && row.group) return false;
    if (groupFilter && groupFilter !== "__none__" && row.group !== groupFilter) return false;
    if (searchText) {
      const haystack = `${row.serial_number} ${row.device_name}`.toLowerCase();
      if (!haystack.includes(searchText)) return false;
    }
    return true;
  });

  filtered = enrollmentSorter.sortRows(filtered, ENROLLMENT_TABLE_COLUMNS);

  renderEnrollmentTableHeader();
  const tbody = document.getElementById("enrollment-status-tbody");
  tbody.innerHTML = "";
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10">沒有符合條件的裝置</td></tr>`;
  } else {
    filtered.forEach((row) => tbody.appendChild(renderEnrollmentRow(row)));
  }
  document.getElementById("enrollment-filter-count").textContent = `顯示 ${filtered.length} / ${allEnrollmentRows.length} 台`;
}

async function loadEnrollmentStatus() {
  const tbody = document.getElementById("enrollment-status-tbody");
  tbody.innerHTML = `<tr><td colspan="10">查詢中...(即時呼叫 Apple API,請耐心等候)</td></tr>`;

  const groupsRes = await apiFetch("/api/groups");
  if (groupsRes.ok) groupNamesListES = groupsRes.data.rows.map((r) => r.group_name);
  populateGroupFilterDropdown();

  const res = await apiFetch("/api/device-enrollment-status");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="10" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  allEnrollmentRows = res.data.rows || [];
  applyEnrollmentFilters();
}

function renderSaveResultInline(tr, result) {
  const existing = tr.querySelector(".es-sync-result-row");
  if (existing) existing.remove();

  const parts = [];

  if (result.name_changed) {
    const push = result.name_push;
    if (push && push.ok) {
      parts.push(`✅ 已推送改名指令`);
    } else if (push) {
      parts.push(`⚠️ 改名指令: ${escapeHtml(push.message || "未送出")}`);
    }
  }

  if (result.group_changed && result.sync_steps) {
    const depStep = result.sync_steps.dep_reassign || {};
    const mcStep = result.sync_steps.mobileconfig_push || {};
    parts.push(depStep.ok ? `✅ DEP 已重新指派(${escapeHtml(depStep.enroll_json || "")})` : `⚠️ DEP 指派: ${escapeHtml(depStep.message || "略過")}`);
    parts.push(mcStep.ok ? `✅ 已推送描述檔(${escapeHtml(mcStep.mobileconfig || "")})` : `⚠️ 描述檔推送: ${escapeHtml(mcStep.message || "略過")}`);
  }

  if (parts.length === 0) return;

  const row = document.createElement("tr");
  row.className = "es-sync-result-row";
  const cell = document.createElement("td");
  cell.colSpan = 10;
  cell.style.cssText = "background:#f8f9fb; padding:10px 14px; font-size:12px;";
  cell.innerHTML = `<div>存檔後續處理結果:</div>` + parts.map((p) => `<div style="margin-top:2px;">${p}</div>`).join("");
  row.appendChild(cell);
  tr.after(row);
}

async function saveEnrollmentRow(tr) {
  const serial = tr.dataset.serial;
  const nameInput = tr.querySelector(".es-name-input");
  const groupSelect = tr.querySelector(".es-group-select");
  const deviceName = nameInput.value.trim();
  const group = groupSelect.value;
  const rowData = allEnrollmentRows.find((r) => r.serial_number === serial) || {};

  if (!NAME_PATTERN_ES.test(deviceName)) {
    alert("裝置名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內");
    return;
  }

  const btn = tr.querySelector(".es-save-btn");
  btn.disabled = true;
  btn.textContent = "儲存中...";

  const res = await apiFetchJSON("/api/device-enrollment-status/save", "POST", {
    serial_number: serial, device_name: deviceName, group: group,
    enrollment_id: rowData.enrollment_id || "", wifi_mac: rowData.wifi_mac || "",
  });

  btn.disabled = false;
  btn.textContent = "存檔";

  if (res.ok) {
    nameInput.style.background = "#e3f6e9";
    groupSelect.style.background = "#e3f6e9";
    setTimeout(() => { nameInput.style.background = ""; groupSelect.style.background = ""; }, 900);
    renderSaveResultInline(tr, res.data);
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 匯出 / 匯入
// ---------------------------------------------------------------------------
function exportAllEnrollmentCsv() {
  window.location.href = apiUrl("/api/device-enrollment-status/export/all");
}

function exportUnassignedEnrollmentCsv() {
  window.location.href = apiUrl("/api/device-enrollment-status/export/unassigned");
}

async function handleImportFileSelected(e) {
  const file = e.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  debugLog("REQUEST POST /api/device-enrollment-status/import/preview", file.name);
  const resp = await fetch(apiUrl("/api/device-enrollment-status/import/preview"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/device-enrollment-status/import/preview", data, !resp.ok);

  e.target.value = "";

  if (!data.ok) {
    alert("分析失敗: " + (data.message || "未知錯誤"));
    return;
  }

  enrollmentImportChanges = data.changes || [];
  renderImportMismatches(data.mismatches || []);
  renderImportPreview(enrollmentImportChanges);
  document.getElementById("enrollment-import-apply-progress").innerHTML = "";

  if (enrollmentImportChanges.length === 0 && (!data.mismatches || data.mismatches.length === 0)) {
    alert("比對結果:沒有發現任何需要變更的項目");
    return;
  }
  openModal("enrollment-import-modal");
}

function renderImportMismatches(mismatches) {
  const container = document.getElementById("enrollment-import-mismatches");
  if (mismatches.length === 0) {
    container.innerHTML = "";
    return;
  }
  let html = `<div style="background:#fdeee0; color:#b45309; padding:10px 14px; border-radius:6px; font-size:12px;">
    <strong>⚠️ 以下 ${mismatches.length} 筆因為資料不一致,已自動排除:</strong>`;
  mismatches.forEach((m) => {
    html += `<div style="margin-top:4px;">${escapeHtml(m.serial_number)}: ${escapeHtml(m.reason)}</div>`;
  });
  html += `</div>`;
  container.innerHTML = html;
}

function renderImportPreview(changes) {
  const tbody = document.getElementById("enrollment-import-preview-tbody");
  tbody.innerHTML = "";
  if (changes.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3">沒有需要套用的變更</td></tr>`;
    return;
  }
  changes.forEach((c) => {
    const tr = document.createElement("tr");
    const nameText = c.name_changed ? `${escapeHtml(c.old_device_name || "(空)")} → ${escapeHtml(c.device_name || "(空)")}` : "(無變更)";
    const groupText = c.group_changed ? `${escapeHtml(c.old_group || "(未分類)")} → ${escapeHtml(c.group || "(未分類)")}` : "(無變更)";
    tr.innerHTML = `
      <td style="font-family:var(--mono);">${escapeHtml(c.serial_number)}</td>
      <td>${nameText}</td>
      <td>${groupText}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function applyImportChanges() {
  if (enrollmentImportChanges.length === 0) {
    alert("沒有任何可套用的變更");
    return;
  }
  if (!confirm(`確定要套用 ${enrollmentImportChanges.length} 筆變更嗎?`)) return;

  const btn = document.getElementById("enrollment-import-apply-btn");
  btn.disabled = true;
  btn.textContent = "套用中...";

  const progressContainer = document.getElementById("enrollment-import-apply-progress");
  progressContainer.innerHTML = "";

  debugLog("REQUEST POST /api/device-enrollment-status/import/apply-stream", { count: enrollmentImportChanges.length });
  const resp = await fetch(apiUrl("/api/device-enrollment-status/import/apply-stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes: enrollmentImportChanges }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    parts.forEach((part) => {
      if (!part.startsWith("data: ")) return;
      let update;
      try { update = JSON.parse(part.slice(6)); } catch (e) { return; }
      renderImportProgressLine(update);
      if (update.done) {
        btn.disabled = false;
        btn.textContent = "套用變更";
        loadEnrollmentStatus();
      }
    });
  }
}

function renderImportProgressLine(update) {
  const container = document.getElementById("enrollment-import-apply-progress");
  if (update.done) {
    const div = document.createElement("div");
    div.style.cssText = "background:#e3f6e9; color:#1c7c3f; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;";
    div.textContent = `✅ 全部完成,共處理 ${update.total} 筆`;
    container.appendChild(div);
    return;
  }
  const div = document.createElement("div");
  div.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-top:6px; font-size:12px;";
  let html = `<strong>[${update.index}/${update.total}] ${escapeHtml(update.serial_number)}</strong>`;
  if (update.save) html += `<div>存檔: ${update.save.ok ? "✅ 成功" : "❌ " + escapeHtml(update.save.message || "失敗")}</div>`;
  if (update.rename_command) html += `<div>改名指令: ${update.rename_command.ok ? "✅ 已送出" : "⚠️ " + escapeHtml(update.rename_command.message || "失敗")}</div>`;
  if (update.group_sync) {
    const dep = update.group_sync.dep_reassign || {};
    const mc = update.group_sync.mobileconfig_push || {};
    html += `<div>DEP 指派: ${dep.ok ? "✅ 成功" : "⚠️ " + escapeHtml(dep.message || "略過")}</div>`;
    html += `<div>描述檔推送: ${mc.ok ? "✅ 成功" : "⚠️ " + escapeHtml(mc.message || "略過")}</div>`;
  }
  div.innerHTML = html;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

document.addEventListener("DOMContentLoaded", () => {
  loadEnrollmentStatus();

  document.getElementById("refresh-enrollment-status-btn").addEventListener("click", loadEnrollmentStatus);
  document.getElementById("export-all-enrollment-btn").addEventListener("click", exportAllEnrollmentCsv);
  document.getElementById("export-unassigned-enrollment-btn").addEventListener("click", exportUnassignedEnrollmentCsv);
  document.getElementById("import-enrollment-btn").addEventListener("click", () => {
    document.getElementById("import-enrollment-file-input").click();
  });
  document.getElementById("import-enrollment-file-input").addEventListener("change", handleImportFileSelected);
  document.getElementById("enrollment-import-apply-btn").addEventListener("click", applyImportChanges);

  document.getElementById("enrollment-filter-group").addEventListener("change", applyEnrollmentFilters);
  document.getElementById("enrollment-filter-search").addEventListener("input", applyEnrollmentFilters);

  document.getElementById("enrollment-status-thead").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th || !th.dataset.sortKey) return;
    enrollmentSorter.handleHeaderClick(th.dataset.sortKey);
    applyEnrollmentFilters();
  });

  document.getElementById("enrollment-status-tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || !tr.dataset.serial) return;
    if (e.target.classList.contains("es-save-btn")) {
      saveEnrollmentRow(tr);
    }
  });
});
