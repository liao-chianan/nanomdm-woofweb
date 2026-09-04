const LOG_COLUMN_SPECS = {
  login: [
    { key: "timestamp", label: "時間戳記", type: "text" },
    { key: "username", label: "帳號", type: "text" },
    { key: "success", label: "登入結果", type: "bool", trueLabel: "成功", falseLabel: "失敗" },
    { key: "ip", label: "登入 IP", type: "text" },
  ],
  activity: [
    { key: "timestamp", label: "時間戳記", type: "text" },
    { key: "username", label: "帳號", type: "text" },
    { key: "command", label: "操作命令", type: "text" },
    { key: "success", label: "是否成功派送", type: "bool", trueLabel: "成功", falseLabel: "失敗" },
    { key: "ip", label: "來源 IP", type: "text" },
    { key: "detail", label: "詳細資訊", type: "text" },
  ],
};

let currentLogType = "login";
let currentPage = 1;
let commandLogsHasSearched = false; // 指派命令紀錄:進頁面時先不自動查詢,要等使用者手動變更篩選條件才觸發
let allEntries = [];
let sortState = { key: "timestamp", dir: "desc" };
let filterValues = {};

function currentColumns() {
  return LOG_COLUMN_SPECS[currentLogType];
}

function renderTableHeader() {
  const thead = document.getElementById("logs-thead");
  const columns = currentColumns();
  const tr = document.createElement("tr");
  columns.forEach((col) => {
    const th = document.createElement("th");
    th.style.cursor = "pointer";
    th.dataset.key = col.key;
    const arrow = sortState.key === col.key ? (sortState.dir === "asc" ? " ▲" : " ▼") : "";
    th.textContent = col.label + arrow;
    tr.appendChild(th);
  });
  thead.innerHTML = "";
  thead.appendChild(tr);
}

function renderFilters() {
  const container = document.getElementById("log-filters-container");
  container.innerHTML = "";
  currentColumns().forEach((col) => {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex; align-items:center; gap:4px;";
    const label = document.createElement("label");
    label.style.cssText = "font-size:12px; color:#6b7280;";
    label.textContent = col.label + ":";
    wrap.appendChild(label);

    if (col.type === "bool") {
      const select = document.createElement("select");
      select.dataset.filterKey = col.key;
      select.innerHTML = `<option value="">全部</option><option value="true">${col.trueLabel}</option><option value="false">${col.falseLabel}</option>`;
      select.addEventListener("change", () => {
        filterValues[col.key] = select.value;
        renderTableBody();
      });
      wrap.appendChild(select);
    } else {
      const input = document.createElement("input");
      input.type = "text";
      input.dataset.filterKey = col.key;
      input.style.cssText = "font-size:12px; width:140px;";
      input.placeholder = "搜尋...";
      input.addEventListener("input", () => {
        filterValues[col.key] = input.value.trim().toLowerCase();
        renderTableBody();
      });
      wrap.appendChild(input);
    }
    container.appendChild(wrap);
  });
}

function getFilteredSortedEntries() {
  let rows = allEntries.filter((entry) => {
    return currentColumns().every((col) => {
      const filterVal = filterValues[col.key];
      if (!filterVal) return true;
      if (col.type === "bool") {
        return String(!!entry[col.key]) === filterVal;
      }
      const cellVal = String(entry[col.key] || "").toLowerCase();
      return cellVal.includes(filterVal);
    });
  });

  rows.sort((a, b) => {
    const av = a[sortState.key];
    const bv = b[sortState.key];
    let cmp;
    if (typeof av === "boolean" || typeof bv === "boolean") {
      cmp = (av === bv) ? 0 : (av ? 1 : -1);
    } else {
      cmp = String(av || "").localeCompare(String(bv || ""));
    }
    return sortState.dir === "asc" ? cmp : -cmp;
  });

  return rows;
}

function renderTableBody() {
  const tbody = document.getElementById("logs-tbody");
  const rows = getFilteredSortedEntries();
  const columns = currentColumns();

  document.getElementById("log-count-info").textContent = `顯示 ${rows.length} / ${allEntries.length} 筆`;

  tbody.innerHTML = "";
  if (rows.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="${columns.length}">沒有符合條件的紀錄</td>`;
    tbody.appendChild(tr);
    return;
  }

  rows.forEach((entry) => {
    const tr = document.createElement("tr");
    columns.forEach((col) => {
      const td = document.createElement("td");
      if (col.type === "bool") {
        const isTrue = !!entry[col.key];
        td.innerHTML = `<span class="badge ${isTrue ? "ok" : "warn"}">${isTrue ? col.trueLabel : col.falseLabel}</span>`;
      } else {
        td.textContent = entry[col.key] || "";
        td.style.fontSize = "12px";
        if (col.key === "ip" || col.key === "timestamp") td.style.fontFamily = "var(--mono)";
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function renderPagination(page, totalPages, totalCount, onPageChange) {
  ["top", "bottom"].forEach((position) => {
    const container = document.getElementById(`logs-pagination-container-${position}`);
    if (!container) return;

    if (totalPages <= 1) {
      container.innerHTML = totalCount > 0 ? `<span style="color:#9ca3af;">共 ${totalCount} 筆</span>` : "";
      return;
    }

    const prevDisabled = page <= 1 ? "disabled" : "";
    const nextDisabled = page >= totalPages ? "disabled" : "";
    container.innerHTML = `
      <button class="secondary" id="logs-page-prev-btn-${position}" type="button" ${prevDisabled}>上一頁</button>
      <span>第 ${page} / ${totalPages} 頁(共 ${totalCount} 筆,每頁 1000 筆)</span>
      <button class="secondary" id="logs-page-next-btn-${position}" type="button" ${nextDisabled}>下一頁</button>
      <span style="margin-left:8px; display:flex; align-items:center; gap:4px;">
        跳至第
        <input type="number" id="logs-page-jump-input-${position}" min="1" max="${totalPages}" value="${page}" style="width:60px; text-align:center;">
        頁
        <button class="secondary" id="logs-page-jump-btn-${position}" type="button">前往</button>
      </span>
    `;

    const prevBtn = document.getElementById(`logs-page-prev-btn-${position}`);
    const nextBtn = document.getElementById(`logs-page-next-btn-${position}`);
    const jumpInput = document.getElementById(`logs-page-jump-input-${position}`);
    const jumpBtn = document.getElementById(`logs-page-jump-btn-${position}`);

    if (prevBtn) prevBtn.addEventListener("click", () => onPageChange(page - 1));
    if (nextBtn) nextBtn.addEventListener("click", () => onPageChange(page + 1));

    const doJump = () => {
      let target = parseInt(jumpInput.value, 10);
      if (isNaN(target)) return;
      target = Math.max(1, Math.min(totalPages, target)); // 超出範圍的頁碼,自動修正到有效範圍內
      onPageChange(target);
    };
    if (jumpBtn) jumpBtn.addEventListener("click", doJump);
    if (jumpInput) {
      jumpInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doJump();
      });
    }
  });
}

async function loadLogs() {
  if (currentLogType === "commands") {
    if (!commandLogsHasSearched) {
      await initCommandLogsFiltersOnly();
      return;
    }
    await loadCommandLogs();
    return;
  }

  const tbody = document.getElementById("logs-tbody");
  tbody.innerHTML = `<tr><td>載入中...</td></tr>`;

  renderTableHeader();
  renderFilters();
  filterValues = {};

  const res = await apiFetch(`/api/system-logs?type=${currentLogType}&page=${currentPage}`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  allEntries = res.data.entries || [];
  currentPage = res.data.page || 1;
  document.getElementById("log-retention-info").textContent = `保留天數: ${res.data.retention_days} 天`;
  renderTableBody();
  renderPagination(res.data.page, res.data.total_pages, res.data.total_count, (newPage) => {
    currentPage = newPage;
    loadLogs();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const urlParams = new URLSearchParams(window.location.search);
  const typeParam = urlParams.get("type");
  const validTypes = ["login", "activity", "commands"];
  if (validTypes.includes(typeParam)) {
    currentLogType = typeParam;
    document.getElementById("log-type-select").value = typeParam;
  }

  loadLogs();

  document.getElementById("log-type-select").addEventListener("change", (e) => {
    currentLogType = e.target.value;
    sortState = { key: "timestamp", dir: "desc" };
    currentPage = 1;
    commandLogsHasSearched = false;
    loadLogs();
  });

  document.getElementById("refresh-logs-btn").addEventListener("click", loadLogs);

  document.getElementById("logs-thead").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th || !th.dataset.key) return;
    const key = th.dataset.key;
    if (sortState.key === key) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState = { key, dir: "asc" };
    }
    renderTableHeader();
    renderTableBody();
  });
});

// ---------------------------------------------------------------------------
// 指派命令紀錄(彙整所有裝置的回應紀錄)
// ---------------------------------------------------------------------------
let commandFilterValues = { search: "", group: "", request_type: "", status: "" };
let selectedCommandItems = new Map(); // key: "enrollment_id::command_uuid", value: actionType("cancel"/"resend")
let lastCommandRows = []; // 快取最後一次從API抓到的原始資料,排序時不用重新打API,純前端重新排列即可

function renderCommandFilters(filterOptions) {
  const container = document.getElementById("log-filters-container");
  container.innerHTML = "";

  const searchWrap = document.createElement("div");
  searchWrap.style.cssText = "display:flex; align-items:center; gap:4px;";
  searchWrap.innerHTML = `<label style="font-size:12px; color:#6b7280;">裝置名稱/序號:</label>`;
  const searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.style.cssText = "font-size:12px; width:160px;";
  searchInput.placeholder = "搜尋...";
  searchInput.value = commandFilterValues.search;
  searchInput.addEventListener("input", () => {
    commandFilterValues.search = searchInput.value.trim();
    loadCommandLogs();
  });
  searchWrap.appendChild(searchInput);
  container.appendChild(searchWrap);

  const buildSelect = (label, key, options) => {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex; align-items:center; gap:4px;";
    const labelEl = document.createElement("label");
    labelEl.style.cssText = "font-size:12px; color:#6b7280;";
    labelEl.textContent = label + ":";
    wrap.appendChild(labelEl);
    const select = document.createElement("select");
    select.style.fontSize = "12px";
    select.innerHTML = `<option value="">全部</option>` +
      options.map(opt => `<option value="${escapeHtml(opt.value)}" ${commandFilterValues[key] === opt.value ? "selected" : ""}>${escapeHtml(opt.label)}</option>`).join("");
    select.addEventListener("change", () => {
      commandFilterValues[key] = select.value;
      loadCommandLogs();
    });
    wrap.appendChild(select);
    container.appendChild(wrap);
  };

  buildSelect("裝置群組", "group", filterOptions.groups.map(g => ({ value: g, label: g })));
  buildSelect("命令類別", "request_type", filterOptions.request_types.map(t => ({ value: t, label: t })));
  buildSelect("命令狀態", "status", filterOptions.statuses.map(s => ({
    value: s,
    label: s === "__pending__" ? "等待中" : s === "__cancelled__" ? "已取消" : s,
  })));
}

function commandStatusBadge(status, active) {
  if (!status) {
    // status是空的,代表裝置還沒有回應過。但這不代表「還在排隊等待中」——
    // 如果active已經是0(通常是被「取消命令」設定的),代表這筆已經被取消掉了,
    // 只是沒有明確的status可以標記,不能再顯示「等待中」這種容易誤導的標籤
    return active
      ? `<span class="badge warn">等待中</span>`
      : `<span class="badge" style="background:#e5e7eb; color:#6b7280;">已取消</span>`;
  }
  const map = { Acknowledged: "ok", Error: "warn", NotNow: "warn", Idle: "ok", CommandFormatError: "warn" };
  const cls = map[status] || "warn";
  return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
}

// 指派命令紀錄的欄位定義,給排序功能用。勾選框、回應內容(展開按鈕)、操作(按鈕)
// 這三欄不是可排序的單一資料值,不列進來。
const COMMAND_TABLE_COLUMNS = [
  { key: "serial_number", label: "裝置序號", type: "text" },
  { key: "group", label: "裝置群組", type: "text" },
  { key: "device_name", label: "裝置名稱", type: "text" },
  { key: "request_type", label: "命令類別", type: "text" },
  { key: "status", label: "命令狀態", type: "text" },
];
const COMMAND_TABLE_COLUMNS_AFTER_RESULT = [
  { key: "created_at", label: "時間", type: "text" },
  { key: "enrollment_id", label: "裝置佈署 ID", type: "text" },
];
const commandSorter = createTableSorter();

function renderCommandTableHeader() {
  const thead = document.getElementById("logs-thead");
  const beforeResultCells = COMMAND_TABLE_COLUMNS
    .map((col) => `<th style="cursor:pointer;" data-sort-key="${col.key}">${escapeHtml(col.label)}${commandSorter.sortArrow(col.key)}</th>`)
    .join("");
  const afterResultCells = COMMAND_TABLE_COLUMNS_AFTER_RESULT
    .map((col) => `<th style="cursor:pointer;" data-sort-key="${col.key}">${escapeHtml(col.label)}${commandSorter.sortArrow(col.key)}</th>`)
    .join("");
  thead.innerHTML = `
    <tr>
      <th style="width:32px;"><input type="checkbox" id="command-select-all-checkbox"></th>
      ${beforeResultCells}
      <th>回應內容</th>
      ${afterResultCells}
      <th>操作</th>
    </tr>
  `;
}

function renderCommandTableBody(rows) {
  const tbody = document.getElementById("logs-tbody");
  document.getElementById("log-count-info").textContent = `顯示 ${rows.length} 筆(最多顯示最近1000筆)`;

  selectedCommandItems.clear();
  updateBatchActionsUI();

  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10">沒有符合條件的紀錄</td></tr>`;
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");

    let requestTypeHtml = escapeHtml(row.request_type || "");
    if (row.app_info) {
      if (row.app_info.name && row.app_info.bundle_id) {
        requestTypeHtml += `<br><span style="color:#6b7280; font-size:12px;">${escapeHtml(row.app_info.name)} (${escapeHtml(row.app_info.bundle_id)})</span>`;
      } else if (row.app_info.bundle_id) {
        requestTypeHtml += `<br><span style="color:#9ca3af; font-size:12px;">${escapeHtml(row.app_info.bundle_id)}</span>`;
      } else if (row.app_info.adam_id) {
        requestTypeHtml += `<br><span style="color:#9ca3af; font-size:12px;">adamId ${escapeHtml(String(row.app_info.adam_id))}</span>`;
      }
    }

    const resultPreview = row.raw_result
      ? `<button class="secondary command-expand-btn" type="button" style="font-size:12px;" data-content="${encodeURIComponent(row.raw_result)}">展開查看</button>`
      : `<span style="color:#9ca3af; font-size:12px;">(無內容)</span>`;

    // actionType決定這一列可以做什麼批次操作:pending狀態才能批次取消,Error狀態才能批次重新派送,
    // 其他狀態(已經正常完成等)不適用任何批次操作,不顯示勾選框
    let actionType = "none";
    let actionHtml = "";
    if (row.status === null && row.active) {
      actionType = "cancel";
      actionHtml = `<button class="secondary command-cancel-btn" type="button" style="font-size:12px;" data-eid="${escapeHtml(row.enrollment_id)}" data-uuid="${escapeHtml(row.command_uuid)}">取消命令</button>`;
    } else if (row.status === "Error" || row.status === "CommandFormatError") {
      actionType = "resend";
      actionHtml = `<button class="secondary command-resend-btn" type="button" style="font-size:12px;" data-eid="${escapeHtml(row.enrollment_id)}" data-uuid="${escapeHtml(row.command_uuid)}">重新派送</button>`;
    }

    const checkboxHtml = actionType !== "none"
      ? `<input type="checkbox" class="command-row-checkbox" data-eid="${escapeHtml(row.enrollment_id)}" data-uuid="${escapeHtml(row.command_uuid)}" data-action-type="${actionType}">`
      : "";

    tr.innerHTML = `
      <td>${checkboxHtml}</td>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(row.serial_number || "")}</td>
      <td style="font-size:12px;">${escapeHtml(row.group || "")}</td>
      <td style="font-size:12px;">${escapeHtml(row.device_name || "")}</td>
      <td style="font-size:12px;">${requestTypeHtml}</td>
      <td>${commandStatusBadge(row.status, row.active)}</td>
      <td>${resultPreview}</td>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(row.result_updated_at || row.created_at || "")}</td>
      <td style="font-family:var(--mono); font-size:11px; color:#9ca3af;">${escapeHtml(row.enrollment_id || "")}</td>
      <td>${actionHtml}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function initCommandLogsFiltersOnly() {
  const tbody = document.getElementById("logs-tbody");
  document.getElementById("log-retention-info").textContent = "";
  document.getElementById("logs-pagination-container-top").innerHTML = "";
  document.getElementById("logs-pagination-container-bottom").innerHTML = "";

  renderCommandTableHeader();

  const res = await apiFetch("/api/system-logs/commands/filter-options");
  if (res.ok) {
    renderCommandFilters(res.data.filter_options);
  }

  tbody.innerHTML = `
    <tr>
      <td colspan="10" style="text-align:center; padding:28px 16px; color:#6b7280; font-size:13px;">
        資料量較大(超過 18 萬筆),請先選擇上方的篩選條件(例如裝置群組、命令類別、狀態,或輸入裝置名稱/序號搜尋)才會開始查詢。<br>
        <span style="color:#b42318; font-weight:600;">⚠️ 選擇「全部群組」進行查詢時,因資料量較大,仍需要一段時間才能完成,請耐心等候。</span>
      </td>
    </tr>
  `;
}

async function loadCommandLogs() {
  commandLogsHasSearched = true; // 只要真的執行過一次查詢(不管是哪個篩選觸發的),之後分頁/排序都能正常繼續使用loadCommandLogs
  const tbody = document.getElementById("logs-tbody");
  tbody.innerHTML = `<tr><td colspan="10">載入中...</td></tr>`;
  document.getElementById("log-retention-info").textContent = "";

  const params = new URLSearchParams();
  if (commandFilterValues.search) params.set("search", commandFilterValues.search);
  if (commandFilterValues.group) params.set("group", commandFilterValues.group);
  if (commandFilterValues.request_type) params.set("request_type", commandFilterValues.request_type);
  if (commandFilterValues.status) params.set("status", commandFilterValues.status);
  params.set("page", currentPage);

  const res = await apiFetch(`/api/system-logs/commands?${params.toString()}`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="10" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  renderCommandTableHeader();
  renderCommandFilters(res.data.filter_options);
  lastCommandRows = res.data.rows;
  currentPage = res.data.page || 1;
  resortAndRenderCommandRows();
  renderPagination(res.data.page, res.data.total_pages, res.data.total_count, (newPage) => {
    currentPage = newPage;
    loadCommandLogs();
  });
}

function resortAndRenderCommandRows() {
  const sortedRows = commandSorter.sortRows(
    lastCommandRows,
    [...COMMAND_TABLE_COLUMNS, ...COMMAND_TABLE_COLUMNS_AFTER_RESULT],
  );
  renderCommandTableBody(sortedRows);
}

async function cancelCommand(enrollmentId, commandUuid) {
  if (!confirm("確定要取消這筆尚未完成的指令嗎?裝置之後就不會再收到這筆指令了。")) return;
  const res = await apiFetchJSON("/api/system-logs/commands/cancel", "POST", {
    enrollment_id: enrollmentId, command_uuid: commandUuid,
  });
  if (res.ok) {
    alert(res.data.message);
    loadCommandLogs();
  } else {
    alert("取消失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function resendCommand(enrollmentId, commandUuid) {
  if (!confirm("確定要重新派送這筆指令嗎?會用跟原本完全相同的內容,再送一次給這台裝置。")) return;
  const res = await apiFetchJSON("/api/system-logs/commands/resend", "POST", {
    enrollment_id: enrollmentId, command_uuid: commandUuid,
  });
  if (res.ok) {
    alert(res.data.message);
    loadCommandLogs();
  } else {
    alert("重新派送失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function updateBatchActionsUI() {
  const container = document.getElementById("batch-actions-container");
  const cancelCount = [...selectedCommandItems.values()].filter(t => t === "cancel").length;
  const resendCount = [...selectedCommandItems.values()].filter(t => t === "resend").length;

  const parts = [];
  if (cancelCount > 0) {
    parts.push(`<button id="batch-cancel-btn" class="danger" type="button" style="font-size:12px;">批次取消所選(${cancelCount})</button>`);
  }
  if (resendCount > 0) {
    parts.push(`<button id="batch-resend-btn" type="button" style="font-size:12px;">批次重新派送所選(${resendCount})</button>`);
  }
  container.innerHTML = parts.join(" ");

  const cancelBtn = document.getElementById("batch-cancel-btn");
  if (cancelBtn) cancelBtn.addEventListener("click", batchCancelSelected);
  const resendBtn = document.getElementById("batch-resend-btn");
  if (resendBtn) resendBtn.addEventListener("click", batchResendSelected);
}

function toggleCommandCheckbox(checkbox) {
  const key = `${checkbox.dataset.eid}::${checkbox.dataset.uuid}`;
  if (checkbox.checked) {
    selectedCommandItems.set(key, checkbox.dataset.actionType);
  } else {
    selectedCommandItems.delete(key);
  }
  updateBatchActionsUI();
}

function toggleSelectAllCommandCheckboxes(checked) {
  document.querySelectorAll(".command-row-checkbox").forEach((cb) => {
    cb.checked = checked;
    toggleCommandCheckbox(cb);
  });
}

async function runBatchOperation(actionType, apiPath, confirmMessage, titleWord) {
  const items = [...selectedCommandItems.entries()].filter(([, type]) => type === actionType);
  if (items.length === 0) return;
  if (!confirm(confirmMessage.replace("{n}", items.length))) return;

  document.getElementById("batch-progress-title").textContent = `批次${titleWord}中...`;
  document.getElementById("batch-progress-bar").style.width = "0%";
  document.getElementById("batch-progress-text").textContent = `準備處理 ${items.length} 筆...`;
  document.getElementById("batch-progress-errors").innerHTML = "";
  document.getElementById("batch-progress-close-btn").style.display = "none";
  openModal("batch-progress-modal");

  let successCount = 0;
  const errors = [];

  for (let i = 0; i < items.length; i++) {
    const [key] = items[i];
    const [enrollmentId, commandUuid] = key.split("::");

    const res = await apiFetchJSON(apiPath, "POST", {
      enrollment_id: enrollmentId, command_uuid: commandUuid,
    });

    if (res.ok) {
      successCount++;
    } else {
      errors.push(`${commandUuid}: ${(res.data && res.data.message) || "未知錯誤"}`);
    }

    const progressPct = Math.round(((i + 1) / items.length) * 100);
    document.getElementById("batch-progress-bar").style.width = `${progressPct}%`;
    document.getElementById("batch-progress-text").textContent = `已處理 ${i + 1}/${items.length}(成功 ${successCount},失敗 ${errors.length})`;
  }

  document.getElementById("batch-progress-title").textContent = `批次${titleWord}完成`;
  if (errors.length > 0) {
    document.getElementById("batch-progress-errors").innerHTML =
      `<strong>失敗項目:</strong><br>` + errors.map(e => escapeHtml(e)).join("<br>");
  }
  document.getElementById("batch-progress-close-btn").style.display = "";

  selectedCommandItems.clear();
  loadCommandLogs();
}

async function batchCancelSelected() {
  await runBatchOperation(
    "cancel", "/api/system-logs/commands/cancel",
    "確定要批次取消這 {n} 筆尚未完成的指令嗎?裝置之後就不會再收到這些指令了。", "取消",
  );
}

async function batchResendSelected() {
  await runBatchOperation(
    "resend", "/api/system-logs/commands/resend",
    "確定要批次重新派送這 {n} 筆指令嗎?會用跟原本完全相同的內容,分別再送一次給對應的裝置。", "重新派送",
  );
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("logs-tbody").addEventListener("click", (e) => {
    if (e.target.classList.contains("command-expand-btn")) {
      const content = decodeURIComponent(e.target.dataset.content);
      document.getElementById("command-result-modal-content").textContent = content;
      openModal("command-result-modal");
    } else if (e.target.classList.contains("command-cancel-btn")) {
      cancelCommand(e.target.dataset.eid, e.target.dataset.uuid);
    } else if (e.target.classList.contains("command-resend-btn")) {
      resendCommand(e.target.dataset.eid, e.target.dataset.uuid);
    }
  });

  document.getElementById("logs-tbody").addEventListener("change", (e) => {
    if (e.target.classList.contains("command-row-checkbox")) {
      toggleCommandCheckbox(e.target);
    }
  });

  document.getElementById("logs-thead").addEventListener("change", (e) => {
    if (e.target.id === "command-select-all-checkbox") {
      toggleSelectAllCommandCheckboxes(e.target.checked);
    }
  });

  document.getElementById("logs-thead").addEventListener("click", (e) => {
    if (currentLogType !== "commands") return;
    const th = e.target.closest("th");
    if (!th || !th.dataset.sortKey) return;
    commandSorter.handleHeaderClick(th.dataset.sortKey);
    renderCommandTableHeader();
    resortAndRenderCommandRows();
  });

  document.getElementById("batch-progress-close-btn").addEventListener("click", () => {
    closeModal("batch-progress-modal");
  });
});
