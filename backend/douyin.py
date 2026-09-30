import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import main # Import the main module to reuse components

router = APIRouter(prefix="/api/douyin")

DOUYIN_HOSTS = {'douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com'}

def is_valid_douyin_host(hostname: str) -> bool:
    if not hostname:
        return False
    return hostname in DOUYIN_HOSTS or hostname.endswith('.douyin.com') or hostname.endswith('.iesdouyin.com')

def extract_url_from_text(text: str) -> str:
    # "3.99 mQd:/ 复制打开抖音，看看【xxxx的作品】 https://v.douyin.com/idk123/ ..."
    text = text.strip()
    urls = re.findall(r'https?://[^\s]+', text)
    if urls:
        return urls[0]
    return text

def validate_douyin_url(value: str) -> str:
    value = extract_url_from_text(value)
    if not value.startswith('http'):
        value = 'https://' + value
        
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not is_valid_douyin_host(p.hostname) or '\\' in value:
            raise ValueError()
    except ValueError:
        raise main.UserError('Chỉ chấp nhận link Douyin (douyin.com, v.douyin.com, iesdouyin.com).')
    
    return value

def canonical_douyin_video(value: str) -> str:
    value = validate_douyin_url(value)
    # Resolve redirects
    with httpx.Client(follow_redirects=False, timeout=15, trust_env=False) as client:
        for _ in range(6):
            with client.stream('HEAD', value, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}) as response:
                if response.is_redirect and response.headers.get('location'):
                    new_url = urljoin(value, response.headers['location'])
                    p = urlsplit(new_url)
                    if not is_valid_douyin_host(p.hostname):
                        raise main.UserError('Link chuyển hướng đến trang không hợp lệ (SSRF bảo vệ).')
                    value = new_url
                else:
                    break
    
    # Douyin URL can be like https://www.douyin.com/video/711...
    return value

def douyin_options(directory=None, audio=False, deadline=None):
    # Reuse main check_progress logic
    def check_progress(data):
        if deadline and time.monotonic() > deadline:
            raise main.UserError('Tác vụ quá thời gian cho phép. Hãy giảm số lượng video.')
        if data.get('downloaded_bytes', 0) > main.MAX_VIDEO:
            raise main.UserError('Video vượt giới hạn dung lượng cho phép.')
        if directory and main.disk_bytes(directory) > main.MAX_ZIP:
            raise main.UserError('Dữ liệu tải vượt giới hạn dung lượng ZIP.')
        if main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise main.UserError('Máy chủ đã đầy bộ nhớ tạm. Hãy thử lại sau 15 phút.')
            
    opts = {
        'quiet': True, 'no_warnings': True, 'logger': main.QuietLogger(),
        'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1,
        'noplaylist': True, 'cachedir': False, 'proxy': '',
        'max_filesize': main.MAX_VIDEO, 'progress_hooks': [check_progress],
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(directory / '%(id)s.%(ext)s') if directory else None,
        'restrictfilenames': True, 'overwrites': False,
    }
    if os.getenv('FFMPEG_LOCATION'):
        opts['ffmpeg_location'] = os.environ['FFMPEG_LOCATION']
    if audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    return opts

def get_douyin_downloader(opts):
    ydl = yt_dlp.YoutubeDL(opts)
    cookie_path = os.getenv('DOUYIN_COOKIES_FILE')
    if cookie_path:
        ydl.cookiejar.load(cookie_path, ignore_discard=True, ignore_expires=True)
    return ydl

def friendly_douyin_error(error):
    msg = str(error).lower()
    if 'fresh cookies are needed' in msg or 'cookie' in msg:
        return 'Douyin yêu cầu cập nhật cookies. Quản trị viên cần làm mới DOUYIN_COOKIES_FILE.'
    return main.friendly_error(error)

class DouyinVideoInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

class DouyinUserInput(BaseModel):
    username: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=20, ge=1, strict=True)

@router.post('/video/info')
def video_info(body: DouyinVideoInput):
    url = canonical_douyin_video(body.url)
    with main.foreground_slot():
        try:
            info_options = douyin_options()
            info_options['ignore_no_formats_error'] = True
            with get_douyin_downloader(info_options) as ydl:
                data = ydl.extract_info(url, download=False)
            
            return {
                'title': data.get('title'),
                'author': data.get('uploader'),
                'thumbnail': data.get('thumbnail'),
                'duration': data.get('duration'),
                'url': url,
                'available_formats': {
                    'images': False, # Douyin images not fully handled yet unless yt-dlp supports it out of the box
                    'mp4': True,
                    'mp3': True,
                }
            }
        except Exception as exc:
            raise HTTPException(502, friendly_douyin_error(exc)) from None

@router.get('/video/download')
def video_download(url: str, format: Literal['mp4', 'mp3'] = 'mp4'):
    canonical = canonical_douyin_video(url)
    with main.foreground_slot():
        directory = Path(tempfile.mkdtemp(dir=main.ROOT))
        try:
            deadline = time.monotonic() + main.TTL
            opts = douyin_options(directory, audio=(format == 'mp3'), deadline=deadline)
            with get_douyin_downloader(opts) as ydl:
                ydl.extract_info(canonical, download=True)
            
            extension = 'mp3' if format == 'mp3' else 'mp4'
            files = list(directory.glob(f'*.{extension}'))
            if not files:
                raise main.UserError('Không có file tải xuống.')
            file = max(files, key=lambda p: p.stat().st_mtime_ns)
            
            with main.lock:
                main.single_dirs[directory] = time.time() + main.TTL
                
            return FileResponse(file, media_type={'mp3': 'audio/mpeg', 'mp4': 'video/mp4'}[format], filename='clipnest-douyin-' + file.name, background=BackgroundTask(main.remove_single, directory))
        except Exception as exc:
            main.remove_single(directory)
            raise HTTPException(502, friendly_douyin_error(exc)) from None

def run_douyin_job(job):
    with main.slots, main.safe_network():
        try:
            main.set_job(job, state='running', message='Đang lấy danh sách bài đăng Douyin…')
            deadline = time.monotonic() + main.TTL
            
            # Using yt-dlp to extract user profile
            opts = douyin_options(job.directory, deadline=deadline)
            opts.update({'extract_flat': True, 'lazy_playlist': True, 'playlistend': job.limit})
            entries = []
            
            try:
                # 1. yt-dlp trực tiếp với URL user (or with cookies)
                # Ensure the url is valid user profile
                url = job.username
                if not url.startswith('http'):
                    # if they provide sec_uid directly
                    url = f'https://www.douyin.com/user/{url}'
                else:
                    url = validate_douyin_url(url)
                    
                with get_douyin_downloader(opts) as ydl:
                    result = ydl.extract_info(url, download=False)
                    for entry in result.get('entries', []):
                        if time.monotonic() > deadline:
                            break
                        if entry and entry.get('id'):
                            entries.append(entry)
                        if len(entries) >= job.limit:
                            break
            except Exception as e:
                # (3) nếu vẫn không được thì trả lỗi rõ ràng bằng tiếng Việt
                main.log.warning(f"Douyin profile extract error: {e}")
                raise main.UserError('Douyin hiện chặn liệt kê video theo tài khoản, hãy dán từng link video.')
                
            if not entries:
                raise main.UserError('Không tìm thấy video công khai hoặc Douyin từ chối danh sách.')
                
            main.set_job(job, total=len(entries))
            archive = job.directory / 'videos.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zf:
                for index, entry in enumerate(entries, 1):
                    if time.monotonic() > deadline:
                        raise main.UserError('Tác vụ quá thời gian. Hãy giảm số lượng video.')
                    main.set_job(job, current=index, message=f'Đang tải bài {index}/{len(entries)}…')
                    folder = job.directory / str(index)
                    folder.mkdir()
                    try:
                        # Construct video URL from entry ID if possible, otherwise use entry URL
                        video_url = entry.get('url') or f"https://www.douyin.com/video/{entry['id']}"
                        
                        v_opts = douyin_options(folder, deadline=deadline)
                        with get_douyin_downloader(v_opts) as ydl:
                            ydl.extract_info(video_url, download=True)
                            
                        files = list(folder.glob('*.mp4'))
                        if files:
                            file = max(files, key=lambda p: p.stat().st_mtime_ns)
                            main.add_to_zip(zf, [file], prefix=f'{index:03d}_')
                            main.set_job(job, completed=job.completed + 1)
                        else:
                            raise main.UserError('Lỗi tải file')
                    except Exception as exc:
                        if isinstance(exc, main.UserError) and any(x in str(exc) for x in ['dung lượng', 'thời gian', 'bộ nhớ']):
                            raise
                        with main.lock:
                            job.failures.append({'index': index, 'message': friendly_douyin_error(exc)})
                    finally:
                        shutil.rmtree(folder, ignore_errors=True)
                        main.set_job(job, processed=index)
                        
                if not job.completed:
                    raise main.UserError(job.failures[0]['message'] if job.failures else 'Không tải được video nào.')
                if job.failures:
                    zf.writestr('LOI_TAI.txt', '\n'.join(f"Video {f['index']}: {f['message']}" for f in job.failures))
            
            if archive.stat().st_size > main.MAX_ZIP:
                raise main.UserError('ZIP vượt giới hạn dung lượng cho phép.')
            main.set_job(job, state='done', message=f'Đã tải {job.completed}/{job.total} bài.', expires=time.time() + main.TTL)
        except Exception as exc:
            shutil.rmtree(job.directory, ignore_errors=True)
            main.set_job(job, state='error', message=friendly_douyin_error(exc), error_code=main.error_code(exc), expires=time.time() + main.TTL)

@router.post('/user/start', status_code=202)
def user_start(body: DouyinUserInput):
    main.cleanup()
    with main.lock:
        pending = sum(j.state in {'queued', 'running'} for j in main.jobs.values())
        if pending >= main.MAX_PENDING or len(main.jobs) >= main.MAX_JOBS or main.disk_bytes(main.ROOT) > main.MAX_STORAGE:
            raise HTTPException(503, 'Hàng đợi hoặc bộ nhớ tạm đã đầy. Vui lòng thử lại sau.', headers={'Retry-After': '30'})
        job_id = uuid.uuid4().hex
        directory = main.ROOT / job_id
        directory.mkdir()
        job = main.Job(job_id, body.username, body.limit, directory)
        main.jobs[job_id] = job
        main.pool.submit(run_douyin_job, job)
    return {'job_id': job_id}

@router.get('/job/{job_id}')
def job_status(job_id: str):
    with main.lock:
        job = main.get_job(job_id)
        return {'job_id': job.id, 'status': job.state, 'downloaded': job.completed, 'processed': job.processed, 'total': job.total, 'current': job.current, 'message': job.message, 'failures': list(job.failures), 'expires_at': job.expires or None, 'error_code': job.error_code}

@router.get('/job/{job_id}/zip')
def job_zip(job_id: str):
    with main.lock:
        job = main.get_job(job_id)
        if job.state != 'done':
            raise HTTPException(409, 'File ZIP chưa sẵn sàng.')
        job.readers += 1
        return FileResponse(job.directory / 'videos.zip', media_type='application/zip', filename=f'clipnest-douyin.zip', background=BackgroundTask(main.release_zip, job))
