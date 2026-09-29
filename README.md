# Clipnest

- Website: https://vanphong2809.github.io/clipnest/
- API: https://clipnest-api.onrender.com/api/health
- Repository: https://github.com/vanphong2809/clipnest

Đã deploy Render Free (Docker, Singapore) và GitHub Pages (nhánh gh-pages) ngày 29/09/2026. Đã kiểm thử tải thật MP4, MP3 và ZIP 2 video trên backend Render; xem TEST_REPORT.md.

Website tải video TikTok bằng FastAPI + yt-dlp, giao diện HTML/CSS/JavaScript thuần. Không có framework hay build step cho frontend.

**Chỉ dùng để tải nội dung của chính mình hoặc nội dung được phép tải; tôn trọng bản quyền và [Điều khoản sử dụng của TikTok](https://www.tiktok.com/legal/page/row/terms-of-service/vi).** Clipnest là công cụ độc lập, không liên kết với TikTok. Công khai không đồng nghĩa với được phép tái sử dụng.

## Chức năng

- Dán link video đầy đủ, `vm.tiktok.com`, `vt.tiktok.com` hoặc `www.tiktok.com/t/…`; xem tiêu đề, tác giả, ảnh thu nhỏ, thời lượng.
- Tải MP4 hoặc tách MP3 bằng ffmpeg. Không cam kết loại bỏ watermark.
- Tải theo `abc`, `@abc`, URL hồ sơ, userId dạng số hoặc secUid; mặc định 20, từ 1 đến 100 video. Tải tuần tự trong mỗi job, theo thứ tự TikTok cung cấp.
- Tiến trình số video đã xử lý/thành công, lưu job trong sessionStorage để có thể tải lại trang. Video lỗi được ghi trong `LOI_TAI.txt` bên trong ZIP. Nếu tất cả video lỗi, job báo lỗi.
- Giao diện tiếng Việt, responsive, điều hướng bàn phím và thông báo trạng thái.

## Cấu trúc

```text
tiktok-downloader/
  backend/main.py
  backend/requirements.txt
  backend/Dockerfile
  backend/.dockerignore
  backend/.env.example
  frontend/index.html
  frontend/style.css
  frontend/app.js
  frontend/config.js
  tests/test_api.py
  render.yaml
  pytest.ini
  .gitignore
  README.md
```

`.venv`, `.venv311`, `.tools` và `work` trên máy này chỉ phục vụ chạy/thử local, không commit. `work/` chứa dữ liệu kiểm thử thật; không nằm trong frontend hoặc Docker image.

## Chạy local

Cần Python 3.11 trở lên và ffmpeg. Trên macOS có thể cài bằng `brew install python@3.11 ffmpeg gh`; trên Ubuntu dùng trình quản lý gói tương ứng. Windows có thể dùng Python từ python.org và `winget install Gyan.FFmpeg`, sau đó mở Terminal mới.

Tại thư mục dự án:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-proxy-headers
```

PowerShell dùng `.venv\Scripts\Activate.ps1` và `Copy-Item backend/.env.example backend/.env`. Nếu chưa có ffmpeg trong PATH, đặt đường dẫn executable/thư mục chứa ffmpeg và ffprobe vào `FFMPEG_LOCATION` trong `backend/.env`.

Trên máy macOS đã chuẩn bị trong phiên xây dựng này, có sẵn Python 3.11 và ffmpeg portable; chạy:

```sh
cd ~/Desktop/tiktok-downloader
FFMPEG_LOCATION="$PWD/.tools/ffmpeg" .venv311/bin/uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Mở Terminal thứ hai:

```sh
cd ~/Desktop/tiktok-downloader
python3 -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Truy cập <http://localhost:5500>. API kiểm tra tại <http://localhost:8000/api/health>. Không mở trực tiếp `index.html` bằng `file://` vì origin không hợp lệ. `frontend/config.js` tự dùng `http://localhost:8000` khi mở localhost/127.0.0.1, và dùng `https://clipnest-api.onrender.com` khi truy cập website public.

Chạy Docker:

```sh
docker build -t clipnest backend
docker run --rm -p 8000:8000 --env-file backend/.env clipnest
```

## API

| Endpoint | Dữ liệu | Kết quả |
|---|---|---|
| `GET /api/health` | — | `status`, giới hạn ZIP |
| `POST /api/video/info` | `{"url":"https://…"}` | `title`, `author`, `thumbnail`, `duration`, `url` chuẩn hoá |
| `GET /api/video/download?url=…&format=mp4` | URL được encode | MP4, `Content-Disposition: attachment` |
| `GET /api/video/download?url=…&format=mp3` | URL được encode | MP3, `Content-Disposition: attachment` |
| `POST /api/user/start` | `{"username":"@abc","limit":20}` | HTTP 202, `job_id` |
| `GET /api/job/{job_id}` | — | `status`, `downloaded`, `processed`, `total`, `current`, `message`, `failures`, `expires_at` |
| `GET /api/job/{job_id}/zip` | — | ZIP khi `done`; 409 khi chưa xong, 404 nếu hết hạn |

`status`: `queued`, `running`, `done`, `error`. `total=0` trong lúc đang lấy danh sách. `downloaded` đếm thành công; `processed` gồm cả lỗi. `done` có thể có video lỗi; luôn kiểm tra `failures`.

FileResponse gửi file theo khối, không đọc cả MP4/ZIP vào RAM backend. Frontend dùng Blob để nhận lỗi JSON trước khi lưu; ZIP lớn có thể tốn RAM trên điện thoại. MP4/MP3 được dọn sau khi trả file; ZIP cho tải lại trong 15 phút rồi dọn. Sweeper chạy mỗi 30 giây, nên xóa vật lý có thể trễ tối đa khoảng 30 giây; API chặn tải ngay khi hết hạn. File ZIP đang được gửi không bị sweeper xóa giữa chừng.

## Cấu hình và giới hạn

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---:|---|
| `ALLOWED_ORIGINS` | localhost và 127.0.0.1, cổng 5500 | Các origin ngăn cách dấu phẩy, không có đường dẫn; không dùng `*` |
| `COOKIES_FILE` | trống | Đường dẫn cookies Netscape do bạn cung cấp hợp lệ |
| `FFMPEG_LOCATION` | PATH | Thư mục hoặc executable ffmpeg |
| `MAX_CONCURRENT_JOBS` | 2 | Tổng lượt tải/lấy info nặng đang chạy, cả đơn lẻ và hàng loạt |
| `MAX_PENDING_JOBS` | 6 | Job đang chờ + đang chạy |
| `MAX_STORED_JOBS` | 12 | Tổng job giữ trong RAM, kể cả đã xong |
| `MAX_VIDEO_MB` | 80 | Giới hạn từng file, MiB |
| `MAX_ZIP_MB` | 300 | Giới hạn ZIP, MiB |
| `MAX_STORAGE_MB` | 1024 | Ngưỡng tổng dữ liệu tạm, MiB; có thể vượt nhẹ giữa các callback đồng thời |
| `FILE_TTL_SECONDS` | 900 | Thời gian giữ file sau hoàn tất; cũng là ngân sách thời gian tải |
| `RATE_LIMIT_PER_MINUTE` | 8 | Yêu cầu nặng/IP/phút; trạng thái/tải ZIP tối đa 90/IP/phút |
| `TRUST_RENDER_PROXY` | false | Chỉ bật trên Render với `RENDER=true` |

Chỉ chạy **một Uvicorn worker và một instance**: job, giới hạn tốc độ và semaphore nằm trong RAM. Khởi động lại làm mất job. Muốn nhiều instance cần Redis/hàng đợi và lưu trữ dùng chung.

Bảo vệ SSRF: input chỉ nhận HTTPS, host TikTok chính xác; không userinfo, port lạ hoặc URL tuỳ ý. Link rút gọn kiểm tra từng redirect. Socket audit chặn IP private/loopback/link-local sau khi phân giải DNS, kể cả redirect của thư viện. yt-dlp dùng transport Urllib thuần Python; không cài transport curl để tránh đi vòng qua socket audit. ffmpeg chỉ đọc file đã tải local; chỉ chọn định dạng HTTP MP4, không truyền URL/manifest vào ffmpeg. Không hỗ trợ ảnh carousel/live hoặc các nền tảng khác.

Ở local, rate limit dựa trên socket IP và bỏ qua các header IP do client gửi. Render Free nhận public HTTP qua Cloudflare; khi hai biến `RENDER=true` và `TRUST_RENDER_PROXY=true`, dùng `CF-Connecting-IP` do edge gắn, fallback socket nếu thiếu/sai. Không bật chế độ này khi server có thể nhận kết nối trực tiếp. Cần xác nhận header thực tế khi chuyển proxy/nền tảng; không lấy tuỳ tiện IP đầu tiên trong X-Forwarded-For. CORS kiểm soát trình duyệt, không phải hệ thống xác thực API.

Timeout kết nối 20 giây, retry có giới hạn và kiểm tra deadline giữa các video/callback. Deadline là giới hạn hợp tác; một thao tác thư viện/ffmpeg đang chạy không bị kill cưỡng chế ngay tại mốc 900 giây.

## Cookies

Chỉ dùng cookies của tài khoản bạn được phép sử dụng, ở định dạng Netscape. Không tự động đọc cookie trình duyệt. Đặt `COOKIES_FILE=/đường/dẫn/cookies.txt` trong `.env` local. Cookies được nạp riêng vào RAM mỗi downloader, không ghi lại secret file dùng chung.

Trên Render, vào service → Environment → Secret Files, thêm `cookies.txt`, đặt `COOKIES_FILE=/etc/secrets/cookies.txt`, rồi deploy lại. Không dán cookies vào frontend, GitHub, logs hoặc issue. `.gitignore` chặn `.env`, cookies, token; `.dockerignore` chỉ cho phép source cần thiết vào image. Cookies không đảm bảo vượt được chặn IP và không cấp quyền tải nội dung mà tài khoản không được phép truy cập.

## Deploy GitHub Pages + Render Free

Tên cân nhắc: `clipnest`, `vidgrab`, `clipsaver`. Chọn `clipnest`; domain không chứa “tiktok”. Chưa tra cứu quyền nhãn hiệu cho tên Clipnest.

### 1. Đăng nhập và tạo repo

```sh
gh auth login
# Máy này có bản portable nếu gh chưa ở PATH:
# ~/Desktop/tiktok-downloader/.tools/gh auth login

git init -b main
git add .
git commit -m "Build Clipnest video downloader"
gh repo create clipnest --public --source=. --remote=origin --push
```

Nếu repo đã được tạo hoặc đã có commit trong phiên xây dựng, bỏ qua bước tương ứng; xem `git status`, `git remote -v` trước. Không tạo repo trùng.

### 2. Backend Render

Trong [Render Dashboard](https://dashboard.render.com), đăng nhập → New → Blueprint → kết nối repo vừa tạo. `render.yaml` đã chọn Docker, Free, Singapore và health check. Nhập `ALLOWED_ORIGINS=https://vanphong2809.github.io` (origin không bao gồm `/clipnest`).

Có thể tạo Web Service thủ công: runtime Docker, Dockerfile `./backend/Dockerfile`, Docker build context `./backend`, plan **Free**, health check `/api/health`. Đặt `TRUST_RENDER_PROXY=true`; Render cung cấp `RENDER=true`. Không nâng gói trả phí nếu chưa chủ động muốn.

Lấy URL thực tế Render cấp; tên dự kiến là `clipnest-api` nhưng suffix/domain thực tế phụ thuộc tên còn trống. Kiểm tra `https://clipnest-api.onrender.com/api/health` trả `{"status":"ok",…}`.

### 3. Frontend Pages

Sửa `frontend/config.js`:

```js
window.CLIPNEST_CONFIG = { API_BASE_URL: 'https://clipnest-api.onrender.com' };
```

Frontend được xuất bản từ nhánh **gh-pages**, không cần build step hay quyền GitHub Actions. Sau khi sửa API_BASE_URL, chạy:

```sh
git add frontend/config.js
git commit -m "Configure deployed API"
git push origin main
git subtree split --prefix frontend -b gh-pages
git push origin gh-pages
gh api --method POST repos/vanphong2809/clipnest/pages -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'
```

Nếu Pages đã có, dùng `PUT` để cập nhật cấu hình thay vì `POST`. Sau mỗi lần sửa frontend: commit vào main, chạy `git subtree split --prefix frontend` để lấy commit rồi push commit đó lên gh-pages. Không cần build JavaScript. Chờ Pages báo build thành công. Website mặc định: `https://vanphong2809.github.io/clipnest/`.

Kiểm tra từ frontend thật: info, MP4, MP3, user limit=2, tiến trình, ZIP, origin CORS. Kiểm tra vượt rate limit và xác thực IP proxy sau triển khai. Tải được local không đảm bảo tải được từ IP cloud của Render.

### 4. Domain riêng (tuỳ chọn)

Không cần mua domain để dùng website. Nếu bạn đã có domain:

1. Thêm `frontend/CNAME` chứa đúng hostname, ví dụ `clips.example.com`, rồi commit/push.
2. Repo → Settings → Pages → Custom domain: nhập cùng hostname, lưu. Nhánh gh-pages cũng phải chứa CNAME; bước subtree split/push sẽ mang file từ frontend sang nhánh này.
3. Với subdomain, DNS CNAME `clips` trỏ `vanphong2809.github.io` (không có đường dẫn repo).
4. Với apex/root domain, đặt bốn A record trỏ `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`. Xoá record xung đột. Xem [hướng dẫn DNS chính thức](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).
5. Chờ DNS/chứng chỉ hoàn tất, bật **Enforce HTTPS**. Nên xác minh quyền sở hữu domain trong GitHub trước khi gắn DNS.
6. Thêm `https://clips.example.com` vào `ALLOWED_ORIGINS` trên Render và redeploy.

## Kiểm thử

```sh
pip install pytest==9.1.1
pytest -q
```

Test tự động dùng dữ liệu giả có chủ đích để kiểm tra API/security/resource lifecycle, **không phải chứng minh TikTok tải thật**. Báo cáo tải thật nằm trong `TEST_REPORT.md`, ghi rõ mẫu kiểm thử và kết quả. Các file mẫu ở `work/` chỉ có trên máy local, không public.

## Cập nhật yt-dlp

Đang ghim `yt-dlp==2026.8.19` trong `backend/requirements.txt`. TikTok hay thay đổi. Kiểm tra [release chính thức](https://github.com/yt-dlp/yt-dlp/releases), chọn một phiên bản cụ thể mới hơn, sửa pin và chạy:

```sh
pip install -r backend/requirements.txt --upgrade
pytest -q
```

Thử lại MP4, MP3, link rút gọn và user limit=2 bằng nội dung bạn có quyền tải. Kiểm tra thêm transport Urllib/socket audit vì code có dùng `_request_director` nội bộ yt-dlp. Chỉ commit/deploy sau khi kiểm thử; nếu lỗi thì hoàn nguyên pin về bản trước. Không tự động nâng yt-dlp mỗi lần server khởi động.

## Hạn chế vận hành

- TikTok có thể yêu cầu đăng nhập, giới hạn vùng, trả danh sách thiếu hoặc chặn IP cloud. Cookies/yt-dlp mới chỉ có thể cải thiện, không đảm bảo tải được mọi lúc. Job trống và lỗi được hiển thị rõ.
- Render Free ngủ sau 15 phút không có traffic; khởi động lại khoảng một phút, file và job tạm có thể mất. Không phù hợp cho dịch vụ production đông người. Xem [giới hạn Free chính thức](https://render.com/docs/free).
- Render có quota băng thông/build và có thể đình chỉ dịch vụ Free phát sinh lượng traffic ra ngoài cao. Nếu tài khoản đã gắn phương thức thanh toán, có thể có phí vượt quota; hãy kiểm tra spend limit trong dashboard. Dự án không tự nâng gói hay mua dịch vụ.
- Tải lớn có thể vượt 80 MiB/video, 300 MiB/ZIP hoặc giới hạn RAM phía trình duyệt. Giảm số lượng video nếu gặp lỗi.
- Không có tài khoản ứng dụng; job_id ngẫu nhiên đóng vai trò link khó đoán. Không chia sẻ job_id nếu không muốn người khác tải ZIP trong thời gian còn hiệu lực.
