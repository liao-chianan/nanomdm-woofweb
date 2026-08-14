--------------------------------------------------------------
# nanomdm-woofweb  說明

**利用開源專案micromdm/nanomdm，搭配自製的精簡web介面來管理校內的iPad**

**使用前提**

1.請用 debian 作業系統，硬體為x86/x64，測試平台為proxmox ve虛擬機搭配debian 12

2.操作環境需要具備一個**對外的域名**與網際網路連線，**80與443 port需要對外開放**


**安裝方式**

1.使用具有sudo權限的使用者，透過下載一鍵安裝的bash命令

```wget https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/nanomdm-woofwebui-install.sh -O nanomdm-woofwebui-install.sh && sudo bash nanomdm-woofwebui-install.sh```


2.安裝過程中會詢問必要參數，API KEY與資料庫密碼會預設用亂數產生，並且自動部署docker與相關服務

3.安裝完畢後，還需要透過網頁介面進行後續的憑證處理作業

4.[憑證狀態檢視]與[系統狀態] 如果都正常，就可以開始進行設備部署作業

--------------------------------------------------------------
# mdmcert利用方式與前提說明

大多數憑證都可以透過ASM/ABM平台直接取得，但APNs的推播憑證是最難取得的憑證，以下說明這個工具用途

來源是免費mdm cert工具mdmctl，這是由micromdm/nanomdm的開發者
Jesse Peterson  https://github.com/jessepeterson  所提供的免費平台與工具

如果需要linux / macos版本可以到原始官方網站下載release zip檔案  https://github.com/micromdm/micromdm/releases

官方操作說明：
https://github.com/micromdm/micromdm/blob/main/docs/user-guide/mdmctl-signing-profiles.md


--------------------------------------------------------------
# 自製的 mdmcert-free-cert-apply_win-x64.zip 操作說明

https://raw.githubusercontent.com/liao-chianan/nanomdm-woofweb/main/mdmcert-free-cert-apply_win-x64.zip

**這個檔案是自製的windows x64的編譯執行檔與自動化power shell，讓使用者可以windows環境底下處理取得APNs推播憑證**

1.請先到https://mdmcert.download/ 註冊與驗證，email需要是edu的網域，需要收信驗證

2.驗證成功後請解壓縮這個檔案，用powershell執行 01-mdmctl-freecert-email.ps1，再次輸入你申請的email

3.大約等個幾分鐘，去email收信，會收到帶有時間戳記的plist.b64.p7檔案，把這個檔案下載後放到mdmctl同一個資料夾

4.執行02-mdmctl-freecert-decrypt.ps1，會再產出一個push.req檔案

5.透過https://identity.apple.com/pushcert/  可以搭配這個push.req檔案來產生並且pem憑證

6.push.key檔案為私鑰，pem檔案為配對的憑證，去nanomdm的webui中就可以上傳

!!重要，這個資料夾的資料務必保留好!!   

pem效期只有一年，屆時需要再利用push.req重新產生一次pem憑證
(push.req不需要更新，可以直接用舊的)

P.S. 教育單位也可以申請免費apple developer方案，藉此產生mdm憑證，但是需要許多額外的申請步驟

--------------------------------------------------------------

開發原因：2026年6月份，apple ac2的功能故障，不得不開發這個工具  (詳見此討論串)

https://forums.macrumors.com/threads/apple-configurator-2-cannot-sign-in-error-message-displayed.2484580/

感謝原始開發nanomdm與micromdm的貢獻者，釋出這個工具，希望對沒有管理經費的小型學校有所助益




