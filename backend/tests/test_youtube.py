import pytest
from fastapi.testclient import TestClient
import main
import youtube
import zipfile
import io

client = TestClient(main.app)

youtube.JS_RUNTIME_AVAILABLE = True
youtube.FFMPEG_AVAILABLE = True
youtube.YOUTUBE_ENABLED = True

@pytest.mark.parametrize('url,expected_status', [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 200),
    ('https://youtu.be/dQw4w9WgXcQ?si=abcdef', 200),
    ('https://www.youtube.com/shorts/dQw4w9WgXcQ', 200),
    ('https://music.youtube.com/watch?v=dQw4w9WgXcQ', 200),
    ('https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10s', 200),
    ('https://www.youtube.com/playlist?list=PL123456', 200),
    ('https://www.youtube.com/@MrBeast', 200),
    ('https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA', 200),
    ('https://www.youtube.com/c/Creator', 200),
    ('Link: https://youtu.be/dQw4w9WgXcQ text', 200),
    ('https://www.tiktok.com/@user/video/123', 400),
    ('https://example.com/youtube.com', 400),
    ('https://youtube.com.evil.com/watch', 400),
    ('http://192.168.1.1', 400),
    ('http://localhost:8000', 400)
])
def test_validate_youtube_url(url, expected_status):
    if expected_status == 400:
        response = client.post('/api/youtube/video/info', json={'url': url})
        assert response.status_code == 400
    else:
        assert youtube.canonical_youtube_video(url).startswith('https://')


def test_downloader_enables_node_runtime(monkeypatch):
    monkeypatch.setattr(youtube, 'JS_RUNTIME_NAME', 'node')
    monkeypatch.setattr(youtube, 'YOUTUBE_COOKIES_FILE', None)
    with youtube.get_youtube_downloader(youtube.youtube_options()) as downloader:
        assert 'node' in downloader.params['js_runtimes']

def test_youtube_video_info_mocked(monkeypatch):
    url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    
    class MockDownloader:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            return {
                'title': 'Test YouTube Video',
                'uploader': 'Test Channel',
                'thumbnail': 'https://example.com/thumb.jpg',
                'duration': 212,
                'formats': [
                    {'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'mp4a', 'height': 1080},
                    {'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none', 'height': 720},
                    {'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a'}
                ]
            }
            
    monkeypatch.setattr('youtube.get_youtube_downloader', lambda opts: MockDownloader())
    
    response = client.post('/api/youtube/video/info', json={'url': url})
    assert response.status_code == 200
    data = response.json()
    assert data['title'] == 'Test YouTube Video'
    assert data['author'] == 'Test Channel'
    assert '1080p' in data['available_formats']
    assert '720p' in data['available_formats']
    assert 'mp3' in data['available_formats']


def test_youtube_info_does_not_hide_extraction_failures(monkeypatch):
    def downloader(opts):
        assert not opts.get('ignore_no_formats_error')
        raise Exception("Sign in to confirm you're not a bot")

    monkeypatch.setattr(youtube, 'get_youtube_downloader', downloader)
    response = client.post('/api/youtube/video/info', json={'url': 'https://youtu.be/BaW_jenozKc'})
    assert response.status_code == 502
    assert 'bot' in response.json()['detail']


def test_youtube_info_without_formats_is_an_error(monkeypatch):
    class MockDownloader:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            return {'id': 'BaW_jenozKc', 'formats': []}

    monkeypatch.setattr(youtube, 'get_youtube_downloader', lambda opts: MockDownloader())
    response = client.post('/api/youtube/video/info', json={'url': 'https://youtu.be/BaW_jenozKc'})
    assert response.status_code == 502
    assert 'định dạng tải' in response.json()['detail']

def test_youtube_video_info_private(monkeypatch):
    url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    
    class MockDownloader:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            raise Exception('Private video')
            
    monkeypatch.setattr('youtube.get_youtube_downloader', lambda opts: MockDownloader())
    
    response = client.post('/api/youtube/video/info', json={'url': url})
    assert response.status_code == 502
    assert 'riêng tư' in response.json()['detail'].lower()


@pytest.mark.parametrize('message', [
    'Failed to extract any player response',
    'Unable to extract initial data',
    'Connection timed out',
    'HTTP Error 429: Too Many Requests',
    'HTTP Error 403: Forbidden',
])
def test_youtube_errors_do_not_mention_tiktok(message):
    result = youtube.friendly_youtube_error(Exception(message))
    assert 'YouTube' in result
    assert 'TikTok' not in result
