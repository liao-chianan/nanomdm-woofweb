--------------------------------------------------------------
# nanomdm-woofweb  說明

**利用開源專案micromdm/nanomdm   https://github.com/micromdm/   搭配自製的精簡web介面來管理校內的iPad**

**使用前提**

1.請用 debian 作業系統，硬體為x86/x64，測試平台為proxmox ve虛擬機搭配debian 12

2.操作環境需要具備一個**對外的域名**與網際網路連線，**80與443 port需要對外開放**


**安裝方式**

1.使用具有sudo權限的使用者，透過下載一鍵安裝的bash命令

```wget https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/nanomdm-woofwebui-install.sh -O nanomdm-woofwebui-install.sh && sudo bash nanomdm-woofwebui-install.sh```


2.安裝過程中會詢問必要參數，API KEY與資料庫密碼會預設用亂數產生，並且自動部署docker與相關服務  

3.安裝完畢後，還需要透過網頁介面進行後續的憑證處理作業，[憑證狀態檢視] 中有提示如何取得憑證並且上傳：  
==>APNs Push 憑證：最麻煩的憑證，正常管道是付費訂閱apple developer program後取得mdm憑證再去產生push cert  
免費的管道則是透過mdmcert來申請憑證，請往下看 [mdmcert搭配mdmctl利用方式與前提說明]  
學校單位亦可以透過申請方式免費訂閱apple developer program，但需要不少額外步驟，有興趣可以自行申請  

==>DEP OAuth Token：到 https://school.apple.com/#/main/preferences/myprofile (偏好設定 → 裝置管理服務),  
選擇指定的伺服器:「編輯 → 上傳公用密鑰」是更新公鑰(新設/更換伺服器時需要)，「下載權杖」可以下載 .p7m 檔案上傳給自己的nanomdm伺服器更新 Token;。  

==>VPP Content Token：到 https://school.apple.com/#/main/preferences/paymentsandbilling/appsandbooks (偏好設定 → 付款與帳單 → 內容與代號),  
下載對應的 VPP Token 檔案,新增/取代.vpptoken 檔案即可。  

==>NanoAXM 私鑰/OAuth憑證：到 https://school.apple.com/#/main/preferences/apiaccounts (偏好設定 → API),  
可以查看/建立 Client ID(用戶端ID)與 Key ID(密鑰ID)，過程會下載金鑰，僅能下載一次，請妥善保管(注意client ID和Key ID不要搞混)

4.[憑證狀態檢視]與[系統狀態] 如果都正常，可以到[ASM所有裝置]同步，  
可以撈取ASM中所有裝置的資訊，也可以把其他mdm server管理的裝置改派到這台新的nanomdm (支援csv批次處理)  

5.[裝置註冊狀態]如果有看到設備，代表就可以開始進行部署作業，建議步驟如下：  
==>再製/編修群組註冊檔並套用  
==>再製/編修群組描述檔  
==>新增群組，並選定要搭配的群組註冊檔+群組描述檔，設定此群組要綁定安裝的APP  
==>到[裝置註冊狀態]幫裝置取名，指定群組並存檔，當裝置出現 DEP profile_uuid與對應範本，代表裝置可以清空重新註冊  
==>裝置註冊後會自動派發註冊檔+描述檔+安裝APP，註冊成功就可以進行個別命令派送/群組命令派送  

--------------------------------------------------------------
# mdmcert搭配mdmctl利用方式與前提說明

大多數憑證都可以透過ASM/ABM平台直接取得，但APNs的推播憑證是最難取得的憑證，標準管道是透過付費訂閱Apple Developer Program取得

取得來源是免費mdm cert工具mdmctl，這是由micromdm/nanomdm的開發者
Jesse Peterson  https://github.com/jessepeterson  所提供的免費平台與工具

如果需要linux / macos版本可以到原始官方網站下載release zip檔案  https://github.com/micromdm/micromdm/releases

mdmctl官方操作說明：
https://github.com/micromdm/micromdm/blob/main/docs/user-guide/mdmctl-signing-profiles.md


--------------------------------------------------------------
# 自製的 mdmcert-free-cert-apply_win-x64.zip 操作說明

https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/mdmcert-free-cert-apply_win-x64.zip

**這個檔案是自製的windows x64的編譯執行檔與自動化power shell，讓使用者可以windows環境底下處理取得APNs推播憑證**

1.請先到https://mdmcert.download/ 註冊與驗證，email需要是edu的網域，需要收信驗證

2.驗證成功後請解壓縮這個檔案，用powershell執行 01-mdmctl-freecert-email.ps1，再次輸入你申請的email

3.大約等個幾分鐘，去email收信，會收到帶有時間戳記的plist.b64.p7檔案，把這個檔案下載後放到mdmctl同一個資料夾

4.執行02-mdmctl-freecert-decrypt.ps1，會再產出一個push.req檔案

5.透過https://identity.apple.com/pushcert/  可以搭配這個push.req檔案來產生pem憑證，請下載這個pem憑證檔案

6.此時資料夾中的push.key檔案為私鑰，pem檔案為配對的憑證，在nanomdm woofweb中可以上傳這兩個檔案，來進行APNs推播使用

!!重要，這個資料夾的資料務必保留好!!   

pem效期只有一年，屆時需要再利用push.req重新產生一次pem憑證
(push.req不需要更新，可以直接用舊的)

P.S. 教育單位也可以申請免費apple developer方案，藉此產生mdm憑證，但是需要許多額外的申請步驟

--------------------------------------------------------------

開發原因：2026年6月份，apple ac2的功能故障，不得不開發這個工具  (詳見此討論串)

https://forums.macrumors.com/threads/apple-configurator-2-cannot-sign-in-error-message-displayed.2484580/

感謝原始開發nanomdm與micromdm的貢獻者，釋出這個工具，希望對沒有管理經費的小型學校有所助益  Woof! Woof!




