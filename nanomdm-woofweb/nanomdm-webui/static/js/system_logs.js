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
