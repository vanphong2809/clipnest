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
- Chưa deploy GitHub Pages/Render: CLI GitHub và dashboard Render chưa có phiên đăng nhập tại thời điểm lập báo cáo. Do đó chưa kiểm thử trên IP Render hay cấu hình CORS/origin production.
- Docker build thành công trên Linux arm64. Render sẽ build image cho kiến trúc của nền tảng; cần xác nhận deploy log và health trên Render.
