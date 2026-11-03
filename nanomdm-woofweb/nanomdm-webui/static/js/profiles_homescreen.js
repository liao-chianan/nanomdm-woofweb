// ---------------------------------------------------------------------------
// 描述檔編輯器:主畫面排列視窗(com.apple.homescreenlayout)
// ---------------------------------------------------------------------------
const HS_DEFAULT_DOCK = ["com.apple.mobilesafari", "com.apple.camera", "com.apple.mobileslideshow", "com.apple.mobilenotes"];
const HS_PAGE_LIMIT = 30;         // iPad 實測一頁最多 30 個(6 欄 x 5 列),畫面也照這個格數畫
const HS_DOCK_LIMIT = 13;
const HS_UNKNOWN_BUNDLE_ID = "未知"; // 已下架 App 在 ASM 軟體資訊裡的 Bundle ID,舊版存下來的排列可能有
const HS_REORDER_DWELL_MS = 180;  // 在圖示邊緣停留多久才換位置
const HS_PAGE_TURN_DWELL_MS = 450; // 拖曳時在螢幕左右邊緣停留多久才翻頁
const HS_PAGE_TURN_EDGE_PX = 34;

let hsCatalog = {};  // key -> {type: "app"|"webclip", name, bundleId, url, adamId}
let hsState = null;  // {dock: [key], pages: [[key|folderId]], tray: [key], folders: {folderId: {name, keys}}, openFolder}
let hsFolderSeq = 0;
let hsSortables = [];
let hsMergeTarget = null;
let hsEdgeHover = null;   // 目前停在哪個圖示的邊緣,以及從什麼時候開始 {el, since}
let hsCurrentPage = 0;
let hsDrag = null;        // 拖曳中的滑鼠位置與翻頁計時 {x, y, side, since, timer}

function hsAppKey(bundleId) { return `app:${bundleId}`; }
function hsWebKey(url) { return `web:${url}`; }
function hsIsFolder(key) { return key.startsWith("folder:"); }

function hsNewFolderId() {
  hsFolderSeq += 1;
  return `folder:${hsFolderSeq}`;
}

// 候選清單:群組綁定的 App、網頁捷徑、原生 App(扣掉已隱藏的)
function hsBuildCatalog(candidates, blocked, webclips) {
  const catalog = {};
  (candidates.group_apps || []).forEach((a) => {
    catalog[hsAppKey(a.bundle_id)] = { type: "app", name: a.label, fullName: a.full_name, bundleId: a.bundle_id, adamId: a.adam_id };
  });
  (webclips || []).forEach((w) => {
    catalog[hsWebKey(w.url)] = { type: "webclip", name: w.label || w.url, url: w.url };
  });
  (candidates.native_apps || []).forEach((a) => {
    const key = hsAppKey(a.bundle_id);
    if (!blocked.has(a.bundle_id) && !catalog[key]) {
      catalog[key] = { type: "app", name: a.label, bundleId: a.bundle_id };
    }
  });
  return catalog;
}

// 第一次開啟:Dock 放常用 App,其餘依「群組 App、網頁捷徑、原生 App」順序排入頁面
function hsDefaultState(catalog) {
  const dock = HS_DEFAULT_DOCK.map(hsAppKey).filter((k) => catalog[k]);
  const rest = Object.keys(catalog).filter((k) => !dock.includes(k));
  const pages = [];
  for (let i = 0; i < rest.length; i += HS_PAGE_LIMIT) {
    pages.push(rest.slice(i, i + HS_PAGE_LIMIT));
  }
  return { dock, pages, tray: [], folders: {}, openFolder: null };
}

// 把存在描述檔裡的排列轉回編輯狀態。已被隱藏的 App、已移除的網頁捷徑會拿掉並提醒;
// 不在候選清單裡的 App(例如群組已解除綁定)保留,新出現的候選項目放到「未排入」。
function hsStateFromLayout(layout, catalog, blocked) {
  const placed = new Set();
  const removed = [];
  const folders = {};

  const toKey = (item) => {
    if (item.Type === "WebClip") {
      const key = hsWebKey(item.URL);
      if (!catalog[key]) { removed.push(item.URL); return null; }
      placed.add(key);
      return key;
    }
    if (blocked.has(item.BundleID) || item.BundleID === HS_UNKNOWN_BUNDLE_ID) { removed.push(item.BundleID); return null; }
    const key = hsAppKey(item.BundleID);
    if (!catalog[key]) catalog[key] = { type: "app", name: item.BundleID.split(".").pop(), bundleId: item.BundleID };
    placed.add(key);
    return key;
  };

  const pages = (layout.Pages || []).map((page) => page.map((item) => {
    if (item.Type !== "Folder") return toKey(item);
    const keys = (item.Pages || []).flat().map(toKey).filter(Boolean);
    if (keys.length === 0) return null;
    if (keys.length === 1) return keys[0];
    const id = hsNewFolderId();
    folders[id] = { name: item.DisplayName || "資料夾", keys };
    return id;
  }).filter(Boolean));
  const dock = (layout.Dock || []).map(toKey).filter(Boolean);
  const tray = Object.keys(catalog).filter((k) => !placed.has(k));

  return { state: { dock, pages, tray, folders, openFolder: null }, removed };
}

function hsKeyToItem(key) {
  const entry = hsCatalog[key];
  return entry.type === "webclip" ? { Type: "WebClip", URL: entry.url } : { Type: "Application", BundleID: entry.bundleId };
}

function hsStateToLayout(state) {
  const pages = state.pages.filter((p) => p.length > 0).map((page) => page.map((key) => {
    if (!hsIsFolder(key)) return hsKeyToItem(key);
    const folder = state.folders[key];
    return { Type: "Folder", DisplayName: folder.name || "資料夾", Pages: [folder.keys.map(hsKeyToItem)] };
  }));
  return { Dock: state.dock.map(hsKeyToItem), Pages: pages };
}

// 資料夾少於兩個 App 就解散:剩一個的放回資料夾原本的位置,空的直接拿掉
function hsDissolveSmallFolders(state) {
  state.pages = state.pages.map((page) => page.flatMap((key) => {
    if (!hsIsFolder(key)) return [key];
    const folder = state.folders[key];
    if (folder && folder.keys.length >= 2) return [key];
    delete state.folders[key];
    if (state.openFolder === key) state.openFolder = null;
    return folder ? folder.keys : [];
  }));
}

// 把 dragged 併到 target:target 是資料夾就加進去,否則在 target 的位置建立新資料夾
function hsRemoveKey(state, key) {
  const removeFrom = (list) => { const i = list.indexOf(key); if (i >= 0) list.splice(i, 1); };
  removeFrom(state.dock);
  removeFrom(state.tray);
  state.pages.forEach(removeFrom);
  Object.values(state.folders).forEach((f) => removeFrom(f.keys));
}

function hsMerge(state, draggedKey, targetKey) {
  if (draggedKey === targetKey || hsIsFolder(draggedKey)) return;
  hsRemoveKey(state, draggedKey);

  if (hsIsFolder(targetKey)) {
    state.folders[targetKey].keys.push(draggedKey);
  } else {
    const page = state.pages.find((p) => p.includes(targetKey));
    if (!page) return;
    const id = hsNewFolderId();
    state.folders[id] = { name: "新資料夾", keys: [targetKey, draggedKey] };
    page[page.indexOf(targetKey)] = id;
    state.openFolder = id;
  }
  hsDissolveSmallFolders(state);
}

// 拿掉空白頁,最後固定留一頁空白:拖曳中不能重畫畫面,要拖到新的一頁就靠這頁
function hsNormalizePages(state) {
  state.pages = state.pages.filter((p) => p.length > 0);
  state.pages.push([]);
}

function hsIconHtml(key) {
  const entry = hsCatalog[key];
  const fallback = escapeHtml((entry.name || "?").trim().charAt(0));
  if (entry.type === "webclip") return `<span class="hs-icon hs-icon-letter">🌐</span>`;
  const src = entry.adamId
    ? apiUrl(`/api/app-icon/${encodeURIComponent(entry.adamId)}`)
    : apiUrl(`/api/app-icon-by-bundle/${encodeURIComponent(entry.bundleId)}`);
  return `<img class="hs-icon" loading="lazy" draggable="false" src="${src}" alt="" data-fallback="${fallback}">`;
}

function hsTileHtml(key) {
  if (hsIsFolder(key)) {
    const folder = hsState.folders[key];
    const minis = folder.keys.slice(0, 9).map(hsIconHtml).join("");
    return `<div class="hs-tile hs-folder" data-key="${escapeHtml(key)}" title="點一下打開資料夾">
      <div class="hs-folder-grid">${minis}</div>
      <div class="hs-label">${escapeHtml(folder.name)}</div>
    </div>`;
  }
  const entry = hsCatalog[key];
  return `<div class="hs-tile" data-key="${escapeHtml(key)}" title="${escapeHtml(`${entry.fullName || entry.name}\n${entry.bundleId || entry.url}`)}">
    ${hsIconHtml(key)}
    <div class="hs-label">${escapeHtml(entry.name)}</div>
  </div>`;
}

function hsListHtml(listName, keys, extraClass) {
  return `<div class="hs-list ${extraClass}" data-list="${escapeHtml(listName)}">${keys.map(hsTileHtml).join("")}</div>`;
}

function hsFolderOverlayHtml() {
  const id = hsState.openFolder;
  const folder = id && hsState.folders[id];
  if (!folder) return "";
  return `<div class="hs-folder-overlay" id="hs-folder-overlay">
    <input type="text" id="hs-folder-name" class="hs-folder-name" value="${escapeHtml(folder.name)}" maxlength="30" aria-label="資料夾名稱">
    <div class="hs-folder-panel">${hsListHtml(id, folder.keys, "hs-folder-list")}</div>
    <button type="button" class="hs-folder-dissolve" id="hs-folder-dissolve">解散資料夾</button>
  </div>`;
}

function hsSetPage(index) {
  hsCurrentPage = Math.max(0, Math.min(index, hsState.pages.length - 1));
  document.getElementById("hs-track").style.transform = `translateX(-${hsCurrentPage * 100}%)`;
  document.querySelectorAll("#homescreen-editor .hs-dot").forEach((dot, i) => {
    dot.classList.toggle("active", i === hsCurrentPage);
  });
  document.getElementById("hs-prev").disabled = hsCurrentPage === 0;
  document.getElementById("hs-next").disabled = hsCurrentPage === hsState.pages.length - 1;
}

function hsRender() {
  hsSortables.forEach((s) => s.destroy());
  hsSortables = [];
  hsNormalizePages(hsState);
  const container = document.getElementById("homescreen-editor");
  const lastPage = hsState.pages.length - 1;

  const pagesHtml = hsState.pages.map((page, idx) => hsListHtml(`page:${idx}`, page, "hs-page")).join("");
  const dotsHtml = hsState.pages.map((_, idx) => (
    `<button type="button" class="hs-dot ${idx === lastPage ? "hs-dot-new" : ""}" data-page="${idx}" title="${idx === lastPage ? "新的一頁" : `第 ${idx + 1} 頁`}"></button>`
  )).join("");

  container.innerHTML = `
    <div class="hs-stage">
      <div class="hs-ipad">
        <div class="hs-screen" id="hs-screen">
          <div class="hs-statusbar"><span>9:41</span><span>Wi-Fi・100%</span></div>
          <div class="hs-viewport"><div class="hs-track" id="hs-track">${pagesHtml}</div></div>
          <button type="button" class="hs-nav hs-nav-prev" id="hs-prev" aria-label="上一頁">‹</button>
          <button type="button" class="hs-nav hs-nav-next" id="hs-next" aria-label="下一頁">›</button>
          <div class="hs-dots">${dotsHtml}</div>
          <div class="hs-dock-wrap">${hsListHtml("dock", hsState.dock, "hs-dock")}</div>
          ${hsFolderOverlayHtml()}
        </div>
      </div>
      <aside class="hs-tray-panel">
        <div class="hs-tray-title">未排入(${hsState.tray.length})</div>
        <p class="hs-tray-hint">拖到這裡的 App 不指定位置,裝置會自動排在後面。</p>
        ${hsListHtml("tray", hsState.tray, "hs-tray")}
      </aside>
    </div>`;

  container.querySelectorAll("img.hs-icon").forEach((img) => {
    img.addEventListener("error", () => {
      const span = document.createElement("span");
      span.className = "hs-icon hs-icon-letter";
      span.textContent = img.dataset.fallback;
      img.replaceWith(span);
    }, { once: true });
  });

  container.querySelectorAll(".hs-list").forEach((el) => {
    hsSortables.push(new Sortable(el, {
      group: "homescreen",
      animation: 120,
      forceFallback: true,
      fallbackOnBody: true, // 頁面容器用 transform 翻頁,拖曳中的圖示要放在 body 才不會跑位
      fallbackTolerance: 4,
      scroll: false,
      filter: "input, button",
      preventOnFilter: false,
      onStart: hsStartDragTracking,
      onMove: hsOnMove,
      onEnd: hsOnEnd,
    }));
  });
  hsSetPage(hsCurrentPage);
}

// 拖曳中追蹤滑鼠:拖出資料夾範圍就把資料夾收起來,停在螢幕左右邊緣就翻頁。
// 翻頁不重畫畫面(重畫會中斷拖曳),所有頁面都一直在 DOM 裡,只移動 hs-track。
function hsTrackPointer(e) {
  if (!hsDrag) return;
  const p = hsPointer(e);
  if (p) { hsDrag.x = p.x; hsDrag.y = p.y; }
  const overlay = document.getElementById("hs-folder-overlay");
  if (overlay && hsDrag.fromFolder && !overlay.classList.contains("hs-folder-away")) {
    const r = overlay.querySelector(".hs-folder-panel").getBoundingClientRect();
    if (hsDrag.x < r.left || hsDrag.x > r.right || hsDrag.y < r.top || hsDrag.y > r.bottom) {
      overlay.classList.add("hs-folder-away");
    }
  }
}

function hsDragTick() {
  if (!hsDrag) return;
  if (hsEdgeHover && !hsEdgeHover.done && Date.now() - hsEdgeHover.since >= HS_REORDER_DWELL_MS) {
    document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: hsDrag.x, clientY: hsDrag.y }));
  }
  const overlay = document.getElementById("hs-folder-overlay");
  if (overlay && !overlay.classList.contains("hs-folder-away")) return;
  const r = document.getElementById("hs-screen").getBoundingClientRect();
  let side = 0;
  if (hsDrag.y >= r.top && hsDrag.y <= r.bottom) {
    if (hsDrag.x >= r.left - HS_PAGE_TURN_EDGE_PX && hsDrag.x <= r.left + HS_PAGE_TURN_EDGE_PX) side = -1;
    if (hsDrag.x >= r.right - HS_PAGE_TURN_EDGE_PX && hsDrag.x <= r.right + HS_PAGE_TURN_EDGE_PX) side = 1;
  }
  if (side !== hsDrag.side) {
    hsDrag.side = side;
    hsDrag.since = Date.now();
  } else if (side !== 0 && Date.now() - hsDrag.since >= HS_PAGE_TURN_DWELL_MS) {
    hsSetPage(hsCurrentPage + side);
    hsDrag.since = Date.now();
  }
}

function hsStartDragTracking(evt) {
  const p = hsPointer(evt.originalEvent) || { x: 0, y: 0 };
  hsDrag = { x: p.x, y: p.y, side: 0, since: 0, fromFolder: !!evt.from.closest(".hs-folder-overlay"),
             timer: setInterval(hsDragTick, 50) };
  document.addEventListener("mousemove", hsTrackPointer, true);
  document.addEventListener("touchmove", hsTrackPointer, true);
}

function hsStopDragTracking() {
  if (hsDrag) clearInterval(hsDrag.timer);
  hsDrag = null;
  document.removeEventListener("mousemove", hsTrackPointer, true);
  document.removeEventListener("touchmove", hsTrackPointer, true);
}

function hsPointer(e) {
  const p = (e && e.changedTouches && e.changedTouches[0]) || (e && e.touches && e.touches[0]) || e;
  return p ? { x: p.clientX, y: p.clientY } : null;
}

function hsSetMergeTarget(el) {
  if (hsMergeTarget === el) return;
  if (hsMergeTarget) hsMergeTarget.classList.remove("hs-merge-target");
  hsMergeTarget = el;
  if (el) el.classList.add("hs-merge-target");
}

// 拖到圖示正中央 = 要組成資料夾(不換位置);在圖示邊緣停留一下 = 一般的調整順序。
// 邊緣一定要停留才換位置:滑鼠要到達正中央一定會先經過邊緣,如果一碰到就交換,目標圖示就被移開了,
// 永遠組不成資料夾。滑鼠停著不動時 Sortable 不一定會再呼叫 onMove,所以由 hsDragTick 補送移動事件。
function hsOnMove(evt, originalEvent) {
  const draggedKey = evt.dragged.dataset.key;
  const toList = evt.to.dataset.list || "";
  const toPage = toList.startsWith("page:");
  // 資料夾只能放在頁面上(不能放進資料夾、Dock、未排入)
  if (hsIsFolder(draggedKey) && !toPage) return false;

  const related = evt.related;
  const p = hsPointer(originalEvent);
  if (p && related && related.classList.contains("hs-tile") && toPage && !hsIsFolder(draggedKey)) {
    const r = related.getBoundingClientRect();
    const inX = p.x > r.left + r.width * 0.25 && p.x < r.right - r.width * 0.25;
    const inY = p.y > r.top + r.height * 0.2 && p.y < r.bottom - r.height * 0.2;
    if (inX && inY) {
      hsSetMergeTarget(related);
      hsEdgeHover = null;
      return false;
    }
  }
  hsSetMergeTarget(null);
  const alreadyHere = [...evt.to.children].includes(evt.dragged);
  if (!alreadyHere && toPage && evt.to.children.length >= HS_PAGE_LIMIT) return false;
  if (!alreadyHere && toList === "dock" && evt.to.children.length >= HS_DOCK_LIMIT) return false;
  if (!related || !related.classList.contains("hs-tile")) return true;
  if (!hsEdgeHover || hsEdgeHover.el !== related) {
    hsEdgeHover = { el: related, since: Date.now() };
    return false;
  }
  if (Date.now() - hsEdgeHover.since < HS_REORDER_DWELL_MS) return false;
  hsEdgeHover.done = true;
  return true;
}

// 拖曳結束後,以畫面上的順序為準更新狀態
function hsSyncFromDom() {
  document.querySelectorAll("#homescreen-editor .hs-list").forEach((el) => {
    const keys = [...el.querySelectorAll(":scope > .hs-tile")].map((t) => t.dataset.key);
    const name = el.dataset.list;
    if (name === "dock") hsState.dock = keys;
    else if (name === "tray") hsState.tray = keys;
    else if (name.startsWith("page:")) hsState.pages[Number(name.split(":")[1])] = keys;
    else if (hsState.folders[name]) hsState.folders[name].keys = keys;
  });
}

function hsOnEnd(evt) {
  hsStopDragTracking();
  const overlay = document.getElementById("hs-folder-overlay");
  if (overlay && overlay.classList.contains("hs-folder-away")) hsState.openFolder = null;
  const target = hsMergeTarget;
  hsSetMergeTarget(null);
  hsEdgeHover = null;
  hsSyncFromDom();
  const draggedKey = evt.item.dataset.key;
  const p = hsPointer(evt.originalEvent);
  if (target && p) {
    const r = target.getBoundingClientRect();
    if (p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom) {
      hsMerge(hsState, draggedKey, target.dataset.key);
    }
  } else if (p) {
    hsDropOnDock(draggedKey, p);
  }
  hsDissolveSmallFolders(hsState);
  hsRender();
}

// 放開時滑鼠在 Dock 的圖示正中央,Sortable 不會把圖示放進去(只有碰到圖示邊緣才會),
// 但這是很自然的操作,所以依放開的位置自己補上。資料夾不能放進 Dock。
function hsDropOnDock(draggedKey, p) {
  const dock = document.querySelector('#homescreen-editor [data-list="dock"]');
  const r = dock.getBoundingClientRect();
  const inDock = p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom;
  if (!inDock || hsIsFolder(draggedKey) || hsState.dock.includes(draggedKey) || hsState.dock.length >= HS_DOCK_LIMIT) return;
  const tiles = [...dock.querySelectorAll(":scope > .hs-tile")];
  const before = tiles.find((t) => { const b = t.getBoundingClientRect(); return p.x < b.left + b.width / 2; });
  hsRemoveKey(hsState, draggedKey);
  const at = before ? hsState.dock.indexOf(before.dataset.key) : hsState.dock.length;
  hsState.dock.splice(at, 0, draggedKey);
}

function hsCollectFormContext() {
  const { payloads } = collectFormData();
  const restrictions = payloads.restrictions || {};
  const blocked = new Set(restrictions.enabled ? ((restrictions.fields || {}).blockedAppBundleIDs || []) : []);
  const webclip = payloads.webclip || {};
  const webclips = webclip.enabled
    ? (webclip.instances || []).map((i) => ({ url: (i.fields.URL || "").trim(), label: i.fields.Label })).filter((w) => w.url)
    : [];
  return { blocked, webclips, layout: payloads.homescreen.fields.Layout };
}

async function openHomeScreenEditor() {
  const filename = currentFilename || document.getElementById("profile-filename").value.trim();
  const res = await apiFetch(`/api/profiles/homescreen-candidates?filename=${encodeURIComponent(currentIsNew ? "" : filename)}`);
  if (!res.ok) {
    alert("載入失敗: " + ((res.data && res.data.message) || "未知錯誤"));
    return;
  }
  const { blocked, webclips, layout } = hsCollectFormContext();
  hsCatalog = hsBuildCatalog(res.data, blocked, webclips);

  const notices = [];
  if (res.data.group_name) {
    notices.push(["info", `配對群組「${res.data.group_name}」綁定 ${res.data.group_apps.length} 個 App;只有單台裝置授權的 App 不會列在這裡,裝置會自動排在後面。`]);
  } else {
    notices.push(["warn", "這份描述檔還沒有配對群組,所以只會列出原生 App 與網頁捷徑。"]);
  }
  if (res.data.unknown_count > 0) {
    notices.push(["info", `另有 ${res.data.unknown_count} 個群組綁定的 App 是未知 App(已從 App Store 下架或尚未同步),不會列出。`]);
  }
  if ((layout.Dock || []).length > 0 || (layout.Pages || []).length > 0) {
    const restored = hsStateFromLayout(layout, hsCatalog, blocked);
    hsState = restored.state;
    if (restored.removed.length > 0) {
      notices.push(["warn", `已從排列中拿掉已隱藏或已移除的項目: ${restored.removed.join(", ")}`]);
    }
  } else {
    hsState = hsDefaultState(hsCatalog);
  }
  notices.push(["warn", "描述檔裝在裝置上時,小工具會消失;移除描述檔或取消這個項目後會恢復。"]);

  document.getElementById("homescreen-notices").innerHTML = notices.map(([kind, text]) => {
    const style = kind === "warn" ? "background:#fdeee0; color:#b45309;" : "background:#e8eefc; color:#1e40af;";
    return `<div style="${style} padding:7px 12px; border-radius:6px; font-size:12px; margin-bottom:6px;">${escapeHtml(text)}</div>`;
  }).join("");

  hsCurrentPage = 0;
  hsRender();
  openModal("homescreen-modal");
}

function confirmHomeScreenEditor() {
  const layout = hsStateToLayout(hsState);
  const row = document.querySelector("#payloads-container .homescreen-edit-btn").parentElement;
  row.querySelector('input[data-field="Layout"]').value = encodeURIComponent(JSON.stringify(layout));
  row.querySelector(".homescreen-summary").textContent = `${homeScreenLayoutSummary(layout)}(尚未儲存)`;
  closeModal("homescreen-modal");
}

function hsHandleEditorClick(e) {
  if (e.target.id === "hs-prev" || e.target.id === "hs-next") {
    hsSetPage(hsCurrentPage + (e.target.id === "hs-next" ? 1 : -1));
  } else if (e.target.classList.contains("hs-dot")) {
    hsSetPage(Number(e.target.dataset.page));
  } else if (e.target.id === "hs-folder-overlay") {
    hsState.openFolder = null;
    hsRender();
  } else if (e.target.id === "hs-folder-dissolve") {
    const id = hsState.openFolder;
    const page = hsState.pages.find((p) => p.includes(id));
    page.splice(page.indexOf(id), 1, ...hsState.folders[id].keys);
    delete hsState.folders[id];
    hsState.openFolder = null;
    hsRender();
  } else {
    const folderTile = e.target.closest(".hs-folder");
    if (folderTile) {
      hsState.openFolder = folderTile.dataset.key;
      hsRender();
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("homescreen-confirm-btn").addEventListener("click", confirmHomeScreenEditor);
  const editor = document.getElementById("homescreen-editor");
  editor.addEventListener("click", hsHandleEditorClick);
  editor.addEventListener("input", (e) => {
    if (e.target.id === "hs-folder-name" && hsState.openFolder) {
      hsState.folders[hsState.openFolder].name = e.target.value;
    }
  });
  editor.addEventListener("change", (e) => {
    if (e.target.id === "hs-folder-name") hsRender();
  });
});
