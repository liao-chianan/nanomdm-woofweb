let vppEventSource = null;

function renderVppTable(rows) {
  const tbody = document.getElementById("vpp-tbody");
  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5">尚無資料,請按「立即同步軟體」執行第一次同步</td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-family: var(--mono);">${escapeHtml(row["Adam ID"] || "")}</td>
      <td style="font-family: var(--mono);">${escapeHtml(row["Bundle ID"] || "")}</td>
      <td>${escapeHtml(row["軟體名稱"] || "")}</td>
      <td>${escapeHtml(row["總數量"] || "")}</td>
      <td>${escapeHtml(row["剩餘量"] || "")}</td>
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

document.addEventListener("DOMContentLoaded", () => {
  loadVppCache();
  document.getElementById("run-vpp-btn").addEventListener("click", runVppQuery);
  document.getElementById("download-vpp-btn").addEventListener("click", downloadVppCsv);
});
