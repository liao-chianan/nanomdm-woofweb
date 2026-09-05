let ASM_DATA = null;   // 快取的 {servers, device_by_server, unassigned_devices}
let currentDeviceListContext = null;
let asmProgressEventSource = null;
let asmImportChanges = [];

// ---------------------------------------------------------------------------
// 讀取快取 / 手動重新整理
// ---------------------------------------------------------------------------
function renderAsmServersTable() {
  const tbody = document.getElementById("asm-servers-tbody");
  tbody.innerHTML = "";
  if (!ASM_DATA.servers || ASM_DATA.servers.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5">目前 ASM 底下沒有任何 MDM Server</td></tr>`;
    return;
  }

  // 把「自己這台」(serverName跟current_server_name相符)排到最前面,其餘維持原本順序
  const currentName = ASM_DATA.current_server_name;
  const servers = [...ASM_DATA.servers];
  if (currentName) {
    servers.sort((a, b) => {
      const aIsSelf = a.serverName === currentName;
      const bIsSelf = b.serverName === currentName;
      if (aIsSelf && !bIsSelf) return -1;
      if (!aIsSelf && bIsSelf) return 1;
      return 0;
    });
  }

  servers.forEach((s) => {
    const isSelf = currentName && s.serverName === currentName;
    const tr = document.createElement("tr");
    if (isSelf) {
      tr.style.background = "#eef6ff";
      tr.style.fontWeight = "600";
    }
    const selfBadge = isSelf
      ? ` <span style="background:#2563eb; color:#fff; font-size:11px; font-weight:600; padding:1px 8px; border-radius:10px; margin-left:6px;">本機</span>`
      : "";
    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:11px;">${escapeHtml(s.id)}</td>
      <td><span class="serial-link" data-action="server-detail" data-server-id="${escapeHtml(s.id)}">${escapeHtml(s.serverName)}</span>${selfBadge}</td>
      <td>${escapeHtml(s.serverType)}</td>
      <td>${escapeHtml(s.status)}</td>
      <td><span class="serial-link" data-action="server-devices" data-server-id="${escapeHtml(s.id)}">${s.device_count}</span></td>
    `;
    tbody.appendChild(tr);
  });
}


async function loadAsmCache() {
  const tbody = document.getElementById("asm-servers-tbody");
  tbody.innerHTML = `<tr><td colspan="5">載入中...</td></tr>`;
  const res = await apiFetch("/api/asm-devices/cache");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  ASM_DATA = res.data;
  document.getElementById("asm-last-sync").textContent = ASM_DATA.last_sync
    ? `最後同步時間: ${ASM_DATA.last_sync}`
    : "尚未同步過,請按「立即同步裝置」";
  renderAsmServersTable();
  document.getElementById("unassigned-count-link").textContent = ASM_DATA.unassigned_count;
}

function runAsmRefresh() {
  const panel = document.getElementById("asm-refresh-progress-panel");
  const body = document.getElementById("asm-refresh-progress-body");
  const btn = document.getElementById("refresh-asm-btn");

  panel.classList.remove("hidden");
  body.innerHTML = "";
  btn.disabled = true;
  btn.textContent = "查詢中...";

  if (asmProgressEventSource) asmProgressEventSource.close();
  asmProgressEventSource = new EventSource(apiUrl("/api/asm-devices/refresh-stream"));

  asmProgressEventSource.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }

    const line = document.createElement("div");
    line.style.cssText = "font-size:13px; padding:3px 0; color:#4b5563;";
    line.textContent = update.message || update.error || "";
    body.appendChild(line);
    body.scrollTop = body.scrollHeight;

    if (update.done) {
      btn.disabled = false;
      btn.textContent = "立即同步裝置";
      asmProgressEventSource.close();
      asmProgressEventSource = null;
      loadAsmCache();
    }
  };

  asmProgressEventSource.onerror = () => {
    btn.disabled = false;
    btn.textContent = "立即同步裝置";
    if (asmProgressEventSource) { asmProgressEventSource.close(); asmProgressEventSource = null; }
  };
}

// ---------------------------------------------------------------------------
// 匯出 / 匯入
// ---------------------------------------------------------------------------
function exportAsmCsv() {
  window.location.href = apiUrl("/api/asm-devices/export");
}

async function handleImportFileSelected(e) {
  const file = e.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  debugLog("REQUEST POST /api/asm-devices/import/preview", file.name);
  const resp = await fetch(apiUrl("/api/asm-devices/import/preview"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/asm-devices/import/preview", data, !resp.ok);

  e.target.value = ""; // 重置檔案選擇,方便下次重新選同一個檔案

  if (!data.ok) {
    alert("分析失敗: " + (data.message || "未知錯誤"));
    return;
  }
  if (data.changes.length === 0) {
    alert("比對結果:沒有發現任何需要改派的變更");
    return;
  }

  asmImportChanges = data.changes;
  renderImportPreview(asmImportChanges);
  document.getElementById("asm-import-apply-progress").innerHTML = "";
  openModal("asm-import-modal");
}

function renderImportPreview(changes) {
  const tbody = document.getElementById("asm-import-preview-tbody");
  tbody.innerHTML = "";
  changes.forEach((c) => {
    const tr = document.createElement("tr");
    if (!c.matched) tr.style.color = "#9ca3af";
    const statusText = c.matched ? "✅ 可套用" : `❌ ${escapeHtml(c.reason || "無法套用")}`;
    tr.innerHTML = `
      <td style="font-family:var(--mono);">${escapeHtml(c.serialNumber)}</td>
      <td>${escapeHtml(c.current_server_name || "(未指派)")}</td>
      <td>${escapeHtml(c.target_server_name || "(清空)")}</td>
      <td>${statusText}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function applyImportChanges() {
  const matched = asmImportChanges.filter((c) => c.matched);
  if (matched.length === 0) {
    alert("沒有任何可套用的變更");
    return;
  }
  if (!confirm(`確定要套用 ${matched.length} 筆改派變更嗎?`)) return;

  const btn = document.getElementById("asm-import-apply-btn");
  btn.disabled = true;
  btn.textContent = "送出中...";

  const res = await apiFetchJSON("/api/asm-devices/import/apply", "POST", {
    changes: matched.map((c) => ({ device_id: c.device_id, target_server_id: c.target_server_id })),
  });

  btn.disabled = false;
  btn.textContent = "套用改派";

  const progressContainer = document.getElementById("asm-import-apply-progress");
  if (!res.ok) {
    progressContainer.innerHTML = `<div style="color:#d64545; font-size:13px;">送出失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</div>`;
    return;
  }

  progressContainer.innerHTML = "";
  const trackedActivities = res.data.activities.filter((a) => a.ok && a.activity_id);
  let pendingCount = trackedActivities.length;

  const onActivityDone = () => {
    pendingCount -= 1;
    if (pendingCount <= 0) {
      const notice = document.createElement("div");
      notice.style.cssText = "color:#1c7c3f; font-size:12px; margin-top:6px;";
      notice.textContent = "所有改派作業已完成,自動重新同步裝置清單...";
      progressContainer.appendChild(notice);
      runAsmRefresh();
    }
  };

  res.data.activities.forEach((activity, idx) => {
    const div = document.createElement("div");
    div.id = `import-activity-${idx}`;
    div.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-bottom:6px; font-size:13px;";
    div.innerHTML = `目標伺服器 ${escapeHtml(activity.target_server_id)}(${activity.device_count} 台裝置): 準備中...`;
    progressContainer.appendChild(div);

    if (activity.ok && activity.activity_id) {
      trackImportActivity(activity.activity_id, div, onActivityDone);
    } else {
      div.innerHTML = `目標伺服器 ${escapeHtml(activity.target_server_id)}: ❌ 建立作業失敗 ${escapeHtml(activity.error || "")}`;
    }
  });
}

function trackImportActivity(activityId, container, onDone) {
  const es = new EventSource(apiUrl(`/api/asm-devices/reassign/progress/${encodeURIComponent(activityId)}`));
  es.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }
    const statusText = update.status || (update.error ? "錯誤" : "查詢中");
    container.innerHTML = `activity ${escapeHtml(activityId)}: <span class="badge ${update.done ? 'ok' : 'warn'}">${escapeHtml(statusText)}</span> (第 ${update.attempt || 0} 次查詢)`;
    if (update.done) {
      es.close();
      if (onDone) onDone();
    }
  };
  es.onerror = () => {
    es.close();
    if (onDone) onDone();
  };
}

// ---------------------------------------------------------------------------
// Server 詳細資訊 / 裝置清單 / 單筆改派 (跟原本邏輯相同)
// ---------------------------------------------------------------------------
async function openServerDetail(serverId) {
  const server = (ASM_DATA.servers || []).find((s) => s.id === serverId);
  document.getElementById("asm-server-detail-title").textContent = `MDM Server 詳細資訊 - ${server ? server.serverName : serverId}`;
  document.getElementById("asm-server-detail-body").innerHTML = "載入中...";
  openModal("asm-server-detail-modal");

  const res = await apiFetch(`/api/asm-devices/server/${encodeURIComponent(serverId)}`);
  const body = document.getElementById("asm-server-detail-body");
  if (!res.ok) {
    body.innerHTML = `<p style="color:#d64545;">取得失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const attrs = (res.data.detail.data && res.data.detail.data.attributes) || {};
  let rows = `<tr><td class="k">id</td><td>${escapeHtml(res.data.detail.data.id || "")}</td></tr>`;
  Object.keys(attrs).forEach((key) => {
    rows += `<tr><td class="k">${escapeHtml(key)}</td><td>${escapeHtml(String(attrs[key]))}</td></tr>`;
  });
  body.innerHTML = `<table class="kv-table">${rows}</table>`;
}

function populateTargetServerSelect(excludeServerId) {
  const select = document.getElementById("asm-target-server-select");
  select.innerHTML = "";
  (ASM_DATA.servers || []).forEach((s) => {
    if (s.id === excludeServerId) return;
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.serverName;
    select.appendChild(opt);
  });
}

function renderDeviceListRows(devices) {
  const tbody = document.getElementById("asm-device-list-tbody");
  if (!devices || devices.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5">沒有任何裝置</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  devices.forEach((d) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="asm-device-checkbox" value="${escapeHtml(d.id)}" data-serial="${escapeHtml(d.serialNumber)}"></td>
      <td style="font-family:var(--mono);">${escapeHtml(d.serialNumber)}</td>
      <td>${escapeHtml(d.deviceModel)}</td>
      <td>${escapeHtml(d.color)}</td>
      <td>${escapeHtml(d.status)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function openDeviceListModal(context) {
  currentDeviceListContext = context;
  document.getElementById("asm-reassign-progress").innerHTML = "";
  document.getElementById("asm-select-all").checked = false;

  let devices, titleText, excludeServerId;
  if (context.type === "server") {
    devices = (ASM_DATA.device_by_server || {})[context.serverId] || [];
    const server = (ASM_DATA.servers || []).find((s) => s.id === context.serverId);
    titleText = `裝置清單 - ${server ? server.serverName : context.serverId}`;
    excludeServerId = context.serverId;
  } else {
    devices = ASM_DATA.unassigned_devices || [];
    titleText = "尚未指派的裝置";
    excludeServerId = null;
  }

  document.getElementById("asm-device-list-title").textContent = titleText;
  renderDeviceListRows(devices);
  populateTargetServerSelect(excludeServerId);
  openModal("asm-device-list-modal");
}

function getSelectedDeviceIds() {
  return Array.from(document.querySelectorAll(".asm-device-checkbox:checked")).map((el) => el.value);
}

function getSelectedDeviceSerials() {
  return Array.from(document.querySelectorAll(".asm-device-checkbox:checked")).map((el) => el.dataset.serial);
}

function renderReassignProgress(update) {
  const container = document.getElementById("asm-reassign-progress");
  const statusText = update.status || (update.error ? "錯誤" : "查詢中");
  const cls = update.done ? (update.error ? "warn" : "ok") : "warn";
  container.innerHTML = `
    <div style="background:#f8f9fb; border:1px solid var(--border-color); border-radius:6px; padding:10px 14px; font-size:13px;">
      <span class="badge ${cls}">${escapeHtml(statusText)}</span>
      <span style="color:#6b7280; margin-left:8px;">第 ${update.attempt || 0} 次查詢</span>
      ${update.message ? `<div style="margin-top:6px; color:#b45309;">${escapeHtml(update.message)}</div>` : ""}
      ${update.error ? `<div style="margin-top:6px; color:#d64545;">${escapeHtml(update.error)}</div>` : ""}
    </div>
  `;
}

function startReassignCleanup(serials) {
  const modal = document.getElementById("asm-reassigned-cleanup-modal");
  const title = document.getElementById("asm-reassigned-cleanup-title");
  const container = document.getElementById("asm-reassigned-cleanup-progress");
  title.textContent = `清理本地資料 (${serials.length} 台裝置)`;
  container.innerHTML = "";
  modal.classList.remove("hidden");

  const stepDivs = {};
  const params = new URLSearchParams({ serials: serials.join(",") });
  const es = new EventSource(apiUrl(`/api/asm-devices/reassign-cleanup-stream?${params.toString()}`));

  es.onmessage = (event) => {
    let update;
    try {
      update = JSON.parse(event.data);
    } catch (e) {
      return;
    }

    if (update.done !== undefined && update.serial === undefined) {
      // 全部裝置都處理完畢的最終訊息
      const finalDiv = document.createElement("div");
      finalDiv.style.cssText = "margin-top:8px; font-weight:600; color:#1c7c3f;";
      finalDiv.textContent = "✅ 全部裝置的本地資料清理已完成";
      container.appendChild(finalDiv);
      es.close();
      return;
    }

    if (update.step === "start") {
      const header = document.createElement("div");
      header.style.cssText = "margin-top:14px; font-weight:600; font-size:13px;";
      header.textContent = `序號 ${update.serial}`;
      container.appendChild(header);
      return;
    }
    if (update.step === "end") {
      return; // 每台裝置的個別步驟已經逐一顯示過,結尾訊息不用重複呈現
    }

    const stepKey = `${update.serial}-step-${update.step}`;
    let stepDiv = stepDivs[stepKey];
    if (!stepDiv) {
      stepDiv = document.createElement("div");
      stepDiv.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-top:6px; font-size:13px;";
      container.appendChild(stepDiv);
      stepDivs[stepKey] = stepDiv;
    }
    const iconMap = { running: "⏳", done: "✅", skipped: "➖", error: "❌" };
    const icon = iconMap[update.status] || "•";
    const msgText = update.message ? `: ${escapeHtml(update.message)}` : "";
    stepDiv.innerHTML = `${icon} 步驟${update.step} - ${escapeHtml(update.step_name)}${msgText}`;
  };

  es.onerror = () => {
    es.close();
  };
}

async function reassignSelectedDevices() {
  const deviceIds = getSelectedDeviceIds();
  const serials = getSelectedDeviceSerials();
  const targetServerId = document.getElementById("asm-target-server-select").value;

  if (deviceIds.length === 0) { alert("請至少勾選一台裝置"); return; }
  if (!targetServerId) { alert("請選擇要改派的目標伺服器"); return; }
  const targetLabel = document.getElementById("asm-target-server-select").selectedOptions[0].textContent;
  if (!confirm(`確定要把選取的 ${deviceIds.length} 台裝置改派到「${targetLabel}」嗎?`)) return;

  const btn = document.getElementById("asm-reassign-btn");
  btn.disabled = true;
  btn.textContent = "送出中...";
  document.getElementById("asm-reassign-progress").innerHTML = "";

  const res = await apiFetchJSON("/api/asm-devices/reassign/start", "POST", {
    device_ids: deviceIds, target_server_id: targetServerId,
  });

  if (!res.ok) {
    btn.disabled = false;
    btn.textContent = "改派";
    document.getElementById("asm-reassign-progress").innerHTML =
      `<div style="color:#d64545; font-size:13px;">建立改派作業失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</div>`;
    return;
  }

  btn.textContent = "處理中...(輪詢進度)";

  if (asmProgressEventSource) asmProgressEventSource.close();
  const es = new EventSource(apiUrl(`/api/asm-devices/reassign/progress/${encodeURIComponent(res.data.activity_id)}`));

  es.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }
    renderReassignProgress(update);
    if (update.done) {
      btn.disabled = false;
      btn.textContent = "改派";
      es.close();
      loadAsmCache();

      // 只有「目前開啟的裝置清單,正好是本機這台伺服器」時,才顯示清理本地資料的提示——
      // 查看別台伺服器的清單、或未指派裝置清單時改派,不該出現這個按鈕(裝置本來就不是
      // 從本機移出去的,沒有本機資料需要清理)。
      const isFromThisServer =
        currentDeviceListContext &&
        currentDeviceListContext.type === "server" &&
        ASM_DATA.current_server_name &&
        (ASM_DATA.servers || []).some(
          (s) => s.id === currentDeviceListContext.serverId && s.serverName === ASM_DATA.current_server_name
        );

      if (!isFromThisServer) return;

      // 不自動觸發本地資料清理——改派作業的確切成功/失敗狀態,Apple回傳的字串我們沒有
      // 100%把握判讀,所以在上方顯示完整原始狀態讓管理者自己確認,這裡改成額外顯示一個
      // 按鈕,由管理者看過結果後自己決定要不要清理,不自動猜測、自動觸發。
      const progressEl = document.getElementById("asm-reassign-progress");
      const cleanupPrompt = document.createElement("div");
      cleanupPrompt.style.cssText = "margin-top:10px; padding:10px 14px; background:#fffbeb; border:1px solid #fde68a; border-radius:6px; font-size:13px;";
      cleanupPrompt.innerHTML = `
        請先確認上方改派結果是否成功,如果確定裝置已經成功改派到別台伺服器,
        可以點下方按鈕清理這批裝置在本機的本地資料(不會去動 ASM 的指派狀態)。<br>
        <button type="button" id="asm-reassign-cleanup-trigger-btn" style="margin-top:6px; font-size:12px;">清理本地資料</button>
      `;
      progressEl.appendChild(cleanupPrompt);
      document.getElementById("asm-reassign-cleanup-trigger-btn").addEventListener("click", () => {
        startReassignCleanup(serials);
      });
    }
  };
  es.onerror = () => {
    btn.disabled = false;
    btn.textContent = "改派";
    es.close();
  };
}

// ---------------------------------------------------------------------------
// 事件綁定
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadAsmCache();

  document.getElementById("refresh-asm-btn").addEventListener("click", runAsmRefresh);
  document.getElementById("export-asm-btn").addEventListener("click", exportAsmCsv);
  document.getElementById("import-asm-btn").addEventListener("click", () => {
    document.getElementById("import-asm-file-input").click();
  });
  document.getElementById("import-asm-file-input").addEventListener("change", handleImportFileSelected);
  document.getElementById("asm-import-apply-btn").addEventListener("click", applyImportChanges);
  document.getElementById("asm-reassign-btn").addEventListener("click", reassignSelectedDevices);

  document.getElementById("asm-select-all").addEventListener("change", (e) => {
    document.querySelectorAll(".asm-device-checkbox").forEach((cb) => { cb.checked = e.target.checked; });
  });

  document.getElementById("asm-servers-tbody").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.action === "server-detail") openServerDetail(el.dataset.serverId);
    else if (el.dataset.action === "server-devices") openDeviceListModal({ type: "server", serverId: el.dataset.serverId });
  });

  document.getElementById("unassigned-count-link").addEventListener("click", () => {
    openDeviceListModal({ type: "unassigned" });
  });
});
