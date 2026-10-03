import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
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

router = APIRouter(prefix="/api/instagram")

INSTAGRAM_ENABLED = os.getenv('INSTAGRAM_ENABLED', 'true').lower() == 'true'
INSTAGRAM_MAX_DURATION_SEC = int(os.getenv('INSTAGRAM_MAX_DURATION_SEC', '3600'))
INSTAGRAM_MAX_FILESIZE_MB = int(os.getenv('INSTAGRAM_MAX_FILESIZE_MB', '500'))
INSTAGRAM_MAX_VIDEO_BYTES = INSTAGRAM_MAX_FILESIZE_MB * 1024**2
INSTAGRAM_MAX_ZIP_MB = int(os.getenv('INSTAGRAM_MAX_ZIP_MB', '2048'))
INSTAGRAM_MAX_ZIP_BYTES = INSTAGRAM_MAX_ZIP_MB * 1024**2
INSTAGRAM_COOKIES_FILE = os.getenv('INSTAGRAM_COOKIES_FILE')

INSTAGRAM_HOSTS = {'instagram.com', 'www.instagram.com', 'm.instagram.com'}

def check_instagram_enabled():
    if not INSTAGRAM_ENABLED:
        raise HTTPException(status_code=404, detail="Tính năng Instagram đã bị tắt.")

def is_valid_instagram_host(hostname: str) -> bool:
    if not hostname:
        return False
    return hostname in INSTAGRAM_HOSTS or hostname.endswith('.instagram.com')

def extract_url_from_text(text: str) -> str:
    text = text.strip()
    urls = re.findall(r'https?://[^\s]+', text)
    if urls:
        return urls[0]
    return text

def validate_instagram_url(value: str) -> str:
    value = extract_url_from_text(value)
    if not value.startswith('http'):
        value = 'https://' + value

    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not is_valid_instagram_host(p.hostname) or '\\' in value:
            raise ValueError()
    except ValueError:
        raise main.UserError('Chỉ chấp nhận link Instagram (instagram.com).')

    return value

def canonical_instagram_video(value: str) -> str:
    value = validate_instagram_url(value)
    # Remove tracking parameters
    p = urlsplit(value)
    clean_url = f"{p.scheme}://{p.hostname}{p.path}"
    return clean_url

def friendly_instagram_error(error):
    msg = str(error).lower()
    if 'login' in msg or 'logged in' in msg or 'authentication' in msg or 'empty media response' in msg:
        return 'Instagram yêu cầu đăng nhập để xem nội dung này. Vui lòng cung cấp cookies hợp lệ cho Instagram.'
    if 'private' in msg or 'is private' in msg:
        return 'Bài viết là riêng tư. Bạn cần cung cấp cookies của tài khoản có quyền xem.'
    if 'not found' in msg or 'unavailable' in msg or 'removed' in msg or 'deleted' in msg:
        return 'Bài viết không tồn tại hoặc đã bị xoá.'
    if 'ffmpeg' in msg:
        return 'Lỗi ghép file bằng ffmpeg trên máy chủ.'
    if 'requested format is not available' in msg:
        return 'Instagram không cung cấp định dạng tải phù hợp cho video này.'
    return main.friendly_error(error)

def instagram_options(directory=None, format_str=None, deadline=None):
    def check_progress(data):
        if deadline and time.monotonic() > deadline:
            raise main.UserError('Tác vụ quá thời gian cho phép.')
        if data.get('downloaded_bytes', 0) > INSTAGRAM_MAX_VIDEO_BYTES:
            raise main.UserError(f'Video vượt giới hạn dung lượng cho phép ({INSTAGRAM_MAX_FILESIZE_MB}MB).')
        if directory and main.disk_bytes(directory) > INSTAGRAM_MAX_ZIP_BYTES:
            raise main.UserError(f'Dữ liệu tải vượt giới hạn dung lượng ZIP ({INSTAGRAM_MAX_ZIP_MB}MB).')
        if main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise main.UserError('Máy chủ đã đầy bộ nhớ tạm. Hãy thử lại sau 15 phút.')

    opts = {
        'quiet': True, 'no_warnings': True, 'logger': main.QuietLogger(),
        'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1,
        'noplaylist': True, 'cachedir': False, 'proxy': '',
        'max_filesize': INSTAGRAM_MAX_VIDEO_BYTES, 'progress_hooks': [check_progress],
        'outtmpl': str(directory / '%(id)s.%(ext)s') if directory else None,
        'restrictfilenames': True, 'overwrites': False,
        'merge_output_format': 'mp4',
    }
    if format_str:
        opts['format'] = format_str

    if os.getenv('FFMPEG_LOCATION'):
        opts['ffmpeg_location'] = os.environ['FFMPEG_LOCATION']

    return opts

def get_instagram_downloader(opts):
    ydl = yt_dlp.YoutubeDL(opts)
    if INSTAGRAM_COOKIES_FILE:
        ydl.cookiejar.load(INSTAGRAM_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
    return ydl

class InstagramVideoInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

class InstagramBatchInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=5, ge=1, le=30, strict=True)
    quality: str = Field(default='mp4', max_length=10)

@router.post('/video/info')
def video_info(body: InstagramVideoInput):
    check_instagram_enabled()
    url = canonical_instagram_video(body.url)
    with main.foreground_slot():
        try:
            # Drop playlist param if it's a single video request
            opts = instagram_options(format_str=None)
            opts['noplaylist'] = True
            opts['ignore_no_formats_error'] = True

            with get_instagram_downloader(opts) as ydl:
                data = ydl.extract_info(url, download=False)

            if not data:
                raise main.UserError("Không lấy được thông tin bài viết.")

            duration = data.get('duration', 0)
            if duration and duration > INSTAGRAM_MAX_DURATION_SEC:
                raise main.UserError(f"Video quá dài ({duration}s). Giới hạn là {INSTAGRAM_MAX_DURATION_SEC}s.")

            qualities = ["mp4"]
            # Check if there is audio
            has_mp3 = False
            for f in data.get('formats', []):
                acodec = f.get('acodec')
                if acodec and acodec != 'none':
                    has_mp3 = True
            if has_mp3:
                qualities.append("mp3")

            return {
                'title': data.get('title') or data.get('description'),
                'author': data.get('uploader'),
                'thumbnail': data.get('thumbnail'),
                'duration': duration,
                'url': url,
                'available_formats': qualities
            }
        except Exception as exc:
            raise HTTPException(502, friendly_instagram_error(exc)) from None

@router.get('/video/download')
def video_download(url: str, quality: str = 'mp4'):
    check_instagram_enabled()
    canonical = canonical_instagram_video(url)
    with main.foreground_slot():
        directory = Path(tempfile.mkdtemp(dir=main.ROOT))
        try:
            deadline = time.monotonic() + main.TTL

            format_str = 'best'
            if quality == 'mp3':
                format_str = 'bestaudio/best'
            elif quality == 'mp4':
                format_str = 'best'

            opts = instagram_options(directory, format_str=format_str, deadline=deadline)
            if quality == 'mp3':
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

            with get_instagram_downloader(opts) as ydl:
                ydl.extract_info(canonical, download=True)

            extension = 'mp3' if quality == 'mp3' else 'mp4'
            files = list(directory.glob(f'*.{extension}'))
            if not files:
                # If mp4 not found, maybe yt-dlp saved as something else, try to find any file
                files = list(directory.glob('*.*'))
                if not files:
                    raise main.UserError('Không có file tải xuống.')
            file = max(files, key=lambda p: p.stat().st_mtime_ns)

            if file.stat().st_size > INSTAGRAM_MAX_VIDEO_BYTES:
                raise main.UserError(f'Video vượt giới hạn dung lượng ({INSTAGRAM_MAX_FILESIZE_MB}MB).')

            with main.lock:
                main.single_dirs[directory] = time.time() + main.TTL

            return FileResponse(file, media_type={'mp3': 'audio/mpeg'}.get(quality, 'video/mp4'), filename='clipnest-instagram-' + file.name, background=BackgroundTask(main.remove_single, directory))
        except Exception as exc:
            main.remove_single(directory)
            raise HTTPException(502, friendly_instagram_error(exc)) from None

def run_instagram_job(job, quality: str):
    with main.slots, main.safe_network():
        try:
            main.set_job(job, state='running', message='Đang lấy danh sách bài viết Instagram…')
            deadline = time.monotonic() + main.TTL

            opts = instagram_options(job.directory, deadline=deadline)
            opts.update({'extract_flat': 'in_playlist', 'lazy_playlist': True, 'playlistend': job.limit, 'noplaylist': False})

            entries = []

            url = validate_instagram_url(job.username) # it's actually url here

            with get_instagram_downloader(opts) as ydl:
                result = ydl.extract_info(url, download=False)
                # If it's a playlist/user profile
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
                raise main.UserError('Không tìm thấy bài viết công khai trong link đã cho.')

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
                        video_url = entry.get('url') or f"https://www.instagram.com/p/{entry['id']}/"

                        format_str = 'best'
                        if quality == 'mp3':
                            format_str = 'bestaudio/best'
                        elif quality == 'mp4':
                            format_str = 'best'

                        v_opts = instagram_options(folder, format_str=format_str, deadline=deadline)
                        if quality == 'mp3':
                            v_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

                        with get_instagram_downloader(v_opts) as ydl:
                            # Verify duration before download if available
                            v_info = ydl.extract_info(video_url, download=False, process=False)
                            if v_info and v_info.get('duration') and v_info['duration'] > INSTAGRAM_MAX_DURATION_SEC:
                                raise main.UserError(f"Video quá dài ({v_info['duration']}s).")

                            ydl.extract_info(video_url, download=True)

                        # Glob all downloaded files (Instagram might download multiple images in a carousel)
                        files = sorted(folder.glob('*.*'))
                        if files:
                            for file in files:
                                main.add_to_zip(zf, [file], prefix=f'{index:03d}_{file.stem}/')
                            main.set_job(job, completed=job.completed + 1)
                        else:
                            raise main.UserError('Lỗi tải file')
                    except Exception as exc:
                        if isinstance(exc, main.UserError) and any(x in str(exc) for x in ['thời gian', 'bộ nhớ', 'vượt giới hạn dung lượng ZIP']):
                            raise
                        with main.lock:
                            job.failures.append({'index': index, 'message': friendly_instagram_error(exc)})
                    finally:
                        shutil.rmtree(folder, ignore_errors=True)
                        main.set_job(job, processed=index)

                if not job.completed:
                    raise main.UserError(job.failures[0]['message'] if job.failures else 'Không tải được video nào.')
                if job.failures:
                    zf.writestr('LOI_TAI.txt', '\n'.join(f"Bài viết {f['index']}: {f['message']}" for f in job.failures))

            if archive.stat().st_size > INSTAGRAM_MAX_ZIP_BYTES:
                raise main.UserError('ZIP vượt giới hạn dung lượng cho phép.')
            main.set_job(job, state='done', message=f'Đã tải {job.completed}/{job.total} bài.', expires=time.time() + main.TTL)
        except Exception as exc:
            shutil.rmtree(job.directory, ignore_errors=True)
            main.set_job(job, state='error', message=friendly_instagram_error(exc), error_code=main.error_code(exc), expires=time.time() + main.TTL)

@router.post('/batch/start', status_code=202)
def batch_start(body: InstagramBatchInput):
    check_instagram_enabled()
    main.cleanup()

    validate_instagram_url(body.url)

    with main.lock:
        instagram_active = sum(1 for j in main.jobs.values() if j.state in {'queued', 'running'} and getattr(j, 'is_instagram', False))
        INSTAGRAM_MAX_CONCURRENT = int(os.getenv('INSTAGRAM_MAX_CONCURRENT_JOBS', '1'))

        if instagram_active >= INSTAGRAM_MAX_CONCURRENT:
            raise HTTPException(503, 'Hàng đợi tải Instagram đang đầy. Vui lòng đợi người khác tải xong.', headers={'Retry-After': '30'})

        pending = sum(j.state in {'queued', 'running'} for j in main.jobs.values())
        if pending >= main.MAX_PENDING or len(main.jobs) >= main.MAX_JOBS or main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise HTTPException(503, 'Hệ thống đã đầy tải. Vui lòng thử lại sau.', headers={'Retry-After': '30'})

        job_id = uuid.uuid4().hex
        directory = main.ROOT / job_id
        directory.mkdir()
        job = main.Job(job_id, body.url, body.limit, directory)
        job.is_instagram = True # flag for independent concurrency limit
        main.jobs[job_id] = job
        main.pool.submit(run_instagram_job, job, body.quality)
    return {'job_id': job_id}

@router.get('/job/{job_id}')
def job_status(job_id: str):
    check_instagram_enabled()
    with main.lock:
        job = main.get_job(job_id)
        return {'job_id': job.id, 'status': job.state, 'downloaded': job.completed, 'processed': job.processed, 'total': job.total, 'current': job.current, 'message': job.message, 'failures': list(job.failures), 'expires_at': job.expires or None, 'error_code': job.error_code}

@router.get('/job/{job_id}/zip')
def job_zip(job_id: str):
    check_instagram_enabled()
    with main.lock:
        job = main.get_job(job_id)
        if job.state != 'done':
            raise HTTPException(409, 'File ZIP chưa sẵn sàng.')
        job.readers += 1
        return FileResponse(job.directory / 'videos.zip', media_type='application/zip', filename=f'clipnest-instagram.zip', background=BackgroundTask(main.release_zip, job))
