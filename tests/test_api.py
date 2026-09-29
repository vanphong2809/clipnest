import contextlib
import io
import socket
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

@pytest.fixture(autouse=True)
def reset_limits():
    main.rate_buckets.clear()
    yield
    main.rate_buckets.clear()

@pytest.mark.parametrize('url', [
    'https://example.com/video/123', 'http://tiktok.com/@a/video/1',
    'https://tiktok.com.evil.com/@a/video/1', 'https://tiktok.com@127.0.0.1/',
    'https://www.tiktok.com:8080/@a/video/1', 'https://user:pass@tiktok.com/',
    'file:///etc/passwd', 'https://127.0.0.1/', 'https://tiktok.com\\@example.com/',
])
def test_reject_untrusted_url(url):
    assert client.post('/api/video/info', json={'url': url}).status_code == 400

@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '::ffff:127.0.0.1'])
def test_socket_guard(address):
    with main.safe_network(), pytest.raises(ValueError):
        main.network_audit('socket.connect', (None, (address, 80)))

def test_reject_redirect_to_private(monkeypatch):
    @contextlib.contextmanager
    def response(*args, **kwargs):
        yield type('Response', (), {'is_redirect':True, 'headers':{'location':'http://169.254.169.254/'}})()
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        stream = response
    monkeypatch.setattr(main.httpx, 'Client', FakeClient)
    with pytest.raises(main.UserError):
        main.canonical_video('https://vm.tiktok.com/abc/')

@pytest.mark.parametrize('value', ['abc', '@abc', 'https://www.tiktok.com/@abc?lang=en'])
def test_user_normalization(value):
    assert main.normalize_user(value) == 'abc'

@pytest.mark.parametrize('limit', [0,1.1,'20',True])
def test_invalid_limit(limit):
    assert client.post('/api/user/start', json={'username':'abc','limit':limit}).status_code == 422

def test_cors_and_rate_limit():
    r=client.options('/api/video/info',headers={'Origin':'http://localhost:5500','Access-Control-Request-Method':'POST'})
    assert r.headers['access-control-allow-origin']=='http://localhost:5500'
    r=client.options('/api/video/info',headers={'Origin':'https://evil.com','Access-Control-Request-Method':'POST'})
    assert 'access-control-allow-origin' not in r.headers
    for _ in range(8): client.post('/api/video/info',json={'url':'https://evil.com'})
    r=client.post('/api/video/info',json={'url':'https://evil.com'},headers={'X-Forwarded-For':'1.1.1.1'})
    assert r.status_code==429
    assert client.get('/api/health').status_code==200

def test_busy_and_missing_job():
    acquired=[main.slots.acquire() for _ in range(main.MAX_ACTIVE)]
    try:
        r=client.post('/api/video/info',json={'url':'https://www.tiktok.com/@a/video/1'})
        assert r.status_code==503
    finally:
        for _ in acquired: main.slots.release()
    assert client.get('/api/job/missing').status_code==404

def fake_downloader(opts):
    class Fake:
        def extract_info(self,*args,**kwargs): return {'entries':[{'id':'1'},{'id':'2'}]}
    return contextlib.nullcontext(Fake())

def test_zip_partial_success_and_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'downloader', fake_downloader)
    def download(url, folder, **kwargs):
        if url.endswith('/2'): raise RuntimeError('private video')
        path=folder/'1.mp4'; path.write_bytes(b'fake mp4 content'); return path
    monkeypatch.setattr(main,'download_one',download)
    job=main.Job('unit-partial','abc',2,tmp_path)
    main.jobs[job.id]=job
    main.run_job(job)
    assert (job.state,job.completed,job.processed,job.total)==('done',1,2,2)
    r=client.get('/api/job/unit-partial/zip')
    assert r.status_code==200
    assert 'attachment' in r.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist()==['001_1.mp4','LOI_TAI.txt']
    assert job.readers==0
    job.expires=time.time()-1;main.cleanup()
    assert not tmp_path.exists()
    assert client.get('/api/job/unit-partial').status_code==404

def test_zip_limit(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'downloader',fake_downloader)
    monkeypatch.setattr(main,'MAX_ZIP',4)
    def download(url,folder,**kwargs):
        path=folder/'1.mp4';path.write_bytes(b'12345');return path
    monkeypatch.setattr(main,'download_one',download)
    job=main.Job('oversize','abc',2,tmp_path);main.run_job(job)
    assert job.state=='error'
    assert not tmp_path.exists()

def test_single_download_cleanup(monkeypatch):
    def download(url,folder,**kwargs):
        path=folder/'1.mp4';path.write_bytes(b'fake mp4');return path
    monkeypatch.setattr(main,'download_one',download)
    r=client.get('/api/video/download',params={'url':'https://www.tiktok.com/@a/video/1'})
    assert r.status_code==200 and r.content==b'fake mp4'
    assert main.single_dirs=={}

def test_render_proxy_identity(monkeypatch):
    from starlette.requests import Request
    req=Request({'type':'http','client':('10.1.1.1',1234),'headers':[(b'cf-connecting-ip',b'8.8.8.8'),(b'x-forwarded-for',b'1.1.1.1')]})
    monkeypatch.setenv('TRUST_RENDER_PROXY','true')
    monkeypatch.delenv('RENDER',raising=False)
    assert main.client_ip(req)=='10.1.1.1'
    monkeypatch.setenv('RENDER','true')
    assert main.client_ip(req)=='8.8.8.8'

@pytest.mark.parametrize('short_url',['https://vm.tiktok.com/abc/','https://vt.tiktok.com/abc/'])
def test_short_redirect_success(monkeypatch,short_url):
    @contextlib.contextmanager
    def response(*args,**kwargs):
        yield type('Response',(),{'is_redirect':True,'headers':{'location':'https://www.tiktok.com/@abc/video/123?share=1'}})()
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        stream=response
    monkeypatch.setattr(main.httpx,'Client',FakeClient)
    assert main.canonical_video(short_url)=='https://www.tiktok.com/@abc/video/123'

@pytest.mark.parametrize('message,code',[
    ('Unable to download webpage: timed out','network'),
    ('Unable to extract secondary user ID','profile_id'),
    ('Failed to parse JSON','upstream_response'),
    ('HTTP Error 403: Forbidden','access_denied'),
    ('HTTP Error 429: Too Many Requests','tiktok_rate_limit'),
    ('This account is private','login_required'),
    ('Requested format is not available. Use --list-formats','format_unavailable'),
])
def test_precise_error_codes(message,code):
    assert main.error_code(RuntimeError(message))==code
    if code in {'profile_id','upstream_response','network'}:
        assert 'hoặc chặn IP' not in main.friendly_error(RuntimeError(message))


def test_retry_transient_profile_error(monkeypatch,tmp_path):
    calls=[]
    class Fake:
        def extract_info(self,url,**kwargs):
            calls.append(url)
            if len(calls)==1:raise RuntimeError('Failed to parse JSON')
            return {'entries':[{'id':'1'},{'id':'2'},{'id':'3'}]}
    monkeypatch.setattr(main,'downloader',lambda opts:contextlib.nullcontext(Fake()))
    monkeypatch.setattr(main.time,'sleep',lambda seconds:None)
    job=main.Job('retry','retryuser',2,tmp_path)
    assert main.profile_entries(job,time.monotonic()+10)==[{'id':'1'},{'id':'2'}]
    assert len(calls)==2


def test_no_retry_private_or_rate_limit(monkeypatch,tmp_path):
    for error in ['private account','HTTP Error 429']:
        calls=[]
        class Fake:
            def extract_info(self,*args,**kwargs):
                calls.append(1);raise RuntimeError(error)
        monkeypatch.setattr(main,'downloader',lambda opts:contextlib.nullcontext(Fake()))
        with pytest.raises(RuntimeError):main.profile_entries(main.Job('x','x',2,tmp_path),time.monotonic()+10)
        assert len(calls)==1


def test_profile_hint_uses_verified_author_and_expires(monkeypatch,tmp_path):
    main.profile_hints.clear()
    sec_uid='MS4wLjABAAAA'+'a'*64
    main.remember_profile({'uploader_url':'https://www.tiktok.com/@actual_author','channel_id':sec_uid})
    calls=[]
    class Fake:
        def extract_info(self,url,**kwargs):calls.append(url);return {'entries':[{'id':'1'}]}
    monkeypatch.setattr(main,'downloader',lambda opts:contextlib.nullcontext(Fake()))
    job=main.Job('hint','actual_author',2,tmp_path)
    main.profile_entries(job,time.monotonic()+10)
    assert calls[-1]=='tiktokuser:'+sec_uid
    main.profile_hints['actual_author']=(sec_uid,time.time()-1)
    main.profile_entries(job,time.monotonic()+10)
    assert calls[-1]=='https://www.tiktok.com/@actual_author'
    main.profile_hints.clear()


@pytest.mark.parametrize('audio,formats,expected', [
    (False, [('silent', 'mp4', 'h264', 'none', 'https')], 'silent'),
    (False, [('sound', 'mp3', 'none', 'mp3', 'https')], None),
    (True, [('sound', 'mp3', 'none', 'mp3', 'https')], 'sound'),
    (False, [('remote_manifest', 'mp4', 'h264', 'aac', 'm3u8_native')], None),
    (True, [('remote_manifest', 'mp4', 'h264', 'aac', 'm3u8_native')], None),
    (False, [('silent', 'mp4', 'h264', 'none', 'https'), ('full', 'mp4', 'h264', 'aac', 'https')], 'full'),
])
def test_direct_format_selection(audio, formats, expected):
    data = {'id': '123', 'title': 'Fixture', 'extractor': 'TikTok', 'webpage_url': 'https://www.tiktok.com/@abc/video/123',
            'formats': [dict(zip(('format_id', 'ext', 'vcodec', 'acodec', 'protocol'), f),
                             url='https://example.com/' + f[0]) for f in formats]}
    with main.downloader(main.options(audio=audio)) as ydl:
        if expected is None:
            with pytest.raises(main.yt_dlp.utils.ExtractorError, match='Requested format is not available'):
                ydl.process_ie_result(data, download=False)
        else:
            result = ydl.process_ie_result(data, download=False)
            assert result['format_id'] == expected

@pytest.mark.parametrize('url', ['https://127.0.0.1/a.jpg', 'https://tiktokcdn.com.evil.com/a.jpg',
    'https://tiktokcdn.com@localhost/a.jpg', 'http://p16.tiktokcdn.com/a.jpg', 'https://p16.tiktokcdn.com:8080/a.jpg'])
def test_image_url_guard(url):
    with pytest.raises(main.UserError): main.validate_image_url(url)


def test_photo_url_normalizes():
    assert main.canonical_video('https://www.tiktok.com/@abc/photo/123') == 'https://www.tiktok.com/@abc/video/123'


def mock_image_client(monkeypatch, handler):
    original = main.httpx.Client
    monkeypatch.setattr(main.httpx, 'Client', lambda **kwargs: original(transport=main.httpx.MockTransport(handler), **kwargs))


def test_images_order_original_bytes_and_no_audio(monkeypatch, tmp_path):
    payloads = [b'\xff\xd8\xfffirst', b'\x89PNG\r\n\x1a\nsecond']
    mock_image_client(monkeypatch, lambda req: main.httpx.Response(200, content=payloads[int(req.url.path[1:])]))
    data = {'clipnest_images': [['https://p16.tiktokcdn.com/0'], ['https://p16.tiktokcdn.com/1']]}
    main.download_images(data, tmp_path, time.monotonic()+10)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['001.jpg', '002.png']
    assert (tmp_path/'001.jpg').read_bytes() == payloads[0]
    assert (tmp_path/'002.png').read_bytes() == payloads[1]


def test_image_redirect_blocked_before_request(monkeypatch,tmp_path):
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        return main.httpx.Response(302,headers={'location':'https://127.0.0.1/secret'})
    mock_image_client(monkeypatch,handler)
    with pytest.raises(main.UserError):
        main.download_images({'clipnest_images':[['https://p16.tiktokcdn.com/a']]},tmp_path,time.monotonic()+10)
    assert len(calls)==1


def test_images_limit_and_invalid_payload(monkeypatch,tmp_path):
    mock_image_client(monkeypatch,lambda req:main.httpx.Response(200,content=b'<html>blocked</html>'))
    data={'clipnest_images':[['https://p16.tiktokcdn.com/a']]}
    with pytest.raises(main.UserError,match='file ảnh hợp lệ'):main.download_images(data,tmp_path,time.monotonic()+10)
    monkeypatch.setattr(main,'MAX_ZIP',3)
    with pytest.raises(main.UserError,match='dung lượng'):main.download_images(data,tmp_path,time.monotonic()+10)


def test_single_image_zip_cleanup(monkeypatch):
    class Fake:
        def extract_info(self,*args,**kwargs):return {'clipnest_images':[['https://p16.tiktokcdn.com/a']]}
    monkeypatch.setattr(main,'downloader',lambda opts:contextlib.nullcontext(Fake()))
    mock_image_client(monkeypatch,lambda req:main.httpx.Response(200,content=b'\xff\xd8\xffimage'))
    response=client.get('/api/video/download',params={'url':'https://www.tiktok.com/@a/photo/123','format':'images'})
    assert response.status_code==200
    assert response.headers['content-type']=='application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:assert archive.namelist()==['001.jpg']
    assert main.single_dirs=={}


def test_bulk_mixed_video_gallery(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'downloader',fake_downloader)
    def download(url,folder,**kwargs):
        if url.endswith('/1'):
            file=folder/'1.mp4';file.write_bytes(b'movie');return file
        for name in ['001.jpg','002.jpg']:(folder/name).write_bytes(b'image')
        return folder
    monkeypatch.setattr(main,'download_one',download)
    job=main.Job('mixed','abc',2,tmp_path);main.run_job(job)
    assert (job.state,job.completed,job.processed)==('done',2,2)
    with zipfile.ZipFile(tmp_path/'videos.zip') as archive:
        assert archive.namelist()==['001_1.mp4','002_2/001.jpg','002_2/002.jpg']
