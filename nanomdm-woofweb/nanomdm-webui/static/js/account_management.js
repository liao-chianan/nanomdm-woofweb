let currentUsername = null;

// ---------------------------------------------------------------------------
// 帳號清單
// ---------------------------------------------------------------------------
async function loadAccounts() {
  const tbody = document.getElementById("accounts-tbody");
  tbody.innerHTML = `<tr><td colspan="3">載入中...</td></tr>`;
  const res = await apiFetch("/api/accounts");
  if (!res.ok) {
    tbody.innerHTML = `<tr><td colspan="3" style="color:#d64545;">載入失敗: ${escapeHtml((res.data && res.data.message) || "未知錯誤")}</td></tr>`;
    return;
  }
  currentUsername = res.data.current_username;
  tbody.innerHTML = "";
  res.data.accounts.forEach((a) => {
    const isSelf = a.username === currentUsername;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(a.username)} ${isSelf ? '<span class="badge ok">目前登入中</span>' : ""}</td>
      <td>${escapeHtml(a.created_at || "-")}</td>
      <td>
        <button class="secondary" data-action="change-password" data-username="${escapeHtml(a.username)}" type="button" style="font-size:12px;">改密碼</button>
        <button class="danger" data-action="delete-account" data-username="${escapeHtml(a.username)}" type="button" style="font-size:12px;" ${isSelf ? "disabled" : ""}>刪除</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function openAddAccountModal() {
  document.getElementById("new-account-username").value = "";
  document.getElementById("new-account-password").value = "";
  document.getElementById("new-account-password-confirm").value = "";
  openModal("add-account-modal");
}

async function confirmAddAccount() {
  const username = document.getElementById("new-account-username").value.trim();
  const password = document.getElementById("new-account-password").value;
  const confirm2 = document.getElementById("new-account-password-confirm").value;

  if (!username) {
    alert("請輸入帳號名稱");
    return;
  }
  if (password !== confirm2) {
    alert("兩次輸入的密碼不一致");
    return;
  }

  const res = await apiFetchJSON("/api/accounts/add", "POST", { username, password });
  if (res.ok) {
    closeModal("add-account-modal");
    loadAccounts();
  } else {
    alert("新增失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

async function deleteAccount(username) {
  if (!confirm(`確定要刪除帳號「${username}」嗎?`)) return;
  const res = await apiFetchJSON("/api/accounts/delete", "POST", { username });
  if (res.ok) {
    loadAccounts();
  } else {
    alert("刪除失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

function openChangePasswordModal(username) {
  document.getElementById("change-password-username-label").textContent = username;
  document.getElementById("change-password-username-label").dataset.username = username;
  document.getElementById("change-password-new").value = "";
  document.getElementById("change-password-confirm").value = "";
  openModal("change-password-modal");
}

async function confirmChangePassword() {
  const username = document.getElementById("change-password-username-label").dataset.username;
  const newPassword = document.getElementById("change-password-new").value;
  const confirm2 = document.getElementById("change-password-confirm").value;

  if (newPassword !== confirm2) {
    alert("兩次輸入的密碼不一致");
    return;
  }

  const res = await apiFetchJSON("/api/accounts/change-password", "POST", { username, new_password: newPassword });
  if (res.ok) {
    closeModal("change-password-modal");
    alert(res.data.message);
  } else {
    alert("修改失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

// ---------------------------------------------------------------------------
// IP 白名單
// ---------------------------------------------------------------------------
function addIpRuleRow(value) {
  const container = document.getElementById("ip-rules-container");
  const row = document.createElement("div");
  row.className = "ip-rule-row";
  row.style.cssText = "display:flex; gap:8px; align-items:center; margin-bottom:6px;";
  row.innerHTML = `
    <input type="text" class="ip-rule-input" value="${escapeHtml(value || "")}" placeholder="例如 192.168.1.0/24" style="flex:1; font-family:var(--mono);">
    <button class="danger remove-ip-rule-btn" type="button" style="font-size:12px;">移除</button>
  `;
  container.appendChild(row);
}

async function loadIpRules() {
  const container = document.getElementById("ip-rules-container");
  container.innerHTML = "載入中...";
  const res = await apiFetch("/api/ip-allowlist");
  if (!res.ok) {
    container.innerHTML = `<p style="color:#d64545;">載入失敗</p>`;
    return;
  }
  document.getElementById("your-ip-display").textContent = res.data.your_ip;
  container.innerHTML = "";
  if (res.data.rules.length === 0) {
    addIpRuleRow(""); // 給一個空白列方便使用者直接輸入
  } else {
    res.data.rules.forEach((r) => addIpRuleRow(r));
  }
}

async function saveIpRules() {
  const inputs = document.querySelectorAll(".ip-rule-input");
  const rules = Array.from(inputs).map((el) => el.value.trim()).filter((v) => v);

  const btn = document.getElementById("save-ip-rules-btn");
  btn.disabled = true;
  btn.textContent = "儲存中...";

  const res = await apiFetchJSON("/api/ip-allowlist/save", "POST", { rules });

  btn.disabled = false;
  btn.textContent = "儲存 IP 白名單";

  if (res.ok) {
    alert(res.data.message + (res.data.rules.length === 0 ? "(目前為不限制狀態)" : ""));
    loadIpRules();
  } else {
    alert("儲存失敗: " + ((res.data && res.data.message) || "未知錯誤"));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadAccounts();
  loadIpRules();

  document.getElementById("add-account-btn").addEventListener("click", openAddAccountModal);
  document.getElementById("add-account-confirm-btn").addEventListener("click", confirmAddAccount);
  document.getElementById("change-password-confirm-btn").addEventListener("click", confirmChangePassword);
  document.getElementById("add-ip-rule-btn").addEventListener("click", () => addIpRuleRow(""));
  document.getElementById("save-ip-rules-btn").addEventListener("click", saveIpRules);

  document.getElementById("accounts-tbody").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const username = el.dataset.username;
    if (el.dataset.action === "delete-account") deleteAccount(username);
    else if (el.dataset.action === "change-password") openChangePasswordModal(username);
  });

  document.getElementById("ip-rules-container").addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-ip-rule-btn")) {
      e.target.closest(".ip-rule-row").remove();
    }
  });
});
