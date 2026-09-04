let vppEventSource = null;

function renderVppTable(rows) {
  const tbody = document.getElementById("vpp-tbody");
  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9">尚無資料,請按「立即同步軟體」執行第一次同步</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const adamId = row["Adam ID"] || "";
    const isAutoUpdate = row["自動更新"] === "true";
    tr.innerHTML = `
      <td style="font-family: var(--mono);">${escapeHtml(adamId)}</td>
      <td style="font-family: var(--mono);">${escapeHtml(row["Bundle ID"] || "")}</td>
      <td>${escapeHtml(row["軟體名稱"] || "")}</td>
      <td>${row["當下版本"] ? escapeHtml(row["當下版本"]) : '<span style="color:#9ca3af;">(查無資料)</span>'}</td>
      <td>${row["版本日期"] ? escapeHtml(row["版本日期"]) : '<span style="color:#9ca3af;">(查無資料)</span>'}</td>
      <td>${escapeHtml(row["總數量"] || "")}</td>
      <td>${escapeHtml(row["剩餘量"] || "")}</td>
      <td><button class="secondary app-update-btn" type="button" style="font-size:12px;" data-adam-id="${escapeHtml(adamId)}" data-app-name="${escapeHtml(row["軟體名稱"] || "")}">更新 App</button></td>
      <td style="text-align:center;"><input type="checkbox" class="app-auto-update-checkbox" data-adam-id="${escapeHtml(adamId)}" ${isAutoUpdate ? "checked" : ""}></td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadVppCache() {
  const syncEl = document.getElementById("vpp-last-sync");
  const res = await apiFetch("/api/asm/cache");
  if (res.ok) {
    syncEl.textContent = res.data.last_sync
      ? `最後同步時間: ${res.data.last_sync}`
      : "尚未同步過,請按「立即同步軟體」";
    renderVppTable(res.data.rows);
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
  const res = await apiFetchJSON("/api/asm/toggle-auto-update", "POST", { adam_id: adamId, enabled });
  if (!res.ok) {
    alert("設定失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    loadVppCache(); // 失敗時重新載入,讓勾選框狀態恢復成實際存檔的狀態,不要讓畫面停留在使用者剛點擊、但其實沒儲存成功的狀態
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadVppCache();
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
