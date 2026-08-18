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

function readFieldValue(container, field) {
  const el = container.querySelector(`[data-field="${field.name}"]`);
  if (!el) return field.default;
  if (field.type === "checkbox") return el.checked;
  return el.value;
}

// ---- Debug 面板 ----
const DEBUG_VISIBLE_KEY = "nanomdm_webui_debug_visible";

function initDebugPanel() {
  const body = document.getElementById("debugbar-body");
  const checkbox = document.getElementById("debug-toggle");
  const clearBtn = document.getElementById("clear-debug-btn");

  const saved = localStorage.getItem(DEBUG_VISIBLE_KEY);
  const visible = saved === null ? true : saved === "1";
  checkbox.checked = visible;
  body.classList.toggle("hidden", !visible);

  checkbox.addEventListener("change", () => {
    body.classList.toggle("hidden", !checkbox.checked);
    localStorage.setItem(DEBUG_VISIBLE_KEY, checkbox.checked ? "1" : "0");
  });

  clearBtn.addEventListener("click", () => {
    body.innerHTML = "";
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

function formatDepAccountInfo(fields) {
  if (!fields) return null;
  const line1Parts = [];
  const line2Parts = [];

  if (fields.org_name) line1Parts.push(fields.org_name);
  if (fields.server_name) line1Parts.push(`Server: ${fields.server_name}`);

  if (fields.admin_id && fields.facilitator_id && fields.admin_id === fields.facilitator_id) {
    line1Parts.push(`帳號: ${fields.admin_id}`);
  } else {
    if (fields.admin_id) line1Parts.push(`管理者: ${fields.admin_id}`);
    if (fields.facilitator_id) line1Parts.push(`Facilitator: ${fields.facilitator_id}`);
  }

  if (fields.org_id) line2Parts.push(`組織代碼: ${fields.org_id}`);
  if (fields.server_uuid) line2Parts.push(`UUID: ${fields.server_uuid}`);

  return {
    line1: line1Parts.join("  ・  "),
    line2: line2Parts.join("  ・  "),
  };
}

// ---- 頂部 dep-account-detail ----
async function loadDepAccountDetail() {
  const line1El = document.getElementById("dep-info-line1-text");
  const line2El = document.getElementById("dep-info-line2");
  const lightEl = document.getElementById("dep-status-light");
  if (!line1El) return;

  line1El.textContent = "載入中...";
  line2El.textContent = "";
  lightEl.className = "status-light";

  const res = await apiFetch("/api/dep-account-detail");
  const success = res.ok && res.data && res.data.returncode === 0 && res.data.fields;

  lightEl.className = "status-light " + (success ? "ok" : "error");

  if (success) {
    const formatted = formatDepAccountInfo(res.data.fields);
    line1El.textContent = formatted.line1 || "(無資料)";
    line2El.textContent = formatted.line2 || "";
  } else {
    const out = (res.data && (res.data.stdout || res.data.stderr || "").trim()) || "";
    line1El.textContent = out || "無法取得 ASM 帳號資訊";
    line2El.textContent = "";
  }
}

// ---- Modal 共用 ----
function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

document.addEventListener("DOMContentLoaded", () => {
  initDebugPanel();
  loadDepAccountDetail();
});
