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
// 名稱看起來是密碼、金鑰、權杖的 .env 變數,值預設遮蔽(例如 MYSQL_ROOT_PASSWORD、NANOMDM_API_KEY、SCEP_CHALLENGE)
const SENSITIVE_ENV_KEY_RE = /(PASSWORD|PASSPHRASE|SECRET|TOKEN|API_KEY|CHALLENGE)$/i;

function renderEnvFieldRow(field) {
  const tr = document.createElement("tr");
  tr.dataset.key = field.key;
  const sensitive = SENSITIVE_ENV_KEY_RE.test(field.key);
  const valueCell = sensitive
    ? `<div style="display:flex; gap:6px;">
         <input type="password" class="env-field-value" value="${escapeHtml(field.value)}" autocomplete="off" style="flex:1; font-family:var(--mono);">
         <button class="secondary env-field-toggle-btn" type="button" style="font-size:12px; white-space:nowrap;">顯示</button>
       </div>`
    : `<input type="text" class="env-field-value" value="${escapeHtml(field.value)}" style="width:100%; font-family:var(--mono);">`;
  tr.innerHTML = `
    <td style="font-family:var(--mono); font-size:13px; word-break:break-all;">${escapeHtml(field.key)}</td>
    <td>${valueCell}</td>
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
// ---------------------------------------------------------------------------
// MySQL 設定(buffer pool)
// ---------------------------------------------------------------------------
let mysqlTuningStatus = null;

function mysqlTuningRow(label, value) {
  return `<tr><td class="k">${escapeHtml(label)}</td><td>${value}</td></tr>`;
}

async function loadMysqlTuningStatus() {
  const box = document.getElementById("mysql-tuning-status");
  const res = await apiFetch("/api/mysql-tuning/status");
  if (!res.ok) {
    box.innerHTML = `<span style="color:#d64545;">${escapeHtml((res.data && res.data.message) || "載入失敗")}</span>`;
    return null;
  }
  const st = res.data.status;
  mysqlTuningStatus = st;

  let sourceText = escapeHtml(st.source_label);
  if (st.persisted_mb === null && (st.source === "PERSISTED" || st.source === "DYNAMIC") && st.size_mb !== st.default_mb) {
    sourceText += `<br><span style="color:#b45309;">下次重新啟動 MySQL 後會回到 ${st.default_mb} MB</span>`;
  } else if (st.persisted_mb !== null && st.persisted_mb !== st.size_mb) {
    sourceText += `<br><span style="color:#b45309;">已設定為 ${st.persisted_mb} MB,調整完成或重新啟動 MySQL 後生效</span>`;
  }
  const pct = (v) => (v === null || v === undefined ? "-" : `${v}%`);
  box.innerHTML = `
    <table class="kv-table">
      ${mysqlTuningRow("目前大小", `<strong>${st.size_mb} MB</strong>`)}
      ${mysqlTuningRow("設定來源", sourceText)}
      ${mysqlTuningRow("主機總記憶體", `${st.host_memory_mb} MB(最多可設定 ${st.max_mb} MB,約一半)`)}
      ${mysqlTuningRow("nanomdm 資料量", `${st.data_mb} MB(資料+索引)`)}
      ${mysqlTuningRow("快取命中率", pct(st.hit_ratio))}
      ${mysqlTuningRow("快取空閒比例", pct(st.free_ratio))}
      ${st.resize_status ? mysqlTuningRow("最近一次調整狀態", escapeHtml(st.resize_status)) : ""}
    </table>`;

  const input = document.getElementById("mysql-tuning-size-input");
  input.min = st.min_mb;
  input.max = st.max_mb;
  input.step = st.unit_mb;
  if (!input.value) input.value = st.size_mb;
  document.getElementById("mysql-tuning-size-hint").textContent =
    `會自動換成 ${st.unit_mb} MB 的倍數,範圍 ${st.min_mb}~${st.max_mb} MB`;
  return st;
}

function normalizeBufferPoolMb(value, st) {
  const unit = st.unit_mb;
  const rounded = Math.max(unit, Math.round(value / unit) * unit);
  return Math.min(Math.max(rounded, st.min_mb), st.max_mb);
}

async function applyMysqlBufferPool() {
  const st = mysqlTuningStatus || await loadMysqlTuningStatus();
  if (!st) return;
  const requested = parseInt(document.getElementById("mysql-tuning-size-input").value, 10);
  if (!requested || requested < 1) {
    alert("請輸入大於0的整數(MB)");
    return;
  }
  const target = normalizeBufferPoolMb(requested, st);
  const note = target !== requested ? `(輸入的 ${requested} MB 會調整成 ${target} MB)` : "";
  if (!confirm(`確定要把 MySQL buffer pool 從 ${st.size_mb} MB 調整為 ${target} MB 嗎?${note}\n調整會在背景進行,不需要重新啟動 MySQL,重新啟動後也會保留這個設定。`)) return;
  const password = await askPassword("請輸入您目前登入的密碼以確認調整:");
  if (!password) return;

  const btn = document.getElementById("mysql-tuning-apply-btn");
  btn.disabled = true;
  const res = await apiFetchJSON("/api/mysql-tuning/buffer-pool", "POST", { size_mb: requested, password });
  btn.disabled = false;
  if (!res.ok) {
    alert("調整失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }
  document.getElementById("mysql-tuning-size-input").value = res.data.size_mb;
  watchMysqlResize(res.data.size_mb);
}

// MySQL 在背景調整大小,每2秒查一次狀態,直到目前大小變成目標大小(最多約2分鐘)
async function watchMysqlResize(targetMb) {
  const progress = document.getElementById("mysql-tuning-progress");
  for (let i = 0; i < 60; i++) {
    const st = await loadMysqlTuningStatus();
    const statusText = st && st.resize_status ? `(${st.resize_status})` : "";
    if (st && st.size_mb === targetMb) {
      progress.textContent = `✅ 已調整為 ${targetMb} MB ${statusText}`;
      return;
    }
    progress.textContent = `調整中... 目前 ${st ? st.size_mb : "-"} MB → ${targetMb} MB ${statusText}`;
    await new Promise((r) => setTimeout(r, 2000));
  }
  progress.textContent = "調整時間較長,請稍後按「重新整理」確認目前大小";
}

async function resetMysqlBufferPool() {
  const st = mysqlTuningStatus;
  const defaultMb = st ? st.default_mb : 128;
  if (!confirm(`確定要恢復預設嗎?\n會清除手動設定,目前大小不變,下次重新啟動 MySQL 後才會回到 ${defaultMb} MB。`)) return;
  const password = await askPassword("請輸入您目前登入的密碼以確認:");
  if (!password) return;
  const res = await apiFetchJSON("/api/mysql-tuning/buffer-pool/reset", "POST", { password });
  if (!res.ok) {
    alert("恢復預設失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }
  document.getElementById("mysql-tuning-progress").textContent =
    `已清除手動設定,下次重新啟動 MySQL 後回到 ${defaultMb} MB`;
  loadMysqlTuningStatus();
}

// ---------------------------------------------------------------------------
// nanomdm 指令歷史清理
// ---------------------------------------------------------------------------
async function loadCleanupSettings() {
  const res = await apiFetch("/api/cleanup/settings");
  if (!res.ok) return;
  document.getElementById("cleanup-retention-days-input").value = res.data.settings.retention_days;
  document.getElementById("cleanup-auto-enabled-input").checked = res.data.settings.auto_enabled;
}

async function saveCleanupSettings() {
  const retentionDays = parseInt(document.getElementById("cleanup-retention-days-input").value, 10);
  const autoEnabled = document.getElementById("cleanup-auto-enabled-input").checked;

  if (!retentionDays || retentionDays < 1) {
    alert("保留天數必須是大於0的整數");
    return;
  }

  const res = await apiFetchJSON("/api/cleanup/settings", "POST", {
    retention_days: retentionDays, auto_enabled: autoEnabled,
  });
  if (res.ok) {
    alert("設定已儲存");
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function previewCleanup() {
  const retentionDays = parseInt(document.getElementById("cleanup-retention-days-input").value, 10);
  if (!retentionDays || retentionDays < 1) {
    alert("保留天數必須是大於0的整數");
    return;
  }

  const btn = document.getElementById("cleanup-preview-btn");
  btn.disabled = true;
  btn.textContent = "查詢中...";

  const res = await apiFetch(`/api/cleanup/preview?retention_days=${retentionDays}`);

  btn.disabled = false;
  btn.textContent = "預覽清理範圍";

  if (!res.ok) {
    alert("預覽失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }

  const data = res.data;
  document.getElementById("cleanup-preview-commands-count").textContent = data.commands_count;
  document.getElementById("cleanup-preview-results-count").textContent =
    data.command_results_count !== null ? data.command_results_count : "未知";
  document.getElementById("cleanup-preview-queue-count").textContent =
    data.enrollment_queue_count !== null ? data.enrollment_queue_count : "未知";
  document.getElementById("cleanup-preview-pending-count").textContent =
    data.pending_queue_count !== null && data.pending_queue_count !== undefined ? data.pending_queue_count : "未知";
  document.getElementById("cleanup-preview-kept-count").textContent =
    data.kept_unfinished_count !== null && data.kept_unfinished_count !== undefined ? data.kept_unfinished_count : "未知";

  const breakdownEl = document.getElementById("cleanup-preview-breakdown");
  if (data.by_request_type && data.by_request_type.length > 0) {
    breakdownEl.innerHTML = "<strong>依指令類型分類：</strong><br>" +
      data.by_request_type.map(item => `${escapeHtml(item.request_type)}: ${item.count} 筆`).join("　");
  } else {
    breakdownEl.innerHTML = "";
  }

  document.getElementById("cleanup-preview-result").style.display = "";

  if (data.commands_count === 0) {
    alert("目前沒有超過保留天數的指令紀錄");
  }
}

async function executeCleanup() {
  const retentionDays = parseInt(document.getElementById("cleanup-retention-days-input").value, 10);
  const previewCount = document.getElementById("cleanup-preview-commands-count").textContent;

  if (!confirm(`確定要清除這 ${previewCount} 筆指令紀錄嗎?此操作無法復原,長時間沒連線的裝置還沒完成的指令也會一併清除。`)) {
    return;
  }

  const password = await askPassword("請輸入您目前登入的密碼以確認執行清理:");
  if (!password) return;

  const btn = document.getElementById("cleanup-execute-btn");
  btn.disabled = true;
  btn.textContent = "執行中...";

  const res = await apiFetchJSON("/api/cleanup/execute", "POST", {
    retention_days: retentionDays, password,
  });

  btn.disabled = false;
  btn.textContent = "確認執行清理";

  if (res.ok) {
    alert(res.data.message);
    document.getElementById("cleanup-preview-result").style.display = "none";
  } else {
    alert("清理失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function loadSystemParams() {
  const res = await apiFetch("/api/system-params");
  if (!res.ok) return;
  const p = res.data.params;
  document.getElementById("param-asm-devices-interval").value = p.asm_devices_interval_minutes;
  document.getElementById("param-vpp-interval").value = p.vpp_interval_minutes;
  document.getElementById("param-devices-status-interval").value = p.devices_status_interval_minutes;
  document.getElementById("param-pending-retry-hours").value = p.pending_retry_threshold_hours;
  document.getElementById("param-installed-apps-interval").value = p.installed_apps_interval_hours;
}

async function saveSystemParams() {
  const payload = {
    asm_devices_interval_minutes: document.getElementById("param-asm-devices-interval").value,
    vpp_interval_minutes: document.getElementById("param-vpp-interval").value,
    devices_status_interval_minutes: document.getElementById("param-devices-status-interval").value,
    pending_retry_threshold_hours: document.getElementById("param-pending-retry-hours").value,
    installed_apps_interval_hours: document.getElementById("param-installed-apps-interval").value,
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
    document.title = siteLabel; // 瀏覽器分頁標題也跟著更新
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
  const favicon = document.querySelector('link[rel="icon"]');
  if (favicon) favicon.href = apiUrl("/favicon.ico") + bust; // 分頁圖示也跟著換
}

// ---- 電子書下載IP範圍限制 ----
function addEbookIpRuleRow(value) {
  const container = document.getElementById("ebook-ip-rules-container");
  const row = document.createElement("div");
  row.className = "ebook-ip-rule-row";
  row.style.cssText = "display:flex; gap:8px; align-items:center; margin-bottom:6px;";
  row.innerHTML = `
    <input type="text" class="ebook-ip-rule-input" value="${escapeHtml(value || "")}" placeholder="例如 192.168.0.0/16" style="flex:1; font-family:var(--mono);">
    <button class="danger ebook-remove-ip-rule-btn" type="button" style="font-size:12px;">移除</button>
  `;
  container.appendChild(row);
}

async function loadEbookSettings() {
  const container = document.getElementById("ebook-ip-rules-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/ebooks");
  if (!res.ok || !res.data.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗</p>`;
    return;
  }
  const d = res.data;
  document.getElementById("ebook-your-ip-display").textContent = d.your_ip;
  document.getElementById("ebook-domain-display").textContent = d.domain || "(無法判斷網域,請在 .env 設定 SERVER_DOMAIN)";
  document.getElementById("ebook-max-size-input").value = d.settings.max_size_mb;
  container.innerHTML = "";
  if (d.settings.ip_allowlist.length === 0) addEbookIpRuleRow("");
  else d.settings.ip_allowlist.forEach((r) => addEbookIpRuleRow(r));

  const statusEl = document.getElementById("ebook-nginx-status");
  const btn = document.getElementById("ebook-nginx-install-btn");
  if (d.nginx.installed) {
    statusEl.innerHTML = `<span style="color:#1c7c3f;">✅ 已加入</span>`;
    btn.classList.add("hidden");
  } else if (d.nginx.error) {
    statusEl.innerHTML = `<span style="color:#b42318;">❌ 無法檢查:${escapeHtml(d.nginx.error)}</span>`;
    btn.classList.add("hidden");
  } else {
    statusEl.innerHTML = `<span style="color:#b45309;">⚠️ 還沒加入,iPad 目前下載不到電子書</span>`;
    btn.classList.remove("hidden");
  }
}

async function saveEbookSettings() {
  const rules = Array.from(document.querySelectorAll(".ebook-ip-rule-input")).map((el) => el.value.trim()).filter((v) => v);
  const maxSize = parseInt(document.getElementById("ebook-max-size-input").value, 10);
  if (!maxSize || maxSize < 1 || maxSize > 2048) {
    alert("檔案大小上限必須是 1~2048 之間的整數(MB)");
    return;
  }
  if (rules.length === 0 && !confirm("IP 範圍清單是空的,代表不限制:只要知道網址,任何來源都可以下載電子書。確定要儲存嗎?")) return;
  const btn = document.getElementById("ebook-settings-save-btn");
  btn.disabled = true;
  const res = await apiFetchJSON("/api/ebooks/settings", "POST", { max_size_mb: maxSize, ip_allowlist: rules });
  btn.disabled = false;
  if (res.ok && res.data.ok) {
    alert(res.data.message + (res.data.settings.ip_allowlist.length === 0 ? "(目前為不限制 IP)" : ""));
    loadEbookSettings();
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function installEbookNginx() {
  if (!confirm("即將修改 nginx 站台設定,加入電子書下載與上傳路徑。會先備份,測試通過才重新載入,失敗會自動還原。確定要加入嗎?")) return;
  const btn = document.getElementById("ebook-nginx-install-btn");
  btn.disabled = true;
  btn.textContent = "處理中...";
  const res = await apiFetchJSON("/api/ebooks/nginx/install", "POST", {});
  btn.disabled = false;
  btn.textContent = "加入 nginx 設定";
  alert((res.ok && res.data.ok ? "" : "失敗: ") + ((res.data && res.data.message) || "未知錯誤"));
  loadEbookSettings();
}

document.addEventListener("DOMContentLoaded", () => {
  loadEbookSettings();
  document.getElementById("ebook-add-ip-rule-btn").addEventListener("click", () => addEbookIpRuleRow(""));
  document.getElementById("ebook-settings-save-btn").addEventListener("click", saveEbookSettings);
  document.getElementById("ebook-nginx-install-btn").addEventListener("click", installEbookNginx);
  document.getElementById("ebook-ip-rules-container").addEventListener("click", (e) => {
    if (e.target.classList.contains("ebook-remove-ip-rule-btn")) e.target.closest(".ebook-ip-rule-row").remove();
  });
});

document.addEventListener("DOMContentLoaded", () => {
  loadEnvFields();
  loadEnvBackups();
  loadDevicesCsvRaw();
  loadGroupsJson();
  loadBrandingSiteLabel();
  loadSystemParams();

  document.getElementById("system-params-save-btn").addEventListener("click", saveSystemParams);
  loadMysqlTuningStatus();
  document.getElementById("mysql-tuning-refresh-btn").addEventListener("click", loadMysqlTuningStatus);
  document.getElementById("mysql-tuning-apply-btn").addEventListener("click", applyMysqlBufferPool);
  document.getElementById("mysql-tuning-reset-btn").addEventListener("click", resetMysqlBufferPool);
  loadCleanupSettings();
  document.getElementById("cleanup-save-settings-btn").addEventListener("click", saveCleanupSettings);
  document.getElementById("cleanup-preview-btn").addEventListener("click", previewCleanup);
  document.getElementById("cleanup-execute-btn").addEventListener("click", executeCleanup);

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
    } else if (e.target.classList.contains("env-field-toggle-btn")) {
      const input = tr.querySelector(".env-field-value");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      e.target.textContent = show ? "隱藏" : "顯示";
    }
  });
});
