'use strict';
const $ = id => document.getElementById(id);
const API = window.CLIPNEST_CONFIG.API_BASE_URL.replace(/\/$/, '');
let selectedVideo = null;
let activeJob = null;
let polling = false;
function message(id, text, error = false) {
  const el = $(id); el.textContent = text; el.classList.toggle('error', error); el.hidden = !text;
}
function errorText(error) {
  if (error.name === 'TimeoutError' || error.name === 'AbortError') return 'Máy chủ phản hồi quá lâu. Render miễn phí có thể cần khoảng một phút để khởi động. Hãy thử lại.';
  if (error instanceof TypeError) return 'Không kết nối được máy chủ. Hãy kiểm tra mạng và thử lại sau khoảng một phút.';
  return error.message;
}
async function request(path, options = {}, timeout = 120000) {
  const response = await fetch(API + path, { ...options, signal: AbortSignal.timeout(timeout) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Máy chủ gặp lỗi (${response.status}). Vui lòng thử lại.`);
  }
  return response;
}
// Lưu file bằng Blob giúp đọc được thông báo lỗi JSON trước khi bắt đầu tải.
async function saveFile(path, filename) {
  const response = await request(path, {}, 950000);
  const blob = await response.blob();
  const objectURL = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = objectURL; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
}
function activateTab(name) {
  for (const key of ['video', 'user']) {
    const active = name === key;
    $('tab-' + key).classList.toggle('active', active);
    $('tab-' + key).setAttribute('aria-selected', String(active));
    $('tab-' + key).tabIndex = active ? 0 : -1;
    $('panel-' + key).hidden = !active;
  }
}
for (const name of ['video', 'user']) {
  $('tab-' + name).onclick = () => activateTab(name);
  $('tab-' + name).onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const target = event.key === 'Home' ? 'video' : event.key === 'End' ? 'user' : name === 'video' ? 'user' : 'video';
      activateTab(target); $('tab-' + target).focus();
    }
  };
}
$('video-form').onsubmit = async event => {
  event.preventDefault(); $('info-button').disabled = true; $('video-result').hidden = true; selectedVideo = null;
  message('video-message', 'Đang lấy thông tin video… Lần đầu có thể lâu hơn khi máy chủ đang khởi động.');
  try {
    const response = await request('/api/video/info', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: $('video-url').value.trim()}) });
    const data = await response.json(); selectedVideo = data.url;
    $('video-title').textContent = data.title || 'Video TikTok';
    const duration = Number(data.duration);
    $('video-meta').textContent = [data.author || 'Tác giả chưa xác định', Number.isFinite(duration) && duration > 0 ? `${Math.floor(duration / 60)}:${String(Math.floor(duration % 60)).padStart(2, '0')}` : null].filter(Boolean).join(' · ');
    const thumb = $('thumbnail'); thumb.hidden = true; thumb.removeAttribute('src');
    if (data.thumbnail) { try { const url = new URL(data.thumbnail); if (url.protocol === 'https:') { thumb.src = url.href; thumb.hidden = false; } } catch {} }
    thumb.onerror = () => { thumb.hidden = true; };
    $('video-result').hidden = false; message('video-message', '');
  } catch (error) { message('video-message', errorText(error), true); }
  finally { $('info-button').disabled = false; }
};
for (const [button, format] of [['download-video', 'mp4'], ['download-audio', 'mp3']]) {
  $(button).onclick = async () => {
    if (!selectedVideo) return;
    const url = selectedVideo;
    $('download-video').disabled = $('download-audio').disabled = true;
    message('video-message', `Đang chuẩn bị file ${format.toUpperCase()}… Vui lòng giữ trang này mở.`);
    try {
      await saveFile(`/api/video/download?url=${encodeURIComponent(url)}&format=${format}`, `clipnest.${format}`);
      message('video-message', 'File đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.');
    } catch (error) { message('video-message', errorText(error), true); }
    finally { $('download-video').disabled = $('download-audio').disabled = false; }
  };
}
function rememberJob(id) { try { if (id) sessionStorage.setItem('clipnest-job', id); else sessionStorage.removeItem('clipnest-job'); } catch {} }
async function pollJob() {
  if (polling || !activeJob) return;
  polling = true;
  let consecutiveErrors = 0;
  try {
    while (activeJob) {
      try {
        const job = await (await request(`/api/job/${encodeURIComponent(activeJob)}`)).json();
        consecutiveErrors = 0; message('user-message', ''); $('job-progress').hidden = false;
        $('job-label').textContent = job.message;
        $('job-count').textContent = `${job.downloaded} / ${job.total}`;
        $('progress').max = job.total || 1; $('progress').value = job.processed;
        $('job-detail').textContent = job.status === 'queued' ? 'Máy chủ đang xử lý các tác vụ trước.' : `Đã xử lý ${job.processed}/${job.total} video · ${job.failures.length} lỗi.`;
        if (job.status === 'done') {
          $('download-zip').hidden = false; $('start-button').disabled = false;
          message('user-message', job.failures.length ? `Đã tải ${job.downloaded}/${job.total} video. ZIP kèm danh sách lỗi; file sẽ hết hạn sau 15 phút.` : 'File ZIP đã sẵn sàng. Tải về trong vòng 15 phút.');
          break;
        }
        if (job.status === 'error') { throw new Error(job.message); }
      } catch (error) {
        consecutiveErrors++;
        if (!(error instanceof TypeError) && error.name !== 'TimeoutError' && error.name !== 'AbortError' || consecutiveErrors >= 5) {
          message('user-message', errorText(error), true); rememberJob(null); activeJob = null; $('start-button').disabled = false; break;
        }
        message('user-message', 'Mất kết nối tạm thời. Đang thử kết nối lại…');
      }
      await new Promise(resolve => setTimeout(resolve, 2500));
    }
  } finally { polling = false; }
}
$('user-form').onsubmit = async event => {
  event.preventDefault(); $('start-button').disabled = true; $('download-zip').hidden = true; $('job-progress').hidden = true;
  message('user-message', 'Đang tạo tác vụ tải…');
  try {
    const data = await (await request('/api/user/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: $('username').value.trim(), limit: Number($('limit').value)})})).json();
    activeJob = data.job_id; rememberJob(activeJob); await pollJob();
  } catch (error) { message('user-message', errorText(error), true); $('start-button').disabled = false; }
};
$('download-zip').onclick = async () => {
  if (!activeJob) return;
  $('download-zip').disabled = true;
  try { await saveFile(`/api/job/${encodeURIComponent(activeJob)}/zip`, 'clipnest-videos.zip'); message('user-message', 'File ZIP đã sẵn sàng. Kiểm tra mục tải xuống của trình duyệt.'); }
  catch (error) { message('user-message', errorText(error), true); }
  finally { $('download-zip').disabled = false; }
};
$('year').textContent = new Date().getFullYear();
try { activeJob = sessionStorage.getItem('clipnest-job'); } catch {}
if (activeJob) { activateTab('user'); $('start-button').disabled = true; pollJob(); }
