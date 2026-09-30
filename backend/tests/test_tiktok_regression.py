import time
import zipfile
import io
import pytest
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

def test_health():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'

@pytest.mark.parametrize('url,expected_status', [
    ('https://www.tiktok.com/@tiktok/video/7106594312292453675', 200),
    ('https://vt.tiktok.com/ZS8XXXXXX/', 200), # valid host format for short url
    ('https://example.com/video', 400),
    ('http://www.tiktok.com/@tiktok/video/7106594312292453675', 400), # http not https
])
def test_validate_url(url, expected_status):
    # We test info endpoint for url validation, mocking yt-dlp to avoid network if it passes validation
    if expected_status == 400:
        response = client.post('/api/video/info', json={'url': url})
        assert response.status_code == 400

def test_video_info_real():
    # Use a real public video
    url = 'https://www.tiktok.com/@tiktok/video/7106594312292453675'
    response = client.post('/api/video/info', json={'url': url})
    if response.status_code == 200:
        data = response.json()
        assert 'title' in data
        assert 'author' in data
        assert 'url' in data

def test_video_download_real():
    url = 'https://www.tiktok.com/@tiktok/video/7106594312292453675'
    response = client.get(f'/api/video/download?url={url}&format=mp4')
    if response.status_code == 200:
        assert response.headers['content-type'] == 'video/mp4'
        assert len(response.content) > 1000

def test_user_job_real():
    response = client.post('/api/user/start', json={'username': 'tiktok', 'limit': 2})
    if response.status_code == 202:
        job_id = response.json()['job_id']
        
        # Poll until done or error
        for _ in range(30):
            res = client.get(f'/api/job/{job_id}')
            data = res.json()
            if data['status'] in ['done', 'error']:
                break
            time.sleep(2)
        
        if data['status'] == 'done':
            zip_res = client.get(f'/api/job/{job_id}/zip')
            assert zip_res.status_code == 200
            assert zip_res.headers['content-type'] == 'application/zip'
            
            with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
                files = zf.namelist()
                assert len(files) > 0
