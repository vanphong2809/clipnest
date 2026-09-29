# Clipnest — đã triển khai

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
