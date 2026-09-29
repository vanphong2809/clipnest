"""API Clipnest. Chạy một worker để đồng bộ hàng đợi và giới hạn bộ nhớ."""
import asyncio
import contextlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
import yt_dlp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

load_dotenv(Path(__file__).with_name('.env'))
ROOT = Path(tempfile.mkdtemp(prefix='clipnest-'))
TTL = int(os.getenv('FILE_TTL_SECONDS', '900'))
MAX_ZIP = int(os.getenv('MAX_ZIP_MB', '300')) * 1024**2
MAX_VIDEO = int(os.getenv('MAX_VIDEO_MB', '80')) * 1024**2
MAX_ACTIVE = max(1, int(os.getenv('MAX_CONCURRENT_JOBS', '2')))
MAX_PENDING = max(MAX_ACTIVE, int(os.getenv('MAX_PENDING_JOBS', '6')))
MAX_JOBS = int(os.getenv('MAX_STORED_JOBS', '12'))
MAX_STORAGE = int(os.getenv('MAX_STORAGE_MB', '1024')) * 1024**2
ORIGINS = [s.strip().rstrip('/') for s in os.getenv('ALLOWED_ORIGINS', 'http://localhost:5500,http://127.0.0.1:5500').split(',') if s.strip()]
if '*' in ORIGINS:
    raise RuntimeError('ALLOWED_ORIGINS phải liệt kê origin cụ thể.')
HOSTS = {'tiktok.com', 'www.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com', 'm.tiktok.com'}
VIDEO_PATH = re.compile(r'^/@[A-Za-z0-9_.-]+/video/\d+/?$')
USER_RE = re.compile(r'^(?:[A-Za-z0-9_.]{1,64}|MS4wLjABAAAA[A-Za-z0-9_-]{64})$')
lock = threading.RLock()
network_scope = threading.local()
pool = ThreadPoolExecutor(max_workers=MAX_ACTIVE, thread_name_prefix='clipnest')
slots = threading.BoundedSemaphore(MAX_ACTIVE)
rate_buckets = defaultdict(deque)
jobs = {}
single_dirs = {}
profile_hints = {}
log = logging.getLogger("uvicorn.error")
SEC_UID = re.compile(r"MS4wLjABAAAA[A-Za-z0-9_-]{64}")

# Kiểm tra IP ngay lúc socket kết nối, kể cả sau redirect/DNS resolution.
# Chỉ bật trong thread tải; không ảnh hưởng HTTP server hay kiểm thử local.
def network_audit(event, args):
    if event == 'socket.connect' and getattr(network_scope, 'enabled', False):
        address = args[1]
        if not isinstance(address, tuple):
            raise ValueError('Kết nối không hợp lệ.')
        try:
            ip = ipaddress.ip_address(address[0])
        except ValueError:
            raise ValueError('Kết nối phải dùng địa chỉ IP đã phân giải.')
        if not ip.is_global:
            raise ValueError('Chặn truy cập địa chỉ mạng nội bộ.')

sys.addaudithook(network_audit)

@contextlib.contextmanager
def safe_network():
    previous = getattr(network_scope, 'enabled', False)
    network_scope.enabled = True
    try:
        yield
    finally:
        network_scope.enabled = previous

class UserError(Exception):
    pass

class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def validate_url(value):
    value = value.strip()
    try:
        p = urlsplit(value)
        valid = (len(value) <= 2048 and p.scheme == 'https' and p.hostname in HOSTS
                 and p.port in (None, 443) and not p.username and not p.password
                 and not any(ord(c) < 32 for c in value) and '\\' not in value)
    except ValueError:
        valid = False
    if not valid:
        raise UserError('Chỉ chấp nhận link HTTPS thuộc TikTok (tiktok.com, vm.tiktok.com, vt.tiktok.com).')
    return value


def canonical_video(value):
    value = validate_url(value)
    p = urlsplit(value)
    if VIDEO_PATH.fullmatch(p.path):
        return 'https://www.tiktok.com' + p.path.rstrip('/')
    if p.hostname not in {'vm.tiktok.com', 'vt.tiktok.com'} and not re.fullmatch(r'/t/[A-Za-z0-9]+/?', p.path):
        raise UserError('Hãy dán link video TikTok, không phải link hồ sơ hoặc ảnh.')
    # Không tự theo redirect: mỗi đích phải được kiểm tra trước khi truy cập.
    with httpx.Client(follow_redirects=False, timeout=15, trust_env=False) as client:
        for _ in range(6):
            with client.stream('HEAD', value, headers={'User-Agent': 'facebookexternalhit/1.1'}) as response:
                if response.is_redirect and response.headers.get('location'):
                    value = validate_url(urljoin(value, response.headers['location']))
                else:
                    break
            p = urlsplit(value)
            if VIDEO_PATH.fullmatch(p.path):
                return 'https://www.tiktok.com' + p.path.rstrip('/')
    raise UserError('Không giải được link rút gọn. Hãy mở link và sao chép địa chỉ video đầy đủ.')


def normalize_user(value):
    value = value.strip()
    if value.startswith('https://'):
        p = urlsplit(validate_url(value))
        if p.hostname not in {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com'} or not re.fullmatch(r'/@[A-Za-z0-9_.]+/?', p.path):
            raise UserError('Link tài khoản phải có dạng https://www.tiktok.com/@username.')
        value = p.path.strip('/')[1:]
    else:
        value = value.removeprefix('@')
    if not USER_RE.fullmatch(value):
        raise UserError('Username chỉ gồm chữ, số, dấu chấm và dấu gạch dưới; không nhập tên hiển thị.')
    return value


def error_code(error):
    if isinstance(error, UserError):
        return 'input_or_limit'
    message = str(error).lower()
    # Timeout thường kèm chữ webpage; phải phân loại lỗi mạng trước lỗi parse.
    if any(x in message for x in ['timed out', 'timeout', 'connection', 'resolve', 'network is unreachable']):
        return 'network'
    if any(x in message for x in ['private', 'login', 'friends only', 'permission']):
        return 'login_required'
    if '429' in message or 'too many requests' in message:
        return 'tiktok_rate_limit'
    if any(x in message for x in ['403', 'blocked', 'captcha', 'challenge']):
        return 'access_denied'
    if any(x in message for x in ['404', 'not found', 'does not exist', 'unavailable', 'removed', 'not available']):
        return 'unavailable'
    if 'secondary user id' in message:
        return 'profile_id'
    if any(x in message for x in ['json', 'empty', 'webpage', 'extract']):
        return 'upstream_response'
    return 'unknown'


def friendly_error(error):
    if isinstance(error, UserError):
        return str(error)
    code = error_code(error)
    # Chỉ ghi mã lỗi/loại exception, không ghi cookie, URL ký số hay nội dung HTML.
    log.warning('TikTok failure code=%s exception=%s', code, type(error).__name__)
    return {
        'network': 'Kết nối TikTok bị gián đoạn hoặc quá thời gian. Vui lòng thử lại sau.',
        'login_required': 'Nội dung riêng tư hoặc yêu cầu đăng nhập. Chỉ tải nội dung bạn có quyền truy cập; quản trị viên có thể cấu hình cookies.',
        'tiktok_rate_limit': 'TikTok đang giới hạn lượt truy cập (429). Hãy chờ vài phút rồi thử lại.',
        'access_denied': 'TikTok đang từ chối truy cập từ máy chủ hoặc yêu cầu xác minh. Hãy thử lại sau; cookies hợp lệ có thể giúp nhưng không đảm bảo.',
        'unavailable': 'Video/tài khoản không tồn tại, đã bị xoá hoặc không khả dụng ở khu vực này.',
        'profile_id': 'TikTok không trả mã định danh của tài khoản. Hãy lấy thông tin một video công khai của tài khoản ở tab Một video, rồi thử tải theo tài khoản lại trong 15 phút.',
        'upstream_response': 'TikTok trả dữ liệu rỗng hoặc không đúng định dạng. Đây có thể là lỗi tạm thời hoặc thay đổi của TikTok; chưa thể kết luận IP bị chặn.',
    }.get(code, 'Không tải được nội dung. Quản trị viên cần kiểm tra lỗi yt-dlp hoặc cookies.')


def remember_profile(data):
    sec_uid = data.get('channel_id') or ''
    if not SEC_UID.fullmatch(sec_uid):
        return
    # Chỉ tin tác giả do extractor trả về, không tin username người dùng gõ trong URL.
    uploader_url = urlsplit(data.get('uploader_url') or '')
    author = uploader_url.path.removeprefix('/@').rstrip('/')
    names = [author, str(data.get('uploader_id') or ''), sec_uid]
    with lock:
        now = time.time()
        for name in names:
            if USER_RE.fullmatch(name):
                profile_hints[name.lower()] = (sec_uid, now + TTL)
        while len(profile_hints) > 128:
            del profile_hints[next(iter(profile_hints))]


def profile_entries(job, deadline):
    entries = []
    with lock:
        hint, expires = profile_hints.get(job.username.lower(), (None, 0))
    url = 'https://www.tiktok.com/@' + job.username
    if hint and expires > time.time():
        url = 'tiktokuser:' + hint
    # Một số lỗi JSON/profile không được retry bởi extractor_retries của yt-dlp.
    # Tối đa 3 lần với khoảng nghỉ; không retry lỗi riêng tư, 403 hoặc 429.
    for attempt in range(3):
        try:
            if time.monotonic() >= deadline:
                raise UserError('Lấy danh sách quá thời gian. Hãy thử lại sau.')
            opts = options(job.directory, deadline=deadline)
            opts.update({'extract_flat': True, 'lazy_playlist': True, 'playlistend': job.limit, 'noplaylist': False})
            entries = []
            with downloader(opts) as ydl:
                result = ydl.extract_info(url, download=False)
                for entry in result.get('entries', []):
                    if time.monotonic() > deadline:
                        raise UserError('Lấy danh sách quá thời gian. Hãy thử lại sau.')
                    if entry and str(entry.get('id', '')).isdigit():
                        entries.append(entry)
                    if len(entries) >= job.limit:
                        break
            return entries
        except Exception as exc:
            code = error_code(exc)
            log.warning('Profile extraction attempt=%s code=%s', attempt + 1, code)
            if code not in {'profile_id', 'upstream_response', 'network'} or attempt == 2:
                raise
            delay = 2 ** (attempt + 1)
            if time.monotonic() + delay >= deadline:
                raise
            set_job(job, message=f'TikTok phản hồi chưa đầy đủ. Đang thử lại lần {attempt + 2}/3…')
            time.sleep(delay)


def disk_bytes(path):
    total = 0
    for file in path.rglob('*'):
        try:
            if file.is_file():
                total += file.stat().st_size
        except FileNotFoundError:
            pass  # Thread khác vừa dọn file.
    return total


def options(directory=None, audio=False, deadline=None):
    def check_progress(data):
        if deadline and time.monotonic() > deadline:
            raise UserError('Tác vụ quá thời gian cho phép. Hãy giảm số lượng video.')
        if data.get('downloaded_bytes', 0) > MAX_VIDEO:
            raise UserError('Video vượt giới hạn dung lượng cho phép.')
        if directory and disk_bytes(directory) > MAX_ZIP:
            raise UserError('Dữ liệu tải vượt giới hạn dung lượng ZIP.')
        if disk_bytes(ROOT) > MAX_STORAGE:
            raise UserError('Máy chủ đã đầy bộ nhớ tạm. Hãy thử lại sau 15 phút.')
    opts = {
        'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
        'socket_timeout': 20, 'retries': 1, 'extractor_retries': 1,
        'noplaylist': True, 'cachedir': False, 'proxy': '',
        'max_filesize': MAX_VIDEO, 'progress_hooks': [check_progress],
        # Chỉ tải HTTP MP4: ffmpeg chỉ đọc file local, không đọc URL/manifest.
        'format': 'best[ext=mp4][protocol=https]/best[ext=mp4][protocol=http]',
        'outtmpl': str(directory / '%(id)s.%(ext)s') if directory else None,
        'restrictfilenames': True, 'overwrites': False,
    }
    if os.getenv('FFMPEG_LOCATION'):
        opts['ffmpeg_location'] = os.environ['FFMPEG_LOCATION']
    if audio:
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    return opts


@contextlib.contextmanager
def downloader(opts):
    # Nạp cookies vào RAM; không ghi lại secret file dùng chung giữa các job.
    with yt_dlp.YoutubeDL(opts) as ydl:
        cookie_path = os.getenv('COOKIES_FILE')
        if cookie_path:
            ydl.cookiejar.load(cookie_path, ignore_discard=True, ignore_expires=True)
        # Giữ transport thuần Python để audit socket có hiệu lực.
        for key in list(ydl._request_director.handlers):
            if key != 'Urllib':
                del ydl._request_director.handlers[key]
        yield ydl


def download_one(url, directory, audio=False, deadline=None):
    with downloader(options(directory, audio, deadline)) as ydl:
        result = ydl.extract_info(url, download=True)
    extension = 'mp3' if audio else 'mp4'
    files = list(directory.glob(f'*.{extension}'))
    if not result or not files:
        raise UserError('Không có file tải xuống; video có thể vượt giới hạn dung lượng hoặc không có định dạng MP4.')
    path = max(files, key=lambda p: p.stat().st_mtime_ns)
    if path.stat().st_size > MAX_VIDEO:
        raise UserError('Video vượt giới hạn dung lượng cho phép.')
    return path


@dataclass
class Job:
    id: str
    username: str
    limit: int
    directory: Path
    state: str = 'queued'
    completed: int = 0
    processed: int = 0
    total: int = 0
    current: int = 0
    message: str = 'Đang chờ lượt tải…'
    failures: list = field(default_factory=list)
    expires: float = 0
    readers: int = 0
    error_code: str | None = None


def set_job(job, **values):
    with lock:
        for key, value in values.items():
            setattr(job, key, value)


def run_job(job):
    with slots, safe_network():
        try:
            set_job(job, state='running', message='Đang lấy danh sách video…')
            deadline = time.monotonic() + TTL
            entries = profile_entries(job, deadline)
            if not entries:
                raise UserError('Không tìm thấy video công khai. Tài khoản có thể trống, riêng tư hoặc TikTok đang chặn danh sách video.')
            set_job(job, total=len(entries))
            archive = job.directory / 'videos.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zf:
                for index, entry in enumerate(entries, 1):
                    if time.monotonic() > deadline:
                        raise UserError('Tác vụ quá 15 phút. Hãy giảm số lượng video.')
                    set_job(job, current=index, message=f'Đang tải video {index}/{len(entries)}…')
                    folder = job.directory / str(index)
                    folder.mkdir()
                    try:
                        url = 'https://www.tiktok.com/@_/video/' + str(entry['id'])
                        file = download_one(url, folder, deadline=deadline)
                        if archive.stat().st_size + file.stat().st_size + 65536 > MAX_ZIP:
                            raise UserError('Tổng dung lượng vượt giới hạn ZIP. Hãy giảm số lượng video.')
                        zf.write(file, arcname=f'{index:03d}_{file.name}')
                        set_job(job, completed=job.completed + 1)
                    except Exception as exc:
                        if isinstance(exc, UserError) and any(x in str(exc) for x in ['dung lượng', 'thời gian', 'bộ nhớ']):
                            raise
                        with lock:
                            job.failures.append({'index': index, 'message': friendly_error(exc)})
                    finally:
                        shutil.rmtree(folder, ignore_errors=True)
                        set_job(job, processed=index)
                if not job.completed:
                    raise UserError(job.failures[0]['message'] if job.failures else 'Không tải được video nào.')
                if job.failures:
                    zf.writestr('LOI_TAI.txt', '\n'.join(f"Video {f['index']}: {f['message']}" for f in job.failures))
            if archive.stat().st_size > MAX_ZIP:
                raise UserError('ZIP vượt giới hạn dung lượng cho phép.')
            set_job(job, state='done', message=f'Đã tải {job.completed}/{job.total} video.', expires=time.time() + TTL)
        except Exception as exc:
            shutil.rmtree(job.directory, ignore_errors=True)
            set_job(job, state='error', message=friendly_error(exc), error_code=error_code(exc), expires=time.time() + TTL)


def cleanup():
    now = time.time()
    with lock:
        for key, job in list(jobs.items()):
            if job.expires and job.expires < now and job.readers == 0:
                shutil.rmtree(job.directory, ignore_errors=True)
                del jobs[key]
        for key, (_, expiry) in list(profile_hints.items()):
            if expiry < now:
                del profile_hints[key]
        for key, expiry in list(single_dirs.items()):
            if expiry < now:
                shutil.rmtree(key, ignore_errors=True)
                del single_dirs[key]
        for key, bucket in list(rate_buckets.items()):
            if not bucket or bucket[-1] < now - 60:
                del rate_buckets[key]


@asynccontextmanager
async def lifespan(app):
    async def sweeper():
        while True:
            await asyncio.sleep(30)
            cleanup()
    task = asyncio.create_task(sweeper())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    pool.shutdown(wait=True, cancel_futures=True)
    shutil.rmtree(ROOT, ignore_errors=True)

app = FastAPI(title='Clipnest API', lifespan=lifespan, docs_url=None, redoc_url=None)

def client_ip(request):
    peer = request.client.host if request.client else 'unknown'
    # Render Free chỉ nhận public HTTP qua edge; không bật chế độ này ở máy local.
    if os.getenv('TRUST_RENDER_PROXY') == 'true' and os.getenv('RENDER') == 'true':
        try:
            real_ip = ipaddress.ip_address(request.headers.get('cf-connecting-ip', ''))
            if real_ip.is_global:
                return str(real_ip)
        except ValueError:
            pass
    return peer


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    if request.url.path != '/api/health' and request.method != 'OPTIONS':
        ip = client_ip(request)
        expensive = request.url.path in {'/api/video/info', '/api/video/download', '/api/user/start'}
        limit = int(os.getenv('RATE_LIMIT_PER_MINUTE', '8')) if expensive else 90
        key = (ip, expensive)
        now = time.time()
        with lock:
            bucket = rate_buckets[key]
            while bucket and bucket[0] < now - 60:
                bucket.popleft()
            if len(bucket) >= limit:
                return JSONResponse({'detail': 'Bạn thao tác quá nhanh. Vui lòng đợi một phút.'}, 429, headers={'Retry-After': '60'})
            bucket.append(now)
    return await call_next(request)

app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'], expose_headers=['Content-Disposition'], allow_credentials=False)

@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({'detail': 'Dữ liệu không hợp lệ. Nhập đúng link/username và số lượng nguyên từ 1 đến 100.'}, 422)

@app.exception_handler(UserError)
async def user_error(request, exc):
    return JSONResponse({'detail': str(exc)}, 400)

class VideoInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

class UserInput(BaseModel):
    username: str = Field(min_length=1, max_length=2048)
    limit: int = Field(default=20, ge=1, le=100, strict=True)

@contextlib.contextmanager
def foreground_slot():
    if not slots.acquire(blocking=False):
        raise HTTPException(503, 'Máy chủ đang bận tải. Vui lòng thử lại sau.', headers={'Retry-After': '15'})
    try:
        with safe_network():
            yield
    finally:
        slots.release()

@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '0.2.0', 'max_zip_mb': MAX_ZIP // 1024**2}

@app.post('/api/video/info')
def video_info(body: VideoInput):
    validate_url(body.url)
    with foreground_slot():
        try:
            url = canonical_video(body.url)
            with downloader(options()) as ydl:
                data = ydl.extract_info(url, download=False)
            remember_profile(data)
            return {'title': data.get('title'), 'author': data.get('uploader'), 'thumbnail': data.get('thumbnail'), 'duration': data.get('duration'), 'url': url}
        except Exception as exc:
            raise HTTPException(502, friendly_error(exc)) from None


def remove_single(directory):
    with lock:
        shutil.rmtree(directory, ignore_errors=True)
        single_dirs.pop(directory, None)

@app.get('/api/video/download')
def video_download(url: str, format: Literal['mp4', 'mp3'] = 'mp4'):
    validate_url(url)
    with foreground_slot():
        directory = Path(tempfile.mkdtemp(dir=ROOT))
        try:
            file = download_one(canonical_video(url), directory, audio=format == 'mp3', deadline=time.monotonic() + TTL)
            with lock:
                single_dirs[directory] = time.time() + TTL
            return FileResponse(file, media_type='audio/mpeg' if format == 'mp3' else 'video/mp4', filename='clipnest-' + file.name, background=BackgroundTask(remove_single, directory))
        except Exception as exc:
            remove_single(directory)
            raise HTTPException(502, friendly_error(exc)) from None

@app.post('/api/user/start', status_code=202)
def user_start(body: UserInput):
    username = normalize_user(body.username)
    cleanup()
    with lock:
        pending = sum(j.state in {'queued', 'running'} for j in jobs.values())
        if pending >= MAX_PENDING or len(jobs) >= MAX_JOBS or disk_bytes(ROOT) > MAX_STORAGE:
            raise HTTPException(503, 'Hàng đợi hoặc bộ nhớ tạm đã đầy. Vui lòng thử lại sau.', headers={'Retry-After': '30'})
        job_id = uuid.uuid4().hex
        directory = ROOT / job_id
        directory.mkdir()
        job = Job(job_id, username, body.limit, directory)
        jobs[job_id] = job
        pool.submit(run_job, job)
    return {'job_id': job_id}


def get_job(job_id):
    job = jobs.get(job_id)
    if not job or (job.expires and job.expires < time.time()):
        raise HTTPException(404, 'Tác vụ không tồn tại hoặc đã hết hạn. Vui lòng tạo lại.')
    return job

@app.get('/api/job/{job_id}')
def job_status(job_id: str):
    with lock:
        job = get_job(job_id)
        return {'job_id': job.id, 'status': job.state, 'downloaded': job.completed, 'processed': job.processed, 'total': job.total, 'current': job.current, 'message': job.message, 'failures': list(job.failures), 'expires_at': job.expires or None, 'error_code': job.error_code}


def release_zip(job):
    with lock:
        job.readers -= 1

@app.get('/api/job/{job_id}/zip')
def job_zip(job_id: str):
    with lock:
        job = get_job(job_id)
        if job.state != 'done':
            raise HTTPException(409, 'File ZIP chưa sẵn sàng.')
        job.readers += 1
        return FileResponse(job.directory / 'videos.zip', media_type='application/zip', filename=f'clipnest-{job.username}.zip', background=BackgroundTask(release_zip, job))
