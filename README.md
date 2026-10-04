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

### 2. 互動式設定

安裝過程中會詢問必要參數。API KEY 與資料庫密碼會預設用亂數產生，並且自動部署 Docker 與相關服務。

### 3. 憑證處理作業

安裝完畢後，還需要透過網頁介面進行後續的憑證處理作業。**[憑證狀態檢視]** 頁面中有提示如何取得憑證並上傳：

#### APNs Push 憑證

最麻煩的憑證。正常管道是付費訂閱 Apple Developer Program 後取得 MDM 憑證，再去產生 push cert。

免費的管道則是透過 mdmcert 來申請憑證，請參考下方「[mdmcert 搭配 mdmctl 利用方式與前提說明](#mdmcert-搭配-mdmctl-利用方式與前提說明)」章節。

> 學校單位亦可以透過申請方式免費訂閱 Apple Developer Program，但需要不少額外步驟，有興趣可以自行申請(我在2025年有成功申請過，2026年也成功免費續訂)。

#### DEP OAuth Token

1. 先從自己的 nanomdm webui 下載公鑰
2. 接著到 [school.apple.com 偏好設定 → 裝置管理服務](https://school.apple.com/#/main/preferences/myprofile)，選擇指定的伺服器：
   - **編輯 → 上傳公用密鑰**：更新公鑰（新設/更換伺服器時需要）
   - **下載權杖**：下載 `.p7m` 檔案，上傳給自己的 nanomdm 伺服器更新 Token

#### VPP Content Token

到 [school.apple.com 偏好設定 → 付款與帳單 → 內容與代號](https://school.apple.com/#/main/preferences/paymentsandbilling/appsandbooks)，下載對應的 VPP Token 檔案，新增/取代 `.vpptoken` 檔案即可。

#### NanoAXM 私鑰/OAuth 憑證

到 [school.apple.com 偏好設定 → API](https://school.apple.com/#/main/preferences/apiaccounts)，可以查看/建立 Client ID（用戶端 ID）與 Key ID（密鑰 ID）。

> 過程會下載金鑰，**僅能下載一次**，請妥善保管（注意 Client ID 和 Key ID 不要搞混）。

### 4. 同步 ASM 裝置

**[憑證狀態檢視]** 與 **[系統狀態]** 如果都正常，可以到 **[ASM 所有裝置]** 同步：

- 可以撈取 ASM 中所有裝置的資訊
- 也可以把其他 MDM Server 管理的裝置改派到這台新的 nanomdm（支援 CSV 批次處理）

### 5. 開始部署

**[裝置註冊狀態]** 如果有看到設備，代表就可以開始進行部署作業，建議步驟如下：

1. 再製/編修群組註冊檔並套用
2. 再製/編修群組描述檔
3. 新增群組，並選定要搭配的群組註冊檔＋群組描述檔，設定此群組要綁定安裝的 App
4. 到 **[裝置註冊狀態]** 幫裝置取名、指定群組並存檔，當裝置出現 DEP profile_uuid 與對應範本，代表裝置可以清空重新註冊
5. 裝置註冊後會自動派發註冊檔＋描述檔＋安裝 App，註冊成功就可以進行個別命令派送/群組命令派送

---

## mdmcert 搭配 mdmctl 工具與前提說明

大多數憑證都可以透過 ASM/ABM 平台直接取得，但 APNs 的推播憑證是最難取得的憑證，標準管道是透過付費訂閱 Apple Developer Program 取得，教育單位也可以申請免費 Apple Developer Program方案，藉此產生 MDM 憑證，但需要許多額外的申請步驟，這邊暫不說明。

nanomdm提供了 MDM cert 工具 **mdmctl**，這是由 micromdm/nanomdm 的開發者 [Jesse Peterson](https://github.com/jessepeterson) 所提供的工具，可以搭配已訂閱 Apple Developer Program的使用者或想利用[Jesse Peterson](https://github.com/jessepeterson) 免費平台的使用者。

- 原始官方網站提供 Linux / macOS 版本，可下載 release zip 檔案：[github.com/micromdm/micromdm/releases](https://github.com/micromdm/micromdm/releases)
  
- mdmctl 官方操作說明(這是針對已經訂閱Apple Developer Program方案的說明)：[mdmctl-signing-profiles.md](https://github.com/micromdm/micromdm/blob/main/docs/user-guide/mdmctl-signing-profiles.md)

**如果沒有訂閱Apple Developer Program方案，建議直接參考下方說明**

---

## 適用windows x64作業系統，搭配自製的 mdmcert-free-cert-apply_win-x64.zip ，完全免費的APNs取得管道操作說明  

下載連結：[mdmcert-free-cert-apply_win-x64.zip](https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/mdmcert-free-cert-apply_win-x64.zip)

這個檔案是透過 mdmctl 的原始碼自製的 Windows x64 編譯執行檔與自動化 PowerShell，讓使用者可以在 Windows 環境底下搭配[mdmcert.download]網站，用教育體系email申請並取得 APNs 推播憑證。

1. 請先到 [mdmcert.download](https://mdmcert.download/) 註冊與驗證，email 需要是 `.edu` 網域，需要收信驗證
2. 驗證成功後請下載並解壓縮mdmcert-free-cert-apply_win-x64.zip這個檔案，用 PowerShell 執行 `01-mdmctl-freecert-email.ps1`，再次輸入你申請的 email申請p7檔案
3. 大約等個幾分鐘，去 email 收信，會收到帶有時間戳記的 `plist.b64.p7` 檔案，把這個檔案下載後放到 mdmctl 同一個資料夾
4. 執行 `02-mdmctl-freecert-decrypt.ps1`，會再產出一個 `push.req` 檔案
5. 透過 [identity.apple.com/pushcert](https://identity.apple.com/pushcert/) 可以搭配這個 `push.req` 檔案來產生 pem 憑證，請下載這個 pem 憑證檔案
6. 此時資料夾中的 `push.key` 檔案為私鑰，pem 檔案為配對的憑證，在 nanomdm-woofweb 中可以上傳這兩個檔案，來進行 APNs 推播使用

> ⚠️ **重要，這個資料夾的檔案請保留好！**  
> ⚠️ **務必在到期日之前更新apple push cert，一旦過期，就得重新產生全新apple push cert，所有裝置都必須重新註冊**  
> ⚠️ **更新時選renew才不會變更Topic，一旦變更Topic，所有裝置都必須重新註冊**  
>
> pem 效期只有一年，到期前需要再重跑一次完整的mdmctl流程取得 `push.req` 再重新產生一次 pem 憑證。
> （`push.req`上傳後，Vendor會是[Jesse Peterson]，這是因為作者免費開放給大家使用他的訂閱方案 ）



## 適用Linux / macOS 版 搭配官方版本的mdmctl，完全免費的APNs取得管道操作說明 


### 步驟 1：下載mdmctl  

前往 [micromdm/micromdm Releases](https://github.com/micromdm/micromdm/releases)，下載最新版release
(目前最新版本為 v1.13.1  https://github.com/micromdm/micromdm/releases/download/v1.13.1/micromdm_v1.13.1.zip)

下載並解壓縮後，在終端機切換到該資料夾並賦予執行權限：

```bash
wget https://github.com/micromdm/micromdm/releases/download/v1.13.1/micromdm_v1.13.1.zip

unzip unzip micromdm_v1.13.1.zip
```

解壓縮後會得到build資料夾，其中有兩種版本linux與darwin(macOS用)，視你的作業環境進入對應資料夾

```bash
cd build/linux 或者 cd build/darwin
chmod +x mdmctl
```

**macOS 補充**：若首次執行跳出「無法打開，因為無法驗證開發者」：

- 到「系統設定 → 隱私權與安全性」允許執行，或
- 執行以下指令移除隔離標記：
  ```bash
  xattr -d com.apple.quarantine ./mdmctl
  ```

### 步驟 2：建立離線設定檔  

```bash
mkdir -p ~/.micromdm
echo '{}' > ~/.micromdm/servers.json
```
### 步驟 3：發起 mdmcert.download CSR 請求

把 email 換成你在 [mdmcert.download](https://mdmcert.download) 註冊並完成驗證的信箱：

```bash
./mdmctl mdmcert.download -new -email=xxx@oo.xx.edu.tw
```

執行成功後，會在當前資料夾下產生：

```
mdm-certificates/mdmcert.download.push.key
```

這是你的 **push 私鑰**，請妥善保留，後續匯入 MDM 時會用到。

### 步驟 4：收信並下載附件

1. 前往上述信箱收信。
2. 找到 mdmcert.download 寄來的信，下載附件檔（`.p7` 結尾，例如 `mdm_signed_request.xxxx.plist.b64.p7`）。
3. 把這個 `.p7` 檔案放到跟 `mdmctl` **同一個資料夾**內。


### 步驟 5：解密附件

先確認附件檔名（可用 `ls *.p7` 查看），再執行：

```bash
./mdmctl mdmcert.download -decrypt=./mdm_signed_request.xxxx.plist.b64.p7
```

成功後會產生：

```
mdm-certificates/mdmcert.download.push.req
```


### 步驟 6：到 Apple Push Certificates Portal 申請 APNs 憑證

1. 前往 <https://identity.apple.com/pushcert>
2. 用學校/機構專用的 Apple ID 登入。
3. 點選「**Create a Certificate**」（若是續簽既有憑證，選「**Renew**」）。
4. 上傳步驟 5 產生的檔案：
   ```
   mdm-certificates/mdmcert.download.push.req
   ```
5. 下載 Apple 簽發的 APNs 憑證（`.pem` 或 `.cer`）。



## 步驟 7：整理最終檔案

匯入到 MDM 系統（NanoMDM）時，需要搭配以下兩個檔案：

- 🔑 **私鑰**：`mdm-certificates/mdmcert.download.push.key`（步驟 3 產生）
- 📄 **憑證**：剛從 Apple 下載的 `.pem` / `.cer` 檔

⚠️ **請務必將這兩個檔案一起妥善備份**，遺失私鑰會導致該張 APNs 憑證無法繼續使用。  
⚠️ **此步驟每年需重做更新一次，確保憑證到期前處理，避免裝置需要全部重新註冊**

---

## 開發緣起

2026 年 7 月份，免費的Apple Configurator 2 的功能故障了近一個月，不得不開發這個工具  
（[詳見此討論串](https://forums.macrumors.com/threads/apple-configurator-2-cannot-sign-in-error-message-displayed.2484580/)）  

特別感謝原始開發 nanomdm 與 micromdm 的開發者，有了他們才能產出這個小專案!  
釋出這個用Claude自製的工具，希望對沒有管理經費的小型學校有所助益。  

**🐶 Woof! Woof! 🐾**
