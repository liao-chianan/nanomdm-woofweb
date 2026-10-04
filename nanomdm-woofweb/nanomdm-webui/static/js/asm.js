let vppEventSource = null;

// 排序/搜尋用:allVppRows保存伺服器回傳的完整資料,畫面上顯示的是它經過搜尋、排序之後的結果
const vppSorter = createTableSorter();
const VPP_TABLE_COLUMNS = [
  { key: "Adam ID", type: "number" },     // 純數字,用數值比較(文字比較會讓 "1000" 排在 "99" 前面)
  { key: "Bundle ID", type: "text" },
  { key: "軟體名稱", type: "text" },
  { key: "版本日期", type: "text" },       // ISO格式(YYYY-MM-DD),文字排序就等同日期先後
];
let allVppRows = [];

// check_vpp_license.sh 到App Store查不到這個App(通常是已經下架)時,Bundle ID跟軟體名稱都會填入這個字樣
const VPP_UNKNOWN_LABEL = "未知";

function isUnknownVppRow(row) {
  return (row["軟體名稱"] || "").trim() === VPP_UNKNOWN_LABEL || (row["Bundle ID"] || "").trim() === VPP_UNKNOWN_LABEL;
}

function renderVppTable(rows, emptyMessage = "尚無資料,請按「立即同步軟體」執行第一次同步") {
  const tbody = document.getElementById("vpp-tbody");
  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9">${escapeHtml(emptyMessage)}</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const adamId = row["Adam ID"] || "";
    const isAutoUpdate = row["自動更新"] === "true";
    tr.innerHTML = `
      <td style="font-family: var(--mono);">${escapeHtml(adamId)}</td>
      <td style="font-family: var(--mono);">${bundleIdHtml(adamId, row["Bundle ID"] || "")}</td>
      <td>
        <div class="app-name-cell">
          ${appIconHtml(adamId)}
          <span>${escapeHtml(row["軟體名稱"] || "")}</span>
        </div>
      </td>
      <td>${row["當下版本"] ? escapeHtml(row["當下版本"]) : '<span style="color:#9ca3af;">(查無資料)</span>'}</td>
      <td>${row["版本日期"] ? escapeHtml(row["版本日期"]) : '<span style="color:#9ca3af;">(查無資料)</span>'}</td>
      <td>${escapeHtml(row["總數量"] || "")}</td>
      <td>${escapeHtml(row["剩餘量"] || "")}</td>
      <td><button class="secondary app-update-btn" type="button" style="font-size:12px;" data-adam-id="${escapeHtml(adamId)}" data-app-name="${escapeHtml(row["軟體名稱"] || "")}">更新 App</button></td>
      <td style="text-align:center;"><input type="checkbox" class="app-auto-update-checkbox" data-adam-id="${escapeHtml(adamId)}" ${isAutoUpdate ? "checked" : ""}></td>
    `;
    tbody.appendChild(tr);
  });
  attachAppIconFallback(tbody);
}

// Bundle ID:有Adam ID的話,做成連到App Store介紹頁的連結(另開新分頁)。
// App Store網頁可以直接用「id{Adam ID}」開啟(不需要知道App名稱的網址片段),
// 國別帶tw,跟版本/圖示查詢用的是同一個商店。沒有Adam ID時沒辦法產生連結,顯示純文字
function bundleIdHtml(adamId, bundleId) {
  if (!bundleId) return "";
  // 沒有Adam ID、或是「未知」(已下架的App,App Store頁面已經不存在)時,沒辦法/不必產生連結
  if (!/^[0-9]+$/.test(adamId) || bundleId.trim() === VPP_UNKNOWN_LABEL) return escapeHtml(bundleId);
  const url = `https://apps.apple.com/tw/app/id${adamId}`;
  return `<a class="bundle-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="在 App Store 開啟">${escapeHtml(bundleId)}</a>`;
}

// 依照目前的搜尋文字(比對Bundle ID、軟體名稱,不分大小寫)、「隱藏未知App」勾選狀態、
// 排序設定,重新產生表格。處理順序:搜尋 -> 隱藏未知 -> 排序
function applyVppFilters() {
  const searchText = document.getElementById("vpp-filter-search").value.trim().toLowerCase();
  const hideUnknown = document.getElementById("vpp-hide-unknown").checked;

  let filtered = allVppRows;
  if (searchText) {
    filtered = filtered.filter((row) => {
      const haystack = `${row["Bundle ID"] || ""} ${row["軟體名稱"] || ""}`.toLowerCase();
      return haystack.includes(searchText);
    });
  }

  // 被隱藏的筆數只算「符合搜尋條件、卻因為勾選而看不到」的那幾筆,
  // 這樣畫面上的「已隱藏N筆」才能確實對應到「取消勾選之後會多出來幾筆」
  let hiddenCount = 0;
  if (hideUnknown) {
    const visible = filtered.filter((row) => !isUnknownVppRow(row));
    hiddenCount = filtered.length - visible.length;
    filtered = visible;
  }
  filtered = vppSorter.sortRows(filtered, VPP_TABLE_COLUMNS);

  let emptyMessage;   // undefined = 沿用renderVppTable預設的「尚無資料,請按立即同步」
  if (allVppRows.length > 0) {
    emptyMessage = hiddenCount > 0
      ? `沒有符合條件的軟體(另有 ${hiddenCount} 筆未知 App 已隱藏,取消勾選「隱藏未知 App」可以顯示)`
      : "沒有符合搜尋條件的軟體";
  }
  renderVppTable(filtered, emptyMessage);

  let countText = "";
  if (allVppRows.length > 0) {
    countText = (searchText || hiddenCount > 0)
      ? `${searchText ? "符合" : "顯示"} ${filtered.length} / 共 ${allVppRows.length} 筆`
      : `共 ${allVppRows.length} 筆`;
    if (hiddenCount > 0) countText += `(已隱藏 ${hiddenCount} 筆未知 App)`;
  }
  document.getElementById("vpp-filter-count").textContent = countText;
  updateVppSortArrows();
}

function updateVppSortArrows() {
  document.querySelectorAll("#vpp-table th.sortable").forEach((th) => {
    th.querySelector(".sort-arrow").textContent = vppSorter.sortArrow(th.dataset.sortKey);
  });
}

// App圖示:顯示在名稱左側。圖片由後端的 /api/app-icon/<adamId> 提供
// (快取在專案的app_image目錄,還沒快取時後端會即時到App Store抓)
function appIconHtml(adamId) {
  if (!adamId) return `<span class="app-icon app-icon-placeholder"></span>`;
  const src = apiUrl("/api/app-icon/" + encodeURIComponent(adamId));
  return `<img class="app-icon" src="${escapeHtml(src)}" alt="" loading="lazy">`;
}

// 抓不到圖示(後端回404,例如這個App沒有App Store頁面)時,換成灰色佔位方塊,版面才不會跑掉。
// error事件不會往上冒泡,所以要對每一張圖片各自綁定
function attachAppIconFallback(container) {
  container.querySelectorAll("img.app-icon").forEach((img) => {
    img.addEventListener("error", () => {
      const placeholder = document.createElement("span");
      placeholder.className = "app-icon app-icon-placeholder";
      img.replaceWith(placeholder);
    }, { once: true });
  });
}

async function loadVppCache() {
  const syncEl = document.getElementById("vpp-last-sync");
  const res = await apiFetch("/api/asm/cache");
  if (res.ok) {
    syncEl.textContent = res.data.last_sync
      ? `最後同步時間: ${res.data.last_sync}`
      : "尚未同步過,請按「立即同步軟體」";
    allVppRows = res.data.rows || [];
    applyVppFilters();
  } else {
    syncEl.textContent = "無法載入快取資料";
  }
}

function runVppQuery() {
  const progressPanel = document.getElementById("vpp-progress-panel");
  const outputEl = document.getElementById("vpp-output");
  const statusEl = document.getElementById("vpp-status");
  const btn = document.getElementById("run-vpp-btn");

  progressPanel.classList.remove("hidden");
  outputEl.textContent = "";
  statusEl.textContent = "查詢中... (這可能需要一段時間,請耐心等候,結果會逐行顯示)";
  btn.disabled = true;
  btn.textContent = "查詢中...";

  if (vppEventSource) {
    vppEventSource.close();
  }

  debugLog("SSE 開始連線", "/api/asm/stream");
  vppEventSource = new EventSource(apiUrl("/api/asm/stream"));

  vppEventSource.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (payload.line !== undefined) {
      outputEl.textContent += payload.line + "\n";
      outputEl.scrollTop = outputEl.scrollHeight;
    } else if (payload.done) {
      if (payload.cached) {
        statusEl.textContent = `查詢完成,已更新快取(共 ${payload.count} 筆),最後同步時間: ${payload.last_sync}`;
        loadVppCache();
      } else {
        statusEl.textContent = "查詢完成,但 " + (payload.message || "未取得有效資料,快取未更新");
      }
      debugLog("SSE 完成", payload);
      finishVppStream();
    } else if (payload.error) {
      statusEl.textContent = "查詢發生錯誤: " + payload.error;
      debugLog("SSE 錯誤", payload.error, true);
      finishVppStream();
    }
  };

  vppEventSource.onerror = () => {
    statusEl.textContent = "連線中斷或查詢結束";
    finishVppStream();
  };
}

function finishVppStream() {
  const btn = document.getElementById("run-vpp-btn");
  btn.disabled = false;
  btn.textContent = "立即同步軟體";
  if (vppEventSource) {
    vppEventSource.close();
    vppEventSource = null;
  }
}

function downloadVppCsv() {
  window.location.href = apiUrl("/api/asm/download");
}

async function triggerAppUpdate(adamId, appName) {
  if (!confirm(`確定要對「${appName}」派送更新嗎?\n\n會對所有綁定這個 App 的群組裡的全部裝置,重新派送安裝指令。`)) return;

  document.getElementById("app-update-progress-title").textContent = `派送「${appName}」更新中...`;
  document.getElementById("app-update-progress-bar").style.width = "0%";
  document.getElementById("app-update-progress-text").textContent = "準備中...";
  document.getElementById("app-update-progress-errors").innerHTML = "";
  document.getElementById("app-update-progress-close-btn").style.display = "none";
  openModal("app-update-progress-modal");

  const errors = [];
  const es = new EventSource(apiUrl(`/api/asm/update-app-stream?adam_id=${encodeURIComponent(adamId)}`));

  es.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (e) {
      return;
    }

    if (payload.stage === "progress") {
      const pct = Math.round((payload.current / payload.total) * 100);
      document.getElementById("app-update-progress-bar").style.width = `${pct}%`;
      document.getElementById("app-update-progress-text").textContent = `已處理 ${payload.current}/${payload.total}: ${payload.serial}`;
      if (!payload.ok) {
        errors.push(`${payload.serial}: ${payload.message}`);
      }
    } else if (payload.stage === "done") {
      document.getElementById("app-update-progress-bar").style.width = "100%";
      document.getElementById("app-update-progress-title").textContent = "派送完成";
      document.getElementById("app-update-progress-text").textContent = payload.message || "";
      if (errors.length > 0) {
        document.getElementById("app-update-progress-errors").innerHTML =
          `<strong>失敗項目:</strong><br>` + errors.map((e) => escapeHtml(e)).join("<br>");
      }
      document.getElementById("app-update-progress-close-btn").style.display = "";
      es.close();
    }
  };

  es.onerror = () => {
    document.getElementById("app-update-progress-title").textContent = "連線中斷";
    document.getElementById("app-update-progress-close-btn").style.display = "";
    es.close();
  };
}

async function toggleAppAutoUpdate(adamId, enabled) {
  // 先更新記憶體裡的資料(不等伺服器回應):搜尋/排序會用allVppRows重新產生表格,這裡不同步的話,
  // 剛勾選的App一搜尋或排序,勾選框就會跳回舊的狀態。失敗時下面會重新載入,恢復成實際存檔的狀態
  const cachedRow = allVppRows.find((r) => r["Adam ID"] === adamId);
  if (cachedRow) cachedRow["自動更新"] = enabled ? "true" : "false";

  const res = await apiFetchJSON("/api/asm/toggle-auto-update", "POST", { adam_id: adamId, enabled });
  if (!res.ok) {
    alert("設定失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    loadVppCache(); // 失敗時重新載入,讓勾選框狀態恢復成實際存檔的狀態,不要讓畫面停留在使用者剛點擊、但其實沒儲存成功的狀態
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadVppCache();
  document.getElementById("vpp-filter-search").addEventListener("input", applyVppFilters);
  document.getElementById("vpp-hide-unknown").addEventListener("change", applyVppFilters);
  document.querySelector("#vpp-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    vppSorter.handleHeaderClick(th.dataset.sortKey);
    applyVppFilters();
  });
  document.getElementById("run-vpp-btn").addEventListener("click", runVppQuery);
  document.getElementById("download-vpp-btn").addEventListener("click", downloadVppCsv);

  document.getElementById("vpp-tbody").addEventListener("click", (e) => {
    if (e.target.classList.contains("app-update-btn")) {
      triggerAppUpdate(e.target.dataset.adamId, e.target.dataset.appName);
    }
  });

  document.getElementById("vpp-tbody").addEventListener("change", (e) => {
    if (e.target.classList.contains("app-auto-update-checkbox")) {
      toggleAppAutoUpdate(e.target.dataset.adamId, e.target.checked);
    }
  });

  document.getElementById("app-update-progress-close-btn").addEventListener("click", () => {
    closeModal("app-update-progress-modal");
  });
});
