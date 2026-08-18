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

async function loadLogs() {
  if (currentLogType === "commands") {
    await loadCommandLogs();
    return;
  }

  const tbody = document.getElementById("logs-tbody");
  tbody.innerHTML = `<tr><td>載入中...</td></tr>`;

  renderTableHeader();
  renderFilters();
  filterValues = {};

  const res = await apiFetch(`/api/system-logs?type=${currentLogType}`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  allEntries = res.data.entries || [];
  document.getElementById("log-retention-info").textContent = `保留天數: ${res.data.retention_days} 天`;
  renderTableBody();
}

document.addEventListener("DOMContentLoaded", () => {
  loadLogs();

  document.getElementById("log-type-select").addEventListener("change", (e) => {
    currentLogType = e.target.value;
    sortState = { key: "timestamp", dir: "desc" };
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
    value: s, label: s === "__pending__" ? "等待中" : s,
  })));
}

function commandStatusBadge(status) {
  if (!status) return `<span class="badge warn">等待中</span>`;
  const map = { Acknowledged: "ok", Error: "warn", NotNow: "warn", Idle: "ok", CommandFormatError: "warn" };
  const cls = map[status] || "warn";
  return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
}

function renderCommandTableHeader() {
  const thead = document.getElementById("logs-thead");
  thead.innerHTML = `
    <tr>
      <th>裝置序號</th>
      <th>裝置群組</th>
      <th>裝置名稱</th>
      <th>命令類別</th>
      <th>命令狀態</th>
      <th>回應內容</th>
      <th>時間</th>
      <th>操作</th>
    </tr>
  `;
}

function renderCommandTableBody(rows) {
  const tbody = document.getElementById("logs-tbody");
  document.getElementById("log-count-info").textContent = `顯示 ${rows.length} 筆(最多顯示最近500筆)`;

  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8">沒有符合條件的紀錄</td></tr>`;
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

    let actionHtml = "";
    if (row.status === null && row.active) {
      // 尚未完成(仍在排隊等待中):提供取消按鈕
      actionHtml = `<button class="secondary command-cancel-btn" type="button" style="font-size:12px;" data-eid="${escapeHtml(row.enrollment_id)}" data-uuid="${escapeHtml(row.command_uuid)}">取消命令</button>`;
    } else if (row.status === "Error" || row.status === "CommandFormatError") {
      // 發生錯誤:提供重新派送按鈕
      actionHtml = `<button class="secondary command-resend-btn" type="button" style="font-size:12px;" data-eid="${escapeHtml(row.enrollment_id)}" data-uuid="${escapeHtml(row.command_uuid)}">重新派送</button>`;
    }

    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(row.serial_number || "")}</td>
      <td style="font-size:12px;">${escapeHtml(row.group || "")}</td>
      <td style="font-size:12px;">${escapeHtml(row.device_name || "")}</td>
      <td style="font-size:12px;">${requestTypeHtml}</td>
      <td>${commandStatusBadge(row.status)}</td>
      <td>${resultPreview}</td>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(row.result_updated_at || row.created_at || "")}</td>
      <td>${actionHtml}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadCommandLogs() {
  const tbody = document.getElementById("logs-tbody");
  tbody.innerHTML = `<tr><td colspan="8">載入中...</td></tr>`;
  document.getElementById("log-retention-info").textContent = "";

  const params = new URLSearchParams();
  if (commandFilterValues.search) params.set("search", commandFilterValues.search);
  if (commandFilterValues.group) params.set("group", commandFilterValues.group);
  if (commandFilterValues.request_type) params.set("request_type", commandFilterValues.request_type);
  if (commandFilterValues.status) params.set("status", commandFilterValues.status);

  const res = await apiFetch(`/api/system-logs/commands?${params.toString()}`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="8" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  renderCommandTableHeader();
  renderCommandFilters(res.data.filter_options);
  renderCommandTableBody(res.data.rows);
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
});
