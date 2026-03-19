const API = '/api';

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

async function checkAuth() {
  const res = await fetch(API + '/auth/me', { credentials: 'include' });
  if (!res.ok) return null;
  const data = await res.json();
  return data.authenticated ? data : null;
}

async function logout() {
  await fetch(API + '/auth/logout', { method: 'POST', credentials: 'include' });
  location.reload();
}

function saveAndLoadPersonal() {
  const input = document.getElementById('github-username-input');
  const username = input.value.trim();
  if (!username) return;
  localStorage.setItem('github_username', username);
  loadPersonalStats();
}

let _authConfig = null;

async function boot() {
  // Load auth config (public endpoint)
  try {
    _authConfig = await fetch(API + '/auth/config').then(r => r.json());
  } catch (_) {
    _authConfig = { mode: 'google' };
  }

  const user = await checkAuth();
  if (!user) {
    renderLoginScreen(_authConfig);
    document.getElementById('login-screen').style.display = 'flex';
    return;
  }

  // Check if setup is needed
  try {
    const setupRes = await fetch(API + '/setup/status').then(r => r.json());
    if (!setupRes.setup_complete) {
      _setupUser = user;
      document.getElementById('setup-screen').style.display = 'flex';
      setupInit();
      return;
    }

    // Setup done — check if initial collection is still running
    const collStatus = await fetch(API + '/setup/collection-status').then(r => r.json());
    if (!collStatus.all_complete && collStatus.total_repos > 0) {
      document.getElementById('collecting-screen').style.display = 'flex';
      pollCollectionProgress();
      return;
    }
  } catch (_) {}

  document.getElementById('app').style.display = 'block';
  document.getElementById('user-email').textContent = user.username || user.email || '';

  // Restore saved GitHub username
  const saved = localStorage.getItem('github_username');
  if (saved) {
    const input = document.getElementById('github-username-input');
    if (input) input.value = saved;
  }

  loadAll();
}

function renderLoginScreen(cfg) {
  const box = document.querySelector('.login-box');
  if (!box) return;

  const orgText = cfg.github_org ? `Members of @${cfg.github_org}` : '';

  if (cfg.mode === 'disabled') {
    // No auth — auto redirect
    location.reload();
    return;
  }

  if (cfg.mode === 'google') {
    // Google OAuth (legacy)
    const el = document.getElementById('login-domain');
    if (el && cfg.allowed_domain) el.textContent = '@' + cfg.allowed_domain;
    return;  // default HTML already shows Google button
  }

  // GitHub modes — replace login box content
  if (cfg.mode === 'github_web') {
    box.innerHTML = `
      <h1>vvere</h1>
      <p class="muted">${orgText}</p>
      <a href="/api/auth/login" class="github-btn">
        <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        Sign in with GitHub
      </a>
    `;
    return;
  }

  // Device Flow (default)
  box.innerHTML = `
    <h1>vvere</h1>
    <p class="muted">${orgText}</p>
    <button id="device-start-btn" class="github-btn" onclick="startDeviceFlow()">
      <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
      Sign in with GitHub
    </button>
    <div id="device-flow-ui" style="display:none">
      <p>1. Open <a href="https://github.com/login/device" target="_blank" rel="noopener">github.com/login/device</a></p>
      <p>2. Enter code:</p>
      <div class="device-code" id="device-code">----</div>
      <button class="copy-btn" onclick="copyDeviceCode()">Copy</button>
      <p class="muted" id="device-status">Waiting for approval...</p>
    </div>
  `;
}

function copyDeviceCode() {
  const text = document.getElementById('device-code').textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      const btn = document.querySelector('.copy-btn');
      if (btn) { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 1500); }
    }).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
  const btn = document.querySelector('.copy-btn');
  if (btn) { btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 1500); }
}

async function startDeviceFlow() {
  const btn = document.getElementById('device-start-btn');
  if (btn) btn.style.display = 'none';

  const res = await fetch(API + '/auth/device/start', { method: 'POST' });
  const data = await res.json();

  if (data.error) {
    document.getElementById('device-status').textContent = data.error;
    return;
  }

  document.getElementById('device-flow-ui').style.display = 'block';
  document.getElementById('device-code').textContent = data.user_code;

  // Poll for approval
  const interval = (data.interval || 5) * 1000;
  const pollDevice = async () => {
    const pr = await fetch(API + '/auth/device/poll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ device_code: data.device_code }),
    });
    const result = await pr.json();

    if (result.status === 'ok') {
      // Store token for setup wizard GitHub API calls
      if (result.access_token) {
        sessionStorage.setItem('gh_access_token', result.access_token);
      }
      location.reload();
      return;
    }
    if (result.status === 'denied') {
      document.getElementById('device-status').textContent = result.error;
      return;
    }
    if (result.status === 'expired') {
      document.getElementById('device-status').textContent = 'Code expired. Refresh to try again.';
      return;
    }
    if (result.status === 'error') {
      document.getElementById('device-status').textContent = 'Error: ' + result.error;
      return;
    }

    // pending or slow_down — keep polling
    const nextInterval = result.status === 'slow_down' ? (result.interval || 10) * 1000 : interval;
    setTimeout(pollDevice, nextInterval);
  };

  setTimeout(pollDevice, interval);
}

// ---------------------------------------------------------------------------
// Shared state
let allRepos = [];
let rateData = {};    // repo_id -> ci-success-rate response
let durationData = {}; // repo_id -> ci-duration response
let currentRateTab = '7d';
let currentDurationTab = '7d';

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function repoLink(repo) {
  return `<a href="${repo.html_url}" target="_blank" rel="noopener">${repo.name}</a>`;
}

function repoLinkStr(repoFullName) {
  // For cases where we only have "owner/name" string (prs, branches endpoints)
  const name = repoFullName.split('/')[1];
  const url = `https://github.com/${repoFullName}`;
  return `<a href="${url}" target="_blank" rel="noopener">${name}</a>`;
}

function fmtDuration(seconds) {
  if (!seconds) return '-';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function fmtDays(days) {
  if (days === null || days === undefined) return '-';
  if (days < 1) return `${Math.round(days * 24)}h`;
  return `${days.toFixed(1)}d`;
}

function fmtDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toISOString().slice(0, 10);
}

function conclusionBadge(c, url) {
  if (!c) return `<span class="badge unknown">-</span>`;
  const map = { success: 'success', failure: 'failure', cancelled: 'cancelled', timed_out: 'failure' };
  const cls = map[c] || 'unknown';
  const badge = `<span class="badge ${cls}">${c}</span>`;
  return url ? `<a href="${url}" target="_blank" rel="noopener">${badge}</a>` : badge;
}

function rateColor(rate) {
  if (rate === null || rate === undefined) return '#4b5563';
  if (rate >= 90) return '#22c55e';
  if (rate >= 70) return '#f59e0b';
  return '#ef4444';
}

function rateBar(rate) {
  if (rate === null || rate === undefined) return '<span class="muted">-</span>';
  const color = rateColor(rate);
  return `
    <div class="rate-bar">
      <div class="rate-track">
        <div class="rate-fill" style="width:${rate}%;background:${color}"></div>
      </div>
      <span style="color:${color};font-weight:600;min-width:38px;text-align:right">${rate}%</span>
    </div>`;
}

const TRIGGER_ORDER = ['push', 'schedule', 'pull_request', 'workflow_dispatch'];
const TRIGGER_LABEL = { push: 'main push', schedule: 'nightly', pull_request: 'PR', workflow_dispatch: 'dispatch' };

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function fetchJSON(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Section renderers
// ---------------------------------------------------------------------------

async function loadOverview() {
  const data = await fetchJSON('/overview');
  document.getElementById('stat-repos').textContent = data.repo_count;
  document.getElementById('stat-open-prs').textContent = data.open_prs;
  // open_automated_prs removed
  document.getElementById('stat-velocity').textContent = data.velocity_per_day ?? '-';
  document.getElementById('last-updated').textContent =
    data.last_collected_at ? `Last collected: ${fmtDate(data.last_collected_at)}` : 'Never collected';
}

async function loadCIStatus() {
  const el = document.getElementById('ci-status-table');
  if (!allRepos.length) { el.innerHTML = '<div class="empty">No repos</div>'; return; }

  const statuses = await Promise.all(
    allRepos.map(r => fetchJSON(`/repos/${r.id}/ci-status`).then(s => ({ repo: r, status: s })))
  );

  const triggers = ['push', 'schedule', 'pull_request', 'workflow_dispatch'];

  let html = `<table>
    <thead><tr>
      <th>Repo</th>
      ${triggers.map(t => `<th>${TRIGGER_LABEL[t]}</th>`).join('')}
    </tr></thead><tbody>`;

  for (const { repo, status } of statuses) {
    html += `<tr><td class="repo-name">${repoLink(repo)}</td>`;
    for (const t of triggers) {
      const s = status[t];
      if (!s) { html += '<td><span class="muted">-</span></td>'; continue; }
      html += `<td>
        ${conclusionBadge(s.conclusion, s.run_url)}
        <div class="sub">${fmtDate(s.created_at)}</div>
      </td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadSuccessRates() {
  if (!allRepos.length) return;
  rateData = {};
  const results = await Promise.all(
    allRepos.map(r => fetchJSON(`/repos/${r.id}/ci-success-rate`).then(d => [r.id, d]))
  );
  results.forEach(([id, d]) => rateData[id] = d);
  renderSuccessRateTable(currentRateTab);
}

function renderSuccessRateTable(period) {
  const el = document.getElementById('success-rate-table');
  const triggers = ['push', 'schedule', 'pull_request'];

  let html = `<table><thead><tr>
    <th>Repo</th>
    ${triggers.map(t => `<th>${TRIGGER_LABEL[t]}</th>`).join('')}
  </tr></thead><tbody>`;

  for (const repo of allRepos) {
    const d = rateData[repo.id] || {};
    html += `<tr><td class="repo-name">${repoLink(repo)}</td>`;
    for (const t of triggers) {
      const val = d[t]?.[period]?.success_rate ?? null;
      const total = d[t]?.[period]?.total ?? 0;
      html += `<td>
        ${rateBar(val)}
        ${total ? `<div class="sub">${total} runs</div>` : ''}
      </td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadDurations() {
  if (!allRepos.length) return;
  durationData = {};
  const results = await Promise.all(
    allRepos.map(r => fetchJSON(`/repos/${r.id}/ci-duration`).then(d => [r.id, d]))
  );
  results.forEach(([id, d]) => durationData[id] = d);
  renderDurationTable(currentDurationTab);
}

function renderDurationTable(period) {
  const el = document.getElementById('duration-table');
  const triggers = ['push', 'schedule', 'pull_request'];
  const key = `avg_seconds_${period}`;

  let html = `<table><thead><tr>
    <th>Repo</th>
    ${triggers.map(t => `<th>${TRIGGER_LABEL[t]}</th>`).join('')}
  </tr></thead><tbody>`;

  for (const repo of allRepos) {
    const d = durationData[repo.id] || {};
    html += `<tr><td class="repo-name">${repoLink(repo)}</td>`;
    for (const t of triggers) {
      const secs = d[t]?.[key] ?? null;
      html += `<td>${fmtDuration(secs)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadPRSummary() {
  const el = document.getElementById('pr-summary');
  const rows = await fetchJSON('/prs/summary');
  if (!rows.length) { el.innerHTML = '<div class="empty">No open PRs</div>'; return; }

  let html = `<table><thead><tr>
    <th>Repo</th><th>Open</th><th>Avg age</th><th>Oldest</th>
  </tr></thead><tbody>`;
  for (const r of rows) {
    html += `<tr>
      <td class="repo-name">${repoLinkStr(r.repo)}</td>
      <td>${r.open_count}</td>
      <td>${fmtDays(r.avg_age_days)}</td>
      <td>
        ${r.oldest_pr_url
          ? `<a href="${r.oldest_pr_url}" target="_blank">#${r.oldest_pr_number}</a> <span class="sub">(${fmtDays(r.max_age_days)})</span>`
          : '-'}
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadStagingBranches() {
  const el = document.getElementById('staging-branches');
  const rows = await fetchJSON('/branches/staging');
  if (!rows.length) { el.innerHTML = '<div class="empty">No staging branches</div>'; return; }

  let html = `<table><thead><tr>
    <th>Repo</th><th>Branch</th><th>Age</th><th>CI</th>
  </tr></thead><tbody>`;
  for (const r of rows) {
    const ciLink = conclusionBadge(r.latest_ci?.conclusion ?? null, r.latest_ci?.run_url);
    html += `<tr>
      <td class="repo-name">${repoLinkStr(r.repo)}</td>
      <td><code>${r.branch}</code></td>
      <td>${fmtDays(r.age_days)}</td>
      <td>${ciLink}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadPersonalStats() {
  const el = document.getElementById('personal-stats');
  const input = document.getElementById('github-username-input');
  const username = input?.value.trim() || localStorage.getItem('github_username') || '';
  if (!username) {
    el.innerHTML = '<div class="empty">GitHub username을 입력하세요.</div>';
    return;
  }
  localStorage.setItem('github_username', username);

  const data = await fetchJSON(`/personal?author=${encodeURIComponent(username)}`);
  const { authored, review_requested } = data;

  function oldestLink(item) {
    if (!item) return '<span class="muted">-</span>';
    return `<a href="${item.url}" target="_blank">#${item.number} ${item.title}</a>
            <span class="sub">${item.repo.split('/')[1]} · ${fmtDays(item.age_days)}</span>`;
  }

  el.innerHTML = `
    <div class="personal-grid">
      <div class="personal-stat">
        <div class="stat-value">${authored.count}</div>
        <div class="stat-label">My open PRs</div>
        <div class="personal-oldest">${oldestLink(authored.oldest)}</div>
      </div>
      <div class="personal-stat">
        <div class="stat-value">${review_requested.count}</div>
        <div class="stat-label">Review requested</div>
        <div class="personal-oldest">${oldestLink(review_requested.oldest)}</div>
      </div>
    </div>`;
}

// Stale branches state
let staleSortOrder = 'desc';  // newest first by default
let stalePage = 1;
const stalePerPage = 20;

function toggleStaleSort() {
  staleSortOrder = staleSortOrder === 'desc' ? 'asc' : 'desc';
  stalePage = 1;
  const btn = document.getElementById('stale-sort-btn');
  if (btn) btn.textContent = staleSortOrder === 'desc' ? '↓ Newest' : '↑ Oldest';
  loadStaleBranches();
}

async function loadStaleBranches() {
  const el = document.getElementById('stale-branches');
  const pagEl = document.getElementById('stale-pagination');

  const data = await fetchJSON(
    `/branches/stale?page=${stalePage}&per_page=${stalePerPage}&order=${staleSortOrder}`
  );

  // Total for summary bar (always reflects full count)
  document.getElementById('stat-stale').textContent = data.total;

  if (!data.total) {
    el.innerHTML = '<div class="empty">No stale branches</div>';
    pagEl.style.display = 'none';
    return;
  }

  let html = `<table><thead><tr>
    <th>Repo</th><th>Branch</th><th>Last commit</th><th>Open PR</th>
  </tr></thead><tbody>`;
  for (const r of data.items) {
    const prCell = r.pr
      ? `<a href="${r.pr.url}" target="_blank" rel="noopener">#${r.pr.number} ${r.pr.title}</a>`
      : '<span class="muted">-</span>';
    html += `<tr class="stale-row">
      <td class="repo-name">${repoLinkStr(r.repo)}</td>
      <td><code>${r.branch}</code></td>
      <td>${fmtDate(r.last_commit_at)} <span class="sub">(${fmtDays(r.stale_days)} stale)</span></td>
      <td>${prCell}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  el.innerHTML = html;

  // Pagination controls
  const totalPages = Math.ceil(data.total / stalePerPage);
  if (totalPages <= 1) {
    pagEl.style.display = 'none';
  } else {
    pagEl.style.display = 'block';
    pagEl.innerHTML = `
      <button onclick="staleGoPage(${stalePage - 1})" ${stalePage <= 1 ? 'disabled' : ''}>&#8249; Prev</button>
      <span style="margin:0 10px">Page ${stalePage} / ${totalPages}</span>
      <button onclick="staleGoPage(${stalePage + 1})" ${stalePage >= totalPages ? 'disabled' : ''}>Next &#8250;</button>
    `;
  }
}

function staleGoPage(p) {
  stalePage = p;
  loadStaleBranches();
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function switchRateTab(period, btn) {
  currentRateTab = period;
  document.querySelectorAll('#success-rate-table').forEach(() => {});
  btn.closest('.card').querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  renderSuccessRateTable(period);
}

function switchDurationTab(period, btn) {
  currentDurationTab = period;
  btn.closest('.card').querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  renderDurationTable(period);
}

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------

async function triggerRefresh() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  try {
    await fetch(API + '/refresh', { method: 'POST' });
    // Wait a couple seconds for collector to start, then reload data
    await new Promise(r => setTimeout(r, 2000));
    await loadAll();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Refresh';
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function loadRecommendations() {
  try {
    const data = await fetchJSON('/recommend');
    const panel = document.getElementById('recommend-panel');
    const alertsEl = document.getElementById('recommend-alerts');
    const insightEl = document.getElementById('recommend-insight');

    let hasContent = false;

    // Rule alerts
    if (data.alerts && data.alerts.length > 0) {
      hasContent = true;
      alertsEl.innerHTML = data.alerts.map(a => `
        <div class="rec-alert ${a.level}">
          <div class="rec-level">${a.level}</div>
          <div><strong>Signal:</strong> ${a.signal}</div>
          <div><strong>Action:</strong> ${a.action}</div>
        </div>
      `).join('');
    } else {
      alertsEl.innerHTML = '';
    }

    // LLM insight
    if (data.insight) {
      hasContent = true;
      const r = data.insight;
      insightEl.innerHTML = `
        <div class="rec-insight">
          <div class="rec-row"><span class="tag tag-signal">Signal</span><span>${r.signal}</span></div>
          <div class="rec-row"><span class="tag tag-action">Action</span><span>${r.action}</span></div>
          <div class="rec-row"><span class="tag tag-tobe">To-be</span><span>${r.to_be}</span></div>
        </div>
      `;
    } else if (!data.llm_available) {
      insightEl.innerHTML = '<div class="rec-no-llm">Set ANTHROPIC_API_KEY for AI-powered insights</div>';
      hasContent = true;
    } else {
      insightEl.innerHTML = '';
    }

    panel.style.display = hasContent ? 'block' : 'none';
  } catch (e) {
    console.error('Recommendations load error:', e);
  }
}

async function loadAll() {
  try {
    allRepos = await fetchJSON('/repos');
    await Promise.all([
      loadOverview(),
      loadCIStatus(),
      loadSuccessRates(),
      loadDurations(),
      loadPRSummary(),
      loadStagingBranches(),
      loadStaleBranches(),
      loadPersonalStats(),
      loadRecommendations(),
    ]);
  } catch (e) {
    console.error('Dashboard load error:', e);
  }
}

// ---------------------------------------------------------------------------
// Collection progress polling
// ---------------------------------------------------------------------------

async function pollCollectionProgress() {
  const statusEl = document.getElementById('collecting-status');
  const reposEl = document.getElementById('collecting-repos');
  const progressEl = document.getElementById('collecting-progress');

  const update = async () => {
    try {
      const data = await fetch(API + '/setup/collection-status').then(r => r.json());

      const pct = data.total_repos > 0 ? Math.round(data.repos_complete / data.total_repos * 100) : 0;
      progressEl.style.width = pct + '%';
      statusEl.textContent = `${data.repos_complete} / ${data.total_repos} repos collected (${pct}%)`;

      reposEl.innerHTML = data.repos.map(r => {
        const dotClass = r.complete ? 'done' : (r.runs || r.prs ? 'active' : 'pending');
        const status = r.complete ? 'done' : (r.runs || r.prs ? 'collecting...' : 'waiting');
        return `<div class="collecting-repo"><div class="dot ${dotClass}"></div><span>${r.repo}</span><span class="muted" style="margin-left:auto">${status}</span></div>`;
      }).join('');

      if (data.all_complete) {
        // Collection done — switch to dashboard
        document.getElementById('collecting-screen').style.display = 'none';
        document.getElementById('app').style.display = 'block';
        loadAll();
        return;
      }
    } catch (_) {}

    setTimeout(update, 5000);
  };

  update();
}

boot();

// Auto-refresh every 5 minutes (only if already authenticated)
setInterval(() => {
  if (document.getElementById('app').style.display !== 'none') {
    loadAll();
  }
}, 5 * 60 * 1000);

// ---------------------------------------------------------------------------
// Setup Wizard
// ---------------------------------------------------------------------------

let _setupUser = null;
let _setupStep = 0;  // 0=org, 1=repos, 2=period, 3=confirm
let _setupOrg = '';
let _setupRepos = [];
let _setupToken = '';  // the user's OAuth access token from device flow

const SETUP_STEPS = ['setup-step-org', 'setup-step-repos', 'setup-step-period', 'setup-step-confirm'];

function _getSessionToken() {
  // The device flow stored the token in a cookie — we need it for GitHub API calls
  // We'll use a workaround: call /api/auth/me won't give us the token, but
  // the setup endpoints proxy the GitHub API calls using the Authorization header
  // So we need the raw token. For device flow, we store it in sessionStorage.
  return sessionStorage.getItem('gh_access_token') || '';
}

async function setupInit() {
  _setupStep = 0;
  _setupToken = _getSessionToken();

  if (!_setupToken) {
    // No token — need to re-authenticate
    const wizard = document.querySelector('.setup-wizard');
    wizard.innerHTML = `
      <h1>vvere setup</h1>
      <p>Session token expired. Please sign in again to continue setup.</p>
      <button class="btn-primary" onclick="reAuthForSetup()" style="margin-top:16px">Sign in with GitHub</button>
      <div id="setup-reauth-device" style="display:none;margin-top:16px">
        <p>1. Open <a href="https://github.com/login/device" target="_blank" rel="noopener">github.com/login/device</a></p>
        <p>2. Enter code:</p>
        <div class="device-code" id="device-code-setup">----</div>
        <button class="copy-btn" onclick="copySetupCode()">Copy</button>
        <p class="muted" id="setup-reauth-status">Waiting for approval...</p>
      </div>
    `;
    return;
  }

  updateSetupNav();
  await loadSetupOrgs();
}

async function reAuthForSetup() {
  const btn = event.target;
  btn.style.display = 'none';

  const res = await fetch(API + '/auth/device/start', { method: 'POST' });
  const data = await res.json();
  if (data.error) {
    document.getElementById('setup-reauth-status').textContent = data.error;
    return;
  }

  document.getElementById('setup-reauth-device').style.display = 'block';
  document.getElementById('device-code-setup').textContent = data.user_code;

  const interval = (data.interval || 5) * 1000;
  const poll = async () => {
    const pr = await fetch(API + '/auth/device/poll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ device_code: data.device_code }),
    });
    const result = await pr.json();
    if (result.status === 'ok') {
      if (result.access_token) sessionStorage.setItem('gh_access_token', result.access_token);
      location.reload();
      return;
    }
    if (result.status === 'expired' || result.status === 'error' || result.status === 'denied') {
      document.getElementById('setup-reauth-status').textContent = result.error || 'Failed. Refresh to retry.';
      return;
    }
    setTimeout(poll, result.status === 'slow_down' ? (result.interval || 10) * 1000 : interval);
  };
  setTimeout(poll, interval);
}

function copySetupCode() {
  const text = document.getElementById('device-code-setup').textContent;
  fallbackCopy(text);
}

function updateSetupNav() {
  document.getElementById('setup-back-btn').style.display = _setupStep > 0 ? 'inline-flex' : 'none';
  const nextBtn = document.getElementById('setup-next-btn');
  nextBtn.textContent = _setupStep === 3 ? 'Complete setup' : 'Next';

  SETUP_STEPS.forEach((id, i) => {
    document.getElementById(id).style.display = i === _setupStep ? 'block' : 'none';
  });
}

async function setupNext() {
  if (_setupStep === 0) {
    if (!_setupOrg) { alert('Select an organization'); return; }
    _setupStep = 1;
    updateSetupNav();
    await loadSetupRepos();
  } else if (_setupStep === 1) {
    const checked = document.querySelectorAll('#setup-repo-list input:checked');
    _setupRepos = Array.from(checked).map(c => c.value);
    if (_setupRepos.length === 0) { alert('Select at least one repo'); return; }
    _setupStep = 2;
    updateSetupNav();
  } else if (_setupStep === 2) {
    _setupStep = 3;
    renderSetupSummary();
    updateSetupNav();
  } else if (_setupStep === 3) {
    await completeSetup();
  }
}

function setupBack() {
  if (_setupStep > 0) {
    _setupStep--;
    updateSetupNav();
  }
}

async function loadSetupOrgs() {
  const el = document.getElementById('setup-org-list');
  const token = _setupToken;

  try {
    const res = await fetch(API + '/setup/orgs', {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
      credentials: 'include',
    });
    const orgs = await res.json();

    if (!Array.isArray(orgs) || orgs.length === 0) {
      el.innerHTML = '<div style="padding:14px;color:var(--muted)">No organizations found. Make sure your GitHub account belongs to an org.</div>';
      return;
    }

    el.innerHTML = orgs.map(o => `
      <div class="setup-org-item" onclick="selectOrg(this, '${o.login}')" data-org="${o.login}">
        <img src="${o.avatar_url}" alt="">
        <span>${o.login}</span>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div style="padding:14px;color:var(--red)">Failed to load orgs</div>';
  }
}

function selectOrg(el, org) {
  document.querySelectorAll('.setup-org-item').forEach(e => e.classList.remove('selected'));
  el.classList.add('selected');
  _setupOrg = org;
}

async function loadSetupRepos() {
  const el = document.getElementById('setup-repo-list');
  el.innerHTML = '<div class="loading">Loading repos...</div>';
  const token = _setupToken;

  try {
    const res = await fetch(API + '/setup/repos?org=' + _setupOrg, {
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
      credentials: 'include',
    });
    const repos = await res.json();

    if (!Array.isArray(repos) || repos.length === 0) {
      el.innerHTML = '<div style="padding:14px;color:var(--muted)">No repos found</div>';
      return;
    }

    el.innerHTML = repos.map(r => {
      const desc = r.description ? ` — ${r.description}` : '';
      const lang = r.language || '';
      return `
        <label class="setup-repo-item">
          <input type="checkbox" value="${r.full_name}" onchange="filterSetupRepos()">
          <span>${r.name}<span class="muted" style="font-size:11px">${desc}</span></span>
          <span class="repo-meta">${lang}</span>
        </label>
      `;
    }).join('');
    filterSetupRepos();
  } catch (e) {
    el.innerHTML = '<div style="padding:14px;color:var(--red)">Failed to load repos</div>';
  }
}

let _repoFilterTimer = null;
function debouncedFilterRepos() {
  clearTimeout(_repoFilterTimer);
  _repoFilterTimer = setTimeout(filterSetupRepos, 200);
}

function filterSetupRepos() {
  const q = (document.getElementById('setup-repo-search').value || '').toLowerCase();
  const list = document.getElementById('setup-repo-list');
  const items = Array.from(list.querySelectorAll('.setup-repo-item'));

  if (!q) {
    // Empty query — show all in original order
    items.forEach(item => item.style.display = 'flex');
    items.forEach(item => list.appendChild(item)); // restore DOM order
  } else {
    // Split into prefix matches and substring matches
    const prefix = [];
    const substring = [];
    const noMatch = [];

    items.forEach(item => {
      const name = (item.querySelector('input')?.value || '').split('/').pop().toLowerCase();
      if (name.startsWith(q)) {
        prefix.push(item);
      } else if (name.includes(q) || (item.textContent || '').toLowerCase().includes(q)) {
        substring.push(item);
      } else {
        noMatch.push(item);
      }
    });

    // Reorder DOM: prefix first, then substring, hide rest
    [...prefix, ...substring].forEach(item => {
      item.style.display = 'flex';
      list.appendChild(item);
    });
    noMatch.forEach(item => {
      item.style.display = 'none';
      list.appendChild(item);
    });
  }

  const visible = items.filter(i => i.style.display !== 'none').length;
  const checked = list.querySelectorAll('input:checked').length;
  const countEl = document.getElementById('setup-repo-count');
  if (countEl) countEl.textContent = `${checked} selected / ${visible} shown`;
}

function setupSelectAll() {
  document.querySelectorAll('#setup-repo-list .setup-repo-item').forEach(item => {
    if (item.style.display !== 'none') {
      const cb = item.querySelector('input');
      if (cb) cb.checked = true;
    }
  });
  filterSetupRepos();
}
function setupDeselectAll() {
  document.querySelectorAll('#setup-repo-list .setup-repo-item').forEach(item => {
    if (item.style.display !== 'none') {
      const cb = item.querySelector('input');
      if (cb) cb.checked = false;
    }
  });
  filterSetupRepos();
}

function renderSetupSummary() {
  const period = document.querySelector('input[name="period"]:checked')?.value || '30';
  const periodLabel = {'7':'7 days','30':'1 month','90':'3 months','180':'6 months','365':'1 year'}[period] || period + ' days';

  document.getElementById('setup-summary').innerHTML = `
    <div><span class="label">Organization:</span> <span class="val">${_setupOrg}</span></div>
    <div><span class="label">Repos:</span> <span class="val">${_setupRepos.length} selected</span></div>
    <div style="font-size:11px;color:var(--muted);margin-left:16px">${_setupRepos.join(', ')}</div>
    <div><span class="label">Fetch period:</span> <span class="val">${periodLabel}</span></div>
  `;
}

async function completeSetup() {
  const btn = document.getElementById('setup-next-btn');
  btn.disabled = true;
  btn.textContent = 'Setting up...';

  const period = parseInt(document.querySelector('input[name="period"]:checked')?.value || '30');
  const token = _setupToken;

  try {
    const res = await fetch(API + '/setup/complete', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      },
      credentials: 'include',
      body: JSON.stringify({
        github_token: token,
        github_org: _setupOrg,
        repos: _setupRepos,
        username: _setupUser?.username || '',
        initial_fetch_days: period,
      }),
    });

    const data = await res.json();
    if (data.ok) {
      // Setup complete — reload to show dashboard
      location.reload();
    } else {
      alert('Setup failed: ' + (data.error || 'Unknown error'));
      btn.disabled = false;
      btn.textContent = 'Complete setup';
    }
  } catch (e) {
    alert('Setup failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Complete setup';
  }
}
