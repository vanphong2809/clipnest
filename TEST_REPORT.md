# Báo cáo kiểm thử Clipnest — 29/09/2026

## Môi trường

macOS arm64; Python 3.14.5 cho server local ban đầu; bộ kiểm thử xác nhận thêm bằng Python 3.11; Docker image Linux Python 3.11-slim + ffmpeg. yt-dlp ghim 2026.8.19. Không cấu hình cookies khi thử.

## Tải thật qua API local

| Kiểm tra | Kết quả |
|---|---|
| Info video `https://www.tiktok.com/@patroxofficial/video/6742501081818877190` | HTTP 200, tiêu đề/tác giả/thumbnail, thời lượng 27 giây |
| MP4 video trên | HTTP 200, `video/mp4`, 2.748.647 byte; ffmpeg decode toàn bộ không lỗi |
| MP3 video trên | HTTP 200, `audio/mpeg`, 659.114 byte; ffmpeg decode không lỗi |
| Tài khoản `@patroxofficial`, limit=2 | Lần đầu lỗi lấy danh sách; lần thử lại tải 1/1 video được TikTok trả về; ZIP 2.703.623 byte |
| Tài khoản `@corgibobaa`, limit=2 | `done`, tải 2/2 video, 0 lỗi; ZIP 5.074.586 byte |
| Nội dung ZIP 2 video | `001_7674082424720067853.mp4`, `002_7673297579135864077.mp4`; ZIP CRC hợp lệ; cả hai MP4 decode không lỗi |
| Docker Linux: `/api/health` | HTTP 200 |
| Docker Linux: chuyển MP3 video trên | HTTP 200, 659.114 byte |

Các video/tài khoản là mẫu từ bộ kiểm thử công khai của yt-dlp; chỉ dùng kiểm tra chức năng, không đưa media lên repo/website. Kết quả tại thời điểm chạy, không đảm bảo mọi tài khoản/video hay mọi IP cloud đều hoạt động.

## Giao diện

- Trang thật ở `http://localhost:5500` gọi backend ở cổng 8000.
- Hiển thị info của video thật, nút MP4/MP3.
- Tạo job `@corgibobaa` limit=2 từ form; xác nhận UI `Đã tải 2/2 video`, `0 lỗi`, nút tải ZIP sẵn sàng.
- Không ghi nhận lỗi console khi kiểm tra luồng trên.
- Responsive 390px: `innerWidth=390`, `documentElement.scrollWidth=390`, panel rộng 356px; không tràn ngang.

## Tự động

31 test đạt trên Python 3.11, gồm domain/protocol/userinfo/port sai, IP nội bộ, redirect SSRF, chuẩn hoá username, limit, CORS, rate limit, slot đồng thời, job không tồn tại, ZIP thiếu video, giới hạn ZIP, expiry, dọn file đơn, IP proxy và redirect vm/vt hợp lệ giả lập.

Có 1 cảnh báo deprecation từ Starlette TestClient về httpx; không làm test thất bại và không nằm trong luồng server production. Các test giả lập không thay thế kết quả tải thật ở trên.

## Chưa xác minh hoặc bị chặn

- Link mẫu cũ `https://vm.tiktok.com/ZTR45GpSF/`, `https://vt.tiktok.com/ZSe4FqkKd` và `https://www.tiktok.com/t/ZTRC5xgJp` đều trả redirect về trang chủ TikTok. API báo lỗi tiếng Việt phù hợp. Chưa có bằng chứng tải thật từ link rút gọn còn hiệu lực; bộ test có kiểm tra resolver hợp lệ bằng redirect giả lập.
- Phần chờ đăng nhập của lần kiểm thử local đã được giải quyết; xem kết quả production bên dưới.
- Docker local build trên Linux arm64; Render đã build và chạy thành công Linux amd64.

## Kiểm thử production sau deploy — 29/09/2026

- GitHub repo public: https://github.com/vanphong2809/clipnest
- GitHub Pages: https://vanphong2809.github.io/clipnest/ — API GitHub xác nhận `status=built`, source `gh-pages:/`, HTTPS enforced.
- Backend: https://clipnest-api.onrender.com — Render dashboard xác nhận **Live**, Docker, Free, Singapore.
- `GET /api/health`: HTTP 200, `status=ok`, `max_zip_mb=300`.
- CORS preflight từ `https://vanphong2809.github.io`: HTTP 200 và đúng Allow-Origin; từ `https://evil.example`: HTTP 400, không có Allow-Origin.
- Info video mẫu patrox: HTTP 200, đủ metadata.
- MP4 thật: HTTP 200, 2.748.647 byte, `video/mp4`.
- MP3 thật: HTTP 200, 659.114 byte, `audio/mpeg`.
- Job `@corgibobaa`, limit=2: `done`, downloaded=2, total=2, failures=[]; ZIP HTTP 200, 5.074.586 byte, đúng hai video và CRC hợp lệ.
- Frontend public đã nhập link thật, gọi Render và hiện metadata/thumbnail/nút tải; console không có lỗi trong lần kiểm tra.
- Rate limit thực tế trả HTTP 429 cùng Retry-After khi vượt ngưỡng. Request thử giả mạo CF-Connecting-IP/X-Forwarded-For bị edge từ chối HTTP 403; không vượt qua giới hạn.

Kết quả chỉ xác nhận tại thời điểm kiểm thử. TikTok vẫn có thể thay đổi/chặn IP; không dùng kết quả này để đảm bảo khả dụng lâu dài. Không public các media kiểm thử.

## Sửa lỗi tài khoản paohan85 — 29/09/2026

- Lỗi profile tạm thời được retry có giới hạn; lỗi thiếu mã tài khoản có hướng dẫn lấy thông tin một video để xác định tác giả.
- Render 0.2.0 thử username paohan85, limit=2: ZIP hợp lệ có video 7690912206493338888 và một bản ghi lỗi cho bài 7690245358428933383.
- Chẩn đoán bài thứ hai bằng yt-dlp process=False: chỉ có định dạng audio/mp3, không có MP4. Không phải bằng chứng tài khoản bị xoá hay IP bị chặn.
- Bản 0.2.1 local: info HTTP 200, available_formats={mp4:false,mp3:true}; MP3 HTTP 200, 902445 bytes.
- Bổ sung kiểm thử bộ chọn định dạng: video im lặng, chỉ âm thanh, ưu tiên video có tiếng, từ chối HLS ở cả hai chế độ. Tổng 47 tests đạt trên Python 3.11.
- Render 0.2.1 (commit bbf9b34): health OK; job paohan85 limit=2 tạo ZIP 1244166 bytes, CRC hợp lệ, gồm 001_7690912206493338888.mp4 và LOI_TAI.txt. Bài thứ hai được phân loại thiếu định dạng, không còn báo xoá/chặn IP.
- Render info bài thứ hai: HTTP 200, mp4=false/mp3=true; MP3 HTTP 200, 902445 bytes. Cả MP4 trong ZIP và MP3 tải từ Render đều giải mã bằng ffmpeg không lỗi.
- GitHub Pages đã build commit frontend e31c7f0; kiểm tra browser thực tế thấy thông báo chỉ có âm thanh, nút MP4 disabled, MP3 enabled.
- Kết quả chỉ xác nhận tại thời điểm kiểm thử; TikTok có thể thay đổi/chặn yêu cầu về sau. Không thể đảm bảo tải mọi bài.
