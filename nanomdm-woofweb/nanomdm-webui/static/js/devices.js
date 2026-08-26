const NAME_PATTERN = /^[^\x00-\x1f\x7f,"]{0,64}$/;
let currentCommandEnrollmentId = null;
let currentCommandSerial = null;
let groupNamesList = [];
let allDeviceRows = [];
let currentOsStatusFilter = ""; // ""(全部) / "download" / "install"
let selectedDeviceItems = new Set(); // 目前勾選的enrollment_id集合

// 裝置列表的欄位定義,給排序功能用。派送命令欄位是操作按鈕不是資料,不列進來(不能排序)。
const DEVICES_TABLE_COLUMNS = [
  { key: "seq", label: "流水號", type: "number" },
  { key: "device_name", label: "裝置名稱", type: "text" },
  { key: "group", label: "群組", type: "text" },
  { key: "serial_number", label: "裝置序號", type: "text" },
  { key: "wifi_mac", label: "WIFI MAC", type: "text" },
  { key: "battery_level", label: "電量", type: "number" },
  { key: "device_capacity", label: "容量", type: "number" },
  { key: "os_version", label: "作業系統", type: "text" },
  { key: "ip_address", label: "IP 位址", type: "text" },
  { key: "last_seen_at", label: "最後同步", type: "text" },
  { key: "enrollment_id", label: "佈署 ID", type: "text" },
];
const devicesSorter = createTableSorter();

// 只有「下載更新」跟「安裝更新」這兩種篩選模式需要勾選框做批次派送,
// 「安裝中」這種純粹顯示狀態、不該讓使用者對其觸發任何派送動作的模式不需要
function osStatusFilterNeedsCheckbox(filter) {
  return filter === "download" || filter === "install";
}

// 判斷一台裝置目前是不是「可以下載更新」或「可以安裝更新」,跟畫面上單一裝置按鈕
// 顯示的判斷條件完全一致(見renderRow裡的osCell邏輯),確保篩選結果跟畫面上看到的
// 按鈕狀態一致,不會篩出一批裝置、卻有些其實沒有對應的按鈕可以批次操作。
function deviceMatchesOsStatusFilter(row, filter) {
  if (!filter) return true;
  if (!row.available_os_version) return false;
  if (filter === "download") {
    return row.os_update_status !== "Downloading" && row.os_update_status !== "Installing" && row.os_update_is_downloaded !== "true";
  }
  if (filter === "downloading") {
    return row.os_update_status === "Downloading";
  }
  if (filter === "install") {
    // 已下載完成、但還沒開始安裝的才算「可以觸發安裝」;正在安裝中的不算(該歸類到「安裝中」篩選)
    return row.os_update_is_downloaded === "true" && row.os_update_status !== "Installing";
  }
  if (filter === "installing") {
    return row.os_update_status === "Installing";
  }
  return true;
}

function renderDevicesTableHeader() {
  const thead = document.getElementById("devices-thead");
  const cells = DEVICES_TABLE_COLUMNS
    .map((col) => `<th style="cursor:pointer;" data-sort-key="${col.key}">${escapeHtml(col.label)}${devicesSorter.sortArrow(col.key)}</th>`)
    .join("");
  const checkboxHeaderHtml = osStatusFilterNeedsCheckbox(currentOsStatusFilter)
    ? `<th style="width:32px;"><input type="checkbox" id="devices-select-all-checkbox"></th>`
    : `<th style="width:32px;"></th>`;
  thead.innerHTML = `<tr>${checkboxHeaderHtml}${cells}<th>派送命令</th></tr>`;
}

async function loadGroupNames() {
  const res = await apiFetch("/api/groups");
  if (res.ok) {
    groupNamesList = res.data.rows.map((r) => r.group_name);
  }
}

function batteryColor(level) {
  if (level === null || level === undefined || level === "") return "#374151";
  const pct = Number(level) * 100;
  if (pct >= 80) return "#1c7c3f";   // 綠
  if (pct >= 30 && pct <= 60) return "#b45309";  // 黃(30%-60%)
  if (pct < 30) return "#d64545";    // 紅
  return "#374151";  // 60%~80%之間(需求沒有明確定義這個區間,維持一般文字顏色)
}

function formatBatteryPct(level) {
  if (level === null || level === undefined || level === "") return "";
  return `${Math.round(Number(level) * 100)}%`;
}

function formatCapacityCell(total, available) {
  if (total === null || total === undefined || total === "") return "";
  const usedText = (available !== null && available !== undefined && available !== "")
    ? `${(Number(total) - Number(available)).toFixed(1)} GB / `
    : "";
  return `${usedText}${Number(total).toFixed(1)} GB`;
}

function formatOsCell(current, available) {
  if (!current && !available) return "";
  const currentText = current ? escapeHtml(current) : "-";
  const availableText = available ? ` / <span style="color:#b45309;">可更新: ${escapeHtml(available)}</span>` : "";
  return `${currentText}${availableText}`;
}

function renderRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.serial = row.serial_number;
  tr.dataset.enrollmentId = row.enrollment_id;

  const batteryText = formatBatteryPct(row.battery_level);
  const batteryCell = batteryText
    ? `<span style="color:${batteryColor(row.battery_level)}; font-weight:600;">${batteryText}</span>`
    : `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;

  const capacityCell = formatCapacityCell(row.device_capacity, row.available_device_capacity)
    || `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;

  // 作業系統欄位:有可用更新時,依「目前下載/安裝狀態」顯示不同按鈕
  let osCell = formatOsCell(row.os_version, row.available_os_version) || `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;
  if (row.available_os_version) {
    if (row.os_update_status === "Downloading") {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="downloading" style="font-size:11px; padding:2px 8px; background-color:#d1fae5; border-color:#6ee7b7;">下載中...</button>`;
    } else if (row.os_update_status === "Installing") {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="installing" style="font-size:11px; padding:2px 8px; background-color:#ede9fe; border-color:#c4b5fd;">安裝中...</button>`;
    } else if (row.os_update_is_downloaded === "true") {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="install" style="font-size:11px; padding:2px 8px; background-color:#fef3c7; border-color:#fde68a;">安裝更新</button>`;
    } else {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="download" style="font-size:11px; padding:2px 8px;">下載更新</button>`;
    }
  }

  // IP位址欄位:遺失模式已啟用且已經取得過定位資料時,顯示「遺失定位」按鈕
  let ipCell = row.ip_address
    ? escapeHtml(row.ip_address)
    : `<span style="color:#9ca3af; font-size:11px;" title="從nanomdm服務的連線紀錄解析取得,裝置最近沒有連線過或還沒同步過就會是空的">尚無資料</span>`;
  if (row.lost_mode_enabled === "true" && row.location_lat && row.location_lng) {
    ipCell += ` <button class="secondary show-location-btn" type="button" style="font-size:11px; padding:2px 8px;" data-lat="${escapeHtml(String(row.location_lat))}" data-lng="${escapeHtml(String(row.location_lng))}" data-at="${escapeHtml(row.location_at || "")}" data-accuracy="${escapeHtml(String(row.location_accuracy || ""))}">遺失定位</button>`;
  }

  const checkboxCellHtml = osStatusFilterNeedsCheckbox(currentOsStatusFilter)
    ? `<input type="checkbox" class="device-row-checkbox" data-eid="${escapeHtml(row.enrollment_id)}" ${selectedDeviceItems.has(row.enrollment_id) ? "checked" : ""}>`
    : "";

  tr.innerHTML = `
    <td>${checkboxCellHtml}</td>
    <td>${row.seq}</td>
    <td>${escapeHtml(row.device_name) || '<span style="color:#9ca3af;">(未命名)</span>'}</td>
    <td>${escapeHtml(row.group) || '<span style="color:#9ca3af;">(未分類)</span>'}</td>
    <td><span class="serial-link" data-serial="${escapeHtml(row.serial_number)}">${escapeHtml(row.serial_number)}</span></td>
    <td style="font-family: var(--mono); font-size:11px;">${row.wifi_mac ? escapeHtml(row.wifi_mac) : '<span style="color:#9ca3af;">(尚無ASM快取資料)</span>'}</td>
    <td>${batteryCell}</td>
    <td style="font-size:12px;">${capacityCell}</td>
    <td style="font-size:12px;">${osCell}</td>
    <td style="font-size:12px;">${ipCell}</td>
    <td style="font-size:12px;">${escapeHtml(row.last_seen_at || "")}</td>
    <td style="font-family: var(--mono); font-size:10px; word-break:break-all;">${escapeHtml(row.enrollment_id)}</td>
    <td>
      <button class="secondary send-command-btn" type="button">命令</button>
      <button class="secondary history-btn" type="button" style="margin-top:4px;">紀錄</button>
      <button class="secondary sync-one-btn" type="button" style="margin-top:4px;" data-serial="${escapeHtml(row.serial_number)}" data-eid="${escapeHtml(row.enrollment_id)}">同步</button>
    </td>
  `;
  return tr;
}

async function startDeviceOffboard(serial, enrollmentId) {
  const confirmed = confirm(
    `確定要讓裝置「${serial}」退場嗎?\n\n` +
    `接下來會依序執行:\n` +
    `1. 清除 Activation Lock bypass code(裝置需要在線上才會實際生效)\n` +
    `2. 撤銷這台裝置目前所屬群組綁定的 VPP App 授權(釋放授權額度給其他裝置使用)\n` +
    `3. 在 ASM 解除指派(裝置仍留在 ASM 名冊裡,只是不再指派給任何 MDM 伺服器,之後可以重新指派回來,不是永久釋出)\n` +
    `4. 清除 nanomdm 這邊這台裝置的註冊紀錄\n` +
    `5. 清理這套系統自己的本地資料(裝置名稱/群組/電量容量等快取資訊)\n\n` +
    `注意:這個流程不會遠端清除裝置上的資料,裝置本身目前已安裝的描述檔/App 都不會被移除。\n` +
    `如果裝置之後還連得上網路、且沒有解除 MDM 描述檔,它可能還是會繼續回報狀態給 nanomdm,只是這套系統不會再顯示它。\n\n` +
    `確定要繼續嗎?`
  );
  if (!confirmed) return;

  const btn = document.getElementById("offboard-device-btn");
  const container = document.getElementById("offboard-progress-container");
  btn.disabled = true;
  btn.textContent = "執行中...";
  container.innerHTML = "";

  const stepLabels = {};
  const params = new URLSearchParams({ serial, enrollment_id: enrollmentId || "" });
  const es = new EventSource(apiUrl(`/api/devices/offboard-stream?${params.toString()}`));

  es.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }

    if (update.done !== undefined && update.step === undefined) {
      // 最終的整體完成訊息
      const finalDiv = document.createElement("div");
      finalDiv.style.cssText = `margin-top:8px; font-weight:600; color:${update.overall_ok ? "#1c7c3f" : "#b45309"};`;
      finalDiv.textContent = update.overall_ok
        ? "✅ 裝置退場流程已完成"
        : "⚠️ 裝置退場流程已完成,但有部分步驟失敗或略過,請往上檢查每個步驟的結果";
      container.appendChild(finalDiv);
      btn.disabled = false;
      btn.textContent = "開始裝置退場";
      es.close();
      loadDevices(); // 重新整理裝置清單,已退場的裝置會從列表消失
      return;
    }

    const stepKey = `offboard-step-${update.step}`;
    let stepDiv = stepLabels[stepKey];
    if (!stepDiv) {
      stepDiv = document.createElement("div");
      stepDiv.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-top:6px; font-size:13px;";
      container.appendChild(stepDiv);
      stepLabels[stepKey] = stepDiv;
    }

    const iconMap = { running: "⏳", done: "✅", skipped: "➖", error: "❌" };
    const icon = iconMap[update.status] || "•";
    const msgText = update.message ? `: ${escapeHtml(update.message)}` : "";
    stepDiv.innerHTML = `${icon} 步驟${update.step} - ${escapeHtml(update.step_name)}${msgText}`;
  };

  es.onerror = () => {
    btn.disabled = false;
    btn.textContent = "開始裝置退場";
    es.close();
  };
}

function openLocationMapModal(lat, lng, at, accuracy) {
  document.getElementById("location-map-title").textContent = `裝置定位 (${lat}, ${lng})`;
  document.getElementById("location-map-container").innerHTML =
    `<iframe src="https://www.google.com/maps?q=${encodeURIComponent(lat)},${encodeURIComponent(lng)}&output=embed" style="width:100%; height:100%; border:0;" loading="lazy"></iframe>`;
  const accuracyText = accuracy ? `,水平定位精確度約 ${Math.round(Number(accuracy))} 公尺` : "";
  document.getElementById("location-map-info").textContent = at
    ? `定位取得時間: ${at}${accuracyText}(來自最近一次「取得裝置定位」查詢結果,不是即時位置)`
    : "";
  openModal("location-map-modal");
}

async function triggerOsAction(enrollmentId, action) {
  if (action === "downloading") {
    alert("系統軟體下載中，下載時間視網路狀況而定，下載完畢後會才能安裝更新");
    return;
  }
  if (action === "installing") {
    alert("系統軟體安裝中，過程中裝置可能會重新開機，安裝完成前請勿手動關機或中斷電源");
    return;
  }

  const requestType = action === "install" ? "InstallOSUpdate" : "DownloadOSUpdate";
  const label = action === "install" ? "安裝" : "下載";
  if (action === "install" && !confirm(`確定要開始${label}最新的系統更新嗎?\n\n裝置會嘗試安裝,過程中可能需要重新開機,會中斷裝置目前的使用。`)) {
    return;
  }
  const res = await apiFetchJSON("/api/devices/command", "POST", {
    enrollment_id: enrollmentId, request_type: requestType, params: {},
  });
  if (res.ok) {
    alert(`已送出${label}更新指令,裝置連線後會開始處理,可以到「回應記錄」查看進度。`);
  } else {
    alert(`送出${label}更新指令失敗: ` + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function updateDevicesBatchActionsUI() {
  const container = document.getElementById("devices-batch-actions-container");
  if (!osStatusFilterNeedsCheckbox(currentOsStatusFilter) || selectedDeviceItems.size === 0) {
    container.innerHTML = "";
    return;
  }
  const label = currentOsStatusFilter === "install" ? "安裝更新" : "下載更新";
  container.innerHTML = `<button id="devices-batch-os-action-btn" type="button" style="font-size:12px;">批次${label}(${selectedDeviceItems.size})</button>`;
  document.getElementById("devices-batch-os-action-btn").addEventListener("click", batchTriggerOsAction);
}

function toggleDeviceCheckbox(checkbox) {
  if (checkbox.checked) {
    selectedDeviceItems.add(checkbox.dataset.eid);
  } else {
    selectedDeviceItems.delete(checkbox.dataset.eid);
  }
  updateDevicesBatchActionsUI();
}

function toggleSelectAllDeviceCheckboxes(checked) {
  document.querySelectorAll(".device-row-checkbox").forEach((cb) => {
    cb.checked = checked;
    toggleDeviceCheckbox(cb);
  });
}

async function batchTriggerOsAction() {
  const action = currentOsStatusFilter; // "download" 或 "install"
  const label = action === "install" ? "安裝更新" : "下載更新";
  const requestType = action === "install" ? "InstallOSUpdate" : "DownloadOSUpdate";
  const enrollmentIds = [...selectedDeviceItems];

  const confirmMsg = action === "install"
    ? `確定要批次對這 ${enrollmentIds.length} 台裝置開始安裝最新的系統更新嗎?\n\n每台裝置都會嘗試安裝,過程中可能需要重新開機,會中斷裝置目前的使用。`
    : `確定要批次對這 ${enrollmentIds.length} 台裝置開始下載最新的系統更新嗎?`;
  if (!confirm(confirmMsg)) return;

  document.getElementById("devices-batch-progress-title").textContent = `批次${label}中...`;
  document.getElementById("devices-batch-progress-bar").style.width = "0%";
  document.getElementById("devices-batch-progress-text").textContent = `準備處理 ${enrollmentIds.length} 台...`;
  document.getElementById("devices-batch-progress-errors").innerHTML = "";
  document.getElementById("devices-batch-progress-close-btn").style.display = "none";
  openModal("devices-batch-progress-modal");

  let successCount = 0;
  const errors = [];

  for (let i = 0; i < enrollmentIds.length; i++) {
    const eid = enrollmentIds[i];
    const row = allDeviceRows.find((r) => r.enrollment_id === eid);
    const serialLabel = row ? row.serial_number : eid;

    const res = await apiFetchJSON("/api/devices/command", "POST", {
      enrollment_id: eid, request_type: requestType, params: {},
    });

    if (res.ok) {
      successCount++;
    } else {
      errors.push(`${serialLabel}: ${(res.data && res.data.message) || "未知錯誤"}`);
    }

    const progressPct = Math.round(((i + 1) / enrollmentIds.length) * 100);
    document.getElementById("devices-batch-progress-bar").style.width = `${progressPct}%`;
    document.getElementById("devices-batch-progress-text").textContent =
      `已處理 ${i + 1}/${enrollmentIds.length}(成功 ${successCount},失敗 ${errors.length})`;
  }

  document.getElementById("devices-batch-progress-title").textContent = `批次${label}完成`;
  if (errors.length > 0) {
    document.getElementById("devices-batch-progress-errors").innerHTML =
      `<strong>失敗項目:</strong><br>` + errors.map((e) => escapeHtml(e)).join("<br>");
  }
  document.getElementById("devices-batch-progress-close-btn").style.display = "";

  selectedDeviceItems.clear();
}

function statusBadge(status) {
  if (!status) return `<span class="badge warn">等待中</span>`;
  const map = { Acknowledged: "ok", Error: "warn", NotNow: "warn", Idle: "ok" };
  const cls = map[status] || "warn";
  return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
}

function renderDetailValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return `<pre style="white-space:pre-wrap; margin:0; font-size:12px;">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  return escapeHtml(String(value));
}

async function openCommandHistory(enrollmentId, serial) {
  document.getElementById("history-modal-target").textContent = `${serial} (${enrollmentId})`;
  const body = document.getElementById("history-modal-body");
  body.innerHTML = "載入中...";
  openModal("history-modal");

  const res = await apiFetch(`/api/devices/command-history/${encodeURIComponent(enrollmentId)}`);
  if (!res.ok) {
    body.innerHTML = `<p style="color:#d64545;">取得失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  if (res.data.rows.length === 0) {
    body.innerHTML = `<p style="color:#6b7280;">這台裝置目前沒有任何指令紀錄</p>`;
    return;
  }

  let html = "";
  res.data.rows.forEach((row, idx) => {
    const detailKeys = Object.keys(row.detail || {});
    const detailId = `history-detail-${idx}`;
    let detailHtml = "";
    if (detailKeys.length > 0) {
      detailHtml = `<table class="kv-table">` + detailKeys.map((k) =>
        `<tr><td class="k">${escapeHtml(k)}</td><td>${renderDetailValue(row.detail[k])}</td></tr>`
      ).join("") + `</table>`;
    } else {
      detailHtml = `<p style="color:#6b7280; font-size:13px;">(沒有額外回應內容)</p>`;
    }
    let appInfoHtml = "";
    if (row.app_info) {
      if (row.app_info.name && row.app_info.bundle_id) {
        appInfoHtml = ` <span style="color:#374151; font-weight:normal;">— ${escapeHtml(row.app_info.name)} (${escapeHtml(row.app_info.bundle_id)})</span>`;
      } else if (row.app_info.bundle_id) {
        appInfoHtml = ` <span style="color:#6b7280; font-weight:normal;">— ${escapeHtml(row.app_info.bundle_id)}(VPP清單裡查無軟體名稱)</span>`;
      } else if (row.app_info.adam_id) {
        appInfoHtml = ` <span style="color:#6b7280; font-weight:normal;">— adamId ${escapeHtml(String(row.app_info.adam_id))}(VPP清單裡查無對照資訊,請確認VPP授權清單是否已同步)</span>`;
      }
    }
    html += `
      <div style="border-bottom:1px solid var(--border-color); padding:10px 0;">
        <div style="display:flex; justify-content:space-between; align-items:center; cursor:pointer;" onclick="document.getElementById('${detailId}').classList.toggle('hidden')">
          <div>
            <strong>${escapeHtml(row.request_type)}</strong>${appInfoHtml}
            ${statusBadge(row.status)}
          </div>
          <span style="font-size:12px; color:#6b7280;">${escapeHtml(row.created_at || "")}</span>
        </div>
        <div id="${detailId}" class="hidden" style="margin-top:8px;">${detailHtml}</div>
      </div>
    `;
  });
  body.innerHTML = html;
}

function populateDevicesFilterGroupDropdown() {
  const select = document.getElementById("devices-filter-group");
  const current = select.value;
  select.innerHTML = `<option value="">(全部群組)</option><option value="__none__">(未分類)</option>`;
  groupNamesList.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  select.value = current || "";
}

function applyDevicesFilters() {
  const groupFilter = document.getElementById("devices-filter-group").value;
  const searchText = document.getElementById("devices-filter-search").value.trim().toLowerCase();
  currentOsStatusFilter = document.getElementById("devices-filter-os-status").value;

  let filtered = allDeviceRows.filter((row) => {
    if (groupFilter === "__none__" && row.group) return false;
    if (groupFilter && groupFilter !== "__none__" && row.group !== groupFilter) return false;
    if (searchText) {
      const haystack = `${row.serial_number} ${row.device_name}`.toLowerCase();
      if (!haystack.includes(searchText)) return false;
    }
    if (!deviceMatchesOsStatusFilter(row, currentOsStatusFilter)) return false;
    return true;
  });

  filtered = devicesSorter.sortRows(filtered, DEVICES_TABLE_COLUMNS);

  // 篩選條件改變後,清空之前的勾選狀態(避免勾選的裝置已經不在目前篩選結果裡,卻還留著看不到的勾選)
  selectedDeviceItems.clear();

  renderDevicesTableHeader();
  const tbody = document.getElementById("devices-tbody");
  tbody.innerHTML = "";
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="13">沒有符合條件的裝置</td></tr>`;
  } else {
    filtered.forEach((row) => tbody.appendChild(renderRow(row)));
  }
  document.getElementById("devices-filter-count").textContent = `顯示 ${filtered.length} / ${allDeviceRows.length} 台`;
  updateDevicesBatchActionsUI();
}

async function loadDevices() {
  const tbody = document.getElementById("devices-tbody");
  tbody.innerHTML = `<tr><td colspan="13">載入中...</td></tr>`;
  await loadGroupNames();
  populateDevicesFilterGroupDropdown();
  const res = await apiFetch("/api/devices");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="13" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  allDeviceRows = res.data.rows;
  document.getElementById("devices-status-sync-info").textContent = res.data.status_last_sync
    ? `狀態資料同步於: ${res.data.status_last_sync}`
    : "狀態資料尚未同步過";
  applyDevicesFilters();
}

async function syncOneDevice(btn) {
  const serial = btn.dataset.serial;
  const enrollmentId = btn.dataset.eid;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "同步中...";

  const res = await apiFetchJSON("/api/devices/sync-one", "POST", {
    serial, enrollment_id: enrollmentId,
  });

  btn.disabled = false;
  btn.textContent = originalText;

  if (res.ok) {
    loadDevices(); // 重新整理表格,顯示剛剛更新的快取內容
  } else {
    alert(`同步失敗: ` + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function syncDevicesStatus() {
  const deviceCount = allDeviceRows.length;
  const confirmMsg = deviceCount > 0
    ? `即將對全部 ${deviceCount} 台裝置重新查詢狀態,依裝置數量跟連線狀況,可能需要一段時間才能完成,過程中請不要關閉這個頁面。\n\n確定要繼續嗎?`
    : `即將對所有裝置重新查詢狀態,依裝置數量跟連線狀況,可能需要一段時間才能完成,過程中請不要關閉這個頁面。\n\n確定要繼續嗎?`;
  if (!confirm(confirmMsg)) return;

  const btn = document.getElementById("sync-status-btn");
  const infoSpan = document.getElementById("devices-status-sync-info");
  btn.disabled = true;
  btn.textContent = "同步中...";
  infoSpan.textContent = "正在重新查詢所有裝置的最新狀態...";

  const es = new EventSource(apiUrl("/api/devices/status-sync-stream"));
  es.onmessage = (event) => {
    let update;
    try { update = JSON.parse(event.data); } catch (e) { return; }
    if (update.error) {
      infoSpan.textContent = `同步失敗: ${update.error}`;
      btn.disabled = false;
      btn.textContent = "立即同步裝置狀態";
      es.close();
      return;
    }
    if (update.done) {
      infoSpan.textContent = `狀態資料同步於: ${update.last_sync}`;
      btn.disabled = false;
      btn.textContent = "立即同步裝置狀態";
      es.close();
      loadDevices(); // 重新整理表格,顯示剛剛更新的快取內容
    } else if (update.message) {
      infoSpan.textContent = update.message;
    }
  };
  es.onerror = () => {
    btn.disabled = false;
    btn.textContent = "立即同步裝置狀態";
    es.close();
  };
}

function friendlyLabel(key) {
  const map = {
    serial_number: "裝置序號",
    description: "描述",
    model: "型號",
    os: "系統",
    device_family: "裝置類型",
    color: "顏色",
    profile_uuid: "描述檔 UUID",
    profile_assign_time: "描述檔指派時間",
    profile_push_time: "描述檔推送時間",
    profile_status: "描述檔狀態",
    device_assigned_by: "指派人",
    device_assigned_date: "指派日期",
    response_status: "回應狀態",
  };
  return map[key] || key;
}

function formatBattery(level) {
  if (level === null || level === undefined) return null;
  return `${Math.round(level * 100)}%`;
}

function formatCapacity(queryResponses) {
  const total = queryResponses.DeviceCapacity;
  const available = queryResponses.AvailableDeviceCapacity;
  if (total === undefined || total === null) return null;
  const usedText = available !== undefined && available !== null
    ? `已使用 ${(total - available).toFixed(1)} GB / `
    : "";
  return `${usedText}總容量 ${Number(total).toFixed(1)} GB`;
}

function formatDownloadSize(bytes) {
  if (bytes === undefined || bytes === null) return "";
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function renderLiveInfoSection(deviceInfoData, osUpdatesData, enrollmentId) {
  if ((!deviceInfoData || !deviceInfoData.found) && (!osUpdatesData || !osUpdatesData.found)) {
    return `
      <p style="color:#9ca3af; font-size:13px;">
        目前沒有任何裝置回報過的即時資訊(電量、系統版本、可更新版本等)。按下方「重新查詢」送出 MDM 查詢,
        裝置連線後會自動回報,屆時重新打開這個視窗即可看到。
      </p>
    `;
  }

  const qr = (deviceInfoData && deviceInfoData.found) ? (deviceInfoData.data || {}) : {};
  const osVersionText = qr.BuildVersion ? `${qr.OSVersion || ""} (Build ${qr.BuildVersion})` : (qr.OSVersion || "");

  const rows = [
    ["目前作業系統版本", osVersionText || null],
    ["總容量/已使用容量", formatCapacity(qr)],
    ["電量", formatBattery(qr.BatteryLevel)],
    ["是否監管", qr.IsSupervised === undefined ? null : (qr.IsSupervised ? "是" : "否")],
    ["WIFI MAC", qr.WiFiMAC],
  ];

  let rowsHtml = rows
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([label, value]) => `<tr><td class="k">${escapeHtml(label)}</td><td>${escapeHtml(String(value))}</td></tr>`)
    .join("");

  // 可更新版本(只顯示最新的一筆,通常AvailableOSUpdates也只會回一筆最新的)
  if (osUpdatesData && osUpdatesData.found && Array.isArray(osUpdatesData.data) && osUpdatesData.data.length > 0) {
    const update = osUpdatesData.data[0];
    const critical = update.IsCritical ? `<span class="badge warn" style="margin-left:4px;">重要</span>` : "";
    const restartNote = update.RestartRequired ? "、需要重新開機" : "";
    const versionLabel = `${update.HumanReadableName || update.ProductName || ""}${update.Version ? " (" + update.Version + ")" : ""}`;
    rowsHtml += `<tr><td class="k">可更新版本</td><td>${escapeHtml(versionLabel)}${critical}<span style="color:#9ca3af; font-size:11px;">${escapeHtml(formatDownloadSize(update.DownloadSize) ? " · 下載大小 " + formatDownloadSize(update.DownloadSize) + restartNote : "")}</span></td></tr>`;
  } else if (osUpdatesData && osUpdatesData.found) {
    rowsHtml += `<tr><td class="k">可更新版本</td><td style="color:#9ca3af;">目前已是最新版本,或尚未查到可更新項目</td></tr>`;
  }

  const updatedAtParts = [];
  if (deviceInfoData && deviceInfoData.found) updatedAtParts.push(`裝置資訊: ${deviceInfoData.result_updated_at}`);
  if (osUpdatesData && osUpdatesData.found) updatedAtParts.push(`可更新版本: ${osUpdatesData.result_updated_at}`);

  return `
    <table class="kv-table">${rowsHtml}</table>
    <p style="color:#9ca3af; font-size:12px; margin-top:8px;">
      資料更新時間: ${escapeHtml(updatedAtParts.join(" ・ ") || "-")}(來自裝置最近一次回報的結果,不是即時抓取)
    </p>
  `;
}

async function requeryLiveInfo(enrollmentId) {
  const btn = document.getElementById("details-requery-btn");
  const liveContainer = document.getElementById("details-live-info-container");
  btn.disabled = true;
  btn.textContent = "送出中...";

  // 同時查詢裝置資訊、以及可更新版本。AvailableOSUpdates如果從沒被掃描過會查不到任何資料,
  // 所以要先送 ScheduleOSUpdateScan(強制掃描)讓裝置去跟Apple確認一次,才能讓後續的
  // AvailableOSUpdates查詢真的有資料可回報(這是Apple官方論壇證實的已知行為,不是bug)。
  const results = await Promise.all([
    apiFetchJSON("/api/devices/command", "POST", { enrollment_id: enrollmentId, request_type: "DeviceInformation", params: {} }),
    apiFetchJSON("/api/devices/command", "POST", { enrollment_id: enrollmentId, request_type: "ScheduleOSUpdateScan", params: { Force: "true" } }),
    apiFetchJSON("/api/devices/command", "POST", { enrollment_id: enrollmentId, request_type: "AvailableOSUpdates", params: {} }),
  ]);

  btn.disabled = false;
  btn.textContent = "重新查詢";

  const allOk = results.every((r) => r.ok);
  if (allOk) {
    liveContainer.innerHTML = `
      <p style="color:#1c7c3f; font-size:13px;">
        已送出查詢請求(裝置資訊 + 強制掃描更新 + 查詢可更新版本),裝置連線後會自動回報。
        掃描更新版本需要裝置實際連上 Apple 伺服器確認,可能要等裝置檢查入(check-in)完成才會有結果,
        請稍後重新打開這個視窗查看。
      </p>
    `;
  } else {
    const failMsgs = results.filter((r) => !r.ok).map((r) => (r.data && r.data.message) || "未知錯誤");
    alert("部分查詢送出失敗: " + failMsgs.join("; "));
  }
}

async function openDeviceDetails(serial, enrollmentId) {
  document.getElementById("details-modal-title").textContent = `裝置詳細資訊 - ${serial}`;
  document.getElementById("details-modal-body").innerHTML = `
    <h3 style="margin-top:0;">ASM / DEP 靜態資訊</h3>
    <div id="details-static-container">載入中...</div>

    <h3 style="margin-top:20px; border-top:1px solid var(--border-color); padding-top:14px;">
      裝置即時資訊
      <button id="details-requery-btn" class="secondary" type="button" style="float:right; font-size:12px;" ${enrollmentId ? "" : "disabled"}>重新查詢</button>
    </h3>
    <div id="details-live-info-container">載入中...</div>

    <h3 style="margin-top:20px; border-top:1px solid var(--border-color); padding-top:14px; color:#b42318;">
      裝置退場
    </h3>
    <p style="color:#6b7280; font-size:12px; margin-top:4px;">
      這台裝置要離開學校管理範圍時使用(暫時收回、可能之後重新配發的情境)。會在 ASM 解除指派(裝置仍留在 ASM 名冊,不是永久釋出)、
      清除 nanomdm 的註冊紀錄、並清理這套系統自己的本地資料,不會遠端清除裝置上的資料,也不會影響裝置本身目前已安裝的內容。
    </p>
    <button id="offboard-device-btn" class="danger" type="button" style="margin-top:8px;">開始裝置退場</button>
    <div id="offboard-progress-container" style="margin-top:12px;"></div>
  `;
  openModal("details-modal");

  document.getElementById("offboard-device-btn").addEventListener("click", () => startDeviceOffboard(serial, enrollmentId));
  document.getElementById("details-requery-btn").addEventListener("click", () => requeryLiveInfo(enrollmentId));

  const [staticRes, liveRes, osUpdatesRes] = await Promise.all([
    apiFetch(`/api/devices/details/${encodeURIComponent(serial)}`),
    enrollmentId ? apiFetch(`/api/devices/latest-info/${encodeURIComponent(enrollmentId)}?type=DeviceInformation`) : Promise.resolve({ ok: true, data: { found: false } }),
    enrollmentId ? apiFetch(`/api/devices/latest-info/${encodeURIComponent(enrollmentId)}?type=AvailableOSUpdates`) : Promise.resolve({ ok: true, data: { found: false } }),
  ]);

  const staticContainer = document.getElementById("details-static-container");
  if (!staticRes.ok) {
    staticContainer.innerHTML = `<p style="color:#d64545;">取得失敗: ${escapeHtml((staticRes.data && staticRes.data.message) || "未知錯誤")}</p>`;
  } else {
    const data = staticRes.data.data || {};
    let rows = "";
    Object.keys(data).forEach((key) => {
      rows += `<tr><td class="k">${escapeHtml(friendlyLabel(key))}</td><td>${escapeHtml(String(data[key]))}</td></tr>`;
    });
    staticContainer.innerHTML = `<table class="kv-table">${rows}</table>`;
  }

  const liveContainer = document.getElementById("details-live-info-container");
  if (!enrollmentId) {
    liveContainer.innerHTML = `<p style="color:#9ca3af; font-size:13px;">這台裝置還沒有 MDM UUID(尚未完成 MDM 註冊),無法查詢即時資訊。</p>`;
    return;
  }

  if (!liveRes.ok && !osUpdatesRes.ok) {
    liveContainer.innerHTML = `<p style="color:#d64545;">取得失敗: ${escapeHtml((liveRes.data && liveRes.data.message) || "未知錯誤")}</p>`;
  } else {
    liveContainer.innerHTML = renderLiveInfoSection(liveRes.ok ? liveRes.data : null, osUpdatesRes.ok ? osUpdatesRes.data : null, enrollmentId);
  }
}

let vppAppsListCache = null;

async function loadVppAppsListForDatalist() {
  if (vppAppsListCache) return vppAppsListCache;
  const res = await apiFetch("/api/vpp-apps-list");
  vppAppsListCache = (res.ok && res.data.apps) ? res.data.apps : [];
  return vppAppsListCache;
}

async function renderCommandFields(requestType) {
  const container = document.getElementById("command-fields-container");
  container.innerHTML = "";
  const def = window.COMMAND_DEFS[requestType];
  if (!def) return;

  const needsAppList = requestType === "InstallApplication" || requestType === "RemoveApplication";
  const apps = needsAppList ? await loadVppAppsListForDatalist() : [];

  def.fields.forEach((field) => {
    const div = document.createElement("div");
    div.className = "field-row";

    let datalistHtml = "";
    let listAttr = "";
    if (requestType === "InstallApplication" && field.name === "iTunesStoreID") {
      listAttr = ` list="vpp-app-datalist-adamid"`;
      datalistHtml = `<datalist id="vpp-app-datalist-adamid">${apps.map((a) => `<option value="${escapeHtml(a.adam_id)}">${escapeHtml(a.name)}(剩餘授權 ${escapeHtml(a.available)})</option>`).join("")}</datalist>`;
    } else if (requestType === "RemoveApplication" && field.name === "Identifier") {
      listAttr = ` list="vpp-app-datalist-bundleid"`;
      datalistHtml = `<datalist id="vpp-app-datalist-bundleid">${apps.map((a) => `<option value="${escapeHtml(a.bundle_id)}">${escapeHtml(a.name)}</option>`).join("")}</datalist>`;
    }

    div.innerHTML = `
      <label>${escapeHtml(field.label)}</label>
      <input type="text" data-field="${escapeHtml(field.name)}" value="${escapeHtml(field.default || "")}"${listAttr}>
      ${datalistHtml}
    `;
    container.appendChild(div);
  });
}

function populateCommandSelect() {
  const select = document.getElementById("command-select");
  select.innerHTML = "";
  Object.keys(window.COMMAND_DEFS).forEach((key) => {
    const def = window.COMMAND_DEFS[key];
    if (def.hidden) return; // 內部指令(裝置詳細資訊彈窗按鈕直接呼叫用),不出現在這個選單
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = def.label + (def.danger ? " ⚠️" : "");
    select.appendChild(opt);
  });
  select.addEventListener("change", () => renderCommandFields(select.value));
  renderCommandFields(select.value);
}

function openCommandModal(enrollmentId, serial) {
  currentCommandEnrollmentId = enrollmentId;
  currentCommandSerial = serial;
  document.getElementById("command-modal-target").textContent = `${serial} (${enrollmentId})`;
  populateCommandSelect();
  openModal("command-modal");
}

async function sendCommand() {
  const select = document.getElementById("command-select");
  const requestType = select.value;
  const def = window.COMMAND_DEFS[requestType];

  if (def.danger) {
    const confirmed = confirm(`「${def.label}」是危險操作,確定要對這台裝置執行嗎?`);
    if (!confirmed) return;
  }

  const params = {};
  document.querySelectorAll("#command-fields-container [data-field]").forEach((input) => {
    params[input.dataset.field] = input.value;
  });

  const btn = document.getElementById("command-send-btn");
  btn.disabled = true;
  btn.textContent = "送出中...";

  const res = await apiFetchJSON("/api/devices/command", "POST", {
    enrollment_id: currentCommandEnrollmentId,
    serial_number: currentCommandSerial,
    request_type: requestType,
    params: params,
  });

  btn.disabled = false;
  btn.textContent = "送出";

  if (res.ok) {
    alert("指令已送出並觸發 push");
    closeModal("command-modal");
    loadDevices(); // 確保頁面資料是最新的
  } else {
    alert("送出失敗: " + JSON.stringify(res.data));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadDevices();
  document.getElementById("refresh-devices-btn").addEventListener("click", loadDevices);
  document.getElementById("sync-status-btn").addEventListener("click", syncDevicesStatus);
  document.getElementById("command-send-btn").addEventListener("click", sendCommand);
  document.getElementById("devices-filter-group").addEventListener("change", applyDevicesFilters);
  document.getElementById("devices-filter-search").addEventListener("input", applyDevicesFilters);
  document.getElementById("devices-filter-os-status").addEventListener("change", applyDevicesFilters);

  document.getElementById("devices-thead").addEventListener("change", (e) => {
    if (e.target.id === "devices-select-all-checkbox") {
      toggleSelectAllDeviceCheckboxes(e.target.checked);
    }
  });

  document.getElementById("devices-tbody").addEventListener("change", (e) => {
    if (e.target.classList.contains("device-row-checkbox")) {
      toggleDeviceCheckbox(e.target);
    }
  });

  document.getElementById("devices-batch-progress-close-btn").addEventListener("click", () => {
    closeModal("devices-batch-progress-modal");
  });

  document.getElementById("devices-thead").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th || !th.dataset.sortKey) return;
    devicesSorter.handleHeaderClick(th.dataset.sortKey);
    applyDevicesFilters();
  });

  document.getElementById("devices-tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || !tr.dataset.serial) return;

    if (e.target.classList.contains("serial-link")) {
      openDeviceDetails(tr.dataset.serial, tr.dataset.enrollmentId);
    } else if (e.target.classList.contains("send-command-btn")) {
      openCommandModal(tr.dataset.enrollmentId, tr.dataset.serial);
    } else if (e.target.classList.contains("history-btn")) {
      openCommandHistory(tr.dataset.enrollmentId, tr.dataset.serial);
    } else if (e.target.classList.contains("os-action-btn")) {
      triggerOsAction(tr.dataset.enrollmentId, e.target.dataset.action);
    } else if (e.target.classList.contains("show-location-btn")) {
      openLocationMapModal(e.target.dataset.lat, e.target.dataset.lng, e.target.dataset.at, e.target.dataset.accuracy);
    } else if (e.target.classList.contains("sync-one-btn")) {
      syncOneDevice(e.target);
    }
  });
});
