'use strict';

// [INSTAGRAM]
const ig$ = id => document.getElementById(id);
const IG_API = window.CLIPNEST_CONFIG.API_BASE_URL.replace(/\/$/, '');

let igSelectedVideo = null;
let igActiveJob = null;
let igPolling = false;

function igMessage(id, text, error = false) {
  const el = ig$(id);
  el.textContent = text;
  el.classList.toggle('error', error);
  el.hidden = !text;
}

function igErrorText(error) {
  if (error.name === 'TimeoutError' || error.name === 'AbortError') return 'Máy chủ phản hồi quá lâu. Hãy thử lại.';
  if (error instanceof TypeError) return 'Không kết nối được máy chủ. Hãy kiểm tra mạng và thử lại sau.';
  return error.message;
}

async function igRequest(path, options = {}, timeout = 120000) {
  const response = await fetch(IG_API + path, { ...options, signal: AbortSignal.timeout(timeout) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Máy chủ gặp lỗi (${response.status}). Vui lòng thử lại.`);
  }
  return response;
}

async function igSaveFile(path, filename) {
  const response = await igRequest(path, {}, 950000);
  const blob = await response.blob();
  const objectURL = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = objectURL; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
}

function igActivateTab(name) {
  for (const key of ['video', 'playlist']) {
    const active = name === key;
    ig$('ig-tab-' + key).classList.toggle('active', active);
    ig$('ig-tab-' + key).setAttribute('aria-selected', String(active));
    ig$('ig-tab-' + key).tabIndex = active ? 0 : -1;
    ig$('ig-panel-' + key).hidden = !active;
  }
}

for (const name of ['video', 'playlist']) {
  ig$('ig-tab-' + name).onclick = () => igActivateTab(name);
  ig$('ig-tab-' + name).onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const target = event.key === 'Home' ? 'video' : event.key === 'End' ? 'playlist' : name === 'video' ? 'playlist' : 'video';
      igActivateTab(target); ig$('ig-tab-' + target).focus();
    }
  };
}

ig$('ig-video-form').onsubmit = async event => {
  event.preventDefault(); ig$('ig-info-button').disabled = true; ig$('ig-video-result').hidden = true; igSelectedVideo = null;
  igMessage('ig-video-message', 'Đang lấy thông tin bài viết Instagram… (Có thể chậm nếu cần cookies)');
  try {
    const response = await igRequest('/api/instagram/video/info', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: ig$('ig-video-url').value.trim()}) });
    const data = await response.json();
    igSelectedVideo = data.url;

    ig$('ig-video-title').textContent = data.title || 'Bài viết Instagram';
    const duration = Number(data.duration);
    ig$('ig-video-meta').textContent = [data.author || 'Tài khoản chưa xác định', Number.isFinite(duration) && duration > 0 ? `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, '0')}` : null].filter(Boolean).join(' · ');
    const thumb = ig$('ig-thumbnail'); thumb.hidden = true; thumb.removeAttribute('src');
    if (data.thumbnail) { try { const url = new URL(data.thumbnail); if (url.protocol === 'https:') { thumb.src = url.href; thumb.hidden = false; } } catch {} }
    thumb.onerror = () => { thumb.hidden = true; };

    // Populate select
    const select = ig$('ig-quality-select');
    select.innerHTML = '';
    for (const q of (data.available_formats || ['mp4'])) {
        const opt = document.createElement('option');
        opt.value = q;
        opt.textContent = q === 'mp3' ? 'Âm thanh (MP3)' : 'Video/Ảnh (MP4/JPG)';
        select.appendChild(opt);
    }

    ig$('ig-video-result').hidden = false;
    igMessage('ig-video-message', '');
  } catch (error) { igMessage('ig-video-message', igErrorText(error), true); }
  finally { ig$('ig-info-button').disabled = false; }
};

ig$('ig-download-button').onclick = async () => {
    if (!igSelectedVideo) return;
    const url = igSelectedVideo;
    const quality = ig$('ig-quality-select').value || 'mp4';

    ig$('ig-download-button').disabled = true;
    igMessage('ig-video-message', `Đang chuẩn bị file Instagram… Vui lòng giữ trang này mở.`);
    try {
        await igSaveFile(`/api/instagram/video/download?url=${encodeURIComponent(url)}&quality=${quality}`, `clipnest-instagram.${quality === 'mp3' ? 'mp3' : 'mp4'}`);
        igMessage('ig-video-message', 'File đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.');
    } catch (error) { igMessage('ig-video-message', igErrorText(error), true); }
    finally { ig$('ig-download-button').disabled = false; }
};


function igRememberJob(id) { try { if (id) sessionStorage.setItem('clipnest-instagram-job', id); else sessionStorage.removeItem('clipnest-instagram-job'); } catch {} }

async function igPollJob() {
  if (igPolling || !igActiveJob) return;
  igPolling = true;
  let consecutiveErrors = 0;
  try {
    while (igActiveJob) {
      try {
        const job = await (await igRequest(`/api/instagram/job/${encodeURIComponent(igActiveJob)}`)).json();
        consecutiveErrors = 0; igMessage('ig-playlist-message', ''); ig$('ig-job-progress').hidden = false;
        ig$('ig-job-label').textContent = job.message;
        ig$('ig-job-count').textContent = `${job.downloaded} / ${job.total}`;
        ig$('ig-progress').max = job.total || 1; ig$('ig-progress').value = job.processed;
        ig$('ig-job-detail').textContent = job.status === 'queued' ? 'Máy chủ đang xử lý các tác vụ trước.' : `Đã xử lý ${job.processed}/${job.total} bài · ${job.failures.length} lỗi.`;
        if (job.status === 'done') {
          ig$('ig-download-zip').hidden = false; ig$('ig-start-button').disabled = false;
          igMessage('ig-playlist-message', job.failures.length ? `Đã tải ${job.downloaded}/${job.total} bài. ZIP kèm danh sách lỗi; file sẽ hết hạn sau 15 phút.` : 'File ZIP đã sẵn sàng. Tải về trong vòng 15 phút.');
          break;
        }
        if (job.status === 'error') { throw new Error(job.message); }
      } catch (error) {
        consecutiveErrors++;
        if (!(error instanceof TypeError) && error.name !== 'TimeoutError' && error.name !== 'AbortError' || consecutiveErrors >= 5) {
          igMessage('ig-playlist-message', igErrorText(error), true); igRememberJob(null); igActiveJob = null; ig$('ig-start-button').disabled = false; break;
        }
        igMessage('ig-playlist-message', 'Mất kết nối tạm thời. Đang thử kết nối lại…');
      }
      await new Promise(resolve => setTimeout(resolve, 2500));
    }
  } finally { igPolling = false; }
}

ig$('ig-playlist-form').onsubmit = async event => {
  event.preventDefault(); ig$('ig-start-button').disabled = true; ig$('ig-download-zip').hidden = true; ig$('ig-job-progress').hidden = true;
  igMessage('ig-playlist-message', 'Đang tạo tác vụ tải Instagram… (Sẽ bị chặn nếu chưa có cookies)');
  try {
    const data = await (await igRequest('/api/instagram/batch/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            url: ig$('ig-playlist-url').value.trim(),
            limit: Number(ig$('ig-limit').value),
            quality: ig$('ig-batch-quality').value || 'mp4'
        })
    })).json();
    igActiveJob = data.job_id; igRememberJob(igActiveJob); await igPollJob();
  } catch (error) { igMessage('ig-playlist-message', igErrorText(error), true); ig$('ig-start-button').disabled = false; }
};

ig$('ig-download-zip').onclick = async () => {
  if (!igActiveJob) return;
  ig$('ig-download-zip').disabled = true;
  try { await igSaveFile(`/api/instagram/job/${encodeURIComponent(igActiveJob)}/zip`, 'clipnest-instagram.zip'); igMessage('ig-playlist-message', 'File ZIP đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.'); }
  catch (error) { igMessage('ig-playlist-message', igErrorText(error), true); }
  finally { ig$('ig-download-zip').disabled = false; }
};

try { igActiveJob = sessionStorage.getItem('clipnest-instagram-job'); } catch {}
if (igActiveJob) { igActivateTab('playlist'); ig$('ig-start-button').disabled = true; igPollJob(); }

// Logic for Platform Toggling is handled in youtube.js (or globally). We will add logic there.
