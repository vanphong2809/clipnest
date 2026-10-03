import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
import subprocess
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse, urljoin, urlsplit, parse_qs

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

import main

router = APIRouter(prefix="/api/youtube")

YOUTUBE_ENABLED = os.getenv('YOUTUBE_ENABLED', 'true').lower() == 'true'
YOUTUBE_MAX_DURATION_SEC = int(os.getenv('YOUTUBE_MAX_DURATION_SEC', '3600'))
YOUTUBE_MAX_FILESIZE_MB = int(os.getenv('YOUTUBE_MAX_FILESIZE_MB', '500'))
YOUTUBE_MAX_VIDEO_BYTES = YOUTUBE_MAX_FILESIZE_MB * 1024**2
YOUTUBE_MAX_ZIP_MB = int(os.getenv('YOUTUBE_MAX_ZIP_MB', '2048'))
YOUTUBE_MAX_ZIP_BYTES = YOUTUBE_MAX_ZIP_MB * 1024**2
YOUTUBE_COOKIES_FILE = os.getenv('YOUTUBE_COOKIES_FILE')

YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}

# Check dependencies. yt-dlp requires an explicitly enabled JS runtime for
# YouTube challenge solving. Node 22+ is installed in the Render image.
JS_RUNTIME_NAME = None
JS_RUNTIME_AVAILABLE = False
for cmd in ['node', 'deno', 'quickjs', 'bun']:
    try:
        result = subprocess.run([cmd, '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            JS_RUNTIME_NAME = cmd
            JS_RUNTIME_AVAILABLE = True
            break
    except FileNotFoundError:
        pass

FFMPEG_AVAILABLE = False
try:
    ffmpeg_cmd = os.getenv('FFMPEG_LOCATION') or 'ffmpeg'
    if subprocess.run([ffmpeg_cmd, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        FFMPEG_AVAILABLE = True
except FileNotFoundError:
    pass

if not JS_RUNTIME_AVAILABLE or not FFMPEG_AVAILABLE:
    main.log.warning(f"YouTube module dependencies missing! JS Runtime: {JS_RUNTIME_AVAILABLE}, FFMPEG: {FFMPEG_AVAILABLE}")

def check_youtube_enabled():
    if not YOUTUBE_ENABLED:
        raise HTTPException(status_code=404, detail="Tính năng YouTube đã bị tắt.")
    if not JS_RUNTIME_AVAILABLE or not FFMPEG_AVAILABLE:
        raise HTTPException(status_code=503, detail="Máy chủ đang thiếu thư viện xử lý YouTube (JS runtime hoặc ffmpeg). Vui lòng báo quản trị viên cài đặt.")

def is_valid_youtube_host(hostname: str) -> bool:
    if not hostname:
        return False
    return hostname in YOUTUBE_HOSTS or hostname.endswith('.youtube.com')

def extract_url_from_text(text: str) -> str:
    text = text.strip()
    urls = re.findall(r'https?://[^\s]+', text)
    if urls:
        return urls[0]
    return text

def validate_youtube_url(value: str) -> str:
    value = extract_url_from_text(value)
    if not value.startswith('http'):
        value = 'https://' + value
        
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not is_valid_youtube_host(p.hostname) or '\\' in value:
            raise ValueError()
    except ValueError:
        raise main.UserError('Chỉ chấp nhận link YouTube (youtube.com, youtu.be, m.youtube.com, music.youtube.com).')
    
    return value

def canonical_youtube_video(value: str) -> str:
    # yt-dlp understands youtu.be/Shorts/Music URLs itself. Avoid a separate
    # HEAD request here because cloud providers can receive different blocking
    # responses from YouTube than the extractor request that follows.
    return validate_youtube_url(value)

def friendly_youtube_error(error):
    if isinstance(error, main.UserError):
        return str(error)
    msg = str(error).lower()
    if 'failed to extract any player response' in msg:
        main.log.warning('YouTube failure code=player_response exception=%s', type(error).__name__)
        return 'YouTube không trả dữ liệu phát video cho máy chủ. Hãy thử lại sau; quản trị viên cần kiểm tra kết nối hoặc cookies của backend.'
    if 'sign in to confirm you\'re not a bot' in msg or 'bot' in msg:
        return 'YouTube đang chặn máy chủ do nghi ngờ là bot. Hãy thử lại sau, hoặc quản trị viên cần cấu hình cookies.'
    if 'private video' in msg or 'is private' in msg:
        return 'Video này là riêng tư. Bạn cần cung cấp cookies của tài khoản có quyền xem.'
    if 'live event' in msg or 'is live' in msg:
        return 'Video đang phát trực tiếp, không thể tải lúc này.'
    if 'age-restricted' in msg or 'age restricted' in msg:
        return 'Video bị giới hạn độ tuổi. Yêu cầu cookies để tải.'
    if 'members-only' in msg or 'members only' in msg:
        return 'Video chỉ dành cho hội viên.'
    if 'drm' in msg:
        return 'Video có bản quyền DRM, không thể tải.'
    if 'geo-blocked' in msg or 'unavailable in your country' in msg:
        return 'Video bị chặn ở quốc gia của máy chủ.'
    if 'unavailable' in msg or 'removed' in msg or 'deleted' in msg:
        return 'Video không tồn tại hoặc đã bị xoá.'
    if 'ffmpeg' in msg:
        return 'Lỗi ghép file bằng ffmpeg trên máy chủ.'
    if 'requested format is not available' in msg:
        return 'YouTube không cung cấp định dạng tải phù hợp cho video này.'
    code = main.error_code(error)
    # Log only a classification, never cookies, signed URLs or upstream HTML.
    main.log.warning('YouTube failure code=%s exception=%s', code, type(error).__name__)
    return {
        'network': 'Kết nối YouTube bị gián đoạn hoặc quá thời gian. Vui lòng thử lại sau.',
        'login_required': 'YouTube yêu cầu đăng nhập. Quản trị viên cần cấu hình cookies hợp lệ.',
        'tiktok_rate_limit': 'YouTube đang giới hạn lượt truy cập (429). Hãy chờ vài phút rồi thử lại.',
        'access_denied': 'YouTube đang từ chối truy cập từ máy chủ hoặc yêu cầu xác minh. Cookies hợp lệ có thể giúp nhưng không đảm bảo.',
        'upstream_response': 'YouTube trả dữ liệu rỗng hoặc không đúng định dạng. Hãy thử lại sau; quản trị viên cần kiểm tra yt-dlp và kết nối của backend.',
    }.get(code, 'Không tải được nội dung YouTube. Quản trị viên cần kiểm tra yt-dlp hoặc cookies.')

def youtube_options(directory=None, format_str=None, deadline=None):
    def check_progress(data):
        if deadline and time.monotonic() > deadline:
            raise main.UserError('Tác vụ quá thời gian cho phép.')
        if data.get('downloaded_bytes', 0) > YOUTUBE_MAX_VIDEO_BYTES:
            raise main.UserError(f'Video vượt giới hạn dung lượng cho phép ({YOUTUBE_MAX_FILESIZE_MB}MB).')
        if directory and main.disk_bytes(directory) > YOUTUBE_MAX_ZIP_BYTES:
            raise main.UserError(f'Dữ liệu tải vượt giới hạn dung lượng ZIP ({YOUTUBE_MAX_ZIP_MB}MB).')
        if main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise main.UserError('Máy chủ đã đầy bộ nhớ tạm. Hãy thử lại sau 15 phút.')
            
    opts = {
        'quiet': True, 'no_warnings': True, 'logger': main.QuietLogger(),
        'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1,
        'noplaylist': True, 'cachedir': False, 'proxy': '',
        'max_filesize': YOUTUBE_MAX_VIDEO_BYTES, 'progress_hooks': [check_progress],
        'outtmpl': str(directory / '%(id)s.%(ext)s') if directory else None,
        'restrictfilenames': True, 'overwrites': False,
        'merge_output_format': 'mp4',
    }
    if JS_RUNTIME_NAME:
        opts['js_runtimes'] = {JS_RUNTIME_NAME: {}}
    if format_str:
        opts['format'] = format_str
    
    if os.getenv('FFMPEG_LOCATION'):
        opts['ffmpeg_location'] = os.environ['FFMPEG_LOCATION']
    
    return opts

def get_youtube_downloader(opts):
    ydl = yt_dlp.YoutubeDL(opts)
    if YOUTUBE_COOKIES_FILE:
        ydl.cookiejar.load(YOUTUBE_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
    return ydl

class YouTubeVideoInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

class YouTubeBatchInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=5, ge=1, le=30, strict=True)
    quality: str = Field(default='720p', max_length=10)

@router.post('/video/info')
def video_info(body: YouTubeVideoInput):
    check_youtube_enabled()
    url = canonical_youtube_video(body.url)
    with main.foreground_slot():
        try:
            # Drop playlist param if it's a single video request
            opts = youtube_options(format_str=None)
            opts['noplaylist'] = True
            
            with get_youtube_downloader(opts) as ydl:
                data = ydl.extract_info(url, download=False)
            
            if not data:
                raise main.UserError("Không lấy được thông tin video.")
            
            if data.get('is_live'):
                raise main.UserError("Video đang phát trực tiếp, không thể tải.")
                
            duration = data.get('duration', 0)
            if duration and duration > YOUTUBE_MAX_DURATION_SEC:
                raise main.UserError(f"Video quá dài ({duration}s). Giới hạn là {YOUTUBE_MAX_DURATION_SEC}s.")

            formats_info = []
            has_mp3 = False
            for f in data.get('formats', []):
                # Filter useful formats: mp4 video or m4a audio
                ext = f.get('ext')
                vcodec = f.get('vcodec')
                acodec = f.get('acodec')
                height = f.get('height')
                
                if acodec != 'none' and vcodec == 'none':
                    has_mp3 = True
                if height and ext == 'mp4':
                    formats_info.append(height)
                    
            available_qualities = list(set([h for h in formats_info if h in [360, 480, 720, 1080]]))
            available_qualities.sort(reverse=True)
            
            qualities = [f"{q}p" for q in available_qualities]
            if has_mp3:
                qualities.append("mp3")

            if not qualities:
                raise main.UserError('YouTube không cung cấp định dạng tải phù hợp cho video này. Máy chủ có thể đang bị YouTube hạn chế truy cập.')
                
            return {
                'title': data.get('title'),
                'author': data.get('uploader'),
                'thumbnail': data.get('thumbnail'),
                'duration': duration,
                'url': url,
                'available_formats': qualities
            }
        except Exception as exc:
            raise HTTPException(502, friendly_youtube_error(exc)) from None

@router.get('/video/download')
def video_download(url: str, quality: str = '720p'):
    check_youtube_enabled()
    canonical = canonical_youtube_video(url)
    with main.foreground_slot():
        directory = Path(tempfile.mkdtemp(dir=main.ROOT))
        try:
            deadline = time.monotonic() + main.TTL
            
            format_str = 'best'
            if quality == 'mp3':
                format_str = 'bestaudio/best'
            elif quality == '1080p':
                format_str = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
            elif quality == '720p':
                format_str = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
            elif quality == '480p':
                format_str = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
            elif quality == '360p':
                format_str = 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best'
                
            opts = youtube_options(directory, format_str=format_str, deadline=deadline)
            if quality == 'mp3':
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
                
            with get_youtube_downloader(opts) as ydl:
                ydl.extract_info(canonical, download=True)
            
            extension = 'mp3' if quality == 'mp3' else 'mp4'
            files = list(directory.glob(f'*.{extension}'))
            if not files:
                raise main.UserError('Không có file tải xuống.')
            file = max(files, key=lambda p: p.stat().st_mtime_ns)
            
            if file.stat().st_size > YOUTUBE_MAX_VIDEO_BYTES:
                raise main.UserError(f'Video vượt giới hạn dung lượng ({YOUTUBE_MAX_FILESIZE_MB}MB).')
            
            with main.lock:
                main.single_dirs[directory] = time.time() + main.TTL
                
            return FileResponse(file, media_type={'mp3': 'audio/mpeg'}.get(quality, 'video/mp4'), filename='clipnest-youtube-' + file.name, background=BackgroundTask(main.remove_single, directory))
        except Exception as exc:
            main.remove_single(directory)
            raise HTTPException(502, friendly_youtube_error(exc)) from None

def run_youtube_job(job, quality: str):
    with main.slots, main.safe_network():
        try:
            main.set_job(job, state='running', message='Đang lấy danh sách video YouTube…')
            deadline = time.monotonic() + main.TTL
            
            opts = youtube_options(job.directory, deadline=deadline)
            opts.update({'extract_flat': 'in_playlist', 'lazy_playlist': True, 'playlistend': job.limit, 'noplaylist': False})
            
            entries = []
            
            url = validate_youtube_url(job.username) # it's actually url here
            
            with get_youtube_downloader(opts) as ydl:
                result = ydl.extract_info(url, download=False)
                # If it's a playlist/channel
                if 'entries' in result:
                    for entry in result['entries']:
                        if time.monotonic() > deadline:
                            break
                        if entry and entry.get('id'):
                            entries.append(entry)
                        if len(entries) >= job.limit:
                            break
                else:
                    # Single video passed to batch
                    entries.append(result)
                    
            if not entries:
                raise main.UserError('Không tìm thấy video công khai trong link đã cho.')
                
            main.set_job(job, total=len(entries))
            archive = job.directory / 'videos.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zf:
                for index, entry in enumerate(entries, 1):
                    if time.monotonic() > deadline:
                        raise main.UserError('Tác vụ quá thời gian. Đóng gói ZIP các video đã tải.')
                        
                    main.set_job(job, current=index, message=f'Đang tải video {index}/{len(entries)}…')
                    folder = job.directory / str(index)
                    folder.mkdir(exist_ok=True)
                    try:
                        video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}"
                        
                        format_str = 'best'
                        if quality == 'mp3':
                            format_str = 'bestaudio/best'
                        elif quality == '1080p':
                            format_str = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
                        elif quality == '720p':
                            format_str = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
                        elif quality == '480p':
                            format_str = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
                        elif quality == '360p':
                            format_str = 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best'
                            
                        v_opts = youtube_options(folder, format_str=format_str, deadline=deadline)
                        if quality == 'mp3':
                            v_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
                            
                        with get_youtube_downloader(v_opts) as ydl:
                            # Verify duration before download if available
                            v_info = ydl.extract_info(video_url, download=False, process=False)
                            if v_info and v_info.get('duration') and v_info['duration'] > YOUTUBE_MAX_DURATION_SEC:
                                raise main.UserError(f"Video quá dài ({v_info['duration']}s).")
                            if v_info and v_info.get('is_live'):
                                raise main.UserError("Video đang phát trực tiếp.")
                            
                            ydl.extract_info(video_url, download=True)
                            
                        extension = 'mp3' if quality == 'mp3' else 'mp4'
                        files = list(folder.glob(f'*.{extension}'))
                        if files:
                            file = max(files, key=lambda p: p.stat().st_mtime_ns)
                            main.add_to_zip(zf, [file], prefix=f'{index:03d}_')
                            main.set_job(job, completed=job.completed + 1)
                        else:
                            raise main.UserError('Lỗi tải file')
                    except Exception as exc:
                        if isinstance(exc, main.UserError) and any(x in str(exc) for x in ['thời gian', 'bộ nhớ', 'vượt giới hạn dung lượng ZIP']):
                            raise
                        with main.lock:
                            job.failures.append({'index': index, 'message': friendly_youtube_error(exc)})
                    finally:
                        shutil.rmtree(folder, ignore_errors=True)
                        main.set_job(job, processed=index)
                        
                if not job.completed:
                    raise main.UserError(job.failures[0]['message'] if job.failures else 'Không tải được video nào.')
                if job.failures:
                    zf.writestr('LOI_TAI.txt', '\n'.join(f"Video {f['index']}: {f['message']}" for f in job.failures))
            
            if archive.stat().st_size > YOUTUBE_MAX_ZIP_BYTES:
                raise main.UserError('ZIP vượt giới hạn dung lượng cho phép.')
            main.set_job(job, state='done', message=f'Đã tải {job.completed}/{job.total} video.', expires=time.time() + main.TTL)
        except Exception as exc:
            shutil.rmtree(job.directory, ignore_errors=True)
            main.set_job(job, state='error', message=friendly_youtube_error(exc), error_code=main.error_code(exc), expires=time.time() + main.TTL)

@router.post('/batch/start', status_code=202)
def batch_start(body: YouTubeBatchInput):
    check_youtube_enabled()
    main.cleanup()
    
    validate_youtube_url(body.url)
    
    with main.lock:
        youtube_active = sum(1 for j in main.jobs.values() if j.state in {'queued', 'running'} and getattr(j, 'is_youtube', False))
        YOUTUBE_MAX_CONCURRENT = int(os.getenv('YOUTUBE_MAX_CONCURRENT_JOBS', '1'))
        
        if youtube_active >= YOUTUBE_MAX_CONCURRENT:
            raise HTTPException(503, 'Hàng đợi tải YouTube đang đầy. Vui lòng đợi người khác tải xong.', headers={'Retry-After': '30'})
            
        pending = sum(j.state in {'queued', 'running'} for j in main.jobs.values())
        if pending >= main.MAX_PENDING or len(main.jobs) >= main.MAX_JOBS or main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise HTTPException(503, 'Hệ thống đã đầy tải. Vui lòng thử lại sau.', headers={'Retry-After': '30'})
            
        job_id = uuid.uuid4().hex
        directory = main.ROOT / job_id
        directory.mkdir()
        job = main.Job(job_id, body.url, body.limit, directory)
        job.is_youtube = True # flag for independent concurrency limit
        main.jobs[job_id] = job
        main.pool.submit(run_youtube_job, job, body.quality)
    return {'job_id': job_id}

@router.get('/job/{job_id}')
def job_status(job_id: str):
    check_youtube_enabled()
    with main.lock:
        job = main.get_job(job_id)
        return {'job_id': job.id, 'status': job.state, 'downloaded': job.completed, 'processed': job.processed, 'total': job.total, 'current': job.current, 'message': job.message, 'failures': list(job.failures), 'expires_at': job.expires or None, 'error_code': job.error_code}

@router.get('/job/{job_id}/zip')
def job_zip(job_id: str):
    check_youtube_enabled()
    with main.lock:
        job = main.get_job(job_id)
        if job.state != 'done':
            raise HTTPException(409, 'File ZIP chưa sẵn sàng.')
        job.readers += 1
        return FileResponse(job.directory / 'videos.zip', media_type='application/zip', filename=f'clipnest-youtube.zip', background=BackgroundTask(main.release_zip, job))
