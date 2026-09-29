# Trạng thái triển khai

Tên chọn: **clipnest**. Source: `/Users/vanphong/Desktop/tiktok-downloader`.

- Backend và frontend đã hoàn thành bản local, có kiểm thử tải thật MP4/MP3/ZIP 2 video.
- Dockerfile và Blueprint Render Free đã chuẩn bị; Docker image build thành công.
- GitHub Actions Pages đã chuẩn bị.
- `frontend/config.js` hiện dùng `http://localhost:8000`; không phải URL production.
- Chưa tạo repo hoặc xuất bản: đang chờ đăng nhập GitHub và Render.

Để tiếp tục, chạy:

```sh
/Users/vanphong/Desktop/tiktok-downloader/.tools/gh auth login
```

Chọn GitHub.com → HTTPS → đăng nhập qua trình duyệt. Đăng nhập thêm <https://dashboard.render.com> trong tab Codex đã mở. Không gửi token/mật khẩu qua chat. Sau khi hoàn tất, báo lại để tiếp tục tạo repo, deploy Render, cập nhật URL/CORS, bật Pages và kiểm thử production.
