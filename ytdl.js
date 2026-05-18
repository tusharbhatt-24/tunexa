/* ═══════════════════════════════════════════════
   ytdl.js — YouTube Downloader Frontend
   ═══════════════════════════════════════════════ */

// Smart URL: same origin when served via FastAPI /ui/, else localhost:8000
const BACKEND_URL = (location.protocol === 'file:' || location.origin === 'null')
  ? 'http://localhost:8000'
  : location.origin;

/* ── DOM ─────────────────────────────────────── */
const $ = id => document.getElementById(id);

const urlInput  = $('ytdl-url-input');
const clearBtn  = $('ytdl-clear-btn');
const fetchBtn  = $('ytdl-fetch-btn');
const urlError  = $('ytdl-url-error');

const inputCard    = $('ytdl-input-card');
const infoCard     = $('ytdl-info-card');
const progressCard = $('ytdl-progress-card');
const resultCard   = $('ytdl-result-card');
const errorCard    = $('ytdl-error-card');

/* ── STATE ───────────────────────────────────── */
let currentJobId  = null;
let pollTimer     = null;
let currentType   = 'mp3';  // 'mp3' | 'mp4'
let currentTitle  = '';
let isPlaylist    = false;
let playlistEntries = [];
let playlistTitle  = '';

/* ── HELPERS ─────────────────────────────────── */
function hideAll(...cards) { cards.forEach(c => c.classList.add('hidden')); }
function show(card)        { card.classList.remove('hidden'); }

function toast(msg, type = 'info', ms = 4000) {
  const c = $('toast-container');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function isSupportedUrl(url) {
  const val = url.trim();
  const yt = /^https?:\/\/(www\.)?(youtube\.com\/(watch|shorts|playlist|music)|youtu\.be\/|list=)/.test(val);
  const sp = /^https?:\/\/(open\.)?spotify\.com\/(track|playlist)/.test(val);
  return yt || sp;
}

function fmtBytes(bytes) {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ── URL INPUT ───────────────────────────────── */
urlInput.addEventListener('input', () => {
  const val = urlInput.value.trim();
  urlError.textContent = '';
  clearBtn.style.display = val ? 'flex' : 'none';
  fetchBtn.disabled = !isSupportedUrl(val);
});

clearBtn.addEventListener('click', () => {
  urlInput.value = '';
  urlInput.dispatchEvent(new Event('input'));
  urlInput.focus();
});

urlInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click();
});

/* Example pills */
document.querySelectorAll('.ytdl-example-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    urlInput.value = pill.dataset.url;
    urlInput.dispatchEvent(new Event('input'));
    fetchBtn.click();
  });
});

/* ── FETCH VIDEO INFO ────────────────────────── */
fetchBtn.addEventListener('click', fetchVideoInfo);

async function fetchVideoInfo() {
  const url = urlInput.value.trim();
  urlError.textContent = '';
  if (!isSupportedUrl(url)) {
    urlError.textContent = 'Please enter a valid YouTube or Spotify track/playlist URL.';
    return;
  }

  fetchBtn.disabled = true;
  fetchBtn.innerHTML = '<div class="oauth-spinner" style="width:16px;height:16px;border-width:2px"></div> Fetching…';

  try {
    const data = await apiFetch('/api/ytdl/info', { method: 'POST', body: { url } });
    renderVideoInfo(data);
    show(infoCard);
    currentTitle = data.title || 'video';
  } catch (err) {
    urlError.textContent = err.message || 'Could not fetch video info. Check the URL and try again.';
  } finally {
    fetchBtn.disabled = false;
    fetchBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Fetch Info`;
  }
}

function renderVideoInfo(info) {
  $('ytdl-thumb').src = info.thumbnail || '';
  $('ytdl-video-title').textContent = info.title || 'Unknown Title';
  
  if (info.is_playlist) {
    isPlaylist = true;
    playlistEntries = info.entries || [];
    playlistTitle = info.title || 'YouTube Playlist';
    
    $('ytdl-channel').textContent = `${info.uploader || 'YouTube'} · ${info.video_count || playlistEntries.length} videos`;
    $('ytdl-views').textContent = '';
    
    // Render Playlist checklist
    renderPlaylistTracks();
    show($('ytdl-playlist-container'));
  } else {
    isPlaylist = false;
    playlistEntries = [];
    playlistTitle = '';
    
    const dur = info.duration ? formatDur(info.duration) : '';
    $('ytdl-channel').textContent = [info.uploader, dur].filter(Boolean).join(' · ');
    $('ytdl-views').textContent = info.view_count
      ? `${Number(info.view_count).toLocaleString()} views`
      : '';
      
    $('ytdl-playlist-container').classList.add('hidden');
  }
}

function renderPlaylistTracks() {
  const container = $('ytdl-playlist-tracks');
  container.innerHTML = '';

  playlistEntries.forEach((track, i) => {
    const item = document.createElement('div');
    item.className = 'ytdl-playlist-track-item';
    item.id = `track-item-${track.video_id}`;

    // Checkbox
    const checkboxWrapper = document.createElement('label');
    checkboxWrapper.className = 'track-checkbox-wrapper';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'track-checkbox';
    checkbox.checked = true;
    checkbox.dataset.id = track.video_id;

    const customSpan = document.createElement('span');
    customSpan.className = 'track-checkbox-custom';

    checkboxWrapper.appendChild(checkbox);
    checkboxWrapper.appendChild(customSpan);

    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        item.classList.remove('disabled');
      } else {
        item.classList.add('disabled');
      }
      updateSelectedCount();
    });

    // Index
    const indexSpan = document.createElement('span');
    indexSpan.className = 'track-index';
    indexSpan.textContent = i + 1;

    // Details
    const detailsDiv = document.createElement('div');
    detailsDiv.className = 'track-details';

    const titleDiv = document.createElement('div');
    titleDiv.className = 'track-title';
    titleDiv.textContent = track.title;

    const uploaderDiv = document.createElement('div');
    uploaderDiv.className = 'track-uploader';
    uploaderDiv.textContent = track.uploader || playlistTitle;

    detailsDiv.appendChild(titleDiv);
    detailsDiv.appendChild(uploaderDiv);

    // Duration
    const durationSpan = document.createElement('span');
    durationSpan.className = 'track-duration';
    durationSpan.textContent = track.duration ? formatDur(track.duration) : '';

    item.appendChild(checkboxWrapper);
    item.appendChild(indexSpan);
    item.appendChild(detailsDiv);
    item.appendChild(durationSpan);

    container.appendChild(item);
  });

  updateSelectedCount();
}

function updateSelectedCount() {
  const checkboxes = document.querySelectorAll('.track-checkbox');
  const checked = Array.from(checkboxes).filter(cb => cb.checked);
  $('ytdl-playlist-selected-count').textContent = `${checked.length} Selected`;

  const dlBtn = $('ytdl-download-btn');
  dlBtn.disabled = checked.length === 0;

  const toggleBtn = $('ytdl-playlist-toggle-all');
  if (toggleBtn) {
    if (checked.length === checkboxes.length) {
      toggleBtn.textContent = 'Deselect All';
    } else {
      toggleBtn.textContent = 'Select All';
    }
  }
}

// Select All / Deselect All trigger
$('ytdl-playlist-toggle-all').addEventListener('click', () => {
  const checkboxes = document.querySelectorAll('.track-checkbox');
  const checked = Array.from(checkboxes).filter(cb => cb.checked);
  const shouldCheck = checked.length < checkboxes.length;

  checkboxes.forEach(cb => {
    cb.checked = shouldCheck;
    const item = $(`track-item-${cb.dataset.id}`);
    if (item) {
      if (shouldCheck) {
        item.classList.remove('disabled');
      } else {
        item.classList.add('disabled');
      }
    }
  });

  updateSelectedCount();
});

function formatDur(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

/* ── FORMAT TABS ─────────────────────────────── */
document.querySelectorAll('.ytdl-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.ytdl-tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    currentType = tab.dataset.type;

    if (currentType === 'mp3') {
      show($('ytdl-mp3-options'));
      $('ytdl-mp4-options').classList.add('hidden');
      $('ytdl-dl-btn-text').textContent = 'Download MP3';
    } else {
      show($('ytdl-mp4-options'));
      $('ytdl-mp3-options').classList.add('hidden');
      $('ytdl-dl-btn-text').textContent = 'Download MP4';
    }
  });
});

/* ── DOWNLOAD ────────────────────────────────── */
$('ytdl-download-btn').addEventListener('click', startDownload);

async function startDownload() {
  const url = urlInput.value.trim();
  const format = currentType;
  const quality = format === 'mp3'
    ? document.querySelector('input[name="mp3q"]:checked')?.value || '192'
    : document.querySelector('input[name="mp4q"]:checked')?.value || '720';

  hideAll(infoCard, resultCard, errorCard);
  show(progressCard);
  setProgress(0, 'Queuing download…');

  const reqBody = { url, format, quality: parseInt(quality) };

  if (isPlaylist) {
    const checkboxes = document.querySelectorAll('.track-checkbox');
    const checkedIds = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.dataset.id);
    reqBody.video_ids = checkedIds;
    reqBody.playlist_title = playlistTitle;
  }

  try {
    const res = await apiFetch('/api/ytdl/download', {
      method: 'POST',
      body: reqBody
    });
    currentJobId = res.job_id;
    pollDownload();
  } catch (err) {
    showError(err.message || 'Failed to start download.');
  }
}

function setProgress(pct, label, status = '') {
  $('ytdl-prog-fill').style.width = `${pct}%`;
  $('ytdl-prog-bar').setAttribute('aria-valuenow', pct);
  $('ytdl-prog-pct').textContent = `${pct}%`;
  $('ytdl-progress-label').textContent = label;
  $('ytdl-progress-status').textContent = status;
}

function pollDownload() {
  let dots = 0;
  const stages = [
    'Extracting stream details…',
    'Downloading from YouTube…',
    'Encoding tracks…',
    'Structuring batch archive…',
  ];
  let stageIdx = 0;

  pollTimer = setInterval(async () => {
    try {
      const job = await apiFetch(`/api/ytdl/jobs/${currentJobId}`, { method: 'GET' });

      const pct = job.progress || 0;
      const statusMsg = job.status_msg || stages[stageIdx % stages.length];
      setProgress(pct, `Downloading ${currentType.toUpperCase()} batch…`, statusMsg);

      if (pct > 0 && pct % 25 === 0) stageIdx++;

      if (job.status === 'complete') {
        clearInterval(pollTimer);
        showResult(job);
      } else if (job.status === 'failed') {
        clearInterval(pollTimer);
        showError(job.error || 'Download failed on server.');
      }
    } catch (e) {
      // transient — keep polling
    }
  }, 2000);
}

function showResult(job) {
  hideAll(progressCard, errorCard);
  show(resultCard);

  const isZip = job.is_playlist;
  const ext  = isZip ? 'zip' : (job.format || currentType);
  const size = fmtBytes(job.file_size);
  
  const displayTitle = job.title || currentTitle;
  $('ytdl-result-title').textContent = isZip ? `Your Playlist ZIP is ready!` : `Your ${ext.toUpperCase()} is ready!`;
  $('ytdl-result-meta').textContent  = [displayTitle, size].filter(Boolean).join(' · ');

  const link = $('ytdl-result-link');
  link.href = `${BACKEND_URL}/api/ytdl/jobs/${currentJobId}/file`;
  
  const cleanTitle = displayTitle.replace(/[^\w\s-]/g,'').trim();
  link.download = `${cleanTitle}.${ext}`;
  link.textContent = '';
  link.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Save ${isZip ? 'Playlist ZIP' : ext.toUpperCase()}`;
}

function showError(msg) {
  hideAll(progressCard, resultCard, infoCard);
  show(errorCard);
  $('ytdl-error-msg').textContent = msg;
}

/* ── RESET / RETRY ───────────────────────────── */
$('ytdl-reset-btn').addEventListener('click', resetAll);
$('ytdl-retry-btn').addEventListener('click', () => {
  hideAll(errorCard);
  show(infoCard);
});

function resetAll() {
  if (pollTimer) clearInterval(pollTimer);
  currentJobId = null;
  isPlaylist = false;
  playlistEntries = [];
  playlistTitle = '';
  $('ytdl-playlist-container').classList.add('hidden');
  $('ytdl-playlist-tracks').innerHTML = '';
  hideAll(infoCard, progressCard, resultCard, errorCard);
  urlInput.value = '';
  urlInput.dispatchEvent(new Event('input'));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ── API ─────────────────────────────────────── */
async function apiFetch(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BACKEND_URL}${path}`, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || data.detail || `Error ${res.status}`);
  return data;
}

