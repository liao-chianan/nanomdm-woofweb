let pendingDiffTargetTag = null;
let currentDiffFiles = []; // 存放目前開啟的更新確認modal裡,每個檔案的patch內容,供展開/收合時取用

async function loadCurrentVersion() {
  const res = await apiFetch("/api/version/current");
  if (!res.ok) {
    document.getElementById("version-current-display").textContent = "讀取失敗";
    return;
  }
  const current = res.data.current_version;
  const display = document.getElementById("version-current-display");
  const unknownBox = document.getElementById("version-unknown-box");
  if (current) {
    display.textContent = current;
    unknownBox.style.display = "none";
  } else {
    display.textContent = "未知(請在下方確認)";
    unknownBox.style.display = "";
  }
}

async function saveManualVersion() {
  const tag = document.getElementById("version-manual-input").value.trim();
  if (!tag) {
    alert("請輸入版本標籤,例如 v0.8");
    return;
  }
  const res = await apiFetchJSON("/api/version/set-current", "POST", { tag });
  if (res.ok) {
    alert(`已設定目前版本為 ${res.data.current_version}`);
    loadCurrentVersion();
  } else {
    alert("設定失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function checkForUpdate() {
  const btn = document.getElementById("version-check-btn");
  btn.disabled = true;
  btn.textContent = "檢測中...";

  const res = await apiFetch("/api/version/check-update");

  btn.disabled = false;
  btn.textContent = "檢測更新";

  const resultBox = document.getElementById("version-check-result");
  if (!res.ok) {
    alert("檢測失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }

  const data = res.data;
  resultBox.style.display = "";
  const summary = document.getElementById("version-check-summary");
  const notesEl = document.getElementById("version-release-notes");
  const updateBtn = document.getElementById("version-update-btn");

  if (!data.current_version) {
    summary.textContent = `GitHub 最新版本: ${data.latest_version}(目前版本未知,請先在上方設定)`;
    updateBtn.style.display = "none";
  } else if (data.update_available) {
    summary.textContent = `發現新版本: ${data.latest_version}(目前: ${data.current_version})`;
    notesEl.textContent = data.release_notes || "(這個版本沒有額外說明)";
    updateBtn.style.display = "";
    updateBtn.onclick = () => openDiffModal(data.latest_version);
  } else {
    summary.textContent = `目前已經是最新版本(${data.current_version})`;
    notesEl.textContent = "";
    updateBtn.style.display = "none";
  }
}

async function openDiffModal(targetTag) {
  pendingDiffTargetTag = targetTag;
  document.getElementById("version-diff-target").textContent = targetTag;
  document.getElementById("version-diff-target-2").textContent = targetTag;
  document.getElementById("version-diff-current").textContent = "載入中...";
  document.getElementById("version-diff-file-list").innerHTML = "載入中...";
  document.getElementById("version-diff-empty-hint").textContent = "";
  document.getElementById("version-apply-password-input").value = "";
  openModal("version-diff-modal");

  const res = await apiFetch(`/api/version/diff?target_tag=${encodeURIComponent(targetTag)}`);
  if (!res.ok) {
    document.getElementById("version-diff-file-list").innerHTML =
      `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }

  const data = res.data;
  document.getElementById("version-diff-current").textContent = data.current_version;

  const statusLabel = { added: "新增", modified: "修改", removed: "刪除" };
  const files = data.files || [];
  currentDiffFiles = files; // 存起來給展開/收合時取用patch內容,避免大檔案的diff內容塞進HTML屬性
  if (files.length === 0) {
    document.getElementById("version-diff-file-list").innerHTML = "";
    document.getElementById("version-diff-empty-hint").textContent = "這兩個版本之間,沒有任何符合條件的檔案差異。";
  } else {
    document.getElementById("version-diff-file-list").innerHTML = files.map((f, idx) => `
      <div style="border-bottom:1px solid var(--border-color);">
        <div class="version-diff-file-row" data-idx="${idx}" style="font-size:12px; font-family:var(--mono); padding:4px 0; cursor:pointer; display:flex; align-items:center; gap:6px;">
          <span class="version-diff-toggle-icon">▶</span>
          <span>[${statusLabel[f.status] || f.status}] ${escapeHtml(f.repo_path)}</span>
        </div>
        <div class="version-diff-patch-container hidden" style="padding:4px 0 10px 20px;"></div>
      </div>
    `).join("");
  }
}

function renderDiffPatch(idx) {
  const f = currentDiffFiles[idx];
  if (!f.patch) {
    return `<p style="color:#9ca3af; font-size:12px;">(無法顯示逐行差異——可能是二進位內容,或差異過大,GitHub沒有提供diff內容)</p>`;
  }
  return renderPatchHtml(f.patch);
}

// 把unified diff文字轉成有顏色的HTML(新增綠色、刪除紅色)。更新確認視窗跟版本校驗共用
function renderPatchHtml(patch) {
  const lines = patch.split("\n").map((line) => {
    let color = "#374151";
    if (line.startsWith("+") && !line.startsWith("+++")) color = "#1c7c3f";
    else if (line.startsWith("-") && !line.startsWith("---")) color = "#d64545";
    else if (line.startsWith("@@")) color = "#6b7280";
    return `<div style="color:${color};">${escapeHtml(line)}</div>`;
  }).join("");
  return `<pre style="font-size:11px; font-family:var(--mono); background:#f8f9fb; border:1px solid var(--border-color); border-radius:4px; padding:8px 10px; overflow-x:auto; white-space:pre; margin:4px 0 0 0;">${lines}</pre>`;
}

// ---------------------------------------------------------------------------
// 版本校驗:比對本地端程式碼 vs GitHub同版本
// ---------------------------------------------------------------------------
let verifyFiles = [];
let verifyTag = "";   // 這次校驗比對的GitHub版本標籤(展開檔案、載入差異時要用)

const VERIFY_STATUS_LABEL = {
  modified: "❌ 內容不同",
  line_endings_only: "⚠️ 僅換行符號不同",
  missing_local: "➖ 本地缺少",
  extra_local: "➕ 本地多出",
  error: "⚠️ 無法讀取",
};

async function runVersionVerify() {
  const btn = document.getElementById("version-verify-btn");
  const container = document.getElementById("version-verify-result");
  btn.disabled = true;
  btn.textContent = "校驗中...";
  container.innerHTML = "";

  const res = await apiFetch("/api/version/verify");
  btn.disabled = false;
  btn.textContent = "重新校驗";
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545; font-size:13px;">校驗失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  verifyTag = res.data.tag;
  verifyFiles = (res.data.files || []).map((f) => ({ ...f, detailHtml: null }));
  renderVerifyResult(res.data);
}

function renderVerifyResult(data) {
  const container = document.getElementById("version-verify-result");
  const s = data.summary;
  const extText = (data.extensions || []).join(" ");

  const header = `
    <div style="font-size:13px; margin-bottom:8px;">
      校驗版本 <strong>${escapeHtml(data.tag)}</strong>(GitHub 同版本)・共比對 <strong>${s.compared}</strong> 個檔案
      <span style="color:#9ca3af;">(${escapeHtml(extText)})</span>
    </div>`;
  const truncatedWarn = data.truncated
    ? `<div style="color:#b45309; font-size:13px; margin-bottom:8px;">⚠️ GitHub 回傳的檔案清單被截斷了,下面的結果可能不完整</div>`
    : "";

  if (verifyFiles.length === 0) {
    container.innerHTML = `${header}${truncatedWarn}
      <div style="background:#ecfdf5; color:#065f46; padding:10px 14px; border-radius:6px; font-size:13px;">
        ✅ 本地端程式碼跟 GitHub 上的 ${escapeHtml(data.tag)} 完全一致(${s.identical} 個檔案都相同)
      </div>`;
    return;
  }

  const badges = `
    <div style="font-size:13px; margin-bottom:8px;">
      ✅ 相同 <strong>${s.identical}</strong>　
      ❌ 內容不同 <strong>${s.modified}</strong>　
      ⚠️ 僅換行符號不同 <strong>${s.line_endings_only}</strong>　
      ➖ 本地缺少 <strong>${s.missing_local}</strong>　
      ➕ 本地多出 <strong>${s.extra_local}</strong>
      ${s.error ? `　⚠️ 無法讀取 <strong>${s.error}</strong>` : ""}
    </div>`;

  const rows = verifyFiles.map((f, idx) => `
    <div style="border-bottom:1px solid var(--border-color);">
      <div class="verify-file-row" data-idx="${idx}" style="font-size:12px; font-family:var(--mono); padding:5px 0; cursor:pointer; display:flex; align-items:center; gap:8px;">
        <span class="verify-toggle-icon">▶</span>
        <span style="min-width:150px;">${VERIFY_STATUS_LABEL[f.status] || escapeHtml(f.status)}</span>
        <span>${escapeHtml(f.repo_path)}</span>
      </div>
      <div class="verify-detail hidden" style="padding:4px 0 10px 22px;"></div>
    </div>
  `).join("");

  container.innerHTML = `${header}${truncatedWarn}${badges}
    <div style="max-height:420px; overflow-y:auto; background:#f8f9fb; border-radius:6px; padding:6px 12px;">${rows}</div>`;
}

// 展開某個檔案時,產生下方的說明。只有「內容不同」的檔案才需要另外向後端要逐行差異,
// 其他狀態不用再跑任何請求,直接說明是什麼情況
async function loadVerifyDetail(f, tag) {
  if (f.status === "modified") {
    const res = await apiFetch(`/api/version/verify/diff?path=${encodeURIComponent(f.repo_path)}`);
    if (!res.ok) {
      return `<p style="color:#d64545; font-size:12px;">無法取得差異: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    }
    const legend = `<p style="color:#6b7280; font-size:12px; margin:0 0 4px 0;">
      <span style="color:#d64545;">紅色(-)</span>只存在 GitHub ${escapeHtml(tag)}・<span style="color:#1c7c3f;">綠色(+)</span>只存在本地端(已忽略換行符號差異)
    </p>`;
    const note = res.data.note ? `<p style="color:#b45309; font-size:12px; margin:4px 0;">${escapeHtml(res.data.note)}</p>` : "";
    const body = res.data.patch
      ? renderPatchHtml(res.data.patch)
      : `<p style="color:#9ca3af; font-size:12px;">${escapeHtml(res.data.note || "沒有可顯示的差異")}</p>`;
    return `${legend}${body}${res.data.patch ? note : ""}`;
  }
  const notes = {
    line_endings_only: "內容相同,只有換行符號(CRLF/LF)不同,不影響程式內容。網頁介面在執行 .sh 之前會自動把 CRLF 轉成 LF,所以 .sh 檔案出現這種情況通常是正常的。",
    missing_local: `GitHub ${tag} 有這個檔案,但本地端找不到(可能被刪除,或更新沒有完整套用)。`,
    extra_local: `本地端有這個檔案,但 GitHub ${tag} 上沒有(可能是手動新增的檔案、舊版殘留或備份檔)。`,
    error: `本地端這個檔案無法讀取: ${f.error || "未知原因"}`,
  };
  return `<p style="color:#6b7280; font-size:12px; margin:0;">${escapeHtml(notes[f.status] || "")}</p>`;
}

async function confirmApplyUpdate() {
  const password = document.getElementById("version-apply-password-input").value;
  if (!password) {
    alert("請輸入密碼確認");
    return;
  }
  if (!pendingDiffTargetTag) return;

  const btn = document.getElementById("version-apply-confirm-btn");
  btn.disabled = true;
  btn.textContent = "執行中...";

  const res = await apiFetchJSON("/api/version/apply", "POST", {
    password, target_tag: pendingDiffTargetTag,
  });

  btn.disabled = false;
  btn.textContent = "確認執行";

  if (!res.ok) {
    alert("更新失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }

  closeModal("version-diff-modal");

  if (res.data.self_restarting) {
    alert(res.data.message + "\n\n頁面將在 5 秒後自動重新整理。");
    setTimeout(() => window.location.reload(), 5000);
  } else {
    alert(res.data.message);
    loadCurrentVersion();
  }
}

async function loadUpdateHistory() {
  const tbody = document.getElementById("version-history-tbody");
  tbody.innerHTML = `<tr><td colspan="2">載入中...</td></tr>`;

  const res = await apiFetch("/api/version/history");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="2" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  const history = res.data.history || [];
  if (history.length === 0) {
    tbody.innerHTML = `<tr><td colspan="2" style="color:#9ca3af;">目前沒有任何更新歷程紀錄</td></tr>`;
    return;
  }

  tbody.innerHTML = history
    .map((h) => {
      const releaseUrl = `https://github.com/${window.GITHUB_OWNER}/${window.GITHUB_REPO}/releases/tag/${encodeURIComponent(h.target_version)}`;
      return `
        <tr>
          <td style="font-size:13px;">${escapeHtml(h.timestamp)}</td>
          <td style="font-family:var(--mono); font-size:13px;">
            ${escapeHtml(h.target_version)}
            <a href="${releaseUrl}" target="_blank" rel="noopener" style="margin-left:6px; font-size:12px;" title="查看這個版本的 release note">🔗</a>
          </td>
        </tr>
      `;
    })
    .join("");
}

document.addEventListener("DOMContentLoaded", () => {
  loadCurrentVersion();
  loadUpdateHistory();
  checkForUpdate(); // 一進頁面就自動檢測一次,不用使用者自己按按鈕

  document.getElementById("version-manual-save-btn").addEventListener("click", saveManualVersion);
  document.getElementById("version-check-btn").addEventListener("click", checkForUpdate);
  document.getElementById("version-history-refresh-btn").addEventListener("click", loadUpdateHistory);
  document.getElementById("version-apply-confirm-btn").addEventListener("click", confirmApplyUpdate);

  document.getElementById("version-verify-btn").addEventListener("click", runVersionVerify);
  document.getElementById("version-verify-result").addEventListener("click", async (e) => {
    const row = e.target.closest(".verify-file-row");
    if (!row) return;
    const f = verifyFiles[Number(row.dataset.idx)];
    const detail = row.nextElementSibling;
    const icon = row.querySelector(".verify-toggle-icon");
    if (!detail.classList.contains("hidden")) {
      detail.classList.add("hidden");
      icon.textContent = "▶";
      return;
    }
    detail.classList.remove("hidden");
    icon.textContent = "▼";
    if (f.detailHtml === null) {
      detail.innerHTML = `<p style="color:#9ca3af; font-size:12px; margin:0;">載入中...</p>`;
      f.detailHtml = await loadVerifyDetail(f, verifyTag);
    }
    detail.innerHTML = f.detailHtml;
  });

  document.getElementById("version-diff-file-list").addEventListener("click", (e) => {
    const row = e.target.closest(".version-diff-file-row");
    if (!row) return;
    const idx = Number(row.dataset.idx);
    const container = row.nextElementSibling;
    const icon = row.querySelector(".version-diff-toggle-icon");
    const isHidden = container.classList.contains("hidden");
    if (isHidden) {
      container.innerHTML = renderDiffPatch(idx);
      container.classList.remove("hidden");
      icon.textContent = "▼";
    } else {
      container.classList.add("hidden");
      icon.textContent = "▶";
    }
  });
});
