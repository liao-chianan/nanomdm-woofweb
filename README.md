# nanomdm-woofweb

利用開源專案 [micromdm/nanomdm](https://github.com/micromdm/) 搭配自製的精簡 Web 介面，來管理校內的 iPad。

---

## 使用前提

1. 作業系統：Debian，硬體為 x86/x64（測試平台為 Proxmox VE 虛擬機搭配 Debian 12）
2. 操作環境需要具備一個**對外的域名**與網際網路連線，**80 與 443 port 需要對外開放**
3. 具備Apple ASM/ABM 的管理帳號

---

## 安裝方式

### 1. 一鍵安裝

使用具有 `sudo` 權限的使用者，下載並執行一鍵安裝腳本：

```bash
wget https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/nanomdm-woofwebui-install.sh -O nanomdm-woofwebui-install.sh && sudo bash nanomdm-woofwebui-install.sh
```

> P.S.專案安裝過程會用到的docker image，為了保持版本穩定性，主要是用我測試過的docker image直接提供 (直接用官方最新版可能會導致相容性問題)  

> P.S.專案安裝過程會自動透過certbot申請憑證，let's encrypt經實測有機會撞到教育網域的每周申請上限，故再提供ZeroSSL的申請管道(需先註冊ZeroSSL取得EAB資料)，另補上一個測試用的let's encrypt管道，單純是測試能否成功安裝，無法作為正式環境使用

> P.S.安裝完成後，可以在 **[憑證管理]** 的「nginx HTTPS 憑證」區塊按「**更換憑證...**」，隨時把 nginx 憑證換成正式的 Let's Encrypt、ZeroSSL，或手動上傳學校的萬用字元憑證，不需要重新安裝；切換失敗會自動切回原本的憑證，已註冊的裝置也不需要重新註冊

### 2. 互動式設定

安裝過程中會詢問必要參數。API KEY 與資料庫密碼會預設用亂數產生，並且自動部署 Docker 與相關服務。

### 3. 憑證處理作業

安裝完畢後，還需要透過網頁介面進行後續的憑證處理作業。**[憑證管理]** 頁面中有提示如何取得憑證並上傳：

#### APNs Push 憑證

最麻煩的憑證。正常管道是付費訂閱 Apple Developer Program 後取得 MDM 憑證，再去產生 push cert。

免費的管道則是透過 mdmcert.download 來申請憑證，請參考下方「[透過 mdmcert.download 免費取得 APNs 推播憑證](#透過-mdmcertdownload-免費取得-apns-推播憑證)」章節。

> 學校單位亦可以透過申請方式免費訂閱 Apple Developer Program，但需要不少額外步驟，有興趣可以自行申請(我在2025年有成功申請過，2026年也成功免費續訂)。

#### DEP OAuth Token

1. 在 **[憑證管理]** 的「DEP OAuth Token」區塊按「**下載公鑰(供上傳ASM)**」
2. 接著到 [school.apple.com 偏好設定 → 裝置管理服務](https://school.apple.com/#/main/preferences/myprofile)，選擇指定的伺服器：
   - **編輯 → 上傳公用密鑰**：上傳剛剛下載的公鑰（新設/更換伺服器時需要）
   - **下載權杖**：下載 `.p7m` 檔案
3. 回到 **[憑證管理]** 按「**上傳Token...**」，選擇 `.p7m` 檔案匯入

#### VPP Content Token

到 [school.apple.com 地點](https://school.apple.com/#/main/locations)（地點 → 選擇指定的伺服器 → 內容代號 → 下載），下載對應的 VPP Token 檔案（`.vpptoken`），再到 **[憑證管理]** 的「VPP Content Token」區塊按「**上傳新Token...**」上傳即可。

#### NanoAXM 私鑰/OAuth 憑證

到 [school.apple.com 偏好設定 → API](https://school.apple.com/#/main/preferences/apiaccounts)，可以查看/建立 Client ID（用戶端 ID）與 Key ID（密鑰 ID）。

建立後到 **[憑證管理]** 的「NanoAXM 私鑰/OAuth憑證」區塊按「**更新...**」，填入 Client ID、Key ID 並選擇下載的私鑰檔案（`.pem`）。

> 過程會下載金鑰，**僅能下載一次**，請妥善保管（注意 Client ID 和 Key ID 不要搞混）。

### 4. 同步 ASM 裝置

**[憑證管理]** 與 **[系統狀態]** 如果都正常，可以到 **[ASM 所有裝置]** 同步：

- 可以撈取 ASM 中所有裝置的資訊
- 也可以把其他 MDM Server 管理的裝置改派到這台新的 nanomdm（支援 CSV 批次處理）

### 5. 開始部署

**[裝置註冊狀態]** 如果有看到設備，代表就可以開始進行部署作業，建議步驟如下：

1. 再製/編修群組註冊檔並套用
2. 再製/編修群組描述檔
3. 新增群組，並選定要搭配的群組註冊檔＋群組描述檔，設定此群組要綁定安裝的 App
4. 到 **[裝置註冊狀態]** 幫裝置取名、指定群組後按「**套用變更**」，當裝置出現 DEP profile_uuid 與對應範本，代表裝置可以清空重新註冊
5. 裝置註冊後會自動派發註冊檔＋描述檔＋安裝 App，註冊成功就可以進行個別命令派送/群組命令派送

---

## 透過 mdmcert.download 免費取得 APNs 推播憑證

大多數憑證都可以透過 ASM/ABM 平台直接取得，但 APNs 推播憑證是最難取得的憑證。標準管道是付費訂閱 Apple Developer Program（教育單位也可以申請免費方案，但需要許多額外的申請步驟，這邊暫不說明）。

[mdmcert.download](https://mdmcert.download/) 是 micromdm/nanomdm 開發者 [Jesse Peterson](https://github.com/jessepeterson) 提供的免費服務，用他的 Apple Developer 訂閱幫大家代簽推播憑證的 CSR，讓沒有訂閱 Apple Developer Program 的使用者也能取得 APNs 推播憑證。

> ⚠️ **2026 年 11 月 mdmcert.download 已改版，舊的申請方式已經停用**
> - 現在必須用 **GitHub 或 GitLab 帳號登入**才能申請，不再用 email 註冊驗證，也不再寄送加密的 `.p7` 附件。
> - 舊的 `mdmctl mdmcert.download -new / -decrypt` 指令，以及本專案先前提供的 `mdmcert-free-cert-apply_win-x64.zip`（Windows 版工具）都已經**無法使用**，舊 API 會回應 `This API has been deprecated`。
> - 已經用舊方式取得、還在有效期內的推播憑證不受影響，可以繼續使用；到期續約時改用下方的新流程即可。
> - 官網說明這個免費服務主要用於測試與入門，正式營運建議申請自己的 Apple Developer 帳號。

### 步驟 1：準備 GitHub 或 GitLab 帳號

沒有的話，請先到 [github.com](https://github.com/) 或 [gitlab.com](https://gitlab.com/) 免費註冊一個帳號。

### 步驟 2：產生推播私鑰與 CSR

#### 方式一：在 nanomdm-woofweb 產生（推薦）

1. 到 **[憑證管理]** 的「APNs Push 憑證」區塊，按「**產生推播 CSR...**」。
2. 填入國碼（預設 TW）與管理者 email，輸入登入密碼確認後按「產生 CSR」。
3. 瀏覽器會下載 `push.csr`，等一下要上傳到 mdmcert.download。

私鑰只會存在伺服器上（WebUI 目錄下的 `.apns_csr/`，只有 root 可以讀取），不需要自己保管檔案，也不用安裝 openssl：

- 上傳 Apple 的 `.pem` 時會自動找到配對的私鑰。
- 使用中的私鑰會保留到同一個 Topic 下次續約成功才刪除；還沒使用的最多保留 3 份，超過 30 天自動清除。
- 同一個視窗可以「下載私鑰備份」（需要密碼確認），建議另外離線保存一份。

#### 方式二：自己用 openssl 產生

需要 `openssl` 指令：

- **macOS / Linux**：系統內建，直接開終端機執行。
- **Windows**：安裝 [Git for Windows](https://git-scm.com/download/win) 後，開啟「Git Bash」執行（內含 openssl）。

建立一個專用資料夾，在裡面執行（`C=` 是國碼，`emailAddress=` 請換成學校管理者的信箱）：

```bash
openssl req -out push.csr -newkey rsa:2048 -keyout push.key -nodes -subj "/C=TW/CN=MDM APNs Push/emailAddress=mdm-admin@example.edu.tw"
```

執行後會產生兩個檔案：

- 🔑 `push.key`：推播**私鑰**，請妥善保管，不要交給任何人或上傳到 mdmcert.download
- 📄 `push.csr`：憑證簽署請求，等一下要上傳到 mdmcert.download

### 步驟 3：登入 mdmcert.download 並上傳 CSR

1. 前往 [mdmcert.download](https://mdmcert.download/)，點選「**Sign in with GitHub**」或「**Sign in with GitLab**」登入。
2. 第一次登入會進入註冊表單，填寫組織名稱、姓名、國家等資料並同意服務條款。
3. 進入「**Sign**」頁面，上傳步驟 2 取得的 `push.csr`。若是續約既有憑證，請勾選「**This is a renewal of an existing certificate**」。
4. 簽署完成後，瀏覽器會直接下載 `apns_push.plist.b64.req` 檔案（不需要再收信或解密）。

### 步驟 4：到 Apple Push Certificates Portal 申請 APNs 憑證

1. 前往 <https://identity.apple.com/pushcert>
2. 用學校/機構專用的 Apple ID 登入。
3. 第一次申請點選「**Create a Certificate**」；續約既有憑證請在該憑證旁選「**Renew**」。
4. 上傳步驟 3 下載的 `apns_push.plist.b64.req`。
5. 下載 Apple 簽發的 APNs 憑證（`.pem` 檔）。

> 上傳後 Vendor 欄位會顯示為 Jesse Peterson 的組織，這是因為 CSR 是由他的訂閱方案代簽的。

### 步驟 5：上傳到 nanomdm-woofweb

到 **[憑證管理]** 的「APNs Push 憑證」區塊，按「**上傳新憑證...**」：

- 📄 **憑證**：剛從 Apple 下載的 `.pem` 檔
- 🔑 **私鑰**：步驟 2 用方式一產生的話**不用選**，會自動配對；用方式二的話選 `push.key`

> ⚠️ **請務必妥善備份私鑰與憑證**（方式一可以在「產生推播 CSR」視窗下載私鑰備份），遺失私鑰會導致該張 APNs 憑證無法使用。
> ⚠️ **務必在到期日之前續約**，APNs 憑證效期只有一年，一旦過期，就得重新產生全新的憑證，所有裝置都必須重新註冊。
> ⚠️ **續約時一定要在 Apple 網站選 Renew，不要 Create**，Renew 才不會變更 Topic；一旦 Topic 改變，所有裝置都必須重新註冊。
>
> 續約時重跑步驟 2～5 即可（每次都可以產生新的私鑰，只要上傳時和新的 `.pem` 配對使用）。

---

## 開發緣起

2026 年 7 月份，免費的Apple Configurator 2 的功能故障了近一個月，不得不開發這個工具  
（[詳見此討論串](https://forums.macrumors.com/threads/apple-configurator-2-cannot-sign-in-error-message-displayed.2484580/)）  

特別感謝原始開發 nanomdm 與 micromdm 的開發者，有了他們才能產出這個小專案!  
釋出這個用Claude自製的工具，希望對沒有管理經費的小型學校有所助益。  

**🐶 Woof! Woof! 🐾**
