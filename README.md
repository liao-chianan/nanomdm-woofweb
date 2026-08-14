# nanomdm-woofweb
Woof Woof

--------------------------------------------------------------
mdmcert-free-cert-apply_win-x64.zip 說明

免費mdm cert工具mdmctl，這是由micromdm/nanomdm的開發者
Jesse Peterson  https://github.com/jessepeterson  所提供的免費平台與工具

自行補上了windows x64的編譯執行檔與自動化power shell，操作步驟如下

1.請先到https://mdmcert.download/ 註冊與驗證，email需要是edu的網域，需要收信驗證

2.驗證成功後請解壓縮這個檔案，用powershell執行 01-mdmctl-freecert-email.ps1，再次輸入你申請的email

3.大約等個幾分鐘，去email收信，會收到帶有時間戳記的plist.b64.p7檔案，把這個檔案下載後放到mdmctl同一個資料夾

4.執行02-mdmctl-freecert-decrypt.ps1，會再產出一個push.req檔案

5.透過https://identity.apple.com/pushcert/  可以搭配這個push.req檔案來產生並且pem憑證

6.push.key檔案為私鑰，pem檔案為配對的憑證，去nanomdm的webui中就可以上傳

!!重要，這個資料夾的資料務必保留好!!   

pem效期只有一年，屆時需要再利用push.req重新產生一次pem憑證
(push.req不需要更新，可以直接用舊的)
