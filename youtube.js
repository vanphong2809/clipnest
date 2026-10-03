'use strict';

// [YOUTUBE]
const yt$ = id => document.getElementById(id);
const YT_API = window.CLIPNEST_CONFIG.API_BASE_URL.replace(/\/$/, '');

let ytSelectedVideo = null;
let ytActiveJob = null;
let ytPolling = false;

function ytMessage(id, text, error = false) {
  const el = yt$(id); 
  el.textContent = text; 
  el.classList.toggle('error', error); 
  el.hidden = !text;
}

function ytErrorText(error) {
  if (error.name === 'TimeoutError' || error.name === 'AbortError') return 'Máy chủ phản hồi quá lâu. Hãy thử lại.';
  if (error instanceof TypeError) return 'Không kết nối được máy chủ. Hãy kiểm tra mạng và thử lại sau.';
  return error.message;
}

async function ytRequest(path, options = {}, timeout = 120000) {
  const response = await fetch(YT_API + path, { ...options, signal: AbortSignal.timeout(timeout) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Máy chủ gặp lỗi (${response.status}). Vui lòng thử lại.`);
  }
  return response;
}

async function ytSaveFile(path, filename) {
  const response = await ytRequest(path, {}, 950000);
  const blob = await response.blob();
  const objectURL = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = objectURL; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
}

function ytActivateTab(name) {
  for (const key of ['video', 'playlist']) {
    const active = name === key;
    yt$('yt-tab-' + key).classList.toggle('active', active);
    yt$('yt-tab-' + key).setAttribute('aria-selected', String(active));
    yt$('yt-tab-' + key).tabIndex = active ? 0 : -1;
    yt$('yt-panel-' + key).hidden = !active;
  }
}

for (const name of ['video', 'playlist']) {
  yt$('yt-tab-' + name).onclick = () => ytActivateTab(name);
  yt$('yt-tab-' + name).onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const target = event.key === 'Home' ? 'video' : event.key === 'End' ? 'playlist' : name === 'video' ? 'playlist' : 'video';
      ytActivateTab(target); yt$('yt-tab-' + target).focus();
    }
  };
}

yt$('yt-video-form').onsubmit = async event => {
  event.preventDefault(); yt$('yt-info-button').disabled = true; yt$('yt-video-result').hidden = true; ytSelectedVideo = null;
  ytMessage('yt-video-message', 'Đang lấy thông tin video YouTube…');
  try {
    const response = await ytRequest('/api/youtube/video/info', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: yt$('yt-video-url').value.trim()}) });
    const data = await response.json(); 
    ytSelectedVideo = data.url;
    
    yt$('yt-video-title').textContent = data.title || 'Video YouTube';
    const duration = Number(data.duration);
    yt$('yt-video-meta').textContent = [data.author || 'Kênh chưa xác định', Number.isFinite(duration) && duration > 0 ? `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, '0')}` : null].filter(Boolean).join(' · ');
    const thumb = yt$('yt-thumbnail'); thumb.hidden = true; thumb.removeAttribute('src');
    if (data.thumbnail) { try { const url = new URL(data.thumbnail); if (url.protocol === 'https:') { thumb.src = url.href; thumb.hidden = false; } } catch {} }
    thumb.onerror = () => { thumb.hidden = true; };
    
    // Populate select
    const select = yt$('yt-quality-select');
    select.innerHTML = '';
    for (const q of (data.available_formats || ['720p'])) {
        const opt = document.createElement('option');
        opt.value = q;
        opt.textContent = q === 'mp3' ? 'Âm thanh (MP3)' : q;
        if (q === '720p') opt.selected = true;
        select.appendChild(opt);
    }
    
    yt$('yt-video-result').hidden = false;
    ytMessage('yt-video-message', '');
  } catch (error) { ytMessage('yt-video-message', ytErrorText(error), true); }
  finally { yt$('yt-info-button').disabled = false; }
};

yt$('yt-download-button').onclick = async () => {
    if (!ytSelectedVideo) return;
    const url = ytSelectedVideo;
    const quality = yt$('yt-quality-select').value || '720p';
    
    yt$('yt-download-button').disabled = true;
    ytMessage('yt-video-message', `Đang chuẩn bị file YouTube ${quality}… Vui lòng giữ trang này mở.`);
    try {
        await ytSaveFile(`/api/youtube/video/download?url=${encodeURIComponent(url)}&quality=${quality}`, `clipnest-youtube.${quality === 'mp3' ? 'mp3' : 'mp4'}`);
        ytMessage('yt-video-message', 'File đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.');
    } catch (error) { ytMessage('yt-video-message', ytErrorText(error), true); }
    finally { yt$('yt-download-button').disabled = false; }
};


function ytRememberJob(id) { try { if (id) sessionStorage.setItem('clipnest-youtube-job', id); else sessionStorage.removeItem('clipnest-youtube-job'); } catch {} }

async function ytPollJob() {
  if (ytPolling || !ytActiveJob) return;
  ytPolling = true;
  let consecutiveErrors = 0;
  try {
    while (ytActiveJob) {
      try {
        const job = await (await ytRequest(`/api/youtube/job/${encodeURIComponent(ytActiveJob)}`)).json();
        consecutiveErrors = 0; ytMessage('yt-playlist-message', ''); yt$('yt-job-progress').hidden = false;
        yt$('yt-job-label').textContent = job.message;
        yt$('yt-job-count').textContent = `${job.downloaded} / ${job.total}`;
        yt$('yt-progress').max = job.total || 1; yt$('yt-progress').value = job.processed;
        yt$('yt-job-detail').textContent = job.status === 'queued' ? 'Máy chủ đang xử lý các tác vụ trước.' : `Đã xử lý ${job.processed}/${job.total} video · ${job.failures.length} lỗi.`;
        if (job.status === 'done') {
          yt$('yt-download-zip').hidden = false; yt$('yt-start-button').disabled = false;
          ytMessage('yt-playlist-message', job.failures.length ? `Đã tải ${job.downloaded}/${job.total} video. ZIP kèm danh sách lỗi; file sẽ hết hạn sau 15 phút.` : 'File ZIP đã sẵn sàng. Tải về trong vòng 15 phút.');
          break;
        }
        if (job.status === 'error') { throw new Error(job.message); }
      } catch (error) {
        consecutiveErrors++;
        if (!(error instanceof TypeError) && error.name !== 'TimeoutError' && error.name !== 'AbortError' || consecutiveErrors >= 5) {
          ytMessage('yt-playlist-message', ytErrorText(error), true); ytRememberJob(null); ytActiveJob = null; yt$('yt-start-button').disabled = false; break;
        }
        ytMessage('yt-playlist-message', 'Mất kết nối tạm thời. Đang thử kết nối lại…');
      }
      await new Promise(resolve => setTimeout(resolve, 2500));
    }
  } finally { ytPolling = false; }
}

yt$('yt-playlist-form').onsubmit = async event => {
  event.preventDefault(); yt$('yt-start-button').disabled = true; yt$('yt-download-zip').hidden = true; yt$('yt-job-progress').hidden = true;
  ytMessage('yt-playlist-message', 'Đang tạo tác vụ tải YouTube…');
  try {
    const data = await (await ytRequest('/api/youtube/batch/start', {
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({
            url: yt$('yt-playlist-url').value.trim(), 
            limit: Number(yt$('yt-limit').value),
            quality: yt$('yt-batch-quality').value || '720p'
        })
    })).json();
    ytActiveJob = data.job_id; ytRememberJob(ytActiveJob); await ytPollJob();
  } catch (error) { ytMessage('yt-playlist-message', ytErrorText(error), true); yt$('yt-start-button').disabled = false; }
};

yt$('yt-download-zip').onclick = async () => {
  if (!ytActiveJob) return;
  yt$('yt-download-zip').disabled = true;
  try { await ytSaveFile(`/api/youtube/job/${encodeURIComponent(ytActiveJob)}/zip`, 'clipnest-youtube.zip'); ytMessage('yt-playlist-message', 'File ZIP đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.'); }
  catch (error) { ytMessage('yt-playlist-message', ytErrorText(error), true); }
  finally { yt$('yt-download-zip').disabled = false; }
};

try { ytActiveJob = sessionStorage.getItem('clipnest-youtube-job'); } catch {}
if (ytActiveJob) { ytActivateTab('playlist'); yt$('yt-start-button').disabled = true; ytPollJob(); }

// Logic for Platform Toggling
document.querySelectorAll('input[name="platform"]').forEach(radio => {
    radio.addEventListener('change', (e) => {
        const platform = e.target.value;
        const isYoutube = platform === 'youtube';
        const isInstagram = platform === 'instagram';
        
        const tdWorkspace = document.getElementById('td-workspace');
        const ytWorkspace = document.getElementById('yt-workspace');
        const igWorkspace = document.getElementById('ig-workspace');
        
        if (tdWorkspace) tdWorkspace.style.display = (isYoutube || isInstagram) ? 'none' : 'block';
        if (ytWorkspace) ytWorkspace.style.display = isYoutube ? 'block' : 'none';
        if (igWorkspace) igWorkspace.style.display = isInstagram ? 'block' : 'none';
        
        const permText = document.getElementById('permission-text');
        if (permText) {
            if (isYoutube) {
                permText.innerHTML = 'Chỉ tải nội dung của chính bạn hoặc nội dung được phép tải; việc tải video có thể vi phạm Điều khoản dịch vụ của YouTube và bản quyền.';
            } else if (isInstagram) {
                permText.innerHTML = 'Chỉ tải nội dung của chính bạn. Instagram cực kỳ nghiêm ngặt về truy cập, vui lòng không lạm dụng.';
            } else {
                permText.innerHTML = 'Chỉ tải nội dung của chính bạn hoặc nội dung được phép tải. Hãy tôn trọng bản quyền và <a href="https://www.tiktok.com/legal/page/row/terms-of-service/vi" target="_blank" rel="noopener noreferrer">Điều khoản sử dụng của TikTok</a>.';
            }
        }
    });
});
