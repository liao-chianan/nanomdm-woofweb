const NAME_PATTERN_G = /^[^\x00-\x1f\x7f,"]{0,64}$/;
let editingGroupName = null;      // 目前正在編輯的群組(null代表新增)
let currentGroupForDevices = null;
let currentGroupForApps = null;
let commandTarget = null;          // {type:'device', enrollmentId, label} | {type:'group', groupName, label}
let groupNamesList = [];

function buildGroupOptionsHtml(currentGroup) {
  const names = new Set(groupNamesList);
  if (currentGroup) names.add(currentGroup);
  let html = `<option value="">(未分類)</option>`;
  names.forEach((name) => {
    const selected = name === currentGroup ? "selected" : "";
    html += `<option value="${escapeHtml(name)}" ${selected}>${escapeHtml(name)}</option>`;
  });
  return html;
}

// ---------------------------------------------------------------------------
// 群組清單
// ---------------------------------------------------------------------------
async function loadGroups() {
  const tbody = document.getElementById("groups-tbody");
  tbody.innerHTML = `<tr><td colspan="10">載入中...</td></tr>`;
  const res = await apiFetch("/api/groups");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="10" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  document.getElementById("vpp-missing-banner").classList.toggle("hidden", !res.data.vpp_cache_missing);

  tbody.innerHTML = "";
  if (res.data.rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10">尚無群組,請按右上角「新增群組」建立第一個群組</td></tr>`;
    return;
  }
  res.data.rows.forEach((row) => {
    const tr = document.createElement("tr");
    const enrollLink = row.enroll_json
      ? `<a href="${apiUrl('/dep-profiles')}?open=${encodeURIComponent(row.enroll_json)}" class="serial-link" style="text-decoration:underline;">${escapeHtml(row.enroll_json)}</a>`
      : `<span style="color:#9ca3af;">(未指派)</span>`;
    const mobileconfigLink = row.mobileconfig
      ? `<a href="${apiUrl('/profiles')}?open=${encodeURIComponent(row.mobileconfig)}" class="serial-link" style="text-decoration:underline;">${escapeHtml(row.mobileconfig)}</a>`
      : `<span style="color:#9ca3af;">(未指派)</span>`;
    tr.innerHTML = `
      <td>${escapeHtml(row.group_name)}</td>
      <td>${escapeHtml(row.description)}</td>
      <td><span class="serial-link" data-action="devices" data-group="${escapeHtml(row.group_name)}">${row.device_count}</span></td>
      <td><span class="serial-link" data-action="apps" data-group="${escapeHtml(row.group_name)}">${row.app_count}</span></td>
      <td>${enrollLink}</td>
      <td>${mobileconfigLink}</td>
      <td><button class="secondary" data-action="group-command" data-group="${escapeHtml(row.group_name)}" type="button" ${row.device_count === 0 ? "disabled" : ""}>群組命令</button></td>
      <td><button class="secondary" data-action="edit" data-group="${escapeHtml(row.group_name)}" data-description="${escapeHtml(row.description)}" type="button">編輯</button></td>
      <td><button class="secondary" data-action="duplicate-group" data-group="${escapeHtml(row.group_name)}" type="button">再製</button></td>
      <td><button class="danger" data-action="delete" data-group="${escapeHtml(row.group_name)}" type="button">刪除</button></td>
    `;
    tbody.appendChild(tr);
  });
}

async function populateNewGroupFileSelects() {
  const res = await apiFetch("/api/groups/available-files");
  const enrollSelect = document.getElementById("group-edit-enroll-select");
  const mcSelect = document.getElementById("group-edit-mobileconfig-select");
  const enrollHint = document.getElementById("group-edit-enroll-empty-hint");
  const mcHint = document.getElementById("group-edit-mobileconfig-empty-hint");

  enrollSelect.innerHTML = "";
  mcSelect.innerHTML = "";

  const enrollFiles = res.ok ? res.data.available_enroll_json : [];
  const mcFiles = res.ok ? res.data.available_mobileconfig : [];

  enrollHint.classList.toggle("hidden", enrollFiles.length > 0);
  mcHint.classList.toggle("hidden", mcFiles.length > 0);

  enrollFiles.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f; opt.textContent = f;
    enrollSelect.appendChild(opt);
  });
  mcFiles.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f; opt.textContent = f;
    mcSelect.appendChild(opt);
  });
}

async function openGroupEditModal(name, description) {
  editingGroupName = name || null;
  document.getElementById("group-edit-title").textContent = name ? `編輯群組 - ${name}` : "新增群組";
  const nameInput = document.getElementById("group-edit-name");
  nameInput.value = name || "";
  nameInput.readOnly = false; // 名稱可以編輯,改名時會自動同步 devices.csv
  document.getElementById("group-edit-description").value = description || "";

  const newFilesSection = document.getElementById("group-edit-new-files-section");
  if (name) {
    // 編輯既有群組:不在這裡選檔案(檔案配對請到ADE註冊設定/群組描述檔頁面操作)
    newFilesSection.classList.add("hidden");
  } else {
    newFilesSection.classList.remove("hidden");
    await populateNewGroupFileSelects();
  }
  openModal("group-edit-modal");
}

async function saveGroupEdit() {
  const name = document.getElementById("group-edit-name").value.trim();
  const description = document.getElementById("group-edit-description").value.trim();
  if (!NAME_PATTERN_G.test(name) || !name) {
    alert("群組名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內");
    return;
  }

  const isNew = !editingGroupName;
  const payload = {
    group_name: name,
    old_group_name: editingGroupName || "",
    description,
    is_new: isNew,
  };

  if (isNew) {
    const enrollJson = document.getElementById("group-edit-enroll-select").value;
    const mobileconfig = document.getElementById("group-edit-mobileconfig-select").value;
    if (!enrollJson || !mobileconfig) {
      alert("新增群組時必須選擇要使用的註冊檔與描述檔;如果下拉選單是空的,請先到對應頁面新增一份。");
      return;
    }
    payload.enroll_json = enrollJson;
    payload.mobileconfig = mobileconfig;
  }

  const res = await apiFetchJSON("/api/groups/save", "POST", payload);
  if (res.ok) {
    closeModal("group-edit-modal");
    loadGroups();
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function deleteGroup(name) {
  if (!confirm(`確定要刪除群組「${name}」嗎?(不會影響已指派此群組的裝置或 App 資料本身,也不會刪除配對的註冊檔/描述檔案,只會解除配對關係)`)) return;
  const res = await apiFetchJSON("/api/groups/delete", "POST", { group_name: name });
  if (res.ok) {
    loadGroups();
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 複製群組
// ---------------------------------------------------------------------------
function openDuplicateGroupModal(sourceGroup) {
  document.getElementById("group-duplicate-source-label").textContent = sourceGroup;
  document.getElementById("group-duplicate-new-name").value = "";
  document.getElementById("group-duplicate-new-name").dataset.sourceGroup = sourceGroup;
  document.getElementById("group-duplicate-new-description").value = "";
  openModal("group-duplicate-modal");
}

async function confirmDuplicateGroup() {
  const nameInput = document.getElementById("group-duplicate-new-name");
  const sourceGroup = nameInput.dataset.sourceGroup;
  const newGroupName = nameInput.value.trim();
  const newDescription = document.getElementById("group-duplicate-new-description").value.trim();

  if (!NAME_PATTERN_G.test(newGroupName) || !newGroupName) {
    alert("新群組名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內");
    return;
  }

  const btn = document.getElementById("group-duplicate-confirm-btn");
  btn.disabled = true;
  btn.textContent = "複製中...";

  const res = await apiFetchJSON("/api/groups/duplicate", "POST", {
    source_group: sourceGroup, new_group_name: newGroupName, new_description: newDescription,
  });

  btn.disabled = false;
  btn.textContent = "確認複製";

  if (res.ok) {
    closeModal("group-duplicate-modal");
    loadGroups();
    alert(res.data.message);
  } else {
    alert("複製失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 群組裝置清單 (呈現方式比照「所有裝置與命令」)
// ---------------------------------------------------------------------------
function renderGroupDeviceRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.serial = row.serial_number;
  tr.dataset.enrollmentId = row.enrollment_id;

  const batteryText = formatBatteryPct(row.battery_level);
  const batteryCell = batteryText
    ? `<span style="color:${batteryColor(row.battery_level)}; font-weight:600;">${batteryText}</span>`
    : `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;

  const capacityCell = formatCapacityCell(row.device_capacity, row.available_device_capacity)
    || `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;

  let osCell = formatOsCell(row.os_version, row.available_os_version) || `<span style="color:#9ca3af; font-size:11px;">尚無資料</span>`;
  if (row.available_os_version) {
    if (row.os_update_status === "Downloading") {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="downloading" style="font-size:11px; padding:2px 8px; background-color:#d1fae5; border-color:#6ee7b7;">下載中...</button>`;
    } else if (row.os_update_is_downloaded === "true") {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="install" style="font-size:11px; padding:2px 8px; background-color:#fef3c7; border-color:#fde68a;">安裝更新</button>`;
    } else {
      osCell += ` <button class="secondary os-action-btn" type="button" data-action="download" style="font-size:11px; padding:2px 8px;">下載更新</button>`;
    }
  }

  let ipCell = row.ip_address
    ? escapeHtml(row.ip_address)
    : `<span style="color:#9ca3af; font-size:11px;" title="從nanomdm服務的連線紀錄解析取得,裝置最近沒有連線過或還沒同步過就會是空的">尚無資料</span>`;
  if (row.lost_mode_enabled === "true" && row.location_lat && row.location_lng) {
    ipCell += ` <button class="secondary show-location-btn" type="button" style="font-size:11px; padding:2px 8px;" data-lat="${escapeHtml(String(row.location_lat))}" data-lng="${escapeHtml(String(row.location_lng))}" data-at="${escapeHtml(row.location_at || "")}" data-accuracy="${escapeHtml(String(row.location_accuracy || ""))}">遺失定位</button>`;
  }

  tr.innerHTML = `
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
      <button class="secondary send-command-btn" type="button">派送命令</button>
      <button class="secondary history-btn" type="button" style="margin-top:4px;">回應記錄</button>
    </td>
  `;
  return tr;
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
    html += `
      <div style="border-bottom:1px solid var(--border-color); padding:10px 0;">
        <div style="display:flex; justify-content:space-between; align-items:center; cursor:pointer;" onclick="document.getElementById('${detailId}').classList.toggle('hidden')">
          <div>
            <strong>${escapeHtml(row.request_type)}</strong>
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

async function openGroupDevicesModal(groupName) {
  currentGroupForDevices = groupName;
  document.getElementById("group-devices-modal-title").textContent = groupName;
  const tbody = document.getElementById("group-devices-tbody");
  tbody.innerHTML = `<tr><td colspan="12">載入中...</td></tr>`;
  document.getElementById("group-devices-count-info").textContent = "";
  openModal("group-devices-modal");

  await loadGroupNamesList();
  const res = await apiFetch(`/api/groups/${encodeURIComponent(groupName)}/devices`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="12" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  const { rows, registered_count, unregistered_count, total_count } = res.data;
  document.getElementById("group-devices-count-info").innerHTML =
    `已註冊 <strong style="color:#1c7c3f;">${registered_count}</strong> 台 ／ 未註冊 <strong style="color:${unregistered_count > 0 ? '#b45309' : '#9ca3af'};">${unregistered_count}</strong> 台` +
    (total_count > 0 ? `(共指派 ${total_count} 台裝置到這個群組)` : "");

  tbody.innerHTML = "";
  if (rows.length === 0) {
    if (total_count === 0) {
      tbody.innerHTML = `<tr><td colspan="12">這個群組目前沒有指派任何裝置</td></tr>`;
    } else {
      // 群組裡有指派裝置,但一台都還沒完成MDM註冊,所以清單是空的 -> 明確告知原因,不要讓人誤以為是載入失敗或群組真的沒裝置
      tbody.innerHTML = `<tr><td colspan="12" style="color:#b45309;">
        這個群組目前指派了 ${total_count} 台裝置,但都還沒有完成 MDM 註冊,所以這裡還看不到資料。
        請到「裝置註冊狀態」頁確認註冊進度,裝置完成註冊後才會出現在這份清單。
      </td></tr>`;
    }
    return;
  }
  rows.forEach((row) => tbody.appendChild(renderGroupDeviceRow(row)));
}

async function loadGroupNamesList() {
  const res = await apiFetch("/api/groups");
  if (res.ok) {
    groupNamesList = res.data.rows.map((r) => r.group_name);
  }
}

function friendlyLabel(key) {
  const map = {
    serial_number: "裝置序號", description: "描述", model: "型號", os: "系統",
    device_family: "裝置類型", color: "顏色", profile_uuid: "描述檔 UUID",
    profile_assign_time: "描述檔指派時間", profile_push_time: "描述檔推送時間",
    profile_status: "描述檔狀態", device_assigned_by: "指派人",
    device_assigned_date: "指派日期", response_status: "回應狀態",
  };
  return map[key] || key;
}

function batteryColor(level) {
  if (level === null || level === undefined || level === "") return "#374151";
  const pct = Number(level) * 100;
  if (pct >= 80) return "#1c7c3f";
  if (pct >= 30 && pct <= 60) return "#b45309";
  if (pct < 30) return "#d64545";
  return "#374151";
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
    ["總容量/已使用容量", formatCapacityCell(qr.DeviceCapacity, qr.AvailableDeviceCapacity) || null],
    ["電量", (qr.BatteryLevel !== undefined && qr.BatteryLevel !== null) ? `${Math.round(qr.BatteryLevel * 100)}%` : null],
    ["是否監管", qr.IsSupervised === undefined ? null : (qr.IsSupervised ? "是" : "否")],
    ["WIFI MAC", qr.WiFiMAC],
  ];

  let rowsHtml = rows
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([label, value]) => `<tr><td class="k">${escapeHtml(label)}</td><td>${escapeHtml(String(value))}</td></tr>`)
    .join("");

  if (osUpdatesData && osUpdatesData.found && Array.isArray(osUpdatesData.data) && osUpdatesData.data.length > 0) {
    const update = osUpdatesData.data[0];
    const critical = update.IsCritical ? `<span class="badge warn" style="margin-left:4px;">重要</span>` : "";
    const restartNote = update.RestartRequired ? "、需要重新開機" : "";
    const versionLabel = `${update.HumanReadableName || update.ProductName || ""}${update.Version ? " (" + update.Version + ")" : ""}`;
    rowsHtml += `<tr><td class="k">可更新版本</td><td>${escapeHtml(versionLabel)}${critical}<span style="color:#9ca3af; font-size:11px;">${escapeHtml(formatDownloadSize(update.DownloadSize) ? " · 下載大小 " + formatDownloadSize(update.DownloadSize) + restartNote : "")}</span></td></tr>`;
    rowsHtml += `<tr><td class="k">ProductKey</td><td>
      <span style="font-family:var(--mono); font-size:11px; word-break:break-all;">${escapeHtml(update.ProductKey || "")}</span>
      <button class="secondary download-os-update-btn" type="button" data-product-key="${escapeHtml(update.ProductKey || "")}" data-version-label="${escapeHtml(versionLabel)}" style="font-size:11px; margin-left:8px;">1. 下載更新</button>
      <button class="secondary install-os-update-btn" type="button" data-product-key="${escapeHtml(update.ProductKey || "")}" data-version-label="${escapeHtml(versionLabel)}" style="font-size:11px; margin-left:4px;">2. 安裝更新</button>
      <button class="secondary check-os-update-status-btn" type="button" style="font-size:11px; margin-left:4px;">查詢下載狀態</button>
      <div style="color:#9ca3af; font-size:11px; margin-top:4px;">
        iOS/iPadOS 規定下載跟安裝是分開的兩個步驟:請先按「1. 下載更新」,確認裝置下載完成後,再按「2. 安裝更新」。
        如果還沒下載完就按安裝,會收到「安裝前必須先下載」的錯誤。可以到下方「查詢下載狀態」確認是否已下載完成。
      </div>
    </td></tr>`;
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
        已送出查詢請求(裝置資訊 + 強制掃描更新 + 查詢可用版本),裝置連線後會自動回報。
        掃描更新版本需要裝置實際連上 Apple 伺服器確認,可能要等裝置檢查入(check-in)完成才會有結果,
        請稍後重新打開這個視窗查看。
      </p>
    `;
  } else {
    const failMsgs = results.filter((r) => !r.ok).map((r) => (r.data && r.data.message) || "未知錯誤");
    alert("部分查詢送出失敗: " + failMsgs.join("; "));
  }
}

async function downloadOsUpdate(enrollmentId, productKey, versionLabel) {
  const confirmed = confirm(
    `確定要開始下載「${versionLabel}」嗎?\n\n` +
    `這一步只會下載更新檔案,不會安裝、不會重新開機。下載會使用網路流量,依檔案大小可能需要一些時間。\n` +
    `下載完成後,請用「查詢下載狀態」確認,再按「2. 安裝更新」。\n\n` +
    `確定要繼續嗎?`
  );
  if (!confirmed) return;

  const res = await apiFetchJSON("/api/devices/command", "POST", {
    enrollment_id: enrollmentId, request_type: "ScheduleOSUpdate",
    params: { ProductKey: productKey, InstallAction: "DownloadOnly" },
  });

  if (res.ok) {
    alert("已送出下載指令,裝置連線後會開始下載。可以按「查詢下載狀態」確認進度,或到「回應記錄」查看詳細結果。");
  } else {
    alert("送出下載指令失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function installOsUpdate(enrollmentId, productKey, versionLabel) {
  const confirmed = confirm(
    `確定要安裝「${versionLabel}」嗎?\n\n` +
    `這一步會安裝更新(InstallASAP)。iOS/iPadOS 規定安裝前必須已經下載完成,如果還沒下載完會失敗並收到錯誤訊息。\n` +
    `請注意:\n` +
    `・如果裝置有設定密碼,依 Apple 的安全機制,通常還是需要使用者在裝置上輸入密碼才能真正完成安裝,無法保證 100% 全自動靜默完成\n` +
    `・安裝過程可能需要重新開機,會中斷裝置目前的使用\n\n` +
    `確定要繼續嗎?`
  );
  if (!confirmed) return;

  const res = await apiFetchJSON("/api/devices/command", "POST", {
    enrollment_id: enrollmentId, request_type: "ScheduleOSUpdate",
    params: { ProductKey: productKey, InstallAction: "InstallASAP" },
  });

  if (res.ok) {
    alert("已送出安裝指令,裝置連線後會嘗試安裝。可以到「回應記錄」查看進度或錯誤訊息。");
  } else {
    alert("送出安裝指令失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function checkOsUpdateStatus(enrollmentId) {
  const res = await apiFetchJSON("/api/devices/command", "POST", {
    enrollment_id: enrollmentId, request_type: "OSUpdateStatus", params: {},
  });
  if (res.ok) {
    alert("已送出狀態查詢,裝置連線後會回報下載進度。請到「回應記錄」查看結果(裡面會有 IsDownloaded 這個欄位,true 代表已下載完成、可以安裝了)。");
  } else {
    alert("送出查詢失敗: " + ((res.data && res.data.message) || "未知錯誤"));
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
  `;
  openModal("details-modal");

  document.getElementById("details-requery-btn").addEventListener("click", () => requeryLiveInfo(enrollmentId));
  document.getElementById("details-live-info-container").addEventListener("click", (e) => {
    if (e.target.classList.contains("download-os-update-btn")) {
      downloadOsUpdate(enrollmentId, e.target.dataset.productKey, e.target.dataset.versionLabel);
    } else if (e.target.classList.contains("install-os-update-btn")) {
      installOsUpdate(enrollmentId, e.target.dataset.productKey, e.target.dataset.versionLabel);
    } else if (e.target.classList.contains("check-os-update-status-btn")) {
      checkOsUpdateStatus(enrollmentId);
    }
  });

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

// ---------------------------------------------------------------------------
// 派送命令 Modal (跟所有裝置與命令頁相同邏輯)
// ---------------------------------------------------------------------------
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
    if (def.hidden || def.group_excluded) return; // 內部指令,或不適用群組(如修改裝置名稱)的指令
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = def.label + (def.danger ? " ⚠️" : "");
    select.appendChild(opt);
  });
  select.addEventListener("change", () => renderCommandFields(select.value));
  renderCommandFields(select.value);
}

function openCommandModal(enrollmentId, serial) {
  commandTarget = { type: "device", enrollmentId, label: `${serial} (${enrollmentId})` };
  document.getElementById("command-modal-target").textContent = commandTarget.label;
  document.getElementById("command-result-container").innerHTML = "";
  populateCommandSelect();
  openModal("command-modal");
}

function openGroupCommandModal(groupName) {
  commandTarget = { type: "group", groupName, label: `群組「${groupName}」的所有裝置` };
  document.getElementById("command-modal-target").textContent = commandTarget.label;
  document.getElementById("command-result-container").innerHTML = "";
  populateCommandSelect();
  openModal("command-modal");
}

function renderGroupCommandResults(data) {
  const container = document.getElementById("command-result-container");
  let rows = data.results.map((r) => {
    const statusText = r.ok ? "✅ 成功" : `❌ 失敗${r.message ? ": " + escapeHtml(r.message) : ""}`;
    return `<div style="padding:4px 0; border-bottom:1px solid var(--border-color);">
      <span style="font-family:var(--mono);">${escapeHtml(r.serial_number)}</span>
      ${r.device_name ? "(" + escapeHtml(r.device_name) + ")" : ""} - ${statusText}
    </div>`;
  }).join("");
  container.innerHTML = `
    <p style="font-size:13px; color:#6b7280;">送出結果: ${data.success_count} / ${data.total} 台成功</p>
    ${rows}
  `;
}

async function sendCommand() {
  const select = document.getElementById("command-select");
  const requestType = select.value;
  const def = window.COMMAND_DEFS[requestType];
  const confirmScope = commandTarget.type === "group" ? `群組「${commandTarget.groupName}」內的所有裝置` : "這台裝置";
  if (def.danger && !confirm(`「${def.label}」是危險操作,確定要對${confirmScope}執行嗎?`)) return;

  const params = {};
  document.querySelectorAll("#command-fields-container [data-field]").forEach((input) => {
    params[input.dataset.field] = input.value;
  });

  const btn = document.getElementById("command-send-btn");
  btn.disabled = true;
  btn.textContent = "送出中...";
  document.getElementById("command-result-container").innerHTML = "";

  if (commandTarget.type === "group") {
    const res = await apiFetchJSON(`/api/groups/${encodeURIComponent(commandTarget.groupName)}/command`, "POST", {
      request_type: requestType, params,
    });
    btn.disabled = false;
    btn.textContent = "送出";
    if (res.ok) {
      renderGroupCommandResults(res.data);
    } else {
      alert("送出失敗: " + ((res.data && res.data.message) || JSON.stringify(res.data)));
    }
  } else {
    const res = await apiFetchJSON("/api/devices/command", "POST", {
      enrollment_id: commandTarget.enrollmentId, request_type: requestType, params,
    });
    btn.disabled = false;
    btn.textContent = "送出";
    if (res.ok) {
      alert("指令已送出並觸發 push");
      closeModal("command-modal");
    } else {
      alert("送出失敗: " + JSON.stringify(res.data));
    }
  }
}

// ---------------------------------------------------------------------------
// 群組軟體清單
// ---------------------------------------------------------------------------
async function openGroupAppsModal(groupName) {
  currentGroupForApps = groupName;
  document.getElementById("group-apps-modal-title").textContent = groupName;
  const tbody = document.getElementById("group-apps-tbody");
  tbody.innerHTML = `<tr><td colspan="6">載入中...</td></tr>`;
  openModal("group-apps-modal");
  await refreshGroupApps();
}

async function refreshGroupApps() {
  const tbody = document.getElementById("group-apps-tbody");
  const addSelect = document.getElementById("group-apps-add-select");
  const res = await apiFetch(`/api/groups/${encodeURIComponent(currentGroupForApps)}/apps`);
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  if (res.data.assigned_apps.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6">這個群組目前沒有綁定任何 App</td></tr>`;
  } else {
    res.data.assigned_apps.forEach((app) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td style="font-family: var(--mono);">${escapeHtml(app["Adam ID"])}</td>
        <td style="font-family: var(--mono);">${escapeHtml(app["Bundle ID"])}</td>
        <td>${escapeHtml(app["軟體名稱"])}</td>
        <td>${escapeHtml(String(app["總數量"]))}</td>
        <td>${escapeHtml(String(app["剩餘量"]))}</td>
        <td><button class="danger" data-adam-id="${escapeHtml(app["Adam ID"])}" data-action="remove-app" type="button">移除</button></td>
      `;
      tbody.appendChild(tr);
    });
  }

  addSelect.innerHTML = "";
  if (res.data.available_to_add.length === 0) {
    const opt = document.createElement("option");
    opt.textContent = "(沒有其他可加入的 App)";
    opt.disabled = true;
    addSelect.appendChild(opt);
  } else {
    res.data.available_to_add.forEach((app) => {
      const opt = document.createElement("option");
      opt.value = app["Adam ID"];
      opt.textContent = `${app["軟體名稱"]} (${app["Bundle ID"]})`;
      addSelect.appendChild(opt);
    });
  }
}

async function addAppToGroup() {
  const select = document.getElementById("group-apps-add-select");
  const adamId = select.value;
  if (!adamId) return;
  const res = await apiFetchJSON(`/api/groups/${encodeURIComponent(currentGroupForApps)}/apps/add`, "POST", { adam_id: adamId });
  if (res.ok) {
    await refreshGroupApps();
    loadGroups();
  } else {
    alert("加入失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function removeAppFromGroup(adamId) {
  const res = await apiFetchJSON(`/api/groups/${encodeURIComponent(currentGroupForApps)}/apps/remove`, "POST", { adam_id: adamId });
  if (res.ok) {
    await refreshGroupApps();
    loadGroups();
  } else {
    alert("移除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// 事件綁定
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadGroups();

  document.getElementById("new-group-btn").addEventListener("click", () => openGroupEditModal(null, ""));
  document.getElementById("group-edit-save-btn").addEventListener("click", saveGroupEdit);
  document.getElementById("group-duplicate-confirm-btn").addEventListener("click", confirmDuplicateGroup);
  document.getElementById("command-send-btn").addEventListener("click", sendCommand);
  document.getElementById("group-apps-add-btn").addEventListener("click", addAppToGroup);

  document.getElementById("groups-tbody").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const action = el.dataset.action;
    const group = el.dataset.group;
    if (action === "devices") openGroupDevicesModal(group);
    else if (action === "apps") openGroupAppsModal(group);
    else if (action === "group-command") openGroupCommandModal(group);
    else if (action === "edit") openGroupEditModal(group, el.dataset.description);
    else if (action === "duplicate-group") openDuplicateGroupModal(group);
    else if (action === "delete") deleteGroup(group);
  });

  document.getElementById("group-devices-tbody").addEventListener("click", (e) => {
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
    }
  });

  document.getElementById("group-apps-tbody").addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="remove-app"]');
    if (!btn) return;
    removeAppFromGroup(btn.dataset.adamId);
  });
});
