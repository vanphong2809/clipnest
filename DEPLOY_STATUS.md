# Clipnest — đã triển khai

## Cập nhật 04/10/2026

- GitHub Pages đã build thành công frontend `c947661` (run `37142769535`), gồm giao diện Instagram và phiên bản cache mới cho JavaScript.
- Render đã Live bản Docker `33f6eaf`: Node 22, Python 3.11, ffmpeg và `yt-dlp[default]` (kèm EJS). `.dockerignore` đã cho phép module Instagram mà ứng dụng import.
- Kiểm tra Docker local: metadata, MP4 (533916 byte), MP3 (457389 byte) của video thử `jNQXAC9IVRw` đều thành công; không còn file tạm sau response. Bộ kiểm thử trước deploy: 90 passed.
- Health production trả 200 và CORS cho `https://vanphong2809.github.io` đúng. YouTube trên Render vẫn trả 502 khi lấy metadata; chưa thể xác nhận tải YouTube production đã hoạt động. Cần kiểm tra phản hồi upstream/kết nối từ Render, không kết luận cookies chắc chắn giải quyết được.
- Bản sửa tiếp theo phân loại lỗi YouTube riêng, tránh thông báo nhầm TikTok và ghi log mã lỗi thay vì cookies, URL ký số hoặc HTML.

Cập nhật: 29/09/2026.

- Source: `/Users/vanphong/Desktop/tiktok-downloader`
- GitHub: https://github.com/vanphong2809/clipnest
- Website: https://vanphong2809.github.io/clipnest/
- API health: https://clipnest-api.onrender.com/api/health
- Render dashboard: https://dashboard.render.com/web/srv-datudmmk1f9s739mkt30

GitHub Pages xuất bản từ `gh-pages` (root), HTTPS bật. Frontend dùng API Render khi ở domain public và API localhost khi chạy local. Render dùng Docker, Free, Singapore; root `backend`, Dockerfile `Dockerfile`, build context `.`, health check `/api/health`.

ALLOWED_ORIGINS=`https://vanphong2809.github.io`. Không có cookies TikTok hoặc token trong repo/image. GitHub CLI đã đăng nhập; thông tin xác thực ở cấu hình người dùng, ngoài dự án.

Đã kiểm thử production: health, CORS, metadata, MP4, MP3, ZIP 2/2 video, rate limit. Không cần bạn cấp thêm quyền để sử dụng website hiện tại.

Khi sửa frontend: commit/push main, chạy `git subtree split --prefix frontend` và push commit trả về tới `origin gh-pages`. Khi sửa backend: push main, kiểm tra Render Deploys; nếu chưa tự deploy, chọn Manual Deploy → Deploy latest commit trên Render. Dịch vụ được tạo từ public Git Repository, không cài GitHub App Render hay cấp quyền đọc repo riêng.

Render Free có thể ngủ và mất file/job tạm khi restart; TikTok có thể chặn IP cloud. Giảm limit hoặc cấu hình cookies hợp lệ nếu gặp lỗi; không có đảm bảo cookies sẽ giải quyết mọi lần chặn.

## Bản sửa 0.2.1

- Backend Render đã triển khai commit bbf9b34, health trả version 0.2.1.
- GitHub Pages đã build frontend e31c7f0.
- Đã kiểm tra lại paohan85 limit=2: ZIP có 1 MP4 + ghi chú bài thứ hai chỉ có luồng MP3; MP3 tải riêng thành công. Chi tiết trong TEST_REPORT.md.

## Bản tải slideshow 0.3.0 — 30/09/2026

- Render triển khai b9804a9; health version 0.3.0, thử tải riêng bộ 15 ảnh và tài khoản có cả video/ảnh thành công.
- Frontend hỗ trợ nút “Tải bộ ảnh · ZIP”, giữ đúng thứ tự, không kèm nhạc; URL app.js có phiên bản để trình duyệt không dùng mã cũ.
