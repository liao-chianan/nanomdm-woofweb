// ---------------------------------------------------------------------------
// 鎖定畫面資訊頁
// ---------------------------------------------------------------------------
let lsSettings = null;      // 伺服器上「已儲存」的設定(推送時用的就是這份)
let lsDevices = [];
let lsPreviewTimer = null;
let lsPreviewUrl = null;
let lsPreviewSeq = 0;
let lsDirty = false;

function lsEl(id) { return document.getElementById(id); }

function lsNotice(el, type, text) {
  el.className = `ls-notice ${type}`;
  el.textContent = text;
}

// ---- 表單 <-> 設定物件 ----
function lsFillForm(s) {
  lsEl("ls-lines").value = (s.lines || []).join("\n");
  lsEl("ls-org-name").value = s.org_name || "";
  lsEl("ls-org-address").value = s.org_address || "";
  lsEl("ls-org-phone").value = s.org_phone || "";
  lsEl("ls-org-email").value = s.org_email || "";
  lsSetColor("ls-bg-color", s.bg_color);
  lsSetColor("ls-text-color", s.text_color);
  lsEl("ls-title-size").value = s.title_font_px;
  lsEl("ls-body-size").value = s.body_font_px;
  lsEl("ls-min-size").value = s.min_font_px;
  lsEl("ls-band").checked = !!s.band;
  lsEl("ls-home-mode").value = s.home_mode || "color";
  lsSetColor("ls-home-color", s.home_color);
  lsSyncHomeUi();
  lsEl("ls-auto-push").checked = !!s.auto_push;
  lsEl("ls-font-path").value = s.font_path || "";
}

function lsReadForm() {
  return {
    lines: lsEl("ls-lines").value.split("\n"),
    org_name: lsEl("ls-org-name").value,
    org_address: lsEl("ls-org-address").value,
    org_phone: lsEl("ls-org-phone").value,
    org_email: lsEl("ls-org-email").value,
    bg_color: lsEl("ls-bg-color-text").value.trim(),
    text_color: lsEl("ls-text-color-text").value.trim(),
    title_font_px: lsEl("ls-title-size").value,
    body_font_px: lsEl("ls-body-size").value,
    min_font_px: lsEl("ls-min-size").value,
    band: lsEl("ls-band").checked,
    home_mode: lsEl("ls-home-mode").value,
    home_color: lsEl("ls-home-color-text").value.trim(),
    auto_push: lsEl("ls-auto-push").checked,
    font_path: lsEl("ls-font-path").value,
  };
}

function lsSetColor(baseId, value) {
  lsEl(baseId).value = value || "#000000";
  lsEl(baseId + "-text").value = value || "";
}

function lsBindColor(baseId) {
  const picker = lsEl(baseId);
  const text = lsEl(baseId + "-text");
  picker.addEventListener("input", () => { text.value = picker.value; lsOnFormChange(); });
  text.addEventListener("input", () => {
    if (/^#[0-9a-fA-F]{6}$/.test(text.value.trim())) picker.value = text.value.trim();
    lsOnFormChange();
  });
}

function lsOnFormChange() {
  lsDirty = true;
  lsEl("ls-save-msg").innerHTML = `<span style="font-size:12px; color:#b45309;">有尚未儲存的變更</span>`;
  clearTimeout(lsPreviewTimer);
  lsPreviewTimer = setTimeout(lsRefreshPreview, 600);
}

// ---- 相依套件/字型狀態 ----
function lsShowDeps(deps) {
  const box = lsEl("ls-deps-notice");
  const info = lsEl("ls-font-info");
  if (deps && deps.ok) {
    box.classList.add("hidden");
    info.textContent = `目前字型:${deps.font_name}(${deps.font_path})`;
  } else {
    lsNotice(box, "warn", (deps && deps.message) || "無法檢查圖片產生所需的套件");
    box.classList.remove("hidden");
    info.textContent = "";
  }
}

// ---- 載入 ----
async function lsLoadSettings() {
  const res = await apiFetch("/api/lockscreen/settings");
  if (!res.ok || !res.data.ok) {
    lsNotice(lsEl("ls-deps-notice"), "error", "讀取設定失敗");
    lsEl("ls-deps-notice").classList.remove("hidden");
    return;
  }
  lsSettings = res.data.settings;
  lsHomeImageExists = !!res.data.home_image_exists;
  lsFillForm(lsSettings);
  lsShowDeps(res.data.deps);

  // 第一次使用(還沒有設定檔)、組織資訊全空時,自動從 ASM 帶入一次,省一個步驟
  const orgEmpty = !lsSettings.org_name && !lsSettings.org_address && !lsSettings.org_phone && !lsSettings.org_email;
  if (!res.data.settings_exists && orgEmpty) {
    await lsFillFromAsm(true);
  }
}

async function lsLoadDevices() {
  const res = await apiFetch("/api/lockscreen/devices");
  if (!res.ok || !res.data.ok) return;
  lsDevices = res.data.rows || [];

  const preview = lsEl("ls-preview-device");
  const pushDevice = lsEl("ls-push-device");
  const pushGroup = lsEl("ls-push-group");

  for (const d of lsDevices) {
    const label = `${d.device_name || "(未命名)"} / ${d.group || "(未分類)"} / ${d.serial_number}`;
    preview.appendChild(new Option(label, d.serial_number));
    if (d.enrollment_id) pushDevice.appendChild(new Option(label, d.serial_number));
  }
  if (!pushDevice.options.length) pushDevice.appendChild(new Option("(沒有已註冊的裝置)", ""));

  for (const g of res.data.groups || []) {
    const count = lsDevices.filter((d) => d.group === g && d.enrollment_id).length;
    pushGroup.appendChild(new Option(`${g}(${count} 台已註冊)`, g));
  }
  if (!pushGroup.options.length) pushGroup.appendChild(new Option("(沒有群組)", ""));

  if (!res.data.mdm_query_ok) {
    debugLog("查詢 MDM 註冊狀態失敗,推送清單可能不完整", res.data.mdm_query_error, true);
  }
}

// ---- 從 ASM 帶入 ----
async function lsFillFromAsm(silent) {
  const btn = lsEl("ls-asm-fill-btn");
  const status = lsEl("ls-asm-status");
  btn.disabled = true;
  status.textContent = "正在讀取 ASM 組織資訊...";
  const res = await apiFetch("/api/lockscreen/asm-org");
  btn.disabled = false;
  if (!res.ok || !res.data.ok) {
    status.textContent = `無法從 ASM 取得組織資訊:${(res.data && res.data.message) || "連線失敗"}。可以直接手動填寫。`;
    status.style.color = "#b42318";
    return;
  }
  const f = res.data.fields;
  lsEl("ls-org-name").value = f.org_name;
  lsEl("ls-org-address").value = f.org_address;
  lsEl("ls-org-phone").value = f.org_phone;
  lsEl("ls-org-email").value = f.org_email;
  status.style.color = "";
  const missing = [["組織名稱", f.org_name], ["地址", f.org_address], ["電話", f.org_phone], ["Email", f.org_email]]
    .filter(([, v]) => !v).map(([k]) => k);
  status.textContent = missing.length
    ? `已從 ASM 帶入。ASM 上沒有登記:${missing.join("、")},可以手動補上。記得按「儲存設定」。`
    : "已從 ASM 帶入。記得按「儲存設定」。";
  lsOnFormChange();
  if (silent) lsEl("ls-save-msg").innerHTML = `<span style="font-size:12px; color:#b45309;">組織資訊已自動從 ASM 帶入,確認後請按「儲存設定」</span>`;
}

// ---- 預覽 ----
async function lsRefreshPreview() {
  const seq = ++lsPreviewSeq;
  const errBox = lsEl("ls-preview-error");
  const frames = [lsEl("ls-frame-portrait"), lsEl("ls-frame-landscape")];
  frames.forEach((f) => f.classList.add("loading"));

  let resp;
  try {
    resp = await fetch(apiUrl("/api/lockscreen/preview"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: lsReadForm(), serial_number: lsEl("ls-preview-device").value }),
    });
  } catch (e) {
    resp = null;
  }
  if (seq !== lsPreviewSeq) return; // 已經有更新的預覽請求,丟掉這次的結果
  frames.forEach((f) => f.classList.remove("loading"));

  if (!resp || !resp.ok) {
    let msg = "預覽產生失敗";
    try { msg = (await resp.json()).message || msg; } catch (e) { /* 非 JSON */ }
    lsNotice(errBox, "error", msg);
    errBox.classList.remove("hidden");
    return;
  }
  errBox.classList.add("hidden");
  const blob = await resp.blob();
  if (lsPreviewUrl) URL.revokeObjectURL(lsPreviewUrl);
  lsPreviewUrl = URL.createObjectURL(blob);
  frames.forEach((f) => { f.querySelector("img").src = lsPreviewUrl; });
  lsRenderHomePreview();
}

// ---- 主畫面 ----
let lsHomeImageExists = false;
let lsHomeImageVersion = Date.now();

function lsSyncHomeUi() {
  const mode = lsEl("ls-home-mode").value;
  lsEl("ls-home-color-row").classList.toggle("hidden", mode !== "color");
  lsEl("ls-home-image-row").classList.toggle("hidden", mode !== "image");
  lsEl("ls-home-image-status").textContent = lsHomeImageExists ? "已上傳過圖片,重新上傳會取代。" : "尚未上傳圖片。";
}

function lsRenderHomePreview() {
  const frame = lsEl("ls-frame-home");
  const img = frame.querySelector("img");
  const mode = lsEl("ls-home-mode").value;
  if (mode === "color") {
    img.removeAttribute("src");
    frame.style.background = lsEl("ls-home-color-text").value.trim() || "#2b2f36";
  } else if (mode === "image") {
    frame.style.background = "";
    if (lsHomeImageExists) img.src = apiUrl(`/api/lockscreen/home-image?v=${lsHomeImageVersion}`);
    else img.removeAttribute("src");
  } else {
    frame.style.background = "";
    if (lsPreviewUrl) img.src = lsPreviewUrl;
  }
}

async function lsUploadHomeImage() {
  const input = lsEl("ls-home-file");
  const status = lsEl("ls-home-image-status");
  if (!input.files.length) { status.textContent = "請先選擇圖片檔。"; return; }
  const form = new FormData();
  form.append("file", input.files[0]);
  const btn = lsEl("ls-home-upload-btn");
  btn.disabled = true;
  status.textContent = "上傳中...";
  const res = await apiFetch("/api/lockscreen/home-image/upload", { method: "POST", body: form });
  btn.disabled = false;
  if (!res.ok || !res.data.ok) {
    status.textContent = `上傳失敗:${(res.data && res.data.message) || "連線失敗"}`;
    return;
  }
  lsHomeImageExists = true;
  lsHomeImageVersion = Date.now();
  status.textContent = `已上傳(${res.data.width}×${res.data.height})。記得按「儲存設定」。`;
  input.value = "";
  lsRenderHomePreview();
}

// ---- 儲存 ----
async function lsSave() {
  const btn = lsEl("ls-save-btn");
  const msg = lsEl("ls-save-msg");
  btn.disabled = true;
  const res = await apiFetchJSON("/api/lockscreen/settings/save", "POST", lsReadForm());
  btn.disabled = false;
  if (!res.ok || !res.data.ok) {
    msg.innerHTML = `<div class="ls-notice error" style="margin:0;">${escapeHtml((res.data && res.data.message) || "儲存失敗")}</div>`;
    return;
  }
  lsSettings = res.data.settings;
  lsDirty = false;
  lsShowDeps(res.data.deps);
  msg.innerHTML = `<span style="font-size:12px; color:#1c7c3f;">✅ 已儲存${lsSettings.auto_push ? "(自動推送已開啟)" : ""}</span>`;
}

// ---- 推送 ----
function lsSyncScopeUi() {
  const scope = lsEl("ls-push-scope").value;
  lsEl("ls-push-group").classList.toggle("hidden", scope !== "group");
  lsEl("ls-push-device").classList.toggle("hidden", scope !== "serial");
}

function lsPush() {
  const scope = lsEl("ls-push-scope").value;
  const value = scope === "group" ? lsEl("ls-push-group").value : scope === "serial" ? lsEl("ls-push-device").value : "";
  if (scope !== "all" && !value) return;

  if (lsDirty && !confirm("設定還有變更沒有儲存,推送會使用「上次儲存」的設定。要繼續嗎?")) return;
  const scopeLabel = scope === "all" ? "所有已註冊裝置" : scope === "group" ? `群組「${value}」` : `裝置 ${value}`;
  if (scope !== "serial" && !confirm(`要把鎖定畫面推送到${scopeLabel}嗎?這會取代裝置目前的鎖定畫面背景。`)) return;

  const btn = lsEl("ls-push-btn");
  const box = lsEl("ls-push-progress");
  box.innerHTML = "";
  btn.disabled = true;

  const es = new EventSource(apiUrl(`/api/lockscreen/push-stream?scope=${encodeURIComponent(scope)}&value=${encodeURIComponent(value)}`));
  es.onmessage = (event) => {
    let u;
    try { u = JSON.parse(event.data); } catch (e) { return; }
    const div = document.createElement("div");
    if (u.error) {
      div.style.cssText = "background:#fdeaea; color:#b42318; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;";
      div.textContent = `❌ ${u.error}`;
    } else if (u.done) {
      div.style.cssText = "background:#e3f6e9; color:#1c7c3f; padding:8px 12px; border-radius:6px; font-size:12px; margin-top:6px;";
      div.textContent = `✅ 完成,${u.label}共 ${u.success_count}/${u.total} 台已送出指令。執行結果可在「系統紀錄」的命令紀錄查看。`;
    } else if (u.message && !u.serial_number) {
      div.style.cssText = "color:#6b7280; font-size:12px; margin-top:6px;";
      div.textContent = u.message;
    } else {
      div.style.cssText = "border:1px solid var(--border-color); border-radius:6px; padding:6px 10px; font-size:12px; margin-top:4px;";
      div.textContent = `[${u.index}/${u.total}] ${u.device_name || "(未命名)"} ${u.serial_number}:${u.ok ? "✅ 已送出" : "⚠️ " + (u.message || "失敗")}`;
    }
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    if (u.done || u.error) { es.close(); btn.disabled = false; }
  };
  es.onerror = () => { es.close(); btn.disabled = false; };
}

// ---- 欄位代號插入 ----
function lsInsertPlaceholder(key) {
  const ta = lsEl("ls-lines");
  const token = `{${key}}`;
  const start = ta.selectionStart ?? ta.value.length;
  const end = ta.selectionEnd ?? ta.value.length;
  ta.value = ta.value.slice(0, start) + token + ta.value.slice(end);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = start + token.length;
  lsOnFormChange();
}

document.addEventListener("DOMContentLoaded", async () => {
  lsBindColor("ls-bg-color");
  lsBindColor("ls-text-color");

  const watched = ["ls-lines", "ls-org-name", "ls-org-address", "ls-org-phone", "ls-org-email",
    "ls-title-size", "ls-body-size", "ls-min-size", "ls-font-path"];
  watched.forEach((id) => lsEl(id).addEventListener("input", lsOnFormChange));
  ["ls-band", "ls-auto-push"].forEach((id) => lsEl(id).addEventListener("change", lsOnFormChange));
  lsBindColor("ls-home-color");
  lsEl("ls-home-mode").addEventListener("change", () => { lsSyncHomeUi(); lsOnFormChange(); });
  lsEl("ls-home-upload-btn").addEventListener("click", lsUploadHomeImage);
  lsEl("ls-frame-home").querySelector(".ls-home-icons").innerHTML = "<span></span>".repeat(16);

  document.querySelectorAll("#ls-placeholder-chips .ls-chip").forEach((chip) => {
    chip.addEventListener("click", () => lsInsertPlaceholder(chip.dataset.key));
  });
  lsEl("ls-asm-fill-btn").addEventListener("click", () => lsFillFromAsm(false));
  lsEl("ls-save-btn").addEventListener("click", lsSave);
  lsEl("ls-preview-btn").addEventListener("click", lsRefreshPreview);
  lsEl("ls-preview-device").addEventListener("change", lsRefreshPreview);
  lsEl("ls-push-scope").addEventListener("change", lsSyncScopeUi);
  lsEl("ls-push-btn").addEventListener("click", lsPush);

  window.addEventListener("beforeunload", (e) => {
    if (lsDirty) { e.preventDefault(); e.returnValue = ""; }
  });

  await Promise.all([lsLoadSettings(), lsLoadDevices()]);
  if (!lsEl("ls-save-msg").textContent) lsDirty = false;
  lsRefreshPreview();
});
