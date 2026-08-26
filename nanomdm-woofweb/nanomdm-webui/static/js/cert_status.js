function certStatusBadge(status) {
  const map = {
    ok: ["ok", "正常"],
    warning: ["warn", "30天內到期"],
    critical: ["warn", "14天內到期"],
    expired: ["warn", "已過期"],
    error: ["warn", "查詢異常"],
    unknown: ["warn", "無法判斷"],
  };
  const [cls, text] = map[status] || ["warn", status];
  // critical/expired/error 用更醒目的紅色系,跟一般warning區分
  const finalCls = (status === "critical" || status === "expired" || status === "error") ? "warn" : cls;
  const style = (status === "critical" || status === "expired" || status === "error")
    ? "background:#fdeaea; color:#b42318;"
    : "";
  return `<span class="badge ${finalCls}" style="${style}">${escapeHtml(text)}</span>`;
}

function renderCertRow(item) {
  const tr = document.createElement("tr");
  const expiryText = item.expiry_date
    ? `${item.expiry_date}${item.days_left !== null ? ` (剩餘 ${item.days_left} 天)` : ""}`
    : (item.check_type === "health" ? "(無到期日,採健康檢查)" : "-");
  const currentDefaultBadge = item.is_current_default
    ? ` <span style="color:#1c7c3f; font-weight:600;">(當前預設憑證)</span>`
    : "";

  tr.innerHTML = `
    <td>${escapeHtml(item.name)}</td>
    <td style="font-family:var(--mono); font-size:12px; word-break:break-all;">${escapeHtml(item.location || "")}${currentDefaultBadge}</td>
    <td>${escapeHtml(expiryText)}</td>
    <td>${certStatusBadge(item.status)}</td>
  `;
  return tr;
}

function renderCertDetail(item) {
  const div = document.createElement("div");
  div.style.cssText = "border-bottom:1px solid var(--border-color); padding:14px 0;";
  let errorHtml = "";
  if (item.error) {
    errorHtml = `<div style="color:#b42318; font-size:13px; margin-top:6px;">⚠️ ${escapeHtml(item.error)}</div>`;
  }
  let detailHtml = "";
  if (item.detail) {
    detailHtml = `<div style="color:#6b7280; font-size:12px; margin-top:4px;">${escapeHtml(item.detail)}</div>`;
  }
  const currentDefaultBadgeDetail = item.is_current_default
    ? ` <span style="color:#1c7c3f; font-weight:600; font-size:13px;">(當前預設憑證)</span>`
    : "";
  div.innerHTML = `
    <h3 style="margin:0 0 6px 0; font-size:15px;">${escapeHtml(item.name)} ${certStatusBadge(item.status)}${currentDefaultBadgeDetail}</h3>
    <p style="font-size:13px; color:#374151; margin:6px 0;"><strong>功能說明：</strong>${escapeHtml(item.description || "")}</p>
    <p style="font-size:13px; color:#374151; margin:6px 0;"><strong>更新方法：</strong>${escapeHtml(item.renewal_method || "")}</p>
    ${item.renewal_warning ? `<p style="font-size:13px; color:#b42318; font-weight:600; margin:6px 0;">⚠️ ${escapeHtml(item.renewal_warning)}</p>` : ""}
    ${detailHtml}
    ${errorHtml}
    <div class="cert-action-buttons" style="margin-top:10px;"></div>
  `;

  const btnContainer = div.querySelector(".cert-action-buttons");
  if (item.name === "nginx (Let's Encrypt)") {
    btnContainer.innerHTML = `<button class="secondary cert-nginx-renew-btn" type="button" style="font-size:12px;">立即續期 (certbot renew)</button>`;
  } else if (item.name === "SCEP 根 CA (自簽)") {
    btnContainer.innerHTML = `<button class="danger cert-scep-regen-btn" type="button" style="font-size:12px;">重新產生...</button>`;
  } else if (item.name === "APNs Push 憑證") {
    const uploadBtnHtml = document.querySelector(".cert-apns-upload-btn")
      ? ""
      : `<button class="secondary cert-apns-upload-btn" type="button" style="font-size:12px;">上傳新憑證...</button> `;
    const topicCount = item.topic_count || 1;
    const canDelete = topicCount > 1;
    const deviceCountHtml = item.device_count !== undefined
      ? `<div style="color:#374151; font-size:12px; margin-top:4px;">目前有 <strong>${escapeHtml(String(item.device_count))}</strong> 台裝置正在使用這組憑證推播</div>`
      : "";
    btnContainer.innerHTML = `
      ${uploadBtnHtml}
      <button class="secondary danger cert-apns-delete-btn" type="button" style="font-size:12px;" data-topic="${escapeHtml(item.topic || "")}" ${canDelete ? "" : "disabled title=\"僅剩一組憑證時不能刪除\""}>刪除這組憑證</button>
      ${deviceCountHtml}
    `;
  } else if (item.name === "DEP OAuth Token") {
    btnContainer.innerHTML = `
      <button class="secondary cert-dep-download-btn" type="button" style="font-size:12px;">下載公鑰(供上傳ASM)</button>
      <button class="secondary cert-dep-upload-btn" type="button" style="font-size:12px;">上傳Token...</button>
    `;
  } else if (item.name === "VPP Content Token") {
    btnContainer.innerHTML = `<button class="secondary cert-vpp-upload-btn" type="button" style="font-size:12px;">上傳新Token...</button>`;
  } else if (item.name === "NanoAXM 私鑰/OAuth憑證") {
    btnContainer.innerHTML = `<button class="secondary cert-nanoaxm-update-btn" type="button" style="font-size:12px;">更新...</button>`;
  }

  return div;
}

// ---------------------------------------------------------------------------
// 進度顯示 Modal (共用)
// ---------------------------------------------------------------------------
function openProgressModal(title) {
  document.getElementById("cert-progress-title").textContent = title;
  document.getElementById("cert-progress-log").innerHTML = "";
  openModal("cert-progress-modal");
}

function appendProgressLog(message, isFinal, isError) {
  const log = document.getElementById("cert-progress-log");
  const div = document.createElement("div");
  const color = isError ? "#d64545" : (isFinal ? "#1c7c3f" : "#374151");
  div.style.cssText = `color:${color}; margin-bottom:6px; ${isFinal ? "font-weight:600;" : ""}`;
  div.textContent = message;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------------------
// ① nginx certbot renew
// ---------------------------------------------------------------------------
async function renewNginxCert() {
  const res = await apiFetch("/api/cert-status/nginx/days-left");
  let force = false;

  if (res.ok && res.data.days_left > 30) {
    const wantForce = confirm(
      `目前憑證還有 ${res.data.days_left} 天才到期(到期日: ${res.data.expiry_date}),還沒進入 Let's Encrypt 建議的 30 天更新窗口。\n\n` +
      `直接執行 certbot renew 不會有任何效果(未到期不會換發)。\n\n` +
      `如果您確定要「強制」換發新憑證,請注意:\n` +
      `Let's Encrypt 對同一個網域的换發次數有速率限制(每週有上限),非必要不建議強制換發,` +
      `過度使用可能導致之後一段時間內完全無法申請/更新憑證。\n\n` +
      `是否仍要強制換發?`
    );
    if (!wantForce) return;
    force = true;
  } else if (!res.ok) {
    if (!confirm("無法確認目前憑證剩餘天數,是否仍要繼續執行 certbot renew?")) return;
  } else {
    if (!confirm(`目前憑證還有 ${res.data.days_left} 天到期,已在建議更新窗口內。確定要執行 certbot renew 嗎?完成後會自動重新載入 nginx。`)) return;
  }

  openProgressModal(force ? "nginx 憑證強制換發" : "nginx 憑證續期");
  const params = force ? "?force=true" : "";
  const es = new EventSource(apiUrl(`/api/cert-status/nginx/renew-stream${params}`));
  es.onmessage = (event) => {
    const update = JSON.parse(event.data);
    appendProgressLog(update.message, update.done, update.done && !update.ok);
    if (update.done) {
      es.close();
      loadCertStatus();
    }
  };
  es.onerror = () => es.close();
}

// ---------------------------------------------------------------------------
// ② SCEP 根 CA 重新產生
// ---------------------------------------------------------------------------
async function openScepRegenModal() {
  document.getElementById("scep-current-info").textContent = "載入目前 CA 資訊中...";
  document.getElementById("scep-cn-input").value = "";
  document.getElementById("scep-org-input").value = "";
  document.getElementById("scep-ou-input").value = "";
  document.getElementById("scep-country-input").value = "";
  document.getElementById("scep-years-input").value = "15";
  document.getElementById("scep-password-input").value = "";
  openModal("scep-regen-modal");

  const res = await apiFetch("/api/cert-status/scep/current-info");
  if (res.ok) {
    const s = res.data.subject || {};
    document.getElementById("scep-current-info").textContent =
      `目前CA: 簽發者=${s.common_name || "-"}, 組織=${s.organization || "-"}, OU=${s.organizational_unit || "-"}, 國別=${s.country || "-"}, 到期日=${res.data.expiry_date || "-"}`;
    document.getElementById("scep-cn-input").value = res.data.suggested_common_name || s.common_name || "";
    document.getElementById("scep-org-input").value = s.organization || "";
    document.getElementById("scep-ou-input").value = s.organizational_unit || "";
    document.getElementById("scep-country-input").value = s.country || "";
  } else {
    document.getElementById("scep-current-info").textContent = "無法讀取目前CA資訊(可能是第一次設定)";
  }
}

function confirmScepRegen() {
  const commonName = document.getElementById("scep-cn-input").value.trim();
  const org = document.getElementById("scep-org-input").value.trim();
  const ou = document.getElementById("scep-ou-input").value.trim();
  const country = document.getElementById("scep-country-input").value.trim();
  const years = document.getElementById("scep-years-input").value.trim();
  const password = document.getElementById("scep-password-input").value;

  if (!org || !country || !years) {
    alert("組織名稱、國別、效期年數為必填");
    return;
  }
  if (!password) {
    alert("請輸入密碼確認");
    return;
  }
  if (!confirm(`最後確認:即將建立全新的SCEP根CA(效期${years}年),所有已註冊裝置都需要重新註冊,此操作無法復原。真的要繼續嗎?`)) {
    return;
  }

  closeModal("scep-regen-modal");
  openProgressModal("重新產生 SCEP 根 CA");

  const params = new URLSearchParams({
    password, organization: org, organizational_unit: ou, country, years, common_name: commonName,
  });
  const es = new EventSource(apiUrl(`/api/cert-status/scep/regenerate-stream?${params.toString()}`));
  es.onmessage = (event) => {
    const update = JSON.parse(event.data);
    appendProgressLog(update.message, update.done, update.done && !update.ok);
    if (update.done) {
      es.close();
      loadCertStatus();
    }
  };
  es.onerror = () => es.close();
}

// ---------------------------------------------------------------------------
// ③ APNs Push 憑證上傳
// ---------------------------------------------------------------------------
async function openApnsUploadModal() {
  document.getElementById("apns-current-topics").textContent = "載入目前憑證資訊中...";
  document.getElementById("apns-cert-file-input").value = "";
  document.getElementById("apns-key-file-input").value = "";
  document.getElementById("apns-password-input").value = "";
  document.getElementById("apns-key-passphrase-input").value = "";
  document.getElementById("apns-key-passphrase-row").style.display = "none";
  document.getElementById("apns-topic-warning").style.display = "none";
  document.getElementById("apns-confirm-password-row").style.display = "none";
  document.getElementById("apns-confirm-password-input").value = "";
  openModal("apns-upload-modal");

  const res = await apiFetch("/api/cert-status/apns/current-topics");
  if (res.ok && res.data.topics.length > 0) {
    document.getElementById("apns-current-topics").textContent = "目前的Topic: " + res.data.topics.join(", ");
  } else {
    document.getElementById("apns-current-topics").textContent = "目前沒有已知的Topic(可能是第一次設定)";
  }
}

async function checkApnsTopicOnFileSelect() {
  const certFile = document.getElementById("apns-cert-file-input").files[0];
  const warningEl = document.getElementById("apns-topic-warning");
  const confirmRow = document.getElementById("apns-confirm-password-row");
  if (!certFile) {
    warningEl.style.display = "none";
    confirmRow.style.display = "none";
    return;
  }

  const formData = new FormData();
  formData.append("cert", certFile);
  const resp = await fetch(apiUrl("/api/cert-status/apns/detect-topic"), { method: "POST", body: formData });
  const data = await resp.json();

  if (!data.ok) {
    warningEl.style.display = "none";
    confirmRow.style.display = "none";
    return;
  }

  if (data.topic_changed) {
    warningEl.innerHTML =
      `⚠️ <strong>偵測到這張新憑證的 topic 跟目前使用中的不一樣!</strong><br>` +
      `新憑證 topic: ${escapeHtml(data.topic)}<br>` +
      `目前使用中: ${escapeHtml(data.existing_topics.join(", "))}<br><br>` +
      `這代表用舊 topic 註冊的裝置,推播通道會失效,需要重新註冊才能恢復管理。` +
      `如果您原本以為這只是「續簽」,請先確認是否用了「當初申請這張憑證的同一個 Apple ID」重新申請,而不是建立了一張全新憑證。`;
    warningEl.style.display = "";
    confirmRow.style.display = "";
  } else {
    warningEl.style.display = "none";
    confirmRow.style.display = "none";
  }
}

async function deleteApnsCert(topic) {
  const password = prompt(
    `確定要刪除 topic「${topic}」這組APNs推播憑證嗎?\n\n` +
    `刪除後,所有目前用這個topic註冊的裝置會立刻失去推播能力` +
    `(伺服器無法再喚醒它們執行任何MDM指令,包括查詢狀態、安裝App、遠端清除等,直到裝置重新註冊為止)。\n\n` +
    `請輸入您目前登入的密碼以確認:`
  );
  if (!password) return;

  const res = await apiFetchJSON("/api/cert-status/apns/delete", "POST", { topic, password });
  if (res.ok) {
    alert(res.data.message);
    loadCertStatus();
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function confirmApnsUpload() {
  const certFile = document.getElementById("apns-cert-file-input").files[0];
  const keyFile = document.getElementById("apns-key-file-input").files[0];
  const password = document.getElementById("apns-password-input").value;
  const keyPassphrase = document.getElementById("apns-key-passphrase-input").value;
  const topicWarningShown = document.getElementById("apns-topic-warning").style.display !== "none";
  const confirmPassword = document.getElementById("apns-confirm-password-input").value;

  if (!certFile || !keyFile) {
    alert("請選擇憑證檔案與私鑰檔案");
    return;
  }
  if (!password) {
    alert("請輸入密碼確認");
    return;
  }
  if (topicWarningShown && !confirmPassword) {
    alert("偵測到這是不同的 topic,請在「再次確認密碼」欄位裡重新輸入一次密碼,確認您已經了解上面的警告內容");
    return;
  }

  const formData = new FormData();
  formData.append("cert", certFile);
  formData.append("key", keyFile);
  formData.append("password", password);
  if (keyPassphrase) formData.append("key_passphrase", keyPassphrase);
  if (topicWarningShown) formData.append("confirm_password", confirmPassword);

  const btn = document.getElementById("apns-upload-confirm-btn");
  btn.disabled = true;
  btn.textContent = "上傳中...";

  debugLog("REQUEST POST /api/cert-status/apns/upload");
  const resp = await fetch(apiUrl("/api/cert-status/apns/upload"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/cert-status/apns/upload", data, !resp.ok);

  btn.disabled = false;
  btn.textContent = "上傳並更新";

  if (data.ok) {
    closeModal("apns-upload-modal");
    alert(data.message);
    loadCertStatus();
  } else if (data.key_encrypted) {
    document.getElementById("apns-key-passphrase-row").style.display = "";
    document.getElementById("apns-key-passphrase-input").focus();
    alert(data.message);
  } else {
    alert("上傳失敗: " + (data.message || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// ④ DEP Token 下載/上傳
// ---------------------------------------------------------------------------
function downloadDepPublicKey() {
  window.location.href = apiUrl("/api/cert-status/dep/download-cert");
  setTimeout(() => {
    alert("公鑰已下載。接下來請登入 Apple School/Business Manager → Preferences → Your MDM Servers,找到對應的伺服器,上傳這個公鑰檔案,然後下載新的 Token(.p7m 檔案),下載完成後回來點「上傳Token...」");
  }, 500);
}

function openDepUploadModal() {
  document.getElementById("dep-token-file-input").value = "";
  openModal("dep-upload-modal");
}

async function confirmDepUpload() {
  const file = document.getElementById("dep-token-file-input").files[0];
  if (!file) {
    alert("請選擇 .p7m 檔案");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  const btn = document.getElementById("dep-upload-confirm-btn");
  btn.disabled = true;
  btn.textContent = "上傳中...";

  debugLog("REQUEST POST /api/cert-status/dep/upload-token");
  const resp = await fetch(apiUrl("/api/cert-status/dep/upload-token"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/cert-status/dep/upload-token", data, !resp.ok);

  btn.disabled = false;
  btn.textContent = "上傳並匯入";

  if (data.ok) {
    closeModal("dep-upload-modal");
    alert(data.message);
    loadCertStatus();
  } else {
    alert("上傳失敗: " + (data.message || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// ⑤ VPP Content Token 上傳
// ---------------------------------------------------------------------------
function uploadVppToken() {
  const input = document.createElement("input");
  input.type = "file";
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    debugLog("REQUEST POST /api/cert-status/vpp/upload");
    const resp = await fetch(apiUrl("/api/cert-status/vpp/upload"), { method: "POST", body: formData });
    const data = await resp.json();
    debugLog("RESPONSE /api/cert-status/vpp/upload", data, !resp.ok);

    if (data.ok) {
      alert(data.message);
      loadCertStatus();
    } else {
      alert("上傳失敗: " + (data.message || "未知錯誤"));
    }
  });
  input.click();
}

// ---------------------------------------------------------------------------
// ⑥ NanoAXM 憑證更新
// ---------------------------------------------------------------------------
function openNanoaxmUpdateModal() {
  document.getElementById("nanoaxm-client-id-input").value = "";
  document.getElementById("nanoaxm-key-id-input").value = "";
  document.getElementById("nanoaxm-private-key-input").value = "";
  document.getElementById("nanoaxm-password-input").value = "";
  openModal("nanoaxm-update-modal");
}

async function confirmNanoaxmUpdate() {
  const clientId = document.getElementById("nanoaxm-client-id-input").value.trim();
  const keyId = document.getElementById("nanoaxm-key-id-input").value.trim();
  const keyFile = document.getElementById("nanoaxm-private-key-input").files[0];
  const password = document.getElementById("nanoaxm-password-input").value;

  if (!clientId || !keyId || !keyFile) {
    alert("Client ID、Key ID、私鑰檔案皆為必填");
    return;
  }
  if (!password) {
    alert("請輸入密碼確認");
    return;
  }

  const formData = new FormData();
  formData.append("client_id", clientId);
  formData.append("key_id", keyId);
  formData.append("private_key", keyFile);
  formData.append("password", password);

  const btn = document.getElementById("nanoaxm-update-confirm-btn");
  btn.disabled = true;
  btn.textContent = "更新中...";

  debugLog("REQUEST POST /api/cert-status/nanoaxm/update");
  const resp = await fetch(apiUrl("/api/cert-status/nanoaxm/update"), { method: "POST", body: formData });
  const data = await resp.json();
  debugLog("RESPONSE /api/cert-status/nanoaxm/update", data, !resp.ok);

  btn.disabled = false;
  btn.textContent = "更新";

  if (data.ok) {
    closeModal("nanoaxm-update-modal");
    alert(data.message);
    loadCertStatus();
  } else {
    alert("更新失敗: " + (data.message || "未知錯誤"));
  }
}


async function loadCertStatus() {
  const tbody = document.getElementById("cert-status-tbody");
  const detailsContainer = document.getElementById("cert-status-details");
  tbody.innerHTML = `<tr><td colspan="4">檢查中...</td></tr>`;
  detailsContainer.innerHTML = "檢查中...";

  const res = await apiFetch("/api/cert-status");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    detailsContainer.innerHTML = "";
    return;
  }

  tbody.innerHTML = "";
  detailsContainer.innerHTML = "";
  res.data.results.forEach((item) => {
    tbody.appendChild(renderCertRow(item));
    detailsContainer.appendChild(renderCertDetail(item));
  });
}

async function loadProfileSigningStatus() {
  const container = document.getElementById("profile-signing-status");
  const generateBtn = document.getElementById("profile-signing-generate-btn");
  const toggleBtn = document.getElementById("profile-signing-toggle-btn");
  container.textContent = "載入中...";

  const res = await apiFetch("/api/profile-signing/status");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</p>`;
    return;
  }
  const d = res.data;

  let html = "";
  if (!d.cert_exists) {
    html = `<p style="color:#9ca3af; font-size:13px;">尚未產生簽署憑證</p>`;
    generateBtn.textContent = "產生簽署憑證";
    toggleBtn.style.display = "none";
  } else {
    const info = d.cert_info || {};
    const enddate = info.enddate ? info.enddate[0] : "未知";
    html = `
      <p style="font-size:13px;">
        簽署憑證已產生,到期日: ${escapeHtml(enddate)}<br>
        目前狀態: ${d.enabled ? '<span class="badge ok">已啟用</span>' : '<span class="badge warn">尚未啟用</span>'}
      </p>
    `;
    generateBtn.textContent = "重新產生簽署憑證";
    toggleBtn.style.display = "";
    toggleBtn.textContent = d.enabled ? "停用簽署" : "啟用簽署";
  }
  container.innerHTML = html;
}

async function generateProfileSigningCert() {
  if (!confirm("確定要產生新的簽署憑證嗎?\n\n如果已經有一張在用,產生新的之後,之前已經簽署過的舊描述檔不會自動重新簽署(不影響其現有效力),但之後新存檔的描述檔會改用這張新憑證簽署。")) return;
  const res = await apiFetchJSON("/api/profile-signing/generate", "POST");
  if (res.ok) {
    alert("簽署憑證已產生");
    loadProfileSigningStatus();
  } else {
    alert("產生失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function toggleProfileSigning() {
  const statusRes = await apiFetch("/api/profile-signing/status");
  const currentlyEnabled = statusRes.ok && statusRes.data.enabled;
  const res = await apiFetchJSON("/api/profile-signing/toggle", "POST", { enabled: !currentlyEnabled });
  if (res.ok) {
    loadProfileSigningStatus();
  } else {
    alert("設定失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function addCaToEnrollTemplate() {
  if (!confirm("確定要把 CA 根憑證加進註冊模板嗎?\n\n做完之後,裝置需要重新清空註冊,才會拿到含有這張 CA 的新版本,之前已經完成註冊的裝置不會自動套用。")) return;
  const res = await apiFetchJSON("/api/profile-signing/add-ca-to-enroll-template", "POST");
  if (res.ok) {
    alert(res.data.message || "已完成");
  } else {
    alert("失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadCertStatus();
  loadProfileSigningStatus();
  document.getElementById("refresh-cert-btn").addEventListener("click", loadCertStatus);
  document.getElementById("profile-signing-generate-btn").addEventListener("click", generateProfileSigningCert);
  document.getElementById("profile-signing-toggle-btn").addEventListener("click", toggleProfileSigning);
  document.getElementById("profile-signing-add-ca-btn").addEventListener("click", addCaToEnrollTemplate);

  document.getElementById("cert-status-details").addEventListener("click", (e) => {
    if (e.target.classList.contains("cert-nginx-renew-btn")) renewNginxCert();
    else if (e.target.classList.contains("cert-scep-regen-btn")) openScepRegenModal();
    else if (e.target.classList.contains("cert-apns-upload-btn")) openApnsUploadModal();
    else if (e.target.classList.contains("cert-apns-delete-btn")) deleteApnsCert(e.target.dataset.topic);
    else if (e.target.classList.contains("cert-dep-download-btn")) downloadDepPublicKey();
    else if (e.target.classList.contains("cert-dep-upload-btn")) openDepUploadModal();
    else if (e.target.classList.contains("cert-vpp-upload-btn")) uploadVppToken();
    else if (e.target.classList.contains("cert-nanoaxm-update-btn")) openNanoaxmUpdateModal();
  });

  document.getElementById("scep-regen-confirm-btn").addEventListener("click", confirmScepRegen);
  document.getElementById("apns-upload-confirm-btn").addEventListener("click", confirmApnsUpload);
  document.getElementById("dep-upload-confirm-btn").addEventListener("click", confirmDepUpload);
  document.getElementById("nanoaxm-update-confirm-btn").addEventListener("click", confirmNanoaxmUpdate);
});
