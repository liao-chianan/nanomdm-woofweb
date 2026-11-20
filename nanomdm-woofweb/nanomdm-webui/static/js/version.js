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

// 列出目前版本到最新版本之間每一個commit的說明(最新的在上面);
// 後端查不到commit清單時,才退回顯示最新版本的Release說明
function renderUpdateNotes(notesEl, data) {
  notesEl.textContent = "";
  const commits = data.commits;
  if (!Array.isArray(commits) || commits.length === 0) {
    notesEl.textContent = data.release_notes || "(這個版本沒有額外說明)";
    return;
  }
  const intro = document.createElement("div");
  intro.style.cssText = "color:#6b7280; margin-bottom:8px;";
  intro.textContent = `這次更新包含 ${data.commits_total || commits.length} 筆修改:`;
  notesEl.appendChild(intro);
  commits.forEach((c) => {
    const item = document.createElement("div");
    item.style.cssText = "border-left:3px solid #d1d5db; padding:2px 0 2px 10px; margin-bottom:10px;";
    const head = document.createElement("div");
    head.style.cssText = "font-weight:600; color:#111827;";
    const people = [c.author, ...(c.co_authors || [])].filter(Boolean).join(" + ");
    head.textContent = [c.tag, c.date, c.sha, people].filter(Boolean).join(" · ");
    const msg = document.createElement("div");
    msg.textContent = c.message || "(沒有說明文字)";
    item.appendChild(head);
    item.appendChild(msg);
    notesEl.appendChild(item);
  });
  if (data.commits_total > commits.length) {
    const more = document.createElement("div");
    more.style.color = "#6b7280";
    more.textContent = `(共 ${data.commits_total} 筆,這裡只列出 ${commits.length} 筆,其餘請到 GitHub 查看)`;
    notesEl.appendChild(more);
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
    renderUpdateNotes(notesEl, data);
    updateBtn.style.display = "";
    updateBtn.onclick = () => openDiffModal(data.latest_version);
  } else {
    summary.textContent = `目前已經是最新版本(${data.current_version})`;
    notesEl.textContent = "";
    updateBtn.style.display = "none";
  }
}

let pendingDiffRequiresBackup = false;
let pendingDiffHasCompose = false;

// 更新內容包含 docker-compose.woofweb.yml:列出套用後會重建的容器,讓使用者決定要不要更新完自動套用
function renderComposePreview(risk, preview) {
  if (!risk || !risk.compose_files || !risk.compose_files.length) return;
  pendingDiffHasCompose = true;
  const lines = [];
  const autoInput = document.getElementById("version-compose-auto-apply");
  autoInput.disabled = false;
  if (preview && preview.ok) {
    const recreate = preview.recreate_services || [];
    lines.push(recreate.length
      ? `更新後套用會重建這些容器(服務中斷約十幾秒):${recreate.join("、")}`
      : "套用後不會重建任何容器。");
    if (!preview.enabled) lines.push("第一次套用會在 .env 加上一行 COMPOSE_FILE(套用前自動備份)。");
    if (preview.image_changes && preview.image_changes.length) {
      lines.push("⚠️ docker-compose.yml 有尚未套用的映像檔變更,為了安全不會自動套用,更新後請到「Docker Compose 設定」確認後手動套用:");
      preview.image_changes.forEach((c) => lines.push(`  ${c.service}:${c.current_image} → ${c.target_image}`));
      autoInput.checked = false;
      autoInput.disabled = true;
    }
  } else {
    lines.push(`無法預覽套用結果(${(preview && preview.error) || "未知原因"}),更新後請到「Docker Compose 設定」區塊確認後再套用。`);
    autoInput.checked = false;
  }
  document.getElementById("version-diff-compose-detail").textContent = lines.join("\n");
  document.getElementById("version-diff-compose").classList.remove("hidden");
}

// 更新前的退版風險:包含需要手動處理的檔案、或commit說明標記【無法退版】時,要求先自行備份
function renderUpdateRisk(risk, riskError) {
  const box = document.getElementById("version-diff-risk");
  const errEl = document.getElementById("version-diff-risk-error");
  if (riskError) {
    errEl.textContent = `無法確認這次更新能不能退版(${riskError}),建議先自行備份系統再更新。`;
    errEl.classList.remove("hidden");
  }
  if (!risk || !risk.requires_backup) return;
  const lines = [];
  if (risk.manual_files && risk.manual_files.length) {
    lines.push(`包含更新機制不會處理、需要手動調整的檔案:${risk.manual_files.join("、")}`);
  }
  (risk.no_rollback_reasons || []).forEach((r) => lines.push(`${r.title}:${r.reason}`));
  document.getElementById("version-diff-risk-detail").textContent = lines.join("\n");
  box.classList.remove("hidden");
  pendingDiffRequiresBackup = true;
}

async function openDiffModal(targetTag) {
  pendingDiffTargetTag = targetTag;
  document.getElementById("version-diff-target").textContent = targetTag;
  document.getElementById("version-diff-target-2").textContent = targetTag;
  document.getElementById("version-diff-current").textContent = "載入中...";
  document.getElementById("version-diff-file-list").innerHTML = "載入中...";
  document.getElementById("version-diff-empty-hint").textContent = "";
  document.getElementById("version-apply-password-input").value = "";
  document.getElementById("version-diff-risk").classList.add("hidden");
  document.getElementById("version-diff-risk-error").classList.add("hidden");
  document.getElementById("version-backup-confirm").checked = false;
  pendingDiffRequiresBackup = false;
  document.getElementById("version-diff-compose").classList.add("hidden");
  document.getElementById("version-compose-auto-apply").checked = true;
  pendingDiffHasCompose = false;
  openModal("version-diff-modal");

  const res = await apiFetch(`/api/version/diff?target_tag=${encodeURIComponent(targetTag)}`);
  if (!res.ok) {
    document.getElementById("version-diff-file-list").innerHTML =
      `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }

  const data = res.data;
  document.getElementById("version-diff-current").textContent = data.current_version;
  renderUpdateRisk(data.risk, data.risk_error);
  renderComposePreview(data.risk, data.compose_preview);

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

const VERIFY_FRESHNESS_LABEL = {
  local_newer: { text: "🛠️ 開發中", color: "#1d4ed8" },
  github_newer: { text: "⬇️ 待更新", color: "#b45309" },
  unknown: { text: "❔ 無法判斷新舊", color: "#6b7280" },
};

function verifyFreshnessBadge(f) {
  const label = VERIFY_FRESHNESS_LABEL[f.freshness];
  return label ? `<span style="min-width:100px; color:${label.color};">${label.text}</span>` : "";
}

function verifyFreshnessNote(f, tag) {
  if (f.freshness === "local_newer") {
    return `本地端比 GitHub ${tag} 新(本地修改時間 ${f.local_time}・GitHub 最後 commit ${f.github_time}),應該是開發中的檔案。`;
  }
  if (f.freshness === "github_newer") {
    return `GitHub ${tag} 比本地端新(GitHub 最後 commit ${f.github_time}・本地修改時間 ${f.local_time}),應該是待更新的檔案。`;
  }
  return `無法判斷哪一邊比較新: ${f.freshness_error || "未知原因"}`;
}

let verifyRequestedTag = "";   // 空字串=用目前版本校驗;否則是「沒有完整套用的更新」的目標版本

async function runVersionVerify(tag) {
  if (typeof tag === "string") verifyRequestedTag = tag;
  const btn = document.getElementById("version-verify-btn");
  const container = document.getElementById("version-verify-result");
  btn.disabled = true;
  btn.textContent = "校驗中...";
  container.innerHTML = "";

  const query = verifyRequestedTag ? `?tag=${encodeURIComponent(verifyRequestedTag)}` : "";
  const res = await apiFetch("/api/version/verify" + query);
  btn.disabled = false;
  btn.textContent = "重新校驗";
  if (!res.ok) {
    container.innerHTML = renderIncompleteUpdateBox(res.data && res.data.incomplete_update, "") +
      `<p style="color:#d64545; font-size:13px;">校驗失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  verifyTag = res.data.tag;
  verifyFiles = (res.data.files || []).map((f) => ({ ...f, detailHtml: null }));
  renderVerifyResult(res.data);
}

// 最近一次更新沒有完整套用:目前版本還是舊版,但部分檔案已經是新版,要改用那次更新的目標版本校驗才看得出缺了什麼
function renderIncompleteUpdateBox(inc, tag) {
  if (!inc) return "";
  const failed = (inc.failed || []).slice(0, 10).map((p) => `<code>${escapeHtml(p)}</code>`).join("、");
  const more = (inc.failed || []).length > 10 ? ` 等 ${inc.failed.length} 個檔案` : "";
  const usingTarget = tag === inc.to_version;
  const btn = usingTarget
    ? `<button type="button" class="secondary verify-use-tag-btn" data-tag="" style="font-size:12px; margin-top:6px;">改回用目前版本校驗</button>`
    : `<button type="button" class="verify-use-tag-btn" data-tag="${escapeHtml(inc.to_version)}" style="font-size:12px; margin-top:6px;">改用 ${escapeHtml(inc.to_version)} 校驗</button>`;
  return `
    <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:6px; padding:10px 14px; font-size:13px; color:#92400e; margin-bottom:10px; line-height:1.7;">
      ⚠️ ${escapeHtml(inc.applied_at)} 從 ${escapeHtml(inc.from_version || "?")} 更新到 <strong>${escapeHtml(inc.to_version)}</strong> 沒有完整套用,
      目前版本仍記錄為舊版,但部分檔案可能已經是新版。${failed ? `<br>當時失敗的檔案:${failed}${more}` : ""}
      <br>${usingTarget ? `目前是用 ${escapeHtml(inc.to_version)} 校驗:補上缺少/不同的檔案後,如果全部跟 ${escapeHtml(inc.to_version)} 一致,目前版本會自動改為 ${escapeHtml(inc.to_version)}。`
        : `建議改用 ${escapeHtml(inc.to_version)} 校驗,找出還沒補上的檔案。`}
      <br>${btn}
    </div>`;
}

// 可以從 GitHub 補上的檔案:本地缺少的預設勾選;內容不同的預設不勾(可能是自己改過的檔案,覆蓋前會備份)
function renderRepairBox() {
  const candidates = verifyFiles
    .map((f, idx) => ({ f, idx }))
    .filter(({ f }) => f.status === "missing_local" || f.status === "modified");
  if (candidates.length === 0) return "";
  const rows = candidates.map(({ f, idx }) => {
    const checked = f.status === "missing_local" || f.freshness === "github_newer";
    const label = f.status === "missing_local" ? "➖ 本地缺少" : `❌ 內容不同${f.freshness === "local_newer" ? "(🛠️ 本地較新)" : f.freshness === "github_newer" ? "(⬇️ GitHub 較新)" : ""}`;
    return `<label style="display:flex; gap:8px; align-items:center; font-size:12px; font-family:var(--mono); padding:2px 0;">
      <input type="checkbox" class="verify-repair-check" data-idx="${idx}" style="width:auto;" ${checked ? "checked" : ""}>
      <span style="min-width:190px;">${label}</span><span>${escapeHtml(f.repo_path)}</span></label>`;
  }).join("");
  return `
    <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; padding:10px 14px; margin:10px 0; font-size:13px;">
      <strong>從 GitHub ${escapeHtml(verifyTag)} 補上檔案</strong>
      <p style="color:#6b7280; font-size:12px; margin:4px 0 6px 0;">
        本地缺少的檔案預設勾選;內容不同的檔案只預設勾選「GitHub 較新」的,本地較新的通常是自己改過的檔案,請展開下方清單確認差異再決定。
        覆蓋的檔案會先備份到 .update_backups,補上後會自動重啟受影響的服務。
      </p>
      ${rows}
      <button type="button" id="verify-repair-btn" style="font-size:12px; margin-top:8px;">下載並補上勾選的檔案...</button>
    </div>`;
}

async function runVerifyRepair() {
  const selected = Array.from(document.querySelectorAll(".verify-repair-check:checked"))
    .map((el) => verifyFiles[Number(el.dataset.idx)]);
  if (selected.length === 0) {
    alert("請先勾選要補上的檔案");
    return;
  }
  const missing = selected.filter((f) => f.status === "missing_local").length;
  const replace = selected.length - missing;
  const list = selected.slice(0, 15).map((f) => "・" + f.repo_path).join("\n") + (selected.length > 15 ? `\n…共 ${selected.length} 個檔案` : "");
  const msg = `即將從 GitHub ${verifyTag} 下載 ${selected.length} 個檔案:\n${list}\n\n` +
    `新增本地缺少的檔案 ${missing} 個、覆蓋內容不同的檔案 ${replace} 個(覆蓋前會先備份)。` +
    `完成後會自動重啟受影響的服務。\n\n確定要執行嗎?請輸入您目前登入的密碼:`;
  const password = await askPassword(msg);
  if (!password) return;

  const btn = document.getElementById("verify-repair-btn");
  btn.disabled = true;
  btn.textContent = "補檔中...";
  const res = await apiFetchJSON("/api/version/verify/repair", "POST", {
    password, tag: verifyRequestedTag, paths: selected.map((f) => f.repo_path),
  });
  btn.disabled = false;
  btn.textContent = "下載並補上勾選的檔案...";
  const message = (res.data && res.data.message) || "未知錯誤";
  if (!res.ok) {
    alert("補檔失敗: " + message);
    if (res.data && res.data.results) runVersionVerify();
    return;
  }
  alert(message);
  if (res.data.self_restarting) {
    document.getElementById("version-verify-result").innerHTML =
      `<p style="font-size:13px; color:#374151;">webui 服務正在重啟,幾秒後請重新整理頁面,再按「開始校驗」確認結果。</p>`;
    return;
  }
  if (res.data.version_updated) verifyRequestedTag = "";
  loadCurrentVersion();
  runVersionVerify();
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

  const incompleteBox = renderIncompleteUpdateBox(data.incomplete_update, data.tag);

  if (verifyFiles.length === 0) {
    container.innerHTML = `${incompleteBox}${header}${truncatedWarn}
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

  const freshnessLegend = s.modified ? `
    <div style="background:#eff6ff; color:#374151; padding:8px 12px; border-radius:6px; font-size:12px; line-height:1.7; margin-bottom:8px;">
      內容不同的檔案:🛠️ 開發中 <strong>${s.local_newer}</strong>　⬇️ 待更新 <strong>${s.github_newer}</strong>${s.freshness_unknown ? `　❔ 無法判斷 <strong>${s.freshness_unknown}</strong>` : ""}<br>
      🛠️ <strong>開發中</strong>:本地檔案比 GitHub 上的新,應該是還在開發、尚未發布的檔案。<br>
      ⬇️ <strong>待更新</strong>:GitHub 上的檔案比本地新,應該是需要更新的檔案。<br>
      <span style="color:#6b7280;">新舊是比較本地檔案的修改時間,跟該檔案在 GitHub ${escapeHtml(data.tag)} 最後一次 commit 的時間。複製或還原舊檔也會改變修改時間,實際內容請展開檔案確認差異。</span>
    </div>` : "";

  const rows = verifyFiles.map((f, idx) => `
    <div style="border-bottom:1px solid var(--border-color);">
      <div class="verify-file-row" data-idx="${idx}" style="font-size:12px; font-family:var(--mono); padding:5px 0; cursor:pointer; display:flex; align-items:center; gap:8px;">
        <span class="verify-toggle-icon">▶</span>
        <span style="min-width:150px;">${VERIFY_STATUS_LABEL[f.status] || escapeHtml(f.status)}</span>
        ${verifyFreshnessBadge(f)}
        <span>${escapeHtml(f.repo_path)}</span>
      </div>
      <div class="verify-detail hidden" style="padding:4px 0 10px 22px;"></div>
    </div>
  `).join("");

  container.innerHTML = `${incompleteBox}${header}${truncatedWarn}${badges}${freshnessLegend}${renderRepairBox()}
    <div style="max-height:420px; overflow-y:auto; background:#f8f9fb; border-radius:6px; padding:6px 12px;">${rows}</div>`;
}

// 展開某個檔案時,產生下方的說明。只有「內容不同」的檔案才需要另外向後端要逐行差異,
// 其他狀態不用再跑任何請求,直接說明是什麼情況
async function loadVerifyDetail(f, tag) {
  if (f.status === "modified") {
    const res = await apiFetch(`/api/version/verify/diff?path=${encodeURIComponent(f.repo_path)}${verifyRequestedTag ? `&tag=${encodeURIComponent(verifyRequestedTag)}` : ""}`);
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
    const freshness = `<p style="color:#374151; font-size:12px; margin:0 0 4px 0;">${escapeHtml(verifyFreshnessNote(f, tag))}</p>`;
    return `${freshness}${legend}${body}${res.data.patch ? note : ""}`;
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
  const backupConfirmed = document.getElementById("version-backup-confirm").checked;
  if (pendingDiffRequiresBackup && !backupConfirmed) {
    alert("這次更新無法退版,請先自行備份系統,並勾選「我已自行備份系統」");
    return;
  }

  const btn = document.getElementById("version-apply-confirm-btn");
  btn.disabled = true;
  btn.textContent = "執行中...";

  const res = await apiFetchJSON("/api/version/apply", "POST", {
    password, target_tag: pendingDiffTargetTag, backup_confirmed: backupConfirmed,
    compose_auto_apply: pendingDiffHasCompose && document.getElementById("version-compose-auto-apply").checked,
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
    loadComposePlan();
    loadPostUpdateTasks();
  }
}

// 作者(有GitHub帳號的連到帳號頁面)與Co-Authored-By列出的協作者
function renderCommitPeople(c) {
  const parts = [];
  if (c.author) {
    parts.push(c.author_is_account
      ? `<a href="https://github.com/${encodeURIComponent(c.author)}" target="_blank" rel="noopener">${escapeHtml(c.author)}</a>`
      : escapeHtml(c.author));
  }
  (c.co_authors || []).forEach((name) => {
    parts.push(`<span style="color:#6b7280;">+ ${escapeHtml(name)}</span>`);
  });
  return parts.join("<br>");
}

// ---------------------------------------------------------------------------
// 退版:一次退回最近一次更新之前的版本
// ---------------------------------------------------------------------------
let rollbackCandidate = null;

async function loadRollbackInfo() {
  const box = document.getElementById("version-rollback-box");
  const res = await apiFetch("/api/version/rollback-info");
  if (!res.ok) {
    box.innerHTML = `<span style="color:#d64545;">無法取得退版資訊</span>`;
    return;
  }
  const rb = res.data.rollback;
  rollbackCandidate = rb;
  if (!rb.available) {
    box.innerHTML = `<strong>退版</strong>:<span style="color:#6b7280;">${escapeHtml(rb.reason || "目前無法退版")}</span>`;
    return;
  }
  const modified = rb.modified_since.length
    ? `<div style="color:#b45309; margin-top:4px;">⚠️ 更新後有 ${rb.modified_since.length} 個檔案被修改過,退版會覆蓋這些修改:${escapeHtml(rb.modified_since.join("、"))}</div>`
    : "";
  box.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;">
      <div><strong>退版</strong>:最近一次更新是 ${escapeHtml(rb.applied_at)} 從 <strong>${escapeHtml(rb.from_version)}</strong> 更新到 <strong>${escapeHtml(rb.to_version)}</strong>,
        可以退回 ${escapeHtml(rb.from_version)}(還原 ${rb.restore.length} 個檔案、刪除 ${rb.delete.length} 個新增的檔案)</div>
      <button id="version-rollback-btn" class="danger" type="button" style="font-size:12px;">退回 ${escapeHtml(rb.from_version)}</button>
    </div>${modified}`;
  document.getElementById("version-rollback-btn").addEventListener("click", runRollback);
}

async function runRollback() {
  const rb = rollbackCandidate;
  if (!rb || !rb.available) return;
  const fileLines = [...rb.restore.map((f) => `還原 ${f}`), ...rb.delete.map((f) => `刪除 ${f}`)];
  const msg = `確定要從 ${rb.to_version} 退回 ${rb.from_version} 嗎?\n\n` +
    `${fileLines.slice(0, 15).join("\n")}${fileLines.length > 15 ? `\n…共 ${fileLines.length} 個檔案` : ""}\n\n` +
    (rb.modified_since.length ? `更新後被修改過的檔案也會被覆蓋:${rb.modified_since.join("、")}\n\n` : "") +
    `只會還原程式檔案,資料庫、.env、docker-compose.yml 與系統設定不會變回去。相關服務會自動重啟。`;
  if (!confirm(msg)) return;
  const password = await askPassword("請輸入您目前登入的密碼以確認退版:");
  if (!password) return;

  const btn = document.getElementById("version-rollback-btn");
  btn.disabled = true;
  btn.textContent = "退版中...";
  const res = await apiFetchJSON("/api/version/rollback", "POST", { folder: rb.folder, password });
  if (!res.ok) {
    btn.disabled = false;
    btn.textContent = `退回 ${rb.from_version}`;
    alert("退版失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    loadRollbackInfo();
    return;
  }
  if (res.data.self_restarting) {
    alert(res.data.message + "\n\n頁面將在 5 秒後自動重新整理。");
    setTimeout(() => window.location.reload(), 5000);
  } else {
    alert(res.data.message);
    loadCurrentVersion();
    loadRollbackInfo();
    loadUpdateHistory(true);
  }
}

// ---------------------------------------------------------------------------
// Docker Compose 設定
// ---------------------------------------------------------------------------
const COMPOSE_STATE_TEXT = {
  running: ["套用中", "#1d4ed8"],
  succeeded: ["套用成功", "#1c7c3f"],
  failed: ["套用失敗(沒有做任何變更)", "#d64545"],
  rolled_back: ["套用失敗,已自動還原", "#b45309"],
  rollback_failed: ["套用失敗,自動還原也失敗", "#d64545"],
};
const COMPOSE_ACTION_TEXT = { none: "不變", recreate: "重建", create: "新建" };
let composePlan = null;
let composePollTimer = null;

async function loadComposePlan() {
  const container = document.getElementById("compose-plan-container");
  const res = await apiFetch("/api/compose/plan");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    setPanelSummary("compose-panel", "比對失敗", "warning");
    return;
  }
  composePlan = res.data.plan;
  renderComposePlan(res.data.running);
  renderComposeStatus(res.data.status, res.data.running);
  if (res.data.running) startComposePolling();
}

function renderComposePlan(running) {
  const container = document.getElementById("compose-plan-container");
  const p = composePlan;
  if (!p.ok) {
    container.innerHTML = `<p style="color:#b45309; font-size:13px;">${escapeHtml(p.error || "無法比對")}</p>`;
    setPanelSummary("compose-panel", p.error || "無法比對", "warning");
    return;
  }
  // 摺疊時標題右側只顯示比對結果與比對時間
  const checkedAt = p.checked_at ? `(${p.checked_at} 比對)` : "";
  if (running) setPanelSummary("compose-panel", `套用中...${checkedAt}`, "warning");
  else if (p.needs_apply) setPanelSummary("compose-panel", `需要套用,將重建 ${p.recreate_services.join("、") || "無"}${checkedAt}`, "warning");
  else setPanelSummary("compose-panel", `目前的容器已經是最新設定${checkedAt}`, "ok");
  const rows = p.services.map((svc) => {
    const color = svc.action === "none" ? "#6b7280" : "#b45309";
    const image = svc.image_change
      ? `<div style="color:#d64545;">映像檔變更:${escapeHtml(svc.current_image)} → ${escapeHtml(svc.target_image)}</div>`
      : `<span style="color:#6b7280;">${escapeHtml(svc.current_image || svc.target_image)}</span>`;
    return `<tr>
      <td>${escapeHtml(svc.service)}<div style="color:#9ca3af; font-size:11px;">${escapeHtml(svc.container)}</div></td>
      <td style="font-size:12px;">${image}</td>
      <td style="font-size:12px;">${escapeHtml(svc.logging)}</td>
      <td style="color:${color}; font-weight:600;">${COMPOSE_ACTION_TEXT[svc.action] || svc.action}</td>
    </tr>`;
  }).join("");
  const enabledText = p.enabled
    ? `<span style="color:#1c7c3f;">已啟用</span>(.env:<code>COMPOSE_FILE=${escapeHtml(p.env_compose_file)}</code>)`
    : `<span style="color:#b45309;">尚未啟用</span>,第一次套用會在 .env 加上 <code>COMPOSE_FILE=${escapeHtml(p.target_compose_file)}</code>`;
  const summary = p.needs_apply
    ? `<span style="color:#b45309; font-weight:600;">需要套用</span>:將重建 ${escapeHtml(p.recreate_services.join("、") || "無")}`
    : `<span style="color:#1c7c3f; font-weight:600;">目前的容器已經是最新設定</span>`;
  const missing = p.missing_env_keys && p.missing_env_keys.length
    ? `<p style="font-size:12px; color:#b45309; margin:6px 0 0;">提醒:.env 沒有 env.example 裡的這些變數(選用的可以忽略):${escapeHtml(p.missing_env_keys.join("、"))}</p>`
    : "";
  container.innerHTML = `
    <p style="font-size:13px; margin:0 0 6px;">專案管理設定:${enabledText}</p>
    <p style="font-size:13px; margin:0 0 8px;">${summary}<span style="color:#9ca3af; font-size:12px; margin-left:8px;">(${escapeHtml(p.checked_at)} 比對)</span></p>
    <table class="data-table" style="font-size:13px;">
      <thead><tr><th style="width:150px;">服務</th><th>映像檔</th><th style="width:170px;">容器日誌</th><th style="width:70px;">套用後</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${missing}
    ${p.needs_apply ? `<button id="compose-apply-btn" class="danger" type="button" style="margin-top:10px; font-size:12px;" ${running ? "disabled" : ""}>
      ${running ? "套用中..." : "套用 docker compose"}</button>` : ""}`;
  const btn = document.getElementById("compose-apply-btn");
  if (btn) btn.addEventListener("click", runComposeApply);
}

function renderComposeStatus(st, running) {
  const container = document.getElementById("compose-status-container");
  if (!st || !st.state) {
    container.innerHTML = "";
    return;
  }
  const [label, color] = COMPOSE_STATE_TEXT[st.state] || [st.state, "#374151"];
  const steps = (st.steps || []).map((step) =>
    `<div style="color:${step.ok ? "#374151" : "#d64545"};">${escapeHtml(step.time.slice(11))} ${escapeHtml(step.text)}</div>`).join("");
  container.innerHTML = `
    <details ${running || st.state !== "succeeded" ? "open" : ""} style="background:#f8f9fb; border-radius:6px; padding:10px 12px; font-size:13px;">
      <summary style="cursor:pointer;">
        最近一次套用:<strong style="color:${color};">${label}</strong>
        <span style="color:#6b7280;">${escapeHtml(st.started_at || "")}${st.user ? `,${escapeHtml(st.user)}` : ""}${st.reason ? `,${escapeHtml(st.reason)}` : ""}</span>
        ${st.message ? `<div style="margin-top:4px;">${escapeHtml(st.message)}</div>` : ""}
      </summary>
      <div style="margin-top:8px; font-family:var(--mono); font-size:12px; white-space:pre-wrap; max-height:300px; overflow-y:auto;">${steps}</div>
    </details>`;
}

function startComposePolling() {
  clearTimeout(composePollTimer);
  composePollTimer = setTimeout(async () => {
    const res = await apiFetch("/api/compose/status");
    if (!res.ok) {
      startComposePolling(); // WebUI 可能正在重啟,稍後再試
      return;
    }
    renderComposeStatus(res.data.status, res.data.running);
    if (res.data.running) {
      startComposePolling();
    } else {
      loadComposePlan();
    }
  }, 3000);
}

async function runComposeApply() {
  const p = composePlan;
  if (!p || !p.ok) return;
  let allowImageChange = false;
  if (p.image_changes && p.image_changes.length) {
    const changes = p.image_changes.map((c) => `${c.service}:${c.current_image} → ${c.target_image}`).join("\n");
    if (!confirm(`docker-compose.yml 有尚未套用的映像檔變更,這次套用會一起換掉映像檔:\n\n${changes}\n\n確定要繼續嗎?`)) return;
    allowImageChange = true;
  }
  const msg = `確定要套用 docker compose 嗎?\n\n將重建:${p.recreate_services.join("、") || "無"}\n` +
    (p.enabled ? "" : `第一次套用會在 .env 加上 COMPOSE_FILE=${p.target_compose_file}\n`) +
    "\n重建期間 MDM 服務會中斷約十幾秒。套用前會自動備份 .env 與 compose 設定檔,失敗時自動還原。";
  if (!confirm(msg)) return;
  const password = await askPassword("請輸入您目前登入的密碼以確認套用:");
  if (!password) return;
  const res = await apiFetchJSON("/api/compose/apply", "POST", { password, allow_image_change: allowImageChange });
  if (!res.ok) {
    alert("套用失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }
  renderComposePlan(true);
  renderComposeStatus({ state: "running", started_at: "", steps: [], message: "已開始在背景套用..." }, true);
  startComposePolling();
}

async function loadUpdateHistory(forceRefresh) {
  const tbody = document.getElementById("version-history-tbody");
  tbody.innerHTML = `<tr><td colspan="4">載入中...</td></tr>`;

  const res = await apiFetch("/api/version/history" + (forceRefresh === true ? "?refresh=1" : ""));
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    setPanelSummary("history-panel", "載入失敗", "warning");
    return;
  }

  const data = res.data;
  const history = data.history || [];
  const commits = data.commits;

  // GitHub查不到commit清單時,退回只顯示本機的更新紀錄
  if (!Array.isArray(commits)) {
    const err = `<tr><td colspan="4" style="color:#d64545;">無法取得 GitHub 修改紀錄: ${escapeHtml(data.commits_error || "未知錯誤")}</td></tr>`;
    tbody.innerHTML = err + history
      .map((h) => `<tr><td style="font-family:var(--mono); font-size:13px;">${escapeHtml(h.target_version)}</td>
        <td></td><td></td><td style="font-size:12px; color:#6b7280;">${escapeHtml(h.timestamp)} ${h.kind === "rollback" ? "退版" : "更新"}${h.rolled_back_at ? "(已退版)" : ""}</td></tr>`)
      .join("");
    setPanelSummary("history-panel", "無法取得 GitHub 修改紀錄", "warning");
    return;
  }
  if (commits.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:#9ca3af;">GitHub 上沒有任何修改紀錄</td></tr>`;
    setPanelSummary("history-panel", "沒有修改紀錄", "ok");
    return;
  }

  // 每個版本最近一次在這台主機更新的時間(history已經由新到舊排列)
  const updatedAt = {};
  history.forEach((h) => {
    if (h.kind === "update" && !updatedAt[h.target_version]) {
      updatedAt[h.target_version] = h.timestamp + (h.rolled_back_at ? "(已退版)" : "");
    }
  });

  // commits由新到舊排列:目前版本那一筆(含)以下都已經套用,上面的是還沒套用的
  const currentIndex = commits.findIndex((c) => c.tag && c.tag === data.current_version);
  if (currentIndex > 0) {
    setPanelSummary("history-panel", `目前 ${data.current_version},有 ${currentIndex} 個修改尚未套用`, "warning");
  } else if (currentIndex === 0) {
    setPanelSummary("history-panel", `目前 ${data.current_version},已是最新`, "ok");
  } else {
    setPanelSummary("history-panel", `最近 ${commits.length} 筆修改`, "ok");
  }

  tbody.innerHTML = commits
    .map((c, i) => {
      const lines = (c.message || "").split("\n");
      const title = lines[0] || "(沒有說明文字)";
      const rest = lines.slice(1).join("\n").trim();
      const detail = rest
        ? `<details style="margin-top:4px;"><summary style="cursor:pointer; color:#6b7280; font-size:12px;">詳細說明</summary>
             <div style="white-space:pre-wrap; font-size:12px; color:#374151; margin-top:4px;">${escapeHtml(rest)}</div></details>`
        : "";

      const status = [];
      if (currentIndex !== -1) {
        if (i === currentIndex) status.push(`<span style="color:#047857; font-weight:600;">目前版本</span>`);
        else if (i < currentIndex) status.push(`<span style="color:#b45309;">尚未套用</span>`);
        else status.push(`<span style="color:#6b7280;">已包含</span>`);
      }
      if (c.tag && updatedAt[c.tag]) status.push(`<span style="color:#6b7280;">${escapeHtml(updatedAt[c.tag])} 更新</span>`);

      const commitUrl = `https://github.com/${window.GITHUB_OWNER}/${window.GITHUB_REPO}/commit/${encodeURIComponent(c.sha)}`;
      const rowStyle = i === currentIndex ? ' style="background:#ecfdf5;"' : "";
      return `
        <tr${rowStyle}>
          <td style="font-family:var(--mono); font-size:13px; vertical-align:top;">
            ${c.tag ? `<strong>${escapeHtml(c.tag)}</strong><br>` : ""}
            <span style="font-size:12px; color:#6b7280;">${escapeHtml(c.date)} · <a href="${commitUrl}" target="_blank" rel="noopener" title="在 GitHub 查看這次修改">${escapeHtml(c.sha)}</a></span>
          </td>
          <td style="font-size:13px; vertical-align:top;">${escapeHtml(title)}${detail}</td>
          <td style="font-size:12px; vertical-align:top;">${renderCommitPeople(c)}</td>
          <td style="font-size:12px; vertical-align:top;">${status.join("<br>")}</td>
        </tr>
      `;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// 作業系統更新:檢查可更新的套件、是否需要重新開機,切換 Debian 鏡像站(只檢查,不安裝)
// ---------------------------------------------------------------------------
let osUpdateStatus = null;
let osUpdatePollTimer = null;
let osMirrorSpeed = {};          // 鏡像站 id → 測速結果
let osMirrorSelected = "";

function osBadge(text, color, bg) {
  return `<span style="display:inline-block; font-size:11px; padding:1px 6px; border-radius:4px; margin-right:4px; color:${color}; background:${bg};">${escapeHtml(text)}</span>`;
}

function renderOsReboot(reboot) {
  const el = document.getElementById("os-update-reboot");
  if (!reboot || !reboot.required) {
    el.innerHTML = `<p style="font-size:13px; color:#047857; margin:0 0 10px 0;">✓ 不需要重新開機(目前核心 <span style="font-family:var(--mono);">${escapeHtml((reboot && reboot.running_kernel) || "")}</span>)</p>`;
    return;
  }
  el.innerHTML = `
    <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#9a3412;">
      <div style="font-weight:700; margin-bottom:4px;">⚠️ 需要重新開機才會生效</div>
      ${reboot.reasons.map((r) => `<div>・${escapeHtml(r)}</div>`).join("")}
      <div style="margin-top:6px; color:#6b7280;">重新開機時所有服務(含 MDM)會中斷約 1~3 分鐘,請挑選不影響使用的時間。</div>
      <button type="button" class="danger" id="os-reboot-btn" style="margin-top:8px; font-size:12px;">重新開機…</button>
    </div>`;
}

function renderOsJob(job) {
  const el = document.getElementById("os-update-job");
  const btn = document.getElementById("os-update-check-btn");
  const running = job && job.state === "running";
  btn.disabled = running;
  document.querySelectorAll("#os-update-mirror button").forEach((b) => { b.disabled = running; });
  if (!job || job.state === "idle") { el.innerHTML = ""; return; }
  const title = job.kind === "switch" ? `切換鏡像站到 ${job.mirror}` : "檢查更新";
  if (running) {
    el.innerHTML = `<div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#1e3a8a;">
      ⏳ ${escapeHtml(title)}:${escapeHtml(job.message || "")}(${escapeHtml(job.started_at || "")} 開始)</div>`;
  } else if (job.state === "failed") {
    el.innerHTML = `<div style="background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#991b1b;">
      ✕ ${escapeHtml(title)}失敗:${escapeHtml(job.message || "")}</div>`;
  } else if (job.kind === "switch") {
    el.innerHTML = `<div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#065f46;">
      ✓ 已切換到 ${escapeHtml(job.mirror)},套件清單已更新(${escapeHtml(job.finished_at || "")},耗時 ${job.seconds} 秒)</div>`;
  } else {
    el.innerHTML = "";
  }
}

function renderOsResult(st) {
  const el = document.getElementById("os-update-result");
  if (!st.sources.supported) {
    el.innerHTML = `<p style="color:#b45309; font-size:13px;">${escapeHtml(st.sources.unsupported_reason)}(目前系統:${escapeHtml(st.sources.os_name)})</p>`;
    return;
  }
  const lc = st.last_check;
  const listsAt = st.lists_updated_at ? `套件清單最後更新:${escapeHtml(st.lists_updated_at)}` : "";
  if (!lc) {
    el.innerHTML = `<p style="color:#6b7280; font-size:13px;">還沒有檢查過,請按右上角「檢查更新」。${listsAt ? "(" + listsAt + ")" : ""}</p>`;
    return;
  }
  const sm = lc.summary;
  const head = `<p style="font-size:12px; color:#6b7280; margin:0 0 8px 0;">上次檢查:${escapeHtml(lc.checked_at)}${listsAt ? " ・" + listsAt : ""}</p>`;
  if (sm.total === 0 && sm.removals === 0) {
    el.innerHTML = head + `<p style="font-size:14px; color:#047857; font-weight:600;">✓ 所有套件都是最新版本</p>`;
    return;
  }
  const parts = [`<strong>${sm.total} 個套件可以更新</strong>`];
  if (sm.security) parts.push(osBadge(`安全性更新 ${sm.security}`, "#991b1b", "#fee2e2"));
  if (sm.kernel) parts.push(osBadge(`核心 ${sm.kernel}`, "#9a3412", "#ffedd5"));
  if (sm.docker) parts.push(osBadge(`Docker ${sm.docker}`, "#1e40af", "#dbeafe"));
  if (sm.new_install) parts.push(osBadge(`新安裝 ${sm.new_install}`, "#374151", "#f3f4f6"));

  const notes = [];
  if (sm.kernel) notes.push("包含核心更新,安裝後需要重新開機才會生效。");
  if (sm.docker) notes.push("包含 Docker 相關套件,安裝時 Docker 會重新啟動,所有容器(含 MDM 服務)會中斷約 1 分鐘。");
  if (sm.new_install) notes.push("有些更新需要安裝新套件(通常是新版核心),「一般更新」會一起安裝。");
  if (sm.removals) notes.push(`<span style="color:#991b1b; font-weight:600;">完整更新(full-upgrade)會移除 ${sm.removals} 個套件:${lc.removals.map((r) => escapeHtml(r.name)).join("、")}。「一般更新」不會移除任何套件。</span>`);
  if (lc.warnings && lc.warnings.length) notes.push(`<span style="color:#b45309;">apt 警告:${lc.warnings.map(escapeHtml).join(";")}</span>`);

  const rows = lc.packages.map((p) => {
    const tags = [];
    if (p.security) tags.push(osBadge("安全性", "#991b1b", "#fee2e2"));
    if (p.kernel) tags.push(osBadge("核心", "#9a3412", "#ffedd5"));
    if (p.docker) tags.push(osBadge("Docker", "#1e40af", "#dbeafe"));
    if (p.new_install) tags.push(osBadge("新安裝", "#374151", "#f3f4f6"));
    return `<tr>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(p.name)}</td>
      <td style="font-family:var(--mono); font-size:12px; color:#6b7280;">${escapeHtml(p.old_version || "—")}</td>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(p.new_version)}</td>
      <td style="font-size:12px; color:#6b7280;">${escapeHtml(p.origin)}</td>
      <td>${tags.join("")}</td>
    </tr>`;
  }).join("");

  el.innerHTML = head
    + `<p style="font-size:14px; margin:0 0 8px 0;">${parts.join(" ")}</p>`
    + (notes.length ? `<ul style="font-size:13px; color:#374151; margin:0 0 10px 0; padding-left:20px; line-height:1.7;">${notes.map((n) => `<li>${n}</li>`).join("")}</ul>` : "")
    + `<div style="max-height:360px; overflow-y:auto; border:1px solid var(--border-color); border-radius:6px;">
         <table class="data-table" style="margin:0;">
           <thead><tr><th>套件</th><th>目前版本</th><th>新版本</th><th>來源</th><th>類型</th></tr></thead>
           <tbody>${rows}</tbody>
         </table>
       </div>
       <button type="button" class="danger" id="os-upgrade-open-btn" style="margin-top:10px;">安裝更新…</button>`;
}

function renderOsMirror(st) {
  const el = document.getElementById("os-update-mirror");
  const src = st.sources;
  if (!src.supported) {
    el.innerHTML = `<p style="color:#9ca3af; font-size:13px;">${escapeHtml(src.unsupported_reason)}</p>`;
    return;
  }
  if (!src.uris.length) {
    el.innerHTML = `<p style="color:#b45309; font-size:13px;">在 /etc/apt 裡找不到 Debian 主要套件庫的設定,無法切換鏡像站。</p>`;
    return;
  }
  const currentId = src.current_mirror_id;
  if (!osMirrorSelected) osMirrorSelected = currentId;
  const currentText = currentId
    ? escapeHtml(st.mirrors.find((m) => m.id === currentId).name)
    : `<span style="color:#b45309;">不在下方清單中</span>`;
  const rows = st.mirrors.map((m) => {
    const sp = osMirrorSpeed[m.id];
    let speed = `<span style="color:#9ca3af;">—</span>`;
    if (sp === "testing") speed = `<span style="color:#9ca3af;">測速中...</span>`;
    else if (sp && sp.ok) speed = `<span style="color:${sp.ms < 300 ? "#047857" : sp.ms < 1000 ? "#b45309" : "#d64545"};">${sp.ms} ms</span>`;
    else if (sp) speed = `<span style="color:#d64545;">${escapeHtml(sp.error)}</span>`;
    const isCurrent = m.id === currentId;
    return `<tr${isCurrent ? ' style="background:#ecfdf5;"' : ""}>
      <td style="width:1%;"><input type="radio" name="os-mirror" value="${escapeHtml(m.id)}" style="width:auto;"${m.id === osMirrorSelected ? " checked" : ""}></td>
      <td>${escapeHtml(m.name)}${isCurrent ? " " + osBadge("目前使用", "#065f46", "#d1fae5") : ""}
        ${m.note ? `<div style="font-size:12px; color:#6b7280;">${escapeHtml(m.note)}</div>` : ""}</td>
      <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(m.url)}</td>
      <td style="white-space:nowrap; font-size:12px;">${speed}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <p style="font-size:13px; margin:0 0 8px 0;">目前使用:${currentText}
      <span style="font-family:var(--mono); font-size:12px; color:#6b7280; margin-left:6px;">${src.uris.map(escapeHtml).join("、")}</span>
      <span style="font-size:12px; color:#9ca3af; margin-left:6px;">(設定檔:${src.files.map(escapeHtml).join("、")})</span></p>
    <table class="data-table" id="os-mirror-table">
      <thead><tr><th></th><th>鏡像站</th><th>網址</th><th>回應時間</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div style="margin-top:10px; display:flex; gap:8px;">
      <button type="button" class="secondary" id="os-mirror-speed-btn">測速</button>
      <button type="button" id="os-mirror-apply-btn">切換到選取的鏡像站</button>
    </div>`;
  renderOsJob(st.job);
}

// 摺疊時標題右側的摘要:只顯示目前有沒有可用的更新
function renderOsSummary(st) {
  const el = document.getElementById("os-update-summary");
  const muted = (t) => `<span style="color:#6b7280;">・${t}</span>`;
  const job = st.job || {};
  const lc = st.last_check;
  let html;
  if (!st.sources.supported) html = muted(escapeHtml(st.sources.unsupported_reason));
  else if (st.upgrade_running) {
    const pg = st.upgrade_latest.progress;
    html = `<span style="color:#1e40af; font-weight:600;">・安裝更新中(${pg.configured}/${pg.total})</span>`;
  }
  else if (job.state === "running") html = muted(job.kind === "switch" ? "切換鏡像站中..." : "檢查中...");
  else if (!lc) html = muted("還沒檢查過");
  else if (lc.summary.total === 0 && lc.summary.removals === 0) html = `<span style="color:#047857;">・所有套件都是最新版本</span>`;
  else {
    const sm = lc.summary;
    html = `<span style="color:#b45309; font-weight:600; margin-right:6px;">・${sm.total} 個套件可以更新</span>`;
    if (sm.security) html += osBadge(`安全性更新 ${sm.security}`, "#991b1b", "#fee2e2");
    if (sm.kernel) html += osBadge(`核心 ${sm.kernel}`, "#9a3412", "#ffedd5");
    if (sm.docker) html += osBadge(`Docker ${sm.docker}`, "#1e40af", "#dbeafe");
    if (sm.new_install) html += osBadge(`新安裝 ${sm.new_install}`, "#374151", "#f3f4f6");
  }
  if (st.reboot && st.reboot.required) html += osBadge("需要重新開機", "#9a3412", "#ffedd5");
  const latest = st.upgrade_latest;
  if (latest && (latest.state === "failed" || latest.state === "interrupted" || latest.dpkg_audit)) {
    html += osBadge("上次安裝更新沒有正常完成", "#991b1b", "#fee2e2");
    setPanelExpanded("os-update-panel", true);
  }
  el.innerHTML = html;
}

// ---- 安裝更新(在主機背景用 systemd-run 執行 apt-get,WebUI 重啟也不會中斷) ----
let osUpgradePlan = null;
let osUpgradePlanSeq = 0;

function updateOsButtons(st) {
  const busy = st.upgrade_running || (st.job && st.job.state === "running");
  ["os-update-check-btn", "os-upgrade-open-btn", "os-reboot-btn", "os-mirror-apply-btn", "os-mirror-speed-btn"].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = busy;
  });
}

const UPGRADE_STATE_TEXT = {
  running: ["安裝中", "#1e40af"],
  succeeded: ["完成", "#047857"],
  failed: ["失敗", "#d64545"],
  interrupted: ["中斷", "#d64545"],
};

function renderUpgradeRun(st) {
  const el = document.getElementById("os-upgrade-run");
  const r = st.upgrade_latest;
  if (!r) { el.innerHTML = ""; return; }
  const pg = r.progress;
  const logHtml = `<pre id="os-upgrade-log-tail" style="max-height:260px; overflow:auto; background:#111827; color:#e5e7eb; font-size:11px; padding:8px 10px; border-radius:6px; margin:8px 0 0; white-space:pre-wrap;">${escapeHtml(r.log_tail || "(還沒有輸出)")}</pre>`;
  if (r.state === "running") {
    const pct = pg.total ? Math.round(((pg.unpacked + pg.configured) / (pg.total * 2)) * 100) : 0;
    el.innerHTML = `
      <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#1e3a8a;">
        <div style="font-weight:700;">⏳ 正在安裝更新(${escapeHtml(r.mode_name || "")}${r.exclude_docker ? ",不更新 Docker" : ""})・${escapeHtml(r.started_at)} 開始</div>
        <div style="margin:8px 0 4px;">解壓縮 ${pg.unpacked}/${pg.total}・設定完成 ${pg.configured}/${pg.total}</div>
        <div style="background:#dbeafe; border-radius:4px; height:8px; overflow:hidden;"><div style="background:#2563eb; height:8px; width:${pct}%;"></div></div>
        <div style="margin-top:6px; color:#6b7280;">安裝在主機背景執行,關閉或重新整理這個網頁都不會中斷。${r.docker ? "更新 Docker 時所有容器會重新啟動,MDM 服務會短暫中斷。" : ""}</div>
        <div id="os-upgrade-conn-note" style="color:#b45309;"></div>
        ${logHtml}
      </div>`;
    const pre = document.getElementById("os-upgrade-log-tail");
    pre.scrollTop = pre.scrollHeight;
    return;
  }
  const [label, color] = UPGRADE_STATE_TEXT[r.state] || [r.state, "#374151"];
  const ok = r.state === "succeeded" && !r.dpkg_audit;
  const notes = [];
  if (r.state === "failed") notes.push(r.start_error ? `無法啟動:${escapeHtml(r.start_error)}` : `apt-get 結束代碼 ${r.rc},請查看下方紀錄找出原因。`);
  if (r.state === "interrupted") notes.push("沒有找到結束紀錄,可能是安裝途中主機被重新開機或程序被強制結束。");
  if (r.dpkg_audit) notes.push(`dpkg 狀態異常,請 SSH 進主機執行 <code>dpkg --configure -a</code> 修復:${escapeHtml(r.dpkg_audit)}`);
  if (ok && r.kernel) notes.push("這次安裝了新核心,需要重新開機才會生效。");
  el.innerHTML = `
    <details ${ok ? "" : "open"} style="background:${ok ? "#f8f9fb" : "#fef2f2"}; border:1px solid ${ok ? "var(--border-color)" : "#fecaca"}; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px;">
      <summary style="cursor:pointer;">最近一次安裝更新:<strong style="color:${color};">${label}</strong>
        <span style="color:#6b7280;">${escapeHtml(r.started_at)} ~ ${escapeHtml(r.finished_at || "?")}・${escapeHtml(r.mode_name || "")}・${r.package_count} 個套件${r.username ? "・" + escapeHtml(r.username) : ""}</span></summary>
      ${notes.length ? `<ul style="margin:8px 0 0; padding-left:20px; color:${ok ? "#374151" : "#991b1b"};">${notes.map((n) => `<li>${n}</li>`).join("")}</ul>` : ""}
      ${logHtml}
    </details>`;
}

function renderUpgradeHistory(runs) {
  const el = document.getElementById("os-upgrade-history");
  if (!runs.length) { el.innerHTML = ""; return; }
  const rows = runs.map((r) => {
    const [label, color] = UPGRADE_STATE_TEXT[r.state] || [r.state, "#374151"];
    return `<tr>
      <td style="font-size:12px; white-space:nowrap;">${escapeHtml(r.started_at)}</td>
      <td style="font-size:12px;">${escapeHtml(r.mode_name || "")}${r.exclude_docker ? "<br><span style=\"color:#6b7280;\">不更新 Docker</span>" : ""}</td>
      <td style="font-size:12px;">${escapeHtml(r.username || "")}</td>
      <td style="font-size:12px;">${r.package_count}</td>
      <td style="font-size:12px; color:${color}; font-weight:600;">${label}${r.dpkg_audit ? "(dpkg 異常)" : ""}</td>
      <td><button type="button" class="secondary os-upgrade-log-btn" data-id="${escapeHtml(r.id)}" style="font-size:11px;">紀錄</button></td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <details style="margin:4px 0 10px;">
      <summary style="cursor:pointer; font-size:13px; color:#374151;">安裝更新紀錄(最近 ${runs.length} 次)</summary>
      <table class="data-table" style="margin-top:6px;">
        <thead><tr><th>開始時間</th><th>模式</th><th>帳號</th><th>套件數</th><th>結果</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </details>`;
}

async function showUpgradeLog(runId) {
  document.getElementById("os-upgrade-log-title").textContent = runId;
  document.getElementById("os-upgrade-log-content").textContent = "載入中...";
  openModal("os-upgrade-log-modal");
  const res = await apiFetch(`/api/os-update/upgrade/${encodeURIComponent(runId)}/log`);
  document.getElementById("os-upgrade-log-content").textContent =
    res.ok ? (res.data.log || "(沒有輸出)") : `載入失敗:${(res.data && res.data.message) || "未知錯誤"}`;
}

function openUpgradeModal() {
  document.querySelector('input[name="os-upgrade-mode"][value="upgrade"]').checked = true;
  document.getElementById("os-upgrade-exclude-docker").checked = false;
  document.getElementById("os-upgrade-backup-confirm").checked = false;
  document.getElementById("os-upgrade-password").value = "";
  openModal("os-upgrade-modal");
  loadUpgradePlan();
}

function upgradeOptions() {
  return {
    mode: document.querySelector('input[name="os-upgrade-mode"]:checked').value,
    exclude_docker: document.getElementById("os-upgrade-exclude-docker").checked,
  };
}

function refreshUpgradeConfirmBtn() {
  const btn = document.getElementById("os-upgrade-confirm-btn");
  btn.disabled = !(osUpgradePlan && osUpgradePlan.can_apply && document.getElementById("os-upgrade-backup-confirm").checked);
}

async function loadUpgradePlan() {
  const el = document.getElementById("os-upgrade-plan");
  const seq = ++osUpgradePlanSeq;
  osUpgradePlan = null;
  refreshUpgradeConfirmBtn();
  el.innerHTML = `<p style="color:#9ca3af; font-size:13px;">正在模擬安裝、執行安裝前檢查...</p>`;
  const res = await apiFetchJSON("/api/os-update/plan", "POST", upgradeOptions());
  if (seq !== osUpgradePlanSeq) return;   // 使用者在等待時又改了選項
  if (!res.ok) {
    el.innerHTML = `<p style="color:#d64545; font-size:13px;">${escapeHtml((res.data && res.data.message) || "模擬失敗")}</p>`;
    return;
  }
  const p = res.data.plan;
  osUpgradePlan = p;
  const icon = { ok: ["✓", "#047857"], warn: ["!", "#b45309"], fail: ["✕", "#d64545"] };
  const checks = p.checks.map((c) => {
    const [i, color] = icon[c.level] || ["?", "#374151"];
    return `<div style="color:${c.level === "ok" ? "#374151" : color};"><span style="color:${color}; font-weight:700; display:inline-block; width:16px;">${i}</span>${escapeHtml(c.name)}:${escapeHtml(c.message)}</div>`;
  }).join("");
  const sm = p.summary;
  const notes = [];
  if (sm.kernel) notes.push(`<li>包含核心更新,安裝後需要<strong>重新開機</strong>才會生效(不會自動重開)。</li>`);
  if (sm.docker) notes.push(`<li style="color:#991b1b;">包含 ${sm.docker} 個 Docker 套件:安裝時 Docker 會重新啟動,<strong>所有容器(含 MDM 服務)會中斷約 1 分鐘</strong>。不想這次更新可以勾選上方「這次不更新 Docker 套件」。</li>`);
  if (p.exclude_docker && p.docker_excluded.length) notes.push(`<li>這次不更新:${p.docker_excluded.map(escapeHtml).join("、")}</li>`);
  if (sm.removals) notes.push(`<li style="color:#991b1b; font-weight:600;">會移除 ${sm.removals} 個套件:${p.removals.map((r) => escapeHtml(r.name)).join("、")},請確認不影響服務。</li>`);
  if (sm.kept_back) notes.push(`<li>有 ${sm.kept_back} 個套件這次不會更新(${p.kept_back.map(escapeHtml).join("、")}),需要「完整更新」才能更新。</li>`);
  const pkgRows = p.packages.map((x) => `<div style="font-family:var(--mono); font-size:11px;">${escapeHtml(x.name)} <span style="color:#6b7280;">${escapeHtml(x.old_version || "(新安裝)")} → ${escapeHtml(x.new_version)}</span></div>`).join("");
  el.innerHTML = `
    <div style="background:#f8f9fb; border-radius:6px; padding:10px 12px; font-size:12px; line-height:1.8; margin-bottom:10px;">
      <div style="font-weight:700; margin-bottom:2px;">安裝前檢查</div>${checks}
    </div>
    ${sm.total ? `<p style="font-size:14px; font-weight:600; margin:0 0 6px;">將安裝 ${sm.total} 個套件${sm.removals ? `、移除 ${sm.removals} 個套件` : ",不會移除任何套件"}</p>
      <details style="margin-bottom:6px;"><summary style="cursor:pointer; font-size:12px; color:#6b7280;">套件清單</summary>
        <div style="max-height:180px; overflow-y:auto; margin-top:4px;">${pkgRows}</div></details>`
      : `<p style="font-size:14px; color:#047857; margin:0 0 6px;">這個模式下沒有需要安裝的更新</p>`}
    ${notes.length ? `<ul style="font-size:13px; margin:0 0 6px; padding-left:20px; line-height:1.7;">${notes.join("")}</ul>` : ""}`;
  refreshUpgradeConfirmBtn();
}

async function confirmUpgrade() {
  if (!osUpgradePlan || !osUpgradePlan.can_apply) return;
  const password = document.getElementById("os-upgrade-password").value;
  if (!password) { alert("請輸入您目前登入的密碼"); return; }
  const btn = document.getElementById("os-upgrade-confirm-btn");
  btn.disabled = true;
  const res = await apiFetchJSON("/api/os-update/upgrade", "POST", {
    ...upgradeOptions(), password, plan_hash: osUpgradePlan.plan_hash,
    backup_confirmed: document.getElementById("os-upgrade-backup-confirm").checked,
  });
  if (!res.ok) {
    alert((res.data && res.data.message) || "無法開始安裝");
    refreshUpgradeConfirmBtn();
    return;
  }
  closeModal("os-upgrade-modal");
  setPanelExpanded("os-update-panel", true);
  loadOsUpdateStatus();
}

async function rebootHost() {
  const st = osUpdateStatus;
  const reasons = st && st.reboot && st.reboot.reasons.length ? `原因:${st.reboot.reasons.join(";")}\n` : "";
  const password = await askPassword(
    `確定要重新開機這台主機嗎?\n${reasons}`
    + "主機會在 10 秒後重新開機,所有服務(含 MDM、資料庫)會中斷約 1~3 分鐘,開機後會自動啟動。\n"
    + "建議先在 PVE 做好快照,並確認沒有人正在使用。\n\n請輸入您目前登入的密碼以確認:");
  if (!password) return;
  const res = await apiFetchJSON("/api/os-update/reboot", "POST", { password });
  if (!res.ok) {
    alert((res.data && res.data.message) || "無法重新開機");
    return;
  }
  document.getElementById("os-update-reboot").innerHTML = `
    <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:6px; padding:10px 12px; margin-bottom:10px; font-size:13px; color:#9a3412;">
      主機將在 ${res.data.delay} 秒後重新開機。開機約需 1~3 分鐘,之後請重新整理這個頁面。</div>`;
  clearTimeout(osUpdatePollTimer);
}

async function loadOsUpdateStatus() {
  const res = await apiFetch("/api/os-update/status");
  if (!res.ok) {
    clearTimeout(osUpdatePollTimer);
    if (osUpdateStatus && osUpdateStatus.upgrade_running) {
      // 安裝 Docker / nginx 等套件時,網頁可能短暫連不上,繼續重試
      const note = document.getElementById("os-upgrade-conn-note");
      if (note) note.textContent = "暫時無法連線到 WebUI(可能是相關服務正在重新啟動),安裝仍在主機背景進行,持續重試中…";
      osUpdatePollTimer = setTimeout(loadOsUpdateStatus, 4000);
      return;
    }
    document.getElementById("os-update-result").innerHTML =
      `<p style="color:#d64545; font-size:13px;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const prevState = osUpdateStatus && osUpdateStatus.job && osUpdateStatus.job.state;
  osUpdateStatus = res.data;
  // 切換鏡像站完成後,選取的項目改回目前使用的鏡像站
  if (prevState === "running" && res.data.job.state !== "running") osMirrorSelected = res.data.sources.current_mirror_id;
  renderOsReboot(res.data.reboot);
  renderOsResult(res.data);
  renderOsMirror(res.data);
  renderOsJob(res.data.job);
  renderUpgradeRun(res.data);
  renderUpgradeHistory(res.data.upgrade_runs || []);
  renderOsSummary(res.data);
  updateOsButtons(res.data);
  clearTimeout(osUpdatePollTimer);
  if (res.data.job.state === "running" || res.data.upgrade_running) {
    osUpdatePollTimer = setTimeout(loadOsUpdateStatus, res.data.upgrade_running ? 3000 : 2000);
  }
}

async function startOsUpdateCheck() {
  const res = await apiFetchJSON("/api/os-update/check", "POST", {});
  if (!res.ok) {
    alert((res.data && res.data.message) || "無法開始檢查");
    return;
  }
  loadOsUpdateStatus();
}

async function runOsMirrorSpeedTest() {
  if (!osUpdateStatus) return;
  osUpdateStatus.mirrors.forEach((m) => { osMirrorSpeed[m.id] = "testing"; });
  renderOsMirror(osUpdateStatus);
  const res = await apiFetchJSON("/api/os-update/speed-test", "POST", {});
  osMirrorSpeed = {};
  if (res.ok) res.data.results.forEach((r) => { osMirrorSpeed[r.id] = r; });
  else alert((res.data && res.data.message) || "測速失敗");
  renderOsMirror(osUpdateStatus);
}

async function applyOsMirror() {
  if (!osUpdateStatus) return;
  const mirror = osUpdateStatus.mirrors.find((m) => m.id === osMirrorSelected);
  if (!mirror) { alert("請先選擇一個鏡像站"); return; }
  if (mirror.id === osUpdateStatus.sources.current_mirror_id) { alert(`目前已經是 ${mirror.name}`); return; }
  const password = await askPassword(
    `要把 Debian 套件鏡像站切換成「${mirror.name}」(${mirror.url})嗎?\n`
    + `會修改:${osUpdateStatus.sources.files.join("、")}(修改前自動備份)。\n`
    + "切換後會重新下載套件清單,新的鏡像站無法使用時自動還原。只改下載來源,不會安裝任何套件。\n\n請輸入您目前登入的密碼以確認:");
  if (!password) return;
  const res = await apiFetchJSON("/api/os-update/mirror", "POST", { password, mirror_id: mirror.id });
  if (!res.ok) {
    alert((res.data && res.data.message) || "切換失敗");
    return;
  }
  loadOsUpdateStatus();
}

// ---- 更新後待處理事項(例如電子書的 nginx 設定):由管理者按按鈕執行,不在背景自動處理 ----
let postUpdateTasks = [];

async function loadPostUpdateTasks() {
  const box = document.getElementById("post-update-tasks");
  const res = await apiFetch("/api/version/post-update-tasks");
  postUpdateTasks = res.ok && res.data.ok ? res.data.tasks : [];
  if (!postUpdateTasks.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<div style="font-weight:600; color:#b45309; margin-bottom:6px;">⚠️ 更新後待處理事項</div>` +
    postUpdateTasks.map((t, i) => `
      <div style="font-size:13px; padding:6px 0; ${i ? "border-top:1px solid #f5d0a9;" : ""}">
        <div style="font-weight:600;">${escapeHtml(t.title)}</div>
        <div style="color:#6b7280; font-size:12px; margin:2px 0 6px;">${escapeHtml(t.description)}</div>
        ${t.error ? `<div style="color:#b42318; font-size:12px; margin-bottom:6px;">無法自動處理:${escapeHtml(t.error)}</div>` : ""}
        ${t.can_apply ? `<button type="button" class="post-update-task-btn" data-index="${i}" style="font-size:12px;">${escapeHtml(t.button)}</button>` : ""}
      </div>`).join("");
  box.classList.remove("hidden");
}

async function runPostUpdateTask(index, btn) {
  const t = postUpdateTasks[index];
  if (!t || !confirm(`確定要執行「${t.title}」嗎?\n\n${t.description}`)) return;
  btn.disabled = true;
  btn.textContent = "處理中...";
  const res = await apiFetchJSON(t.action, "POST", {});
  alert((res.ok && res.data.ok ? "" : "失敗: ") + ((res.data && res.data.message) || "未知錯誤"));
  loadPostUpdateTasks();
}

document.addEventListener("DOMContentLoaded", () => {
  loadPostUpdateTasks();
  document.getElementById("post-update-tasks").addEventListener("click", (e) => {
    const btn = e.target.closest(".post-update-task-btn");
    if (btn) runPostUpdateTask(parseInt(btn.dataset.index, 10), btn);
  });
});

document.addEventListener("DOMContentLoaded", () => {
  loadCurrentVersion();
  initCollapsiblePanels();
  loadOsUpdateStatus();
  document.getElementById("os-update-check-btn").addEventListener("click", startOsUpdateCheck);
  document.getElementById("os-update-mirror").addEventListener("click", (e) => {
    if (e.target.closest("#os-mirror-speed-btn")) runOsMirrorSpeedTest();
    else if (e.target.closest("#os-mirror-apply-btn")) applyOsMirror();
  });
  document.getElementById("os-update-mirror").addEventListener("change", (e) => {
    if (e.target.name === "os-mirror") osMirrorSelected = e.target.value;
  });
  document.getElementById("os-update-panel").addEventListener("click", (e) => {
    if (e.target.closest("#os-upgrade-open-btn")) openUpgradeModal();
    else if (e.target.closest("#os-reboot-btn")) rebootHost();
    else {
      const logBtn = e.target.closest(".os-upgrade-log-btn");
      if (logBtn) showUpgradeLog(logBtn.dataset.id);
    }
  });
  document.querySelectorAll('input[name="os-upgrade-mode"]').forEach((r) => r.addEventListener("change", loadUpgradePlan));
  document.getElementById("os-upgrade-exclude-docker").addEventListener("change", loadUpgradePlan);
  document.getElementById("os-upgrade-backup-confirm").addEventListener("change", refreshUpgradeConfirmBtn);
  document.getElementById("os-upgrade-confirm-btn").addEventListener("click", confirmUpgrade);
  loadUpdateHistory();
  loadRollbackInfo();
  checkForUpdate(); // 一進頁面就自動檢測一次,不用使用者自己按按鈕
  loadComposePlan();
  document.getElementById("compose-refresh-btn").addEventListener("click", loadComposePlan);

  document.getElementById("version-manual-save-btn").addEventListener("click", saveManualVersion);
  document.getElementById("version-check-btn").addEventListener("click", checkForUpdate);
  document.getElementById("version-history-refresh-btn").addEventListener("click", () => loadUpdateHistory(true));
  document.getElementById("version-apply-confirm-btn").addEventListener("click", confirmApplyUpdate);

  document.getElementById("version-verify-btn").addEventListener("click", () => runVersionVerify());
  document.getElementById("version-verify-result").addEventListener("click", async (e) => {
    const tagBtn = e.target.closest(".verify-use-tag-btn");
    if (tagBtn) {
      runVersionVerify(tagBtn.dataset.tag || "");
      return;
    }
    if (e.target.closest("#verify-repair-btn")) {
      runVerifyRepair();
      return;
    }
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
