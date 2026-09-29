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

@pytest.mark.parametrize('limit', [0,101,1.1,'20',True])
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
