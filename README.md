# 🐱 幸せ 591 租屋每日追蹤器

每天中午 12:00（台灣時間）自動抓 591、依你的條件過濾、標「新上架／降價」，
結果發佈成一個牛皮紙風的網頁儀表板（GitHub Pages）。

## ⚠️ 先讀這段（重要）
- 591 有反爬機制且**會不定期改版**，這類爬蟲需要**偶爾維護**，不保證永久有效。
- 若某天抓到 0 筆，先手動觸發 Actions 看 log；多半是網址參數要更新。
- 請維持一天一次、保留請求間隔（`REQUEST_DELAY`），這是對站方的基本禮貌。
- 本工具僅供個人找房整理。三隻貓／寵物附約／獨立電水等細節，系統只能「推測標記」，
  實際一定要看屋時當面向房東確認並寫進合約。

## 設定步驟
1. 建一個新的 GitHub repo（例如 `rent591`），把這些檔案全部上傳。
2. 設定要搜尋的區域 → 打開 `config.json`：
   - 到 591 網頁，選好行政區＋套用「可養寵物」等篩選，
   - 複製網址列整條網址，貼進對應 `url` 欄位（取代 `SECTION_中正` 那幾個佔位符）。
   - 已幫你填好「文山區」「新店區」兩組可直接用，照樣子改其餘四區即可。
3. Repo → **Settings → Pages**，Source 選 `main` 分支的 `/docs` 資料夾。
4. Repo → **Actions** 分頁 → 點 `每日591租屋追蹤` → **Run workflow** 手動跑一次測試。
5. 成功後，你的儀表板網址會是：
   `https://<你的帳號>.github.io/rent591/`
   （建議用 iPhone 加到主畫面，像 App 一樣點開）

## 每天中午自動更新
`.github/workflows/rent.yml` 已設定 `cron: '0 4 * * *'`（UTC 4 點＝台灣 12 點）。
上傳後就會自動排程，不需再動。

## 📧 每天寄 Email 通知（已內建）
跑完後會把「新上架／降價」摘要寄到你信箱。設定一次即可：

1. **開 Gmail 應用程式密碼**（不是你平常的登入密碼）：
   - Google 帳戶要先開兩步驟驗證 → 到「應用程式密碼」產生一組 16 碼密碼。
2. **在 repo 設 3 個 Secret**（Settings → Secrets and variables → Actions → New repository secret）：
   - `GMAIL_USER`：你的 Gmail，例如 `you@gmail.com`
   - `GMAIL_APP_PASSWORD`：剛剛那組 16 碼（貼上時不含空格）
   - `MAIL_TO`：收件信箱（可留空＝寄給自己；多個用逗號分隔）
3. 收工。之後每天中午跑完，**只有在有新上架或降價時才寄信**（避免每天洗版）。
   - 想每天都收一封「確認信」：把 `rent.yml` 裡 `ALWAYS_SEND: "1"` 那行取消註解。

> 密碼只存在 GitHub Secret，程式與儀表板都看不到明碼。
> 若沒設這些 Secret，爬蟲照常更新儀表板，只是不寄信——不會出錯。
> 記得把 `config.json` 的 `dashboard_url` 改成你的實際 Pages 網址，信裡的按鈕才會連對。

（註：LINE Notify 已於 2025/3/31 停止服務，故採用 Email。）

## 檔案結構
```
rent591/
├─ scraper.py                    # 主程式
├─ notify_email.py               # Email 通知模組
├─ config.json                   # 你的搜尋條件（要編輯）
├─ requirements.txt
├─ data/
│  ├─ state.json                 # 價格歷史（自動維護）
│  └─ latest.json                # 今日結果（自動產生）
├─ docs/
│  └─ index.html                 # 儀表板（自動產生，GitHub Pages 來源）
└─ .github/workflows/rent.yml    # 每日排程
```
