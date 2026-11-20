// ---------------------------------------------------------------------------
// 電子書:群組一覽頁的「電子書」區塊(上傳、改書名、刪除)與群組電子書視窗(勾選、派送)
// ---------------------------------------------------------------------------
let ebookLibrary = [];
let currentGroupForEbooks = null;
let groupEbooksState = null; // { group, data }
let groupEbooksRunning = false;

function formatEbookSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function ebookNotice(type, text) {
  const styleOf = { error: "background:#fdeaea; color:#b42318;", warn: "background:#fdeee0; color:#b45309;", info: "background:#eef3fc; color:#2c4f96;" };
  return `<div style="padding:8px 12px; border-radius:6px; font-size:12px; margin-bottom:6px; ${styleOf[type]}">${escapeHtml(text)}</div>`;
}

async function loadEbookLibrary() {
  const tbody = document.getElementById("ebook-library-tbody");
  const res = await apiFetch("/api/ebooks");
  if (!res.ok || !res.data.ok) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  const d = res.data;
  ebookLibrary = d.books;
  const notes = [];
  if (!d.nginx.installed) notes.push(["warn", "nginx 還沒有加入電子書下載設定,iPad 目前下載不到電子書,上傳也只能到 nginx 原本的上限。請到「系統參數 → 電子書下載IP範圍限制」按「加入 nginx 設定」。"]);
  if (!d.domain) notes.push(["warn", "無法判斷本台伺服器的網域,請在 .env 加上 SERVER_DOMAIN,否則無法派送電子書。"]);
  notes.push(["info", `檔案大小上限 ${d.settings.max_size_mb} MB;下載 IP 範圍:${d.settings.ip_allowlist.length ? d.settings.ip_allowlist.join("、") : "不限制(知道網址的人都能下載)"}`]);
  document.getElementById("ebook-library-notice").innerHTML = notes.map(([t, x]) => ebookNotice(t, x)).join("");

  if (!d.books.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:#9ca3af;">還沒有上傳任何電子書</td></tr>`;
    return;
  }
  tbody.innerHTML = d.books.map((b) => `
    <tr>
      <td><strong>${escapeHtml(b.title)}</strong><div style="font-size:11px; color:#9ca3af;">${escapeHtml(b.original_filename || "")}</div></td>
      <td>${b.kind.toUpperCase()}</td>
      <td>${formatEbookSize(b.size)}</td>
      <td>${escapeHtml(b.uploaded_at)}</td>
      <td>${b.groups.length ? b.groups.map(escapeHtml).join("、") : `<span style="color:#9ca3af;">(未綁定)</span>`}</td>
      <td><button class="secondary" type="button" data-ebook-action="rename" data-id="${b.id}">修改書名</button></td>
      <td><button class="danger" type="button" data-ebook-action="delete" data-id="${b.id}">刪除</button></td>
    </tr>`).join("");
}

function uploadEbook() {
  const fileInput = document.getElementById("ebook-upload-file");
  const titleInput = document.getElementById("ebook-upload-title");
  const file = fileInput.files[0];
  const title = titleInput.value.trim();
  if (!file) { alert("請選擇電子書檔案"); return; }
  if (!title) { alert("請輸入書名"); return; }

  const btn = document.getElementById("ebook-upload-btn");
  const progress = document.getElementById("ebook-upload-progress");
  const form = new FormData();
  form.append("title", title);
  form.append("file", file);
  btn.disabled = true;
  progress.classList.remove("hidden");
  progress.textContent = "上傳中 0%";

  // 用 XMLHttpRequest 才拿得到上傳進度
  const xhr = new XMLHttpRequest();
  xhr.open("POST", apiUrl("/api/ebooks/upload"));
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) progress.textContent = `上傳中 ${Math.round((e.loaded / e.total) * 100)}%`;
    if (e.loaded === e.total) progress.textContent = "上傳完成,檢查檔案格式中...";
  };
  xhr.onload = () => {
    btn.disabled = false;
    progress.classList.add("hidden");
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* nginx 錯誤頁不是 JSON */ }
    if (xhr.status === 413) {
      alert("檔案超過 nginx 的上傳上限。請先到「系統參數 → 電子書下載IP範圍限制」按「加入 nginx 設定」再上傳。");
      return;
    }
    if (!data) { alert(`上傳失敗(HTTP ${xhr.status})`); return; }
    alert(data.message || (data.ok ? "已上傳" : "上傳失敗"));
    if (data.ok) {
      fileInput.value = "";
      titleInput.value = "";
      loadEbookLibrary();
    }
  };
  xhr.onerror = () => {
    btn.disabled = false;
    progress.classList.add("hidden");
    alert("上傳失敗:與伺服器的連線中斷");
  };
  xhr.send(form);
}

async function renameEbook(id) {
  const book = ebookLibrary.find((b) => b.id === id);
  if (!book) return;
  const title = prompt("新的書名(已經派送到 iPad 的書不會改名,之後新派送的才會用新書名)", book.title);
  if (title === null || !title.trim() || title.trim() === book.title) return;
  const res = await apiFetchJSON(`/api/ebooks/${id}/rename`, "POST", { title: title.trim() });
  alert((res.data && res.data.message) || (res.ok ? "已修改" : "修改失敗"));
  if (res.ok && res.data.ok) { loadEbookLibrary(); loadGroups(); }
}

async function deleteEbook(id) {
  const book = ebookLibrary.find((b) => b.id === id);
  if (!book) return;
  if (book.groups.length) {
    alert(`「${book.title}」還綁定在群組:${book.groups.join("、")}。\n請先到群組一覽點「電子書」數字取消勾選(可以選擇是否從 iPad 移除),再刪除。`);
    return;
  }
  if (!confirm(`確定要刪除「${book.title}」嗎?伺服器上的檔案會一起刪除,無法復原。\n已經派送到 iPad 的書不會被移除。`)) return;
  const res = await apiFetchJSON(`/api/ebooks/${id}/delete`, "POST", {});
  alert((res.data && res.data.message) || (res.ok ? "已刪除" : "刪除失敗"));
  if (res.ok && res.data.ok) loadEbookLibrary();
}

// ---- 群組電子書視窗 ----
function getGroupEbookChanges() {
  if (!groupEbooksState) return { install: [], remove: [] };
  const byId = Object.fromEntries(groupEbooksState.data.books.map((b) => [b.id, b]));
  const install = [];
  const remove = [];
  document.querySelectorAll("#group-ebooks-list .group-ebook-cb").forEach((cb) => {
    const b = byId[cb.dataset.id];
    if (!b) return;
    if (cb.checked && !b.bound) install.push(b);
    if (!cb.checked && b.bound) remove.push(b);
  });
  return { install, remove };
}

function syncGroupEbookChanges() {
  const { install, remove } = getGroupEbookChanges();
  const n = install.length + remove.length;
  document.getElementById("group-ebooks-save-btn").disabled = n === 0 || groupEbooksRunning;
  document.getElementById("group-ebooks-change-count").textContent = n ? `待儲存:派送 ${install.length} 本、取消 ${remove.length} 本` : "";
}

async function openGroupEbooksModal(groupName) {
  if (groupEbooksRunning) { openModal("group-ebooks-modal"); return; }
  currentGroupForEbooks = groupName;
  groupEbooksState = null;
  document.getElementById("group-ebooks-modal-title").textContent = groupName;
  document.getElementById("group-ebooks-notice").innerHTML = "";
  document.getElementById("group-ebooks-progress").innerHTML = "";
  document.getElementById("group-ebooks-list").innerHTML = "載入中...";
  document.getElementById("group-ebooks-change-count").textContent = "";
  document.getElementById("group-ebooks-save-btn").disabled = true;
  openModal("group-ebooks-modal");
  await refreshGroupEbooks();
}

function closeGroupEbooksModal() {
  if (groupEbooksRunning && !confirm("批次處理還在進行中,關閉視窗不會中斷處理,但看不到後續進度。要關閉嗎?")) return;
  closeModal("group-ebooks-modal");
}

async function refreshGroupEbooks() {
  const list = document.getElementById("group-ebooks-list");
  const notice = document.getElementById("group-ebooks-notice");
  const res = await apiFetch(`/api/groups/${encodeURIComponent(currentGroupForEbooks)}/ebook-manager`);
  if (!res.ok || !res.data.ok) {
    list.innerHTML = `<p style="color:#d64545; font-size:13px;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const d = res.data;
  // 已綁定的置頂,其餘依上傳時間(新的在前,後端已排序)
  d.books.sort((a, b) => (a.bound === b.bound ? 0 : a.bound ? -1 : 1));
  groupEbooksState = { group: currentGroupForEbooks, data: d };

  const notes = [];
  if (d.dispatch_problem) notes.push(["warn", d.dispatch_problem]);
  if (d.device_count === null) notes.push(["warn", "查詢群組裡的裝置失敗,儲存時仍會更新群組綁定。"]);
  else if (d.device_count === 0) notes.push(["info", "這個群組目前沒有已註冊的裝置。儲存只會更新群組綁定,之後新註冊的 iPad 會自動派送。"]);
  notice.innerHTML = notes.map(([t, x]) => ebookNotice(t, x)).join("");

  list.innerHTML = d.books.length ? d.books.map((b) => `
    <label style="display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border-color); cursor:pointer;">
      <input type="checkbox" class="group-ebook-cb" data-id="${b.id}" ${b.bound ? "checked" : ""} style="flex-shrink:0;">
      <div style="min-width:0; flex:1;">
        <div style="font-size:13px; font-weight:600;">${escapeHtml(b.title)}${b.bound ? `<span style="font-weight:400; margin-left:4px; color:#1c7c3f;">(群組綁定)</span>` : ""}</div>
        <div style="font-size:11px; color:#9ca3af;">${b.kind.toUpperCase()} · ${formatEbookSize(b.size)} · 上傳於 ${escapeHtml(b.uploaded_at)}</div>
      </div>
    </label>`).join("")
    : `<p style="color:#9ca3af; font-size:13px;">還沒有上傳任何電子書,請先在群組一覽下方的「電子書」區塊上傳。</p>`;
  syncGroupEbookChanges();
}

function askRemoveFromDevices(removeBooks, deviceCount) {
  // 回傳 Promise:"remove" / "keep" / null(返回)
  return new Promise((resolve) => {
    const countText = deviceCount ? `群組裡已註冊的 iPad 共 ${deviceCount} 台。` : "";
    document.getElementById("group-ebooks-remove-text").textContent =
      `以下 ${removeBooks.length} 本電子書將取消群組綁定:\n${removeBooks.map((b) => `・${b.title}`).join("\n")}\n\n要一併從 iPad 的「書籍」App 移除嗎?${countText}`;
    const done = (v) => { closeModal("group-ebooks-remove-modal"); resolve(v); };
    document.getElementById("group-ebooks-remove-yes-btn").onclick = () => done("remove");
    document.getElementById("group-ebooks-remove-keep-btn").onclick = () => done("keep");
    document.getElementById("group-ebooks-remove-cancel-btn").onclick = () => done(null);
    openModal("group-ebooks-remove-modal");
  });
}

async function saveGroupEbooks() {
  if (!groupEbooksState || groupEbooksRunning) return;
  const { install, remove } = getGroupEbookChanges();
  if (!install.length && !remove.length) return;
  const d = groupEbooksState.data;
  if (install.length && d.dispatch_problem) {
    alert(d.dispatch_problem);
    return;
  }

  let removeFromDevices = false;
  if (remove.length) {
    const choice = await askRemoveFromDevices(remove, d.device_count);
    if (!choice) return;
    removeFromDevices = choice === "remove";
  }
  if (install.length) {
    let msg = `群組「${groupEbooksState.group}」即將派送 ${install.length} 本電子書:\n${install.map((b) => `・${b.title}`).join("\n")}\n`;
    msg += d.device_count ? `\n群組裡已註冊的 iPad 共 ${d.device_count} 台會收到。\n` : "\n群組裡目前沒有已註冊的 iPad,這次只會更新群組綁定。\n";
    msg += "\n確定要執行嗎?";
    if (!confirm(msg)) return;
  }
  runGroupEbooksApply(install.map((b) => b.id), remove.map((b) => b.id), removeFromDevices);
}

function runGroupEbooksApply(installIds, removeIds, removeFromDevices) {
  const box = document.getElementById("group-ebooks-progress");
  const saveBtn = document.getElementById("group-ebooks-save-btn");
  const group = groupEbooksState.group;
  groupEbooksRunning = true;
  saveBtn.disabled = true;
  saveBtn.textContent = "處理中...";
  document.querySelectorAll("#group-ebooks-list .group-ebook-cb").forEach((cb) => { cb.disabled = true; });
  box.innerHTML = "";

  const add = (html, style) => {
    const div = document.createElement("div");
    div.style.cssText = style;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  };
  const finish = async () => {
    groupEbooksRunning = false;
    saveBtn.textContent = "儲存";
    loadGroups();
    loadEbookLibrary();
    if (currentGroupForEbooks === group && !document.getElementById("group-ebooks-modal").classList.contains("hidden")) {
      await refreshGroupEbooks();
    }
  };

  const q = new URLSearchParams({ install: installIds.join(","), remove: removeIds.join(","), remove_from_devices: removeFromDevices ? "1" : "0" });
  const es = new EventSource(apiUrl(`/api/groups/${encodeURIComponent(group)}/ebook-manager/apply-stream?${q.toString()}`));
  es.onmessage = (event) => {
    let u;
    try { u = JSON.parse(event.data); } catch (e) { return; }
    if (u.error) {
      add(`❌ ${escapeHtml(u.error)}`, "background:#fdeaea; color:#b42318; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;");
    } else if (u.done) {
      const partial = u.total && u.success_count < u.total;
      const text = u.message || `完成:${u.success_count}/${u.total} 台已送出指令。iPad 連線後才會實際下載或移除電子書。`;
      add(`${partial ? "⚠️" : "✅"} ${escapeHtml(text)}`,
        `padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px; ${partial ? "background:#fdeee0; color:#b45309;" : "background:#e3f6e9; color:#1c7c3f;"}`);
    } else if (u.message && !u.serial_number) {
      add(escapeHtml(u.message), "color:#6b7280; font-size:12px; margin-top:6px;");
    } else {
      const extra = (u.failures || []).map((t) => `<div style="color:#b42318; margin-left:14px;">${escapeHtml(t)}</div>`).join("");
      add(`[${u.index}/${u.total}] ${escapeHtml(u.device_name || "(未命名)")} ${escapeHtml(u.serial_number)}:${u.ok ? "✅ 已送出" : "❌ 部分失敗"}${extra}`,
        "border:1px solid var(--border-color); border-radius:6px; padding:6px 10px; font-size:12px; margin-top:4px;");
    }
    if (u.done || u.error) { es.close(); finish(); }
  };
  es.onerror = () => {
    es.close();
    add("❌ 與伺服器的連線中斷,請到「系統紀錄」查看實際處理結果。", "background:#fdeaea; color:#b42318; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;");
    finish();
  };
}

document.addEventListener("DOMContentLoaded", () => {
  loadEbookLibrary();
  document.getElementById("ebook-upload-btn").addEventListener("click", uploadEbook);
  document.getElementById("ebook-upload-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    const titleInput = document.getElementById("ebook-upload-title");
    if (f && !titleInput.value.trim()) titleInput.value = f.name.replace(/\.(pdf|epub)$/i, "").slice(0, 100);
  });
  document.getElementById("ebook-library-tbody").addEventListener("click", (e) => {
    const el = e.target.closest("[data-ebook-action]");
    if (!el) return;
    if (el.dataset.ebookAction === "rename") renameEbook(el.dataset.id);
    else if (el.dataset.ebookAction === "delete") deleteEbook(el.dataset.id);
  });
  document.getElementById("group-ebooks-save-btn").addEventListener("click", saveGroupEbooks);
  document.getElementById("group-ebooks-list").addEventListener("change", (e) => {
    if (e.target.classList.contains("group-ebook-cb")) syncGroupEbookChanges();
  });
});
