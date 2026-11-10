// ---- 共用欄位渲染 (供 profiles.js / dep_profiles.js 等 schema 驅動的表單頁面共用) ----
function renderFieldHelp(field) {
  return field.help
    ? `<p style="color:#6b7280; font-size:11px; margin:2px 0 6px 0; line-height:1.5;">${escapeHtml(field.help)}</p>`
    : "";
}

function renderField(field, value) {
  const val = value !== undefined && value !== null ? value : field.default;
  if (field.type === "checkbox") {
    return `
      <label style="display:flex; align-items:center; gap:8px; font-size:13px; padding:4px 0;">
        <input type="checkbox" data-field="${escapeHtml(field.name)}" ${val ? "checked" : ""}>
        ${escapeHtml(field.label)}
      </label>
      ${renderFieldHelp(field)}
    `;
  }
  if (field.type === "app_checklist") {
    const selected = new Set(Array.isArray(val) ? val : []);
    const optionsHtml = field.options.map((opt) => `
      <label style="display:flex; align-items:center; gap:8px; font-size:13px; padding:4px 0;">
        <input type="checkbox" data-field="${escapeHtml(field.name)}" data-value="${escapeHtml(opt.bundle_id)}" ${selected.has(opt.bundle_id) ? "checked" : ""}>
        ${escapeHtml(opt.label)}
      </label>
    `).join("");
    return `
      <div style="margin:6px 0;">
        <p style="font-size:13px; font-weight:600; margin-bottom:4px;">${escapeHtml(field.label)}</p>
        ${renderFieldHelp(field)}
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:2px 10px; margin-top:4px;">
          ${optionsHtml}
        </div>
      </div>
    `;
  }
  if (field.type === "homescreen_layout") {
    return `
      <div style="display:flex; align-items:center; gap:10px; margin:6px 0;">
        <input type="hidden" data-field="${escapeHtml(field.name)}" value="${encodeURIComponent(JSON.stringify(val))}">
        <button type="button" class="secondary homescreen-edit-btn">${escapeHtml(field.label)}</button>
        <span class="homescreen-summary" style="font-size:12px; color:#6b7280;">${escapeHtml(homeScreenLayoutSummary(val))}</span>
      </div>
    `;
  }
  if (field.type === "image_upload") {
    const hasExisting = !!val;
    const previewSrc = hasExisting ? `data:image/png;base64,${val}` : "";
    return `
      <div style="margin:6px 0;">
        <p style="font-size:13px; font-weight:600; margin-bottom:4px;">${escapeHtml(field.label)}</p>
        ${renderFieldHelp(field)}
        <input type="hidden" data-field="${escapeHtml(field.name)}" value="${escapeHtml(val || "")}">
        <div style="display:flex; align-items:center; gap:10px; margin-top:6px;">
          <div class="image-upload-preview" style="width:48px; height:48px; border:1px solid var(--border-color); border-radius:8px; overflow:hidden; display:flex; align-items:center; justify-content:center; background:#f9fafb; flex-shrink:0;">
            ${previewSrc ? `<img src="${previewSrc}" style="width:100%; height:100%; object-fit:cover;">` : `<span style="font-size:11px; color:#9ca3af;">無圖示</span>`}
          </div>
          <input type="file" accept="image/png,image/jpeg" class="image-upload-picker" data-target-field="${escapeHtml(field.name)}" style="font-size:12px;">
          ${hasExisting ? `<button type="button" class="secondary image-upload-clear-btn" data-target-field="${escapeHtml(field.name)}" style="font-size:12px;">清除圖示</button>` : ""}
        </div>
      </div>
    `;
  }
  if (field.type === "select") {
    const options = field.options.map((opt) =>
      `<option value="${escapeHtml(opt)}" ${opt === val ? "selected" : ""}>${escapeHtml(opt)}</option>`
    ).join("");
    return `
      <div class="field-row">
        <label>${escapeHtml(field.label)}</label>
        <select data-field="${escapeHtml(field.name)}" style="width:100%;">${options}</select>
      </div>
      ${renderFieldHelp(field)}
    `;
  }
  // text
  return `
    <div class="field-row">
      <label>${escapeHtml(field.label)}</label>
      <input type="text" data-field="${escapeHtml(field.name)}" value="${escapeHtml(val || "")}">
    </div>
    ${renderFieldHelp(field)}
  `;
}

function homeScreenLayoutSummary(layout) {
  const pages = (layout && layout.Pages) || [];
  const dock = (layout && layout.Dock) || [];
  if (dock.length === 0 && pages.length === 0) return "尚未設定";
  const folders = pages.flat().filter((item) => item.Type === "Folder").length;
  return `Dock ${dock.length} 個・${pages.length} 頁・${folders} 個資料夾`;
}

function readFieldValue(container, field) {
  if (field.type === "homescreen_layout") {
    const input = container.querySelector(`[data-field="${field.name}"]`);
    return input ? JSON.parse(decodeURIComponent(input.value)) : field.default;
  }
  if (field.type === "app_checklist") {
    const checkboxes = container.querySelectorAll(`[data-field="${field.name}"]`);
    return [...checkboxes].filter((cb) => cb.checked).map((cb) => cb.dataset.value);
  }
  const el = container.querySelector(`[data-field="${field.name}"]`);
  if (!el) return field.default;
  if (field.type === "checkbox") return el.checked;
  return el.value;
}

// ---- Debug 面板 ----
// Debug輸出預設關閉;改用新的key,舊版存過「開啟」的瀏覽器也會回到關閉
const DEBUG_VISIBLE_KEY = "nanomdm_webui_debug_panel_open";

function initDebugPanel() {
  const bar = document.getElementById("debugbar");
  const toggleBtn = document.getElementById("debug-toggle-btn");
  const stateEl = document.getElementById("debug-toggle-state");
  const clearBtn = document.getElementById("clear-debug-btn");
  if (!bar || !toggleBtn) return;

  function setVisible(visible) {
    bar.classList.toggle("hidden", !visible);
    toggleBtn.classList.toggle("on", visible);
    toggleBtn.setAttribute("aria-pressed", visible ? "true" : "false");
    stateEl.textContent = visible ? "開啟" : "關閉";
  }

  let saved = null;
  try { saved = localStorage.getItem(DEBUG_VISIBLE_KEY); } catch (e) { /* 無法使用localStorage時維持預設關閉 */ }
  setVisible(saved === "1");

  toggleBtn.addEventListener("click", () => {
    const visible = bar.classList.contains("hidden");
    setVisible(visible);
    try { localStorage.setItem(DEBUG_VISIBLE_KEY, visible ? "1" : "0"); } catch (e) { /* 忽略 */ }
  });

  clearBtn.addEventListener("click", () => {
    document.getElementById("debugbar-body").innerHTML = "";
  });
}

function debugLog(label, data, isError) {
  const body = document.getElementById("debugbar-body");
  if (!body) return;
  const entry = document.createElement("div");
  entry.className = "log-entry" + (isError ? " err" : "");
  const ts = new Date().toLocaleTimeString("zh-TW", { hour12: false });
  let dataStr;
  try {
    dataStr = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  } catch (e) {
    dataStr = String(data);
  }
  entry.innerHTML = `<span class="ts">[${ts}]</span><strong>${escapeHtml(label)}</strong>\n${escapeHtml(dataStr)}`;
  body.appendChild(entry);
  body.scrollTop = body.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

const DEVICE_OFFLINE_DAYS = 3;

function deviceOfflineDays(lastSeenAt, now = Date.now()) {
  const seen = Date.parse(`${(lastSeenAt || "").replace(" ", "T")}+08:00`);
  if (Number.isNaN(seen)) return 0;
  const days = Math.floor((now - seen) / 86400000);
  return days >= DEVICE_OFFLINE_DAYS ? days : 0;
}

function lastSeenCellHtml(lastSeenAt) {
  const text = escapeHtml(lastSeenAt || "");
  const offlineDays = deviceOfflineDays(lastSeenAt);
  if (!offlineDays) return text;
  return `<span style="color:#d64545; font-weight:600;">${text}<br>設備已離線 ${offlineDays} 天</span>`;
}

// ---- 路徑前綴輔助函式 (部署在 nginx 子路徑,例如 /miniweb,後面時需要) ----
function apiUrl(path) {
  const root = window.APP_ROOT || "";
  if (!path.startsWith("/")) return path; // 已經是相對路徑或完整網址,不用處理
  return root + path;
}

// ---- fetch 包裝,自動記錄到 debug 面板,並自動套用路徑前綴 ----
async function apiFetch(url, options) {
  const fullUrl = apiUrl(url);
  options = options || {};
  debugLog(`REQUEST ${options.method || "GET"} ${url}`, options.body || "");
  try {
    const resp = await fetch(fullUrl, options);
    let data;
    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      data = await resp.json();
    } else {
      data = await resp.text();
    }
    debugLog(`RESPONSE ${resp.status} ${url}`, data, !resp.ok);
    return { ok: resp.ok, status: resp.status, data };
  } catch (e) {
    debugLog(`ERROR ${url}`, e.message, true);
    return { ok: false, status: 0, data: { message: e.message } };
  }
}

async function apiFetchJSON(url, method, bodyObj) {
  return apiFetch(url, {
    method: method || "GET",
    headers: { "Content-Type": "application/json" },
    body: bodyObj ? JSON.stringify(bodyObj) : undefined,
  });
}

function depAccountDetailRows(fields) {
  const rows = [
    ["單位", fields.org_name],
    ["伺服器名稱", fields.server_name],
  ];
  if (fields.admin_id && fields.facilitator_id && fields.admin_id === fields.facilitator_id) {
    rows.push(["帳號", fields.admin_id]);
  } else {
    rows.push(["管理者", fields.admin_id], ["Facilitator", fields.facilitator_id]);
  }
  rows.push(
    ["組織代碼", fields.org_id],
    ["伺服器 UUID", fields.server_uuid],
    ["Email", fields.org_email],
    ["電話", fields.org_phone],
    ["地址", fields.org_address],
  );
  return rows.filter(([, value]) => value);
}

// ---- 頂部[ASM連線狀態]按鈕與詳細資訊小視窗 ----
async function loadDepAccountDetail() {
  const orgEl = document.getElementById("asm-status-org");
  const detailEl = document.getElementById("asm-status-detail");
  const lightEl = document.getElementById("dep-status-light");
  if (!orgEl) return;

  orgEl.textContent = "載入中...";
  detailEl.textContent = "載入中...";
  lightEl.className = "status-light";

  const res = await apiFetch("/api/dep-account-detail");
  const success = res.ok && res.data && res.data.returncode === 0 && res.data.fields;

  lightEl.className = "status-light " + (success ? "ok" : "error");

  if (success) {
    const fields = res.data.fields;
    orgEl.textContent = fields.org_name || fields.server_name || "(無資料)";
    const rows = depAccountDetailRows(fields).map(([label, value]) =>
      `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(value))}</dd>`).join("");
    const checkedAt = res.data.checked_at
      ? `<dt>最後檢查</dt><dd>${escapeHtml(res.data.checked_at)}</dd>` : "";
    detailEl.innerHTML = `<div class="asm-popover-status ok">連線正常</div><dl class="asm-popover-list">${rows}${checkedAt}</dl>`;
  } else {
    const out = (res.data && (res.data.stdout || res.data.stderr || "").trim()) || "";
    orgEl.textContent = "連線失敗";
    detailEl.innerHTML = `<div class="asm-popover-status error">無法取得 ASM 帳號資訊</div>`
      + (out ? `<pre class="asm-popover-error">${escapeHtml(out)}</pre>` : "");
  }
}

function initAsmStatusPopover() {
  const btn = document.getElementById("asm-status-btn");
  const pop = document.getElementById("asm-status-popover");
  if (!btn || !pop) return;

  function setOpen(open) {
    pop.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(pop.classList.contains("hidden"));
  });
  document.addEventListener("click", (e) => {
    if (!pop.classList.contains("hidden") && !pop.contains(e.target)) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.classList.contains("hidden")) {
      setOpen(false);
      btn.focus();
    }
  });
}

// ---- Modal 共用 ----
function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

// ---------------------------------------------------------------------------
// 共用表格排序工具,給[裝置與命令]、[裝置註冊狀態]、[系統紀錄]這幾個頁面的資料表格共用。
// ---------------------------------------------------------------------------
/**
 * 建立一個表格排序控制器。
 * initialKey/initialDir: 預設排序欄位與方向(選填,不給的話預設不排序)
 * 回傳: { state, sortRows(rows, columns), sortArrow(key), handleHeaderClick(key) }
 *
 * columns裡每個欄位可以指定type: "text"(預設,字串排序,支援中文)、
 * "number"(數值排序,空值視為最小)、"bool"(布林值排序,false排在true前面)
 */
function createTableSorter(initialKey, initialDir) {
  const state = { key: initialKey || null, dir: initialDir || "asc" };

  function sortRows(rows, columns) {
    if (!state.key) return rows;
    const colSpec = (columns || []).find((c) => c.key === state.key) || {};
    const type = colSpec.type || "text";
    const sorted = [...rows].sort((a, b) => {
      let av = a[state.key];
      let bv = b[state.key];
      let cmp;
      if (type === "number") {
        const an = (av === null || av === undefined || av === "") ? -Infinity : Number(av);
        const bn = (bv === null || bv === undefined || bv === "") ? -Infinity : Number(bv);
        cmp = an - bn;
      } else if (type === "bool") {
        const ab = !!av, bb = !!bv;
        cmp = (ab === bb) ? 0 : (ab ? 1 : -1);
      } else {
        cmp = String(av ?? "").localeCompare(String(bv ?? ""), "zh-Hant");
      }
      return state.dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }

  function sortArrow(key) {
    if (state.key !== key) return "";
    return state.dir === "asc" ? " ▲" : " ▼";
  }

  function handleHeaderClick(key) {
    if (state.key === key) {
      state.dir = state.dir === "asc" ? "desc" : "asc";
    } else {
      state.key = key;
      state.dir = "asc";
    }
  }

  return { state, sortRows, sortArrow, handleHeaderClick };
}

document.addEventListener("DOMContentLoaded", () => {
  initDebugPanel();
  initAsmStatusPopover();
  loadDepAccountDetail();

  document.addEventListener("change", (e) => {
    if (!e.target.classList.contains("image-upload-picker")) return;
    const file = e.target.files[0];
    if (!file) return;

    const fieldName = e.target.dataset.targetField;
    const wrapper = e.target.closest("div").parentElement; // 外層的field容器,包含隱藏欄位跟預覽區
    const hiddenInput = wrapper.querySelector(`input[type="hidden"][data-field="${fieldName}"]`);
    const previewContainer = wrapper.querySelector(".image-upload-preview");

    const reader = new FileReader();
    reader.onload = () => {
      // readAsDataURL的結果格式是"data:image/png;base64,XXXX",只需要取base64部分,
      // 逗號後面才是真正的內容
      const base64Content = reader.result.split(",")[1];
      hiddenInput.value = base64Content;
      previewContainer.innerHTML = `<img src="${reader.result}" style="width:100%; height:100%; object-fit:cover;">`;
    };
    reader.readAsDataURL(file);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.classList.contains("image-upload-clear-btn")) return;
    const fieldName = e.target.dataset.targetField;
    const wrapper = e.target.closest("div").parentElement;
    const hiddenInput = wrapper.querySelector(`input[type="hidden"][data-field="${fieldName}"]`);
    const previewContainer = wrapper.querySelector(".image-upload-preview");
    hiddenInput.value = "";
    previewContainer.innerHTML = `<span style="font-size:11px; color:#9ca3af;">無圖示</span>`;
  });
});
