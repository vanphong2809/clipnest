import time
import zipfile
import io
import pytest
from fastapi.testclient import TestClient
import main
import douyin

client = TestClient(main.app)

@pytest.mark.parametrize('url,expected_status', [
    ('https://www.douyin.com/video/7112345678901234567', 200),
    ('https://v.douyin.com/idk123/', 200), # valid host format for short url
    ('https://example.com/video', 400),
    ('http://www.douyin.com/video/7112345678901234567', 200), # douyin allows http input and validates it
])
def test_validate_douyin_url(url, expected_status):
    if expected_status == 400:
        response = client.post('/api/douyin/video/info', json={'url': url})
        assert response.status_code == 400

def test_douyin_video_info_mocked(monkeypatch):
    url = 'https://www.douyin.com/video/7112345678901234567'
    
    # Mock get_douyin_downloader
    class MockDownloader:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            return {
                'title': 'Test Douyin Video',
                'uploader': 'Test User',
                'thumbnail': 'https://example.com/thumb.jpg',
                'duration': 15
            }
            
    monkeypatch.setattr('douyin.get_douyin_downloader', lambda opts: MockDownloader())
    
    response = client.post('/api/douyin/video/info', json={'url': url})
    assert response.status_code == 200
    data = response.json()
    assert data['title'] == 'Test Douyin Video'
    assert data['author'] == 'Test User'

