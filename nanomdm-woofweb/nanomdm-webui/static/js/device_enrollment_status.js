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
  { key: "profile_filename", label: "對應註冊檔", type: "text" },
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
  let customStyle = null;
  if (status === "pushed") {
    text = "已註冊成功";
  } else if (status === "assigned") {
    // 已經有MDM UUID代表這台裝置之前註冊過一次;重新指派新的profile後,
    // 要等裝置被清空、重新走一次Setup Assistant才會套用,跟「全新裝置尚未註冊過」是不同情境
    if (enrollmentId) {
      text = "註冊檔已修改，等待重新註冊";
      customStyle = "background-color:#dbeafe; color:#000000; border-color:#93c5fd;";
    } else {
      text = "已指派(尚未註冊)";
    }
  } else {
    text = "尚未指派";
  }

  const styleAttr = customStyle ? ` style="${customStyle}"` : "";
  let html = customStyle
    ? `<span class="badge"${styleAttr}>${escapeHtml(text)}</span>`
    : `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
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

// 判斷邏輯要跟statusBadgeES()裡「怎麼把raw status+enrollmentId轉成畫面上四種顯示狀態」
// 完全一致,不然篩選出來的結果會跟畫面上實際看到的文字對不起來
function deviceMatchesAssignmentStatusFilter(row, filter) {
  if (!filter) return true;
  if (filter === "pushed") return row.profile_status === "pushed";
  if (filter === "assigned_reregister") return row.profile_status === "assigned" && !!row.enrollment_id;
  if (filter === "assigned_new") return row.profile_status === "assigned" && !row.enrollment_id;
  if (filter === "empty") return row.profile_status !== "pushed" && row.profile_status !== "assigned";
  return true;
}

function applyEnrollmentFilters() {
  const groupFilter = document.getElementById("enrollment-filter-group").value;
  const searchText = document.getElementById("enrollment-filter-search").value.trim().toLowerCase();
  const statusFilter = document.getElementById("enrollment-filter-status").value;

  let filtered = allEnrollmentRows.filter((row) => {
    if (groupFilter === "__none__" && row.group) return false;
    if (groupFilter && groupFilter !== "__none__" && row.group !== groupFilter) return false;
    if (searchText) {
      const haystack = `${row.serial_number} ${row.device_name}`.toLowerCase();
      if (!haystack.includes(searchText)) return false;
    }
    if (!deviceMatchesAssignmentStatusFilter(row, statusFilter)) return false;
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

  const report = data.rows || [];
  if (report.length === 0) {
    alert("CSV 裡沒有任何資料列");
    return;
  }

  // 群組有問題(blocked)時整批停止:不給變更預覽、也不給套用按鈕。
  // 但不管有沒有blocked,每一列的檢查結果都會列出來(通過的也列)
  enrollmentImportChanges = data.blocked ? [] : (data.changes || []);
  setImportModalBlocked(!!data.blocked);
  renderImportCheckResults(report, data.summary || {}, !!data.blocked, data.available_groups || []);
  renderImportPreview(enrollmentImportChanges);
  document.getElementById("enrollment-import-apply-progress").innerHTML = "";
  openModal("enrollment-import-modal");
}

// 匯入視窗有兩種模式:正常的「變更預覽」,以及群組檢查沒過時的「匯入已停止」
function setImportModalBlocked(blocked) {
  document.getElementById("enrollment-import-title").textContent = blocked ? "匯入已停止:群組欄位有問題" : "匯入變更預覽";
  document.getElementById("enrollment-import-preview-table").style.display = blocked ? "none" : "";
  document.getElementById("enrollment-import-actions").style.display = blocked ? "none" : "";
}

// 列出CSV「每一列」的檢查結果(通過的也列,不是只列出錯誤的)
function renderImportCheckResults(report, summary, blocked, availableGroups) {
  const container = document.getElementById("enrollment-import-check-results");
  const rowStyle = {
    pass: "",
    blocked: "background:#fdecec;",
    excluded: "background:#fdf3e3;",
    skipped: "background:#f3f4f6; color:#6b7280;",
  };

  const rowsHtml = report.map((r) => {
    let resultHtml;
    if (r.status === "pass") {
      resultHtml = `<span style="color:#1c7c3f;">✅ 全部通過</span>`;
    } else {
      resultHtml = r.problems.map((p) => {
        const icon = p.blocking ? "❌" : (r.status === "skipped" ? "➖" : "⚠️");
        return `<div>${icon} ${escapeHtml(p.check)}: ${escapeHtml(p.reason)}</div>`;
      }).join("");
    }

    let changeHtml = "—";
    if (r.status === "pass") {
      if (r.change) {
        const parts = [];
        if (r.change.name_changed) parts.push(`名稱: ${escapeHtml(r.change.old_device_name || "(空)")} → ${escapeHtml(r.device_name || "(空)")}`);
        if (r.change.group_changed) parts.push(`群組: ${escapeHtml(r.change.old_group || "(未分類)")} → ${escapeHtml(r.group || "(未分類)")}`);
        changeHtml = parts.join("<br>");
      } else {
        changeHtml = `<span style="color:#6b7280;">(無變更)</span>`;
      }
    }

    return `
      <tr style="${rowStyle[r.status] || ""}">
        <td>${r.line_no || "-"}</td>
        <td style="font-family:var(--mono);">${r.serial_number ? escapeHtml(r.serial_number) : "(空白)"}</td>
        <td>${escapeHtml(r.device_name || "(空)")}</td>
        <td>${r.group ? escapeHtml(r.group) : "(空白)"}</td>
        <td>${resultHtml}</td>
        <td style="font-size:12px;">${changeHtml}</td>
      </tr>
    `;
  }).join("");

  const s = summary;
  const summaryHtml = `
    <div style="font-size:13px; margin-bottom:6px;">
      共 <strong>${s.total || 0}</strong> 列:
      ✅ 通過 <strong>${s.pass || 0}</strong> 列(其中 ${s.pass_with_change || 0} 列有變更)　
      ❌ 群組有問題 <strong>${s.blocked || 0}</strong> 列　
      ⚠️ 已排除 <strong>${s.excluded || 0}</strong> 列　
      ➖ 略過 <strong>${s.skipped || 0}</strong> 列
    </div>
  `;

  const blockedHtml = blocked ? `
    <div style="background:#fdecec; color:#991b1b; padding:10px 14px; border-radius:6px; font-size:13px; margin-bottom:8px;">
      <strong>❌ 有 ${s.blocked || 0} 列的「群組」欄位缺漏或不正確,本次匯入已停止,沒有變更任何資料。</strong>
      <div style="margin-top:4px;">請修正下表標示 ❌ 的列(行號是 CSV 檔案裡的行數,標題列是第 1 行),存檔後重新匯入。</div>
    </div>
  ` : "";

  const groupsHint = blocked ? `
    <div style="margin-top:8px; font-size:12px; color:#6b7280;">
      ${availableGroups.length
        ? `目前已建立的群組: ${availableGroups.map((g) => escapeHtml(g)).join("、")}`
        : "目前還沒有建立任何群組,請先到「所有群組」頁面建立"}
    </div>
  ` : "";

  container.innerHTML = `
    ${blockedHtml}
    ${summaryHtml}
    <div style="max-height:320px; overflow-y:auto; border:1px solid var(--border-color); border-radius:6px;">
      <table class="data-table" style="margin:0;">
        <thead><tr><th>CSV 行號</th><th>序號</th><th>裝置名稱</th><th>群組</th><th>檢查結果</th><th>變更內容</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
    ${groupsHint}
  `;
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

  // 伺服器端群組檢查沒過(或其他錯誤)時回的是一般JSON錯誤,不是SSE串流,
  // 要在這裡處理掉,不然按鈕會一直卡在「套用中...」
  if (!resp.ok) {
    let errData = {};
    try { errData = await resp.json(); } catch (e) { /* 回應不是JSON,沿用空物件 */ }
    btn.disabled = false;
    btn.textContent = "套用變更";
    alert("套用失敗: " + (errData.message || `HTTP ${resp.status}`));
    return;
  }

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
  document.getElementById("enrollment-filter-status").addEventListener("change", applyEnrollmentFilters);

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
