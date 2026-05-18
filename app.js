/* ═══════════════════════════════════════════════════════════
   Tunexa — Frontend Application Logic
   Communicates with Python backend at BACKEND_URL.
   All OAuth tokens held in memory only (never localStorage).
   ═══════════════════════════════════════════════════════════ */

const BACKEND_URL = (typeof window !== 'undefined' && window.BACKEND_URL) || 'http://localhost:8000';

/* ── STATE ────────────────────────────────────── */
const state = {
  playlistUrl: '',
  sourcePlatform: null,   // 'spotify' | 'youtube'
  playlistName: '',
  tracks: [],             // UnifiedTrack[]
  matchResults: [],       // MatchResult[]
  destination: null,      // 'spotify' | 'youtube' | 'mp3'
  oauthToken: null,       // in-memory only
  jobId: null,
  pollTimer: null,
  resolveIndex: null,     // index of track being resolved
};

/* ── DOM REFS ─────────────────────────────────── */
const $ = id => document.getElementById(id);

const steps = {
  url: $('step-url'),
  tracks: $('step-tracks'),
  destination: $('step-destination'),
  results: $('step-results'),
};

/* ── UTILITY: SHOW / HIDE STEPS ─────────────────*/
function showStep(name) {
  Object.values(steps).forEach(s => s.classList.add('hidden'));
  steps[name].classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ── UTILITY: TOAST ──────────────────────────── */
function toast(msg, type = 'info', duration = 4000) {
  const c = $('toast-container');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), duration);
}

/* ── UTILITY: LOADING OVERLAY ────────────────── */
function showLoading(text = 'Loading…') {
  $('loading-overlay-text').textContent = text;
  $('loading-overlay').classList.remove('hidden');
}
function hideLoading() {
  $('loading-overlay').classList.add('hidden');
}

/* ── UTILITY: FORMAT DURATION ────────────────── */
function fmtDuration(secs) {
  if (!secs) return '--:--';
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/* ── UTILITY: DETECT PLATFORM FROM URL ──────── */
function detectPlatform(url) {
  if (/open\.spotify\.com\/playlist\//i.test(url)) return 'spotify';
  if (/music\.youtube\.com\/playlist\?list=/i.test(url) ||
      /youtube\.com\/playlist\?list=/i.test(url)) return 'youtube';
  return null;
}

/* ── STEP 1: URL INPUT ───────────────────────── */
const urlInput = $('playlist-url-input');
const fetchBtn = $('fetch-playlist-btn');
const clearBtn = $('url-clear-btn');
const urlError = $('url-error');
const platformBadge = $('url-platform-badge');

urlInput.addEventListener('input', () => {
  const val = urlInput.value.trim();
  urlError.textContent = '';
  clearBtn.style.display = val ? 'flex' : 'none';

  const platform = detectPlatform(val);
  if (platform) {
    platformBadge.textContent = platform === 'spotify' ? 'SPOTIFY' : 'YT MUSIC';
    platformBadge.className = `url-platform-badge show ${platform}`;
    urlInput.classList.add('has-badge');
    fetchBtn.disabled = false;
  } else {
    platformBadge.className = 'url-platform-badge';
    urlInput.classList.remove('has-badge');
    fetchBtn.disabled = !val;
  }
});

clearBtn.addEventListener('click', () => {
  urlInput.value = '';
  urlInput.dispatchEvent(new Event('input'));
  urlInput.focus();
});

urlInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !fetchBtn.disabled) fetchBtn.click();
});

fetchBtn.addEventListener('click', handleFetchPlaylist);

async function handleFetchPlaylist() {
  const url = urlInput.value.trim();
  urlError.textContent = '';

  if (!url) { urlError.textContent = 'Please enter a playlist URL.'; return; }

  const platform = detectPlatform(url);
  if (!platform) {
    urlError.textContent = 'URL not recognised. Paste a Spotify or YouTube Music playlist URL.';
    return;
  }

  state.playlistUrl = url;
  state.sourcePlatform = platform;

  showLoading('Fetching playlist tracks…');
  try {
    const res = await apiFetch('/api/fetch-playlist', { method: 'POST', body: { url } });
    state.playlistName = res.name || 'Untitled Playlist';
    state.tracks = res.tracks || [];
    state.matchResults = state.tracks.map((t, i) => ({
      index: i, track: t, status: 'unmatched', destId: null
    }));
    hideLoading();
    renderTracksStep();
    showStep('tracks');
  } catch (err) {
    hideLoading();
    urlError.textContent = err.message || 'Failed to fetch playlist. Check the URL and try again.';
  }
}

/* ── STEP 2: TRACKS REVIEW ───────────────────── */
function renderTracksStep() {
  // header
  const pb = $('playlist-platform-badge');
  pb.textContent = state.sourcePlatform === 'spotify' ? 'Spotify' : 'YouTube Music';
  pb.className = `playlist-platform-badge ${state.sourcePlatform}`;
  $('playlist-name').textContent = state.playlistName;
  $('playlist-stats').textContent = `${state.tracks.length} tracks fetched`;

  renderTrackList();
  updateMatchSummary();
}

function renderTrackList(filter = 'all', query = '') {
  const list = $('tracks-list');
  list.innerHTML = '';

  const results = state.matchResults.filter(r => {
    if (filter !== 'all' && r.status !== filter) return false;
    if (query) {
      const q = query.toLowerCase();
      return r.track.title.toLowerCase().includes(q) ||
             r.track.artist.toLowerCase().includes(q);
    }
    return true;
  });

  if (!results.length) {
    list.innerHTML = '<div style="text-align:center;padding:2rem;color:#475569;font-size:.85rem">No tracks found</div>';
    return;
  }

  results.forEach(r => {
    const div = document.createElement('div');
    div.className = `track-item ${r.status}`;
    div.setAttribute('role', 'listitem');
    div.dataset.index = r.index;

    let statusHtml = '';
    if (r.status === 'matched') {
      statusHtml = '<span class="track-status status-matched">✓ Matched</span>';
    } else if (r.status === 'resolved') {
      statusHtml = '<span class="track-status status-resolved">✦ Resolved</span>';
    } else if (r.status === 'skipped') {
      statusHtml = '<span class="track-status status-skipped">— Skipped</span>';
    } else {
      statusHtml = `<span class="track-status status-unmatched">⚠ Unmatched</span>
        <button class="track-resolve-btn" data-index="${r.index}">Fix</button>`;
    }

    div.innerHTML = `
      <span class="track-num">${r.index + 1}</span>
      <div class="track-info">
        <div class="track-title">${escHtml(r.track.title)}</div>
        <div class="track-artist">${escHtml(r.track.artist)}</div>
      </div>
      <span class="track-duration">${fmtDuration(r.track.duration)}</span>
      ${statusHtml}
    `;
    list.appendChild(div);
  });

  list.querySelectorAll('.track-resolve-btn').forEach(btn => {
    btn.addEventListener('click', () => openResolveModal(parseInt(btn.dataset.index)));
  });
}

function updateMatchSummary() {
  const matched = state.matchResults.filter(r => r.status === 'matched' || r.status === 'resolved').length;
  const unmatched = state.matchResults.filter(r => r.status === 'unmatched').length;
  const skipped = state.matchResults.filter(r => r.status === 'skipped').length;
  
  let html = `<strong>${matched}</strong> matched &nbsp;·&nbsp; <strong>${unmatched}</strong> unmatched &nbsp;·&nbsp; <strong>${skipped}</strong> skipped`;
  
  if (state.matchResults.length > 100) {
    html += `<div style="margin-top: 0.5rem; color: #ff8800; font-size: 0.8rem; font-weight: 500; display: flex; align-items: center; gap: 0.35rem; justify-content: center;">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align: middle;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      Note: You can sync/download up to 100 songs at a time. The first 100 matched tracks will be processed.
    </div>`;
  }
  
  $('match-summary').innerHTML = html;
}

// Filter tabs
document.querySelectorAll('.filter-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const q = $('track-search').value.trim();
    renderTrackList(tab.dataset.filter, q);
  });
});

$('track-search').addEventListener('input', e => {
  const active = document.querySelector('.filter-tab.active');
  renderTrackList(active?.dataset.filter || 'all', e.target.value.trim());
});

$('back-to-url-btn').addEventListener('click', () => showStep('url'));
$('proceed-to-dest-btn').addEventListener('click', () => {
  // Run matching against destination (simulated client-side for now — real matching done via backend)
  runClientSideMatching();
  showStep('destination');
});

/* client-side ISRC / fuzzy stub — real matching done on backend per-destination */
function runClientSideMatching() {
  state.matchResults.forEach(r => {
    if (r.status === 'unmatched') {
      // Mark as matched if track has ISRC (will be confirmed by backend on transfer)
      if (r.track.isrc) r.status = 'matched';
    }
  });
  renderTrackList();
  updateMatchSummary();
}

/* ── RESOLVE MODAL ───────────────────────────── */
function openResolveModal(index) {
  state.resolveIndex = index;
  const r = state.matchResults[index];
  $('modal-track-info').textContent = `${r.track.title} — ${r.track.artist}`;
  $('modal-search-input').value = `${r.track.title} ${r.track.artist}`;
  $('modal-results').innerHTML = '';
  $('resolve-modal').classList.remove('hidden');
  $('modal-search-input').focus();
}

$('resolve-modal-close').addEventListener('click', closeResolveModal);
$('resolve-modal').addEventListener('click', e => {
  if (e.target === $('resolve-modal')) closeResolveModal();
});

function closeResolveModal() {
  $('resolve-modal').classList.add('hidden');
  state.resolveIndex = null;
}

$('modal-skip-btn').addEventListener('click', () => {
  if (state.resolveIndex !== null) {
    state.matchResults[state.resolveIndex].status = 'skipped';
    renderTrackList(
      document.querySelector('.filter-tab.active')?.dataset.filter || 'all',
      $('track-search').value.trim()
    );
    updateMatchSummary();
  }
  closeResolveModal();
});

$('modal-search-btn').addEventListener('click', handleModalSearch);
$('modal-search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') handleModalSearch();
});

async function handleModalSearch() {
  const q = $('modal-search-input').value.trim();
  if (!q) return;
  const resultsEl = $('modal-results');
  resultsEl.innerHTML = '<div style="text-align:center;padding:1rem;color:#64748b;font-size:.8rem">Searching…</div>';

  try {
    const res = await apiFetch('/api/search', {
      method: 'POST',
      body: { query: q, platform: state.destination || state.sourcePlatform }
    });
    const candidates = res.results || [];
    resultsEl.innerHTML = '';

    if (!candidates.length) {
      resultsEl.innerHTML = '<div style="text-align:center;padding:1rem;color:#64748b;font-size:.8rem">No results found</div>';
      return;
    }

    candidates.slice(0, 10).forEach(c => {
      const div = document.createElement('div');
      div.className = 'modal-result-item';
      div.setAttribute('role', 'listitem');
      div.innerHTML = `
        <div class="modal-result-info">
          <div class="track-title">${escHtml(c.title)}</div>
          <div class="track-artist">${escHtml(c.artist)} &nbsp;·&nbsp; ${fmtDuration(c.duration)}</div>
        </div>
        <button class="modal-result-select" data-id="${escHtml(c.id)}">Select</button>
      `;
      div.querySelector('.modal-result-select').addEventListener('click', () => {
        const idx = state.resolveIndex;
        if (idx !== null) {
          state.matchResults[idx].status = 'resolved';
          state.matchResults[idx].destId = c.id;
          renderTrackList(
            document.querySelector('.filter-tab.active')?.dataset.filter || 'all',
            $('track-search').value.trim()
          );
          updateMatchSummary();
          toast(`Track resolved: ${c.title}`, 'success');
        }
        closeResolveModal();
      });
      resultsEl.appendChild(div);
    });
  } catch (err) {
    resultsEl.innerHTML = `<div style="text-align:center;padding:1rem;color:#f87171;font-size:.8rem">${err.message}</div>`;
  }
}

/* ── STEP 3: DESTINATION ─────────────────────── */
$('back-to-tracks-btn').addEventListener('click', () => showStep('tracks'));

document.querySelectorAll('.dest-card').forEach(card => {
  card.addEventListener('click', () => handleDestinationSelect(card.dataset.dest));
});

async function handleDestinationSelect(dest) {
  state.destination = dest;

  if (dest === 'mp3') {
    startTransfer();
    return;
  }

  // Initiate OAuth
  const oauthStatus = $('oauth-status');
  oauthStatus.classList.remove('hidden');
  $('oauth-status-text').textContent = dest === 'spotify'
    ? 'Connecting to Spotify…' : 'Connecting to Google…';

  try {
    const res = await apiFetch(`/api/oauth/${dest}/url`, { method: 'GET' });
    const authUrl = res.url;

    // Open popup
    const popup = window.open(authUrl, 'oauth', 'width=500,height=700,menubar=no,toolbar=no');
    if (!popup) {
      oauthStatus.classList.add('hidden');
      toast('Popup blocked. Allow popups for this site and try again.', 'error');
      return;
    }

    // Poll for token message from popup
    const tokenHandler = e => {
      if (e.data?.type === 'oauth_token') {
        window.removeEventListener('message', tokenHandler);
        state.oauthToken = e.data.token; // memory only
        oauthStatus.classList.add('hidden');
        toast(`Connected to ${dest === 'spotify' ? 'Spotify' : 'YouTube Music'}!`, 'success');
        startTransfer();
      } else if (e.data?.type === 'oauth_error') {
        window.removeEventListener('message', tokenHandler);
        oauthStatus.classList.add('hidden');
        toast(e.data.message || 'Authentication failed.', 'error');
      }
    };
    window.addEventListener('message', tokenHandler);

    // Detect popup closed without auth
    const closedTimer = setInterval(() => {
      if (popup.closed) {
        clearInterval(closedTimer);
        window.removeEventListener('message', tokenHandler);
        oauthStatus.classList.add('hidden');
        if (!state.oauthToken) {
          toast('Authentication cancelled.', 'error');
        }
      }
    }, 500);
  } catch (err) {
    oauthStatus.classList.add('hidden');
    toast(err.message || 'Failed to start OAuth flow.', 'error');
  }
}

/* ── STEP 4: TRANSFER / PROGRESS ─────────────── */
async function startTransfer() {
  let matched = state.matchResults.filter(r => r.status === 'matched' || r.status === 'resolved');

  if (matched.length > 100) {
    toast('Notice: Tunexa supports downloading/syncing up to 100 tracks at a time. The first 100 tracks have been queued.', 'warning', 6000);
    matched = matched.slice(0, 100);
  }

  showStep('results');
  $('progress-view').style.display = '';
  $('results-view').classList.add('hidden');
  setProgress(0, 'Starting…');

  const body = {
    playlist_name: state.playlistName,
    destination: state.destination,
    tracks: matched.map(r => ({
      title: r.track.title,
      artist: r.track.artist,
      isrc: r.track.isrc || null,
      duration: r.track.duration,
      dest_id: r.destId || null,
    })),
  };
  if (state.oauthToken) body.oauth_token = state.oauthToken;

  try {
    if (state.destination === 'mp3') {
      const res = await apiFetch('/api/jobs/mp3', { method: 'POST', body });
      state.jobId = res.job_id;
      pollJobProgress();
    } else {
      // Playlist creation (Spotify / YouTube)
      $('progress-title').textContent = `Creating playlist on ${state.destination === 'spotify' ? 'Spotify' : 'YouTube Music'}…`;
      const res = await apiFetch('/api/create-playlist', { method: 'POST', body });
      showResults({
        playlistUrl: res.playlist_url,
        matched: matched.length,
        failed: res.failed || [],
      });
    }
  } catch (err) {
    toast(err.message || 'Transfer failed.', 'error');
    $('progress-sub').textContent = `Error: ${err.message}`;
  }
}

function setProgress(pct, label) {
  const fill = $('progress-fill');
  const pctEl = $('progress-pct');
  const bar = $('progress-bar');
  fill.style.width = `${pct}%`;
  bar.setAttribute('aria-valuenow', pct);
  pctEl.textContent = `${pct}%`;
  $('progress-track-status').textContent = label || '';
}

function pollJobProgress() {
  $('progress-title').textContent = 'Downloading MP3s…';
  $('progress-sub').textContent = 'This may take a few minutes depending on playlist size.';

  state.pollTimer = setInterval(async () => {
    try {
      const res = await apiFetch(`/api/jobs/${state.jobId}`, { method: 'GET' });
      setProgress(res.progress || 0, res.current_track ? `Downloading: ${res.current_track}` : '');

      if (res.status === 'complete') {
        clearInterval(state.pollTimer);
        showResults({
          downloadUrl: res.download_url,
          matched: res.total,
          failed: res.failed_tracks || [],
        });
      } else if (res.status === 'failed') {
        clearInterval(state.pollTimer);
        toast('Job failed: ' + (res.error || 'Unknown error'), 'error');
      }
    } catch (err) {
      // transient — keep polling
    }
  }, 4000);
}

function showResults({ playlistUrl, downloadUrl, matched, failed }) {
  $('progress-view').style.display = 'none';
  $('results-view').classList.remove('hidden');

  const total = state.tracks.length;
  const resolvedCount = state.matchResults.filter(r => r.status === 'resolved').length;
  const skipped = state.matchResults.filter(r => r.status === 'skipped').length;

  $('results-stats').innerHTML = `
    <div class="stat-item stat-total"><div class="stat-num">${total}</div><div class="stat-label">Total</div></div>
    <div class="stat-item stat-matched"><div class="stat-num">${matched - resolvedCount}</div><div class="stat-label">Matched</div></div>
    <div class="stat-item stat-resolved"><div class="stat-num">${resolvedCount}</div><div class="stat-label">Resolved</div></div>
    <div class="stat-item stat-failed"><div class="stat-num">${skipped + failed.length}</div><div class="stat-label">Skipped/Failed</div></div>
  `;

  const linkWrap = $('results-link-wrap');
  if (playlistUrl) {
    linkWrap.innerHTML = `<a href="${escHtml(playlistUrl)}" target="_blank" rel="noopener" class="results-link">
      Open Playlist ↗
    </a>`;
  } else if (downloadUrl) {
    linkWrap.innerHTML = `<a href="${escHtml(downloadUrl)}" download class="results-link">
      ⬇ Download ZIP Archive
    </a>`;
  }

  if (failed.length) {
    $('results-failed-wrap').classList.remove('hidden');
    $('results-failed-list').innerHTML = failed.map(t =>
      `<li>${escHtml(typeof t === 'string' ? t : `${t.title} — ${t.artist}`)}</li>`
    ).join('');
  }

  // Per-track result list
  const tl = $('results-track-list');
  tl.innerHTML = '';
  state.matchResults.forEach(r => {
    const div = document.createElement('div');
    div.className = `track-item ${r.status}`;
    const failedTrack = failed.find(f => (f.title || f) === r.track.title);
    const status = failedTrack ? 'failed' : r.status;
    const statusHtml = {
      matched: '<span class="track-status status-matched">✓ Matched</span>',
      resolved: '<span class="track-status status-resolved">✦ Resolved</span>',
      skipped: '<span class="track-status status-skipped">— Skipped</span>',
      failed: '<span class="track-status" style="color:#f87171">✗ Failed</span>',
      unmatched: '<span class="track-status status-skipped">— Not included</span>',
    }[status] || '';
    div.innerHTML = `
      <span class="track-num">${r.index + 1}</span>
      <div class="track-info">
        <div class="track-title">${escHtml(r.track.title)}</div>
        <div class="track-artist">${escHtml(r.track.artist)}</div>
      </div>
      ${statusHtml}
    `;
    tl.appendChild(div);
  });
}

$('start-over-btn').addEventListener('click', () => {
  if (state.pollTimer) clearInterval(state.pollTimer);
  Object.assign(state, {
    playlistUrl: '', sourcePlatform: null, playlistName: '',
    tracks: [], matchResults: [], destination: null,
    oauthToken: null, jobId: null, pollTimer: null, resolveIndex: null,
  });
  urlInput.value = '';
  urlInput.dispatchEvent(new Event('input'));
  showStep('url');
});

/* ── API HELPER ──────────────────────────────── */
async function apiFetch(path, { method = 'GET', body } = {}) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  if (state.oauthToken) opts.headers['Authorization'] = `Bearer ${state.oauthToken}`;

  const res = await fetch(`${BACKEND_URL}${path}`, opts);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (res.status === 401) {
      state.oauthToken = null;
      toast('Session expired. Please re-authenticate.', 'error');
    }
    throw new Error(data.message || data.detail || `Request failed (${res.status})`);
  }
  return data;
}

/* ── ESCAPE HTML ─────────────────────────────── */
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ── SETTINGS MODAL CONTROLS ──────────────────── */
const settingsModal = document.getElementById('settings-modal');
const settingsBtn = document.getElementById('nav-settings-btn');
const settingsCloseBtn = document.getElementById('settings-close-btn');
const settingsCancelBtn = document.getElementById('settings-cancel-btn');
const settingsSaveBtn = document.getElementById('settings-save-btn');
const settingsClientId = document.getElementById('spotify-client-id-input');
const settingsClientSecret = document.getElementById('spotify-client-secret-input');

if (settingsBtn && settingsModal) {
  // Open settings
  settingsBtn.addEventListener('click', async () => {
    settingsModal.classList.remove('hidden');
    try {
      const res = await fetch(`${BACKEND_URL}/api/settings/spotify`);
      const data = await res.json();
      if (data.client_id) {
        settingsClientId.value = data.client_id;
      } else {
        settingsClientId.value = '';
      }
      if (data.client_secret_masked) {
        settingsClientSecret.value = data.client_secret_masked;
        settingsClientSecret.placeholder = 'Credentials saved';
      } else {
        settingsClientSecret.value = '';
        settingsClientSecret.placeholder = 'Paste your Spotify Client Secret...';
      }
    } catch (err) {
      toast('Failed to load settings from server', 'error');
    }
  });

  // Close helper
  const closeSettings = () => {
    settingsModal.classList.add('hidden');
  };

  settingsCloseBtn.addEventListener('click', closeSettings);
  settingsCancelBtn.addEventListener('click', closeSettings);
  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettings();
  });

  // Save settings
  settingsSaveBtn.addEventListener('click', async () => {
    const cid = settingsClientId.value.trim();
    const csec = settingsClientSecret.value.trim();
    
    if (!cid || !csec) {
      toast('Please fill in both fields', 'error');
      return;
    }
    
    // If client secret is still masked, don't send asterisks
    if (csec.includes('***')) {
      toast('Spotify secret already saved. If changing, type a new secret.', 'warning');
      return;
    }

    settingsSaveBtn.disabled = true;
    settingsSaveBtn.textContent = 'Saving...';

    try {
      const res = await fetch(`${BACKEND_URL}/api/settings/spotify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: cid, client_secret: csec }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to save settings');
      toast('Spotify API credentials saved successfully!', 'success');
      closeSettings();
    } catch (err) {
      toast(err.message || 'Failed to save settings', 'error');
    } finally {
      settingsSaveBtn.disabled = false;
      settingsSaveBtn.textContent = 'Save Settings';
    }
  });
}
