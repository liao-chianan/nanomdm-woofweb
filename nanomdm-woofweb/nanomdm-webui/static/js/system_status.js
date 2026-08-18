function usageColor(percent) {
  if (percent >= 90) return "#d64545";
  if (percent >= 70) return "#b45309";
  return "#1c7c3f";
}

function renderUsageBar(label, percent, detailText) {
  const color = usageColor(percent);
  return `
    <div style="margin-bottom:10px;">
      <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:3px;">
        <span>${escapeHtml(label)}</span>
        <span style="color:${color}; font-weight:600;">${percent}%${detailText ? " ・ " + escapeHtml(detailText) : ""}</span>
      </div>
      <div style="background:#e5e7eb; border-radius:4px; height:8px; overflow:hidden;">
        <div style="background:${color}; height:100%; width:${Math.min(percent, 100)}%;"></div>
      </div>
    </div>
  `;
}

async function loadSystemStatus() {
  const container = document.getElementById("system-status-container");
  const res = await apiFetch("/api/sysstatus/system");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const d = res.data.data;

  let cpuHtml = "";
  d.cpu.forEach((c) => {
    const label = c.core === "cpu" ? "CPU(全部核心平均)" : `核心 ${c.core.replace("cpu", "")}`;
    cpuHtml += renderUsageBar(label, c.percent, "");
  });

  container.innerHTML = `
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:24px;">
      <div>
        <table class="kv-table">
          <tr><td class="k">目前時間</td><td>${escapeHtml(d.current_time)}</td></tr>
          <tr><td class="k">作業系統</td><td>${escapeHtml(d.os_info.os_name)}</td></tr>
          <tr><td class="k">核心版本</td><td style="font-family:var(--mono); font-size:12px;">${escapeHtml(d.os_info.kernel_version)}</td></tr>
        </table>
        ${renderUsageBar("記憶體", d.memory.percent, `${d.memory.used_mb} / ${d.memory.total_mb} MB`)}
        ${renderUsageBar("磁碟空間 (/)", d.disk.percent, `${d.disk.used_gb} / ${d.disk.total_gb} GB`)}
      </div>
      <div>
        <div style="font-size:12px; color:#6b7280; margin-bottom:8px;">CPU 使用率</div>
        ${cpuHtml}
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Docker
// ---------------------------------------------------------------------------
async function loadDockerStatus() {
  const tbody = document.getElementById("docker-tbody");
  const res = await apiFetch("/api/sysstatus/docker");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  if (res.data.data.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7">沒有監控中的容器(請檢查設定檔的 sysstatus.docker_containers 清單)</td></tr>`;
    return;
  }
  res.data.data.forEach((c) => {
    const tr = document.createElement("tr");
    const stateColor = c.state === "running" ? "#1c7c3f" : "#d64545";
    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(c.name)}</td>
      <td style="font-size:12px; color:#6b7280;">${escapeHtml(c.purpose)}</td>
      <td style="font-size:12px;">${escapeHtml(c.image)}</td>
      <td><span style="color:${stateColor};">${escapeHtml(c.status)}</span></td>
      <td style="font-size:12px;">${escapeHtml(c.ports || "-")}</td>
      <td style="font-size:12px;">${escapeHtml(c.created)}</td>
      <td>
        <button class="secondary docker-log-btn" type="button" data-name="${escapeHtml(c.name)}" style="font-size:11px;">Log</button>
        <button class="secondary restart-btn-pink docker-restart-btn" type="button" data-name="${escapeHtml(c.name)}" style="font-size:11px;">重啟</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function showDockerLog(name) {
  openLogViewer(`Docker Log - ${name}`, `/api/sysstatus/docker/logs/${encodeURIComponent(name)}`);
}

async function restartDockerContainer(name) {
  if (!confirm(`確定要重啟容器「${name}」嗎?這會短暫中斷這個容器提供的服務。`)) return;
  const res = await apiFetchJSON("/api/sysstatus/docker/restart", "POST", { container_name: name });
  if (res.ok) {
    alert(res.data.message);
    loadDockerStatus();
  } else {
    alert("重啟失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// systemd
// ---------------------------------------------------------------------------
async function loadSystemdStatus() {
  const tbody = document.getElementById("systemd-tbody");
  const res = await apiFetch("/api/sysstatus/systemd");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  res.data.data.forEach((s) => {
    const tr = document.createElement("tr");
    const isActive = s.active_state === "active";
    const stateColor = isActive ? "#1c7c3f" : "#d64545";
    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(s.service)}</td>
      <td style="font-size:12px; color:#6b7280;">${escapeHtml(s.purpose)}</td>
      <td><span style="color:${stateColor};">${escapeHtml(s.active_state)}${s.sub_state ? " (" + escapeHtml(s.sub_state) + ")" : ""}</span></td>
      <td style="font-size:12px;">${escapeHtml(s.port || "-")}</td>
      <td style="font-size:12px;">${escapeHtml(s.active_since || "-")}</td>
      <td style="font-size:12px;">${escapeHtml(s.main_pid || "-")}</td>
      <td>
        <button class="secondary systemd-log-btn" type="button" data-name="${escapeHtml(s.service)}" style="font-size:11px;">Log</button>
        <button class="secondary restart-btn-pink systemd-restart-btn" type="button" data-name="${escapeHtml(s.service)}" style="font-size:11px;">重啟</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function showSystemdLog(name) {
  openLogViewer(`Journal Log - ${name}`, `/api/sysstatus/systemd/logs/${encodeURIComponent(name)}`);
}

async function restartSystemdService(name) {
  let warning = `確定要重啟服務「${name}」嗎?這會短暫中斷這個服務。`;
  if (name === "nanomdm-webui.service") {
    warning = `⚠️ 這是目前正在提供這個管理介面本身的服務!重啟後這個網頁會立刻斷線,需要等服務重新啟動完成後重新整理頁面才能繼續使用。\n\n確定要繼續嗎?`;
  }
  if (!confirm(warning)) return;
  const res = await apiFetchJSON("/api/sysstatus/systemd/restart", "POST", { service_name: name });
  if (res.ok) {
    alert(res.data.message);
    loadSystemdStatus();
  } else {
    alert("重啟失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// MySQL
// ---------------------------------------------------------------------------
async function loadMysqlStatus() {
  const container = document.getElementById("mysql-status-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/sysstatus/mysql");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  if (res.data.data.length === 0) {
    container.innerHTML = `<p style="color:#9ca3af;">沒有查到任何資料庫</p>`;
    return;
  }
  let html = "";
  if (res.data.errors && res.data.errors.length > 0) {
    html += `<div style="background:#fef3c7; border:1px solid #fde68a; border-radius:6px; padding:8px 12px; margin-bottom:12px; font-size:12px;">
      ⚠️ 部分資料庫查詢失敗(可能是 .env 裡的密碼設定還沒對應到,或帳號權限不足):<br>
      ${res.data.errors.map((e) => escapeHtml(e)).join("<br>")}
    </div>`;
  }
  res.data.data.forEach((db) => {
    html += `
      <div style="margin-bottom:16px;">
        <h3 style="margin-bottom:6px;">${escapeHtml(db.database)} <span style="color:#9ca3af; font-size:12px; font-weight:normal;">(共 ${db.total_rows.toLocaleString()} 筆資料)</span></h3>
        <table class="data-table">
          <thead><tr><th>資料表</th><th>用途</th><th>估計筆數</th><th>大小 (MB)</th></tr></thead>
          <tbody>
            ${db.tables.map((t) => `<tr><td style="font-family:var(--mono); font-size:12px;">${escapeHtml(t.table)}</td><td style="font-size:12px; color:#6b7280;">${escapeHtml(t.purpose)}</td><td>${t.rows.toLocaleString()}</td><td>${t.size_mb}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
    `;
  });
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Log 檢視 Modal (docker/systemd共用)
// ---------------------------------------------------------------------------
async function openLogViewer(title, apiPath) {
  document.getElementById("log-viewer-title").textContent = title;
  document.getElementById("log-viewer-content").textContent = "載入中...";
  openModal("log-viewer-modal");

  const res = await apiFetch(apiPath);
  const contentEl = document.getElementById("log-viewer-content");
  if (res.ok) {
    contentEl.textContent = res.data.logs || "(沒有log內容)";
    contentEl.scrollTop = contentEl.scrollHeight;
  } else {
    contentEl.textContent = "載入失敗: " + ((res.data && res.data.message) || "未知錯誤");
  }
}

// ---------------------------------------------------------------------------
// 靜態檔案檢視
// ---------------------------------------------------------------------------
function renderFileExistBadge(item) {
  if (item.exists) {
    const detail = item.size_bytes !== undefined ? ` (${item.size_bytes} bytes, ${escapeHtml(item.modified_at)})` : "";
    return `<span style="color:#1c7c3f;">✅ 存在</span><span style="color:#9ca3af; font-size:11px;">${detail}</span>`;
  }
  return `<span style="color:#d64545; font-weight:600;">❌ 不存在</span>`;
}

function renderFileTable(title, items) {
  if (items.length === 0) return "";
  return `
    <h3 style="margin-top:16px; margin-bottom:6px; font-size:14px;">${escapeHtml(title)}</h3>
    <table class="data-table">
      <thead><tr><th style="width:35%;">路徑</th><th style="width:40%;">說明</th><th>狀態</th></tr></thead>
      <tbody>
        ${items.map((item) => `
          <tr>
            <td style="font-family:var(--mono); font-size:11px; word-break:break-all;">${escapeHtml(item.path)}</td>
            <td style="font-size:12px; color:#6b7280;">${escapeHtml(item.description)}</td>
            <td style="font-size:12px;">${renderFileExistBadge(item)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

async function loadStaticFilesStatus() {
  const container = document.getElementById("static-files-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/sysstatus/static-files");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const d = res.data.data;

  let html = "";
  html += renderFileTable("1. Script 檔案", d.scripts);
  html += renderFileTable("2. CSV 檔案", d.csv_files);
  html += renderFileTable("3. JSON 註冊檔案(群組專屬 enroll json)", d.json_files.enroll_profiles);
  html += renderFileTable("其他固定的 JSON 檔案", d.json_files.fixed);
  html += renderFileTable("4. mobileconfig 描述檔案", d.mobileconfig_files);
  html += renderFileTable("5. .env 檔案", [d.env_file]);

  if (d.unaccounted_files && d.unaccounted_files.length > 0) {
    html += `
      <div style="background:#fef3c7; border:1px solid #fde68a; border-radius:6px; padding:10px 14px; margin-top:16px; font-size:12px;">
        ⚠️ 部署目錄裡發現以下檔案,不在上面任何已知清單裡,可能是遺漏未列入管理、或是暫存/備份檔案,建議確認一下:<br>
        ${d.unaccounted_files.map((f) => `<span style="font-family:var(--mono);">${escapeHtml(f)}</span>`).join("<br>")}
      </div>
    `;
  } else {
    html += `<p style="color:#9ca3af; font-size:12px; margin-top:12px;">✅ 部署目錄裡沒有發現未列入清單的檔案</p>`;
  }

  container.innerHTML = html;
}

document.addEventListener("DOMContentLoaded", () => {
  loadSystemStatus();
  loadDockerStatus();
  loadSystemdStatus();
  loadMysqlStatus();
  loadStaticFilesStatus();

  document.getElementById("refresh-system-btn").addEventListener("click", loadSystemStatus);
  document.getElementById("refresh-mysql-btn").addEventListener("click", loadMysqlStatus);
  document.getElementById("refresh-static-files-btn").addEventListener("click", loadStaticFilesStatus);

  document.getElementById("docker-tbody").addEventListener("click", (e) => {
    const name = e.target.dataset.name;
    if (!name) return;
    if (e.target.classList.contains("docker-log-btn")) showDockerLog(name);
    else if (e.target.classList.contains("docker-restart-btn")) restartDockerContainer(name);
  });

  document.getElementById("systemd-tbody").addEventListener("click", (e) => {
    const name = e.target.dataset.name;
    if (!name) return;
    if (e.target.classList.contains("systemd-log-btn")) showSystemdLog(name);
    else if (e.target.classList.contains("systemd-restart-btn")) restartSystemdService(name);
  });

  // 系統資源每10秒自動更新一次(輕量,不影響Docker/systemd/MySQL的手動重新整理)
  setInterval(loadSystemStatus, 10000);
});
