'use strict';
// Update banner, "Updating Story Studio…" overlay, version label and "Check for updates", shared by the
// home page and the editor. Global endpoints are absolute (/api/...), so this works under /s/<id>/ too.
window.StoryUpdate = (() => {
  const BUSY = ['checking', 'downloading', 'verifying'];
  const HIDE_KEY = 'story-studio:update-hidden'; // sessionStorage: "×" hides the banner in this tab until it is closed.
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let opts = null, state = null, timer = null, force = false, overlay = null, waiting = false, notesOpen = false, shown = '';
  let seen = '', unreachable = false; // seen: the app version this page was loaded with.
  // Gives the page a chance to save (the editor flushes pending saves); never blocks the update.
  const prepare = () => Promise.resolve().then(() => opts.beforeInstall?.()).catch(() => {});
  const reload = () => { opts.beforeReload?.(); location.reload(); };

  /* ---------- Release notes: escaped first, then a small, safe markdown subset ---------- */
  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
      .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }
  function markdown(source) {
    const out = [], paragraph = [];
    let list = null;
    const flush = () => {
      if (paragraph.length) out.push(`<p>${inline(paragraph.join(' '))}</p>`);
      paragraph.length = 0;
      if (list) { out.push(`<${list.tag}>${list.items.map(item => `<li>${inline(item)}</li>`).join('')}</${list.tag}>`); list = null; }
    };
    for (const line of esc(source).replace(/\r\n?/g, '\n').split('\n')) {
      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/), item = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
      if (heading) { flush(); const level = Math.min(6, heading[1].length + 2); out.push(`<h${level}>${inline(heading[2])}</h${level}>`); }
      else if (item) {
        const tag = /\d/.test(item[1]) ? 'ol' : 'ul';
        if (paragraph.length || list?.tag !== tag) flush();
        list ||= {tag, items:[]}; list.items.push(item[2]);
      }
      else if (!line.trim()) flush();
      else if (list && /^\s+\S/.test(line)) list.items[list.items.length - 1] += ' ' + line.trim();
      else { if (list) flush(); paragraph.push(line.trim()); }
    }
    flush();
    return out.join('');
  }

  /* ---------- Server ---------- */
  async function token(refresh = false) {
    let value = !refresh && opts.token?.();
    if (!value) {
      const response = await fetch('/api/stories', {cache:'no-store', signal:AbortSignal.timeout(8000)});
      value = (await response.json()).token;
      opts.setToken?.(value);
    }
    return value;
  }
  async function request(path, body) {
    const send = async refresh => fetch(path, body === undefined ? {cache:'no-store', signal:AbortSignal.timeout(8000)} : {
      method:'POST', cache:'no-store', signal:AbortSignal.timeout(30000),
      headers:{'Content-Type':'application/json', 'X-Story-Token':await token(refresh)}, body:JSON.stringify(body)
    });
    let response;
    try {
      response = await send(false);
      if (response.status === 403 && body !== undefined) response = await send(true); // Story Studio restarted: new token.
    } catch { throw Error('Story Studio isn’t responding. Is it still running?'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || `Story Studio answered ${response.status}.`);
    return data;
  }
  function schedule() {
    clearTimeout(timer);
    if (!waiting) timer = setTimeout(poll, BUSY.includes(state?.state) ? 500 : unreachable ? 3000 : 20000);
  }
  async function poll() {
    try { const next = await request('/api/update'); unreachable = false; apply(next); }
    catch { unreachable = true; if (!waiting && state) { state = null; render(); } } // A stale offer would only fail.
    schedule();
  }

  /* ---------- Banner ---------- */
  function hidden(version) { try { return sessionStorage.getItem(HIDE_KEY) === version; } catch { return false; } }
  function apply(next) {
    const was = state?.state;
    // Updated while this page was not watching (another tab installed it): this page's code is outdated.
    if (seen && next.current !== seen) { reload(); return; }
    seen ||= next.current;
    state = next;
    document.querySelectorAll('[data-app-version]').forEach(el => { el.textContent = 'Story Studio ' + next.current; });
    if (next.state === 'installing') { showOverlay(); return; }
    if (next.state === 'downloading' && was !== 'downloading') prepare();
    render();
  }
  function render() {
    const banner = opts.banner, s = state;
    if (!s) { banner.hidden = true; banner.innerHTML = shown = ''; return; }
    const busy = s && ['downloading', 'verifying'].includes(s.state);
    const show = s && s.available && (busy || s.state === 'error' || force || (!s.skipped && !hidden(s.latest)));
    banner.hidden = !show;
    if (!show) { banner.innerHTML = shown = ''; return; }
    const percent = Math.round((s.progress || 0) * 100);
    let message, actions = '';
    if (s.state === 'downloading') message = `<strong>Downloading Story Studio ${esc(s.latest)}…</strong> <span class="update-percent">${percent}%</span>`;
    else if (s.state === 'verifying') message = '<strong>Checking the download…</strong>';
    else if (s.state === 'error') {
      message = `<strong>Story Studio ${esc(s.latest)} could not be installed.</strong> ${esc(s.error || 'Something went wrong.')} Your stories are not affected.`;
      actions = '<button type="button" class="primary" data-update="install">Try again</button><button type="button" data-update="skip">Skip this version</button>';
    } else {
      message = `<strong>Story Studio ${esc(s.latest)} is available.</strong> <span class="hint">You have ${esc(s.current)}.${s.skipped ? ' You skipped this version earlier.' : ''}</span>`;
      actions = '<button type="button" class="primary" data-update="install">Install and restart</button><button type="button" data-update="skip">Skip this version</button>';
    }
    const notes = (s.notes || '').trim();
    const html = `<div class="update-row">
        <p class="update-message" role="status">${message}</p>
        <div class="update-actions">${notes ? `<button type="button" class="link" data-update="notes" aria-expanded="${notesOpen}" aria-controls="update-notes">${notesOpen ? 'Hide what’s new' : 'What’s new'}</button>` : ''}${actions}</div>
      </div>
      ${busy ? '' : '<button type="button" class="icon update-hide" data-update="hide" aria-label="Hide this message" title="Hide until next time">×</button>'}
      ${busy ? `<progress class="update-progress" ${s.state === 'downloading' ? `max="100" value="${percent}"` : ''} aria-label="Update progress"></progress>` : ''}
      ${notes ? `<div id="update-notes" class="update-notes" ${notesOpen ? '' : 'hidden'}>${markdown(notes)}</div>` : ''}`;
    if (html !== shown) banner.innerHTML = shown = html; // Unchanged polls must not steal keyboard focus.
  }
  async function act(action) {
    if (action === 'notes') { notesOpen = !notesOpen; render(); return; }
    if (action === 'hide') { try { sessionStorage.setItem(HIDE_KEY, state.latest); } catch {} force = false; render(); return; }
    const buttons = opts.banner.querySelectorAll('button[data-update]');
    buttons.forEach(b => { b.disabled = true; });
    try {
      if (action === 'skip') {
        const version = state.latest;
        force = false;
        apply(await request('/api/update/skip', {version}));
        opts.notify?.(`Story Studio ${version} will not be offered again. Check for updates shows it anyway.`);
      } else if (action === 'install') {
        await prepare();
        apply(await request('/api/update/install', {}));
      }
    } catch (error) {
      buttons.forEach(b => { b.disabled = false; });
      opts.notify?.('Could not ' + (action === 'skip' ? 'skip this version' : 'start the update') + ': ' + error.message, true);
    }
    schedule();
  }

  /* ---------- "Updating Story Studio…" ---------- */
  function showOverlay() {
    if (waiting) return;
    waiting = true; clearTimeout(timer);
    opts.banner.hidden = true;
    const from = state.current, started = Date.now();
    overlay = document.createElement('dialog');
    overlay.className = 'update-overlay';
    overlay.setAttribute('aria-labelledby', 'update-overlay-title');
    overlay.innerHTML = `<div class="update-spinner" aria-hidden="true"></div>
      <h2 id="update-overlay-title">Updating Story Studio…</h2>
      <p>Installing version ${esc(state.latest || '')}. Your stories are saved. This page reloads by itself when the new version is ready.</p>
      <p class="hint" id="update-overlay-status" role="status">Waiting for Story Studio to restart…</p>
      <p class="update-overlay-actions" hidden><button type="button" data-reload>Reload now</button></p>`;
    overlay.addEventListener('cancel', event => event.preventDefault());
    overlay.querySelector('[data-reload]').addEventListener('click', reload);
    document.body.append(overlay); overlay.showModal();
    prepare();
    const tick = async () => {
      try {
        const info = await (await fetch('/api/info', {cache:'no-store', signal:AbortSignal.timeout(2500)})).json();
        if (info.appVersion && info.appVersion !== from) {
          overlay.querySelector('#update-overlay-status').textContent = `Story Studio ${info.appVersion} is ready. Reloading…`;
          reload(); return;
        }
        const update = await request('/api/update');
        if (update.state === 'error') { // The old version is still running: the install failed.
          overlay.close(); overlay.remove(); waiting = false; apply(update); schedule(); return;
        }
      } catch { /* Not answering yet: expected while it restarts. */ }
      if (Date.now() - started > 90000) {
        overlay.querySelector('#update-overlay-status').textContent = 'This is taking longer than usual. If Story Studio does not come back, start it again from the Start menu. This page keeps waiting.';
        overlay.querySelector('.update-overlay-actions').hidden = false;
      }
      setTimeout(tick, 1000);
    };
    setTimeout(tick, 1000);
  }

  /* ---------- Public ---------- */
  async function check(button) {
    if (!opts) return;
    const label = button?.textContent;
    if (button) { button.disabled = true; button.textContent = 'Checking for updates…'; }
    try {
      const result = await request('/api/update/check', {});
      force = result.available; notesOpen = notesOpen || result.available;
      try { sessionStorage.removeItem(HIDE_KEY); } catch {}
      apply(result);
      if (result.state === 'error' && !result.available) opts.notify?.('Could not check for updates: ' + (result.error || 'unknown error') + '.', true);
      else if (result.available) opts.notify?.(`Story Studio ${result.latest} is available.`);
      else opts.notify?.(`You have the latest version, Story Studio ${result.current}.`);
    } catch (error) {
      opts.notify?.('Could not check for updates: ' + error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = label; }
      schedule();
    }
  }
  function start(options) {
    if (opts) return;
    opts = options;
    opts.banner.classList.add('update-banner');
    opts.banner.addEventListener('click', event => { const button = event.target.closest('[data-update]'); if (button) act(button.dataset.update); });
    document.querySelectorAll('[data-update-check]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => check(button));
    });
    poll();
  }
  return {start, check, markdown};
})();
