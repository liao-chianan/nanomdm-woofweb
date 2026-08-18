let pendingDiffTargetTag = null;

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

async function loadVersionList() {
  const tbody = document.getElementById("version-list-tbody");
  tbody.innerHTML = `<tr><td colspan="4">載入中...</td></tr>`;

  const res = await apiFetch("/api/version/tags");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }

  const tags = res.data.tags || [];
  if (tags.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4">GitHub 上目前沒有任何版本標籤</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  tags.forEach((t) => {
    const tr = document.createElement("tr");
    const notesPreview = t.release_notes
      ? escapeHtml(t.release_notes).slice(0, 60) + (t.release_notes.length > 60 ? "..." : "")
      : `<span style="color:#9ca3af;">(無說明)</span>`;
    tr.innerHTML = `
      <td style="font-family:var(--mono); font-size:13px;">${escapeHtml(t.name)}</td>
      <td style="font-size:12px; color:#6b7280;">${escapeHtml(t.published_at || "-")}</td>
      <td style="font-size:12px;">${notesPreview}</td>
      <td><button class="secondary version-switch-btn" type="button" style="font-size:12px;" data-tag="${escapeHtml(t.name)}">切換到此版本</button></td>
    `;
    tbody.appendChild(tr);
  });
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
  if (files.length === 0) {
    document.getElementById("version-diff-file-list").innerHTML = "";
    document.getElementById("version-diff-empty-hint").textContent = "這兩個版本之間,沒有任何符合條件的檔案差異。";
  } else {
    document.getElementById("version-diff-file-list").innerHTML = files.map((f) =>
      `<div style="font-size:12px; font-family:var(--mono); padding:2px 0;">[${statusLabel[f.status] || f.status}] ${escapeHtml(f.repo_path)}</div>`
    ).join("");
  }
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

document.addEventListener("DOMContentLoaded", () => {
  loadCurrentVersion();
  loadVersionList();

  document.getElementById("version-manual-save-btn").addEventListener("click", saveManualVersion);
  document.getElementById("version-check-btn").addEventListener("click", checkForUpdate);
  document.getElementById("version-list-refresh-btn").addEventListener("click", loadVersionList);
  document.getElementById("version-apply-confirm-btn").addEventListener("click", confirmApplyUpdate);

  document.getElementById("version-list-tbody").addEventListener("click", (e) => {
    if (e.target.classList.contains("version-switch-btn")) {
      openDiffModal(e.target.dataset.tag);
    }
  });
});
