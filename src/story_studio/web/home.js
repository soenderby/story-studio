'use strict';
// Story Studio home page: the list of story folders, New story, Add existing folder, Remove (with Undo), Locate.
(() => {
  const HIDE_KEY = 'illustrated-story-studio:v1:hide-images'; // Shared with the editor's "Hide images".
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const storyURL = id => '/s/' + encodeURIComponent(id) + '/';
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const folderName = path => String(path).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
  let token = '', stories = [], loaded = false, offline = false, retryTimer = null, picking = false;
  let removal = null, noticeTimer = null, newPath = '', flashId = '';

  /* ---------- Server ---------- */
  async function api(path, body, timeout = 30000) {
    const send = () => fetch(path, body === undefined ? {cache:'no-store', signal:AbortSignal.timeout(10000)} : {
      method:'POST', cache:'no-store', headers:{'Content-Type':'application/json', 'X-Story-Token':token},
      body:JSON.stringify(body), ...(timeout ? {signal:AbortSignal.timeout(timeout)} : {})
    });
    let response;
    try {
      response = await send();
      if (response.status === 403 && body !== undefined) { await refreshToken(); response = await send(); } // Restarted: new token.
    } catch {
      throw Object.assign(Error('Story Studio isn’t responding. Is it still running?'), {offline:true});
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(Error(data.error || `Story Studio answered ${response.status}.`), {status:response.status});
    return data;
  }
  async function refreshToken() { token = (await (await fetch('/api/stories', {cache:'no-store'})).json()).token; }

  async function load() {
    clearTimeout(retryTimer);
    try {
      const data = await api('/api/stories');
      token = data.token; stories = data.stories.filter(s => s.id !== removal?.story.id);
      loaded = true; offline = false;
      render();
      window.StoryUpdate?.start({banner:$('update-banner'), token:() => token, setToken:value => { token = value; }, notify});
    } catch (error) {
      offline = true;
      $('offline-detail').textContent = error.offline ? '' : error.message;
      render();
      retryTimer = setTimeout(load, 3000);
    }
  }

  /* ---------- Rendering ---------- */
  function when(iso) {
    const date = iso && new Date(iso);
    if (!date || isNaN(date)) return 'Not opened yet';
    const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((day(new Date()) - day(date)) / 864e5);
    if (days <= 0) return 'Opened today at ' + date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    if (days === 1) return 'Opened yesterday';
    if (days < 7) return `Opened ${days} days ago`;
    return 'Opened ' + date.toLocaleDateString([], {day:'numeric', month:'short', ...(date.getFullYear() === new Date().getFullYear() ? {} : {year:'numeric'})});
  }
  function coverHTML(story) {
    const hide = document.body.classList.contains('hide-images');
    const placeholder = `<span class="cover-placeholder" aria-hidden="true">${esc((story.title || '?').trim().charAt(0).toUpperCase() || '?')}</span>`;
    if (!story.cover || story.missing) return placeholder;
    if (hide) return '<span class="cover-placeholder cover-hidden">Image hidden</span>';
    const src = esc(storyURL(story.id) + story.cover.split('/').map(encodeURIComponent).join('/'));
    return (/\.(mp4|m4v|mov|webm|ogv)$/i.test(story.cover)
      ? `<video src="${src}#t=1" muted playsinline preload="metadata" tabindex="-1"></video>`
      : `<img src="${src}" alt="" loading="lazy" decoding="async">`) + placeholder;
  }
  function cardHTML(story) {
    const id = esc(story.id), title = esc(story.title || 'Untitled story'), url = esc(storyURL(story.id));
    const stats = story.missing ? '' : ` · ${plural(story.chapters, 'chapter', 'chapters')} · ${plural(story.media, 'picture or video', 'pictures and videos')}`;
    return `<li class="story-card ${story.missing ? 'is-missing' : ''} ${story.id === flashId ? 'flash' : ''}" data-id="${id}">
      ${story.missing ? `<div class="story-cover">${coverHTML(story)}</div>` : `<a class="story-cover" href="${url}" tabindex="-1" aria-hidden="true">${coverHTML(story)}</a>`}
      <div class="story-body">
        <h2 class="story-title">${story.missing ? title : `<a href="${url}">${title}</a>`}</h2>
        <p class="story-path" title="${esc(story.path)}">${esc(story.path)}</p>
        ${story.missing
          ? '<p class="story-missing"><span class="missing-badge">Folder not found</span> Moved or renamed it? Locate the folder to reconnect this story.</p>'
          : `<p class="story-meta">${esc(when(story.lastOpened))}${stats}</p>`}
        <div class="story-actions">
          ${story.missing
            ? '<button type="button" class="primary" data-action="locate">Locate folder…</button>'
            : `<a class="button primary" href="${url}" aria-label="Open ${title}">Open</a><button type="button" data-action="reveal" title="Show this story’s folder in Explorer">Show in Explorer</button>`}
          <button type="button" class="quiet" data-action="remove" title="Removes the story from this list only. Its folder and files are not deleted.">Remove from list</button>
        </div>
        <p class="card-error" role="alert" hidden></p>
      </div>
    </li>`;
  }
  function render() {
    const empty = loaded && !offline && !stories.length;
    $('loading').hidden = loaded || offline;
    $('offline').hidden = !offline;
    $('home-heading').hidden = !loaded || offline || empty;
    $('welcome').hidden = !empty || !$('new-form').hidden;
    $('story-list').hidden = offline;
    $('story-list').innerHTML = offline ? '' : stories.map(cardHTML).join('');
    if (flashId) {
      $('story-list').querySelector(`[data-id="${CSS.escape(flashId)}"]`)?.scrollIntoView({block:'nearest', behavior:'smooth'});
      flashId = '';
    }
  }
  function cardError(id, message) {
    const error = $('story-list').querySelector(`[data-id="${CSS.escape(id)}"] .card-error`);
    if (error) { error.textContent = message; error.hidden = false; } else notify(message, true);
  }

  /* ---------- Notice (with Undo) ---------- */
  function notify(message, error = false, action = null) {
    if (!action?.keep) commitRemoval();
    clearTimeout(noticeTimer);
    $('notice-text').textContent = message;
    $('notice').classList.toggle('error', error); $('notice').hidden = false;
    $('notice-action').hidden = !action;
    if (action) { $('notice-action').textContent = action.label; $('notice-action').onclick = () => { clearTimeout(noticeTimer); $('notice').hidden = true; action.run(); }; }
    noticeTimer = setTimeout(hideNotice, error ? 12000 : action ? 10000 : 5000);
  }
  function hideNotice() { clearTimeout(noticeTimer); $('notice').hidden = true; commitRemoval(); }

  /* ---------- Folder dialog ---------- */
  function setPicking(on, text = '') {
    picking = on;
    $('pick-status').hidden = !on; $('pick-status-text').textContent = text;
    ['new-story', 'add-story', 'welcome-new', 'welcome-add', 'new-other', 'new-create'].forEach(id => { $(id).disabled = on; });
    $('story-list').querySelectorAll('[data-action="locate"]').forEach(b => { b.disabled = on; });
  }
  async function pickFolder(purpose) {
    if (picking) return null;
    setPicking(true, (purpose === 'new' ? 'Choose or create an empty folder for the new story' : 'Choose the story’s folder')
      + ' in the folder window that opened. It may be behind this browser window.');
    try {
      const result = await api('/api/stories/pick-folder', {purpose}, 0);
      return result.cancelled ? null : result.path;
    } catch (error) {
      notify('Could not open the folder window: ' + error.message, true); return null;
    } finally { setPicking(false); }
  }

  /* ---------- Actions ---------- */
  async function newStory() {
    const path = await pickFolder('new'); if (!path) return;
    newPath = path;
    $('new-path').textContent = path; $('new-title').value = ''; $('new-title').placeholder = folderName(path);
    $('new-error').hidden = true; $('new-form').hidden = false; render();
    $('new-form').scrollIntoView({block:'nearest'}); $('new-title').focus();
  }
  function closeNewForm() { $('new-form').hidden = true; newPath = ''; render(); ($('home-heading').hidden ? $('welcome-new') : $('new-story')).focus(); }
  async function createStory(event) {
    event.preventDefault();
    $('new-create').disabled = true; $('new-error').hidden = true;
    try {
      const title = $('new-title').value.trim();
      const {story} = await api('/api/stories', {path:newPath, ...(title ? {title} : {})});
      location.href = storyURL(story.id);
    } catch (error) {
      $('new-error').textContent = 'The story could not be created: ' + error.message; $('new-error').hidden = false;
      $('new-create').disabled = false;
    }
  }
  async function addStory() {
    const path = await pickFolder('add'); if (!path) return;
    try {
      const response = await fetchAdd(path);
      flashId = response.story.id;
      await load();
      notify(response.created ? `Added “${response.story.title}”.` : `“${response.story.title}” is already in your list.`);
    } catch (error) { notify('That folder could not be added: ' + error.message, true); }
  }
  async function fetchAdd(path) {
    const known = new Set(stories.map(s => s.id));
    const {story} = await api('/api/stories', {path});
    if (removal?.story.id === story.id) removal = null; // Added back while its removal was still undoable.
    return {story, created:!known.has(story.id)};
  }
  async function locate(id) {
    const story = stories.find(s => s.id === id); if (!story) return;
    const path = await pickFolder('add'); if (!path) return;
    try {
      const result = await api(`/api/stories/${encodeURIComponent(id)}/relocate`, {path});
      flashId = result.story.id;
      await load();
      notify(`Found it. “${result.story.title}” is connected to its new folder.`);
    } catch (error) { cardError(id, 'That folder did not work: ' + error.message); }
  }
  async function reveal(id) {
    try { await api(`/api/stories/${encodeURIComponent(id)}/reveal`, {}); notify('The folder opened in Explorer. It may be behind this browser window.'); }
    catch (error) { cardError(id, 'Could not show the folder: ' + error.message); }
  }
  // Removal waits until the Undo notice goes away (or the page closes), so Undo never needs the server.
  function removeStory(id) {
    const index = stories.findIndex(s => s.id === id); if (index < 0) return;
    commitRemoval();
    const story = stories[index];
    removal = {story, index};
    stories.splice(index, 1); render();
    const next = $('story-list').querySelectorAll('.story-card')[Math.min(index, stories.length - 1)];
    (next?.querySelector('.story-actions > *') || ($('home-heading').hidden ? $('welcome-new') : $('add-story'))).focus();
    notify(`Removed “${story.title}” from the list. Its folder and files were not deleted.`, false, {label:'Undo', keep:true, run:undoRemoval});
  }
  function undoRemoval() {
    if (!removal) return;
    const {story, index} = removal; removal = null;
    stories.splice(Math.min(index, stories.length), 0, story); render();
    $('story-list').querySelector(`[data-id="${CSS.escape(story.id)}"] .story-actions > *`)?.focus();
  }
  function commitRemoval(keepalive = false) {
    if (!removal) return;
    const {story} = removal; removal = null;
    const url = `/api/stories/${encodeURIComponent(story.id)}/remove`;
    if (keepalive) { fetch(url, {method:'POST', keepalive:true, headers:{'Content-Type':'application/json', 'X-Story-Token':token}, body:'{}'}).catch(() => {}); return; }
    api(url, {}).catch(error => { if (error.status !== 404) { notify(`“${story.title}” could not be removed: ${error.message}`, true); load(); } });
  }

  /* ---------- Hide images (global preference, shared with the editor) ---------- */
  function applyHideImages(on) {
    $('hide-images').checked = on;
    if (document.body.classList.contains('hide-images') === on) return;
    document.body.classList.toggle('hide-images', on); render();
  }

  /* ---------- Events ---------- */
  $('new-story').addEventListener('click', newStory);
  $('welcome-new').addEventListener('click', newStory);
  $('add-story').addEventListener('click', addStory);
  $('welcome-add').addEventListener('click', addStory);
  $('new-other').addEventListener('click', async () => {
    const path = await pickFolder('new'); if (!path) return;
    newPath = path; $('new-path').textContent = path; $('new-title').placeholder = folderName(path); $('new-title').focus();
  });
  $('new-cancel').addEventListener('click', closeNewForm);
  $('new-form').addEventListener('submit', createStory);
  $('new-form').addEventListener('keydown', event => { if (event.key === 'Escape') closeNewForm(); });
  $('retry').addEventListener('click', () => { $('offline-detail').textContent = 'Trying again…'; load(); });
  $('story-list').addEventListener('click', event => {
    const button = event.target.closest('[data-action]'), id = button?.closest('[data-id]')?.dataset.id;
    if (!button || !id || button.disabled) return;
    button.closest('.story-body').querySelector('.card-error').hidden = true;
    ({locate, reveal, remove:removeStory})[button.dataset.action]?.(id);
  });
  $('story-list').addEventListener('error', event => { // A cover that no longer loads falls back to the placeholder.
    if (event.target.matches('img, video')) event.target.remove();
  }, true);
  $('notice-close').addEventListener('click', hideNotice);
  $('settings-button').addEventListener('click', () => $('settings').showModal());
  $('hide-images').addEventListener('change', event => {
    try { localStorage.setItem(HIDE_KEY, String(event.target.checked)); } catch {}
    applyHideImages(event.target.checked);
  });
  window.addEventListener('storage', event => { if (event.key === HIDE_KEY) applyHideImages(event.newValue === 'true'); });
  window.addEventListener('pagehide', () => commitRemoval(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && loaded && !picking && !removal) load(); });
  document.querySelectorAll('.brand-icon, .welcome-icon').forEach(icon => { // icon.svg may be absent in development.
    const show = () => { icon.hidden = !icon.naturalWidth; };
    icon.addEventListener('load', show); if (icon.complete) show();
  });
  try { applyHideImages(localStorage.getItem(HIDE_KEY) === 'true'); } catch {}
  load();
})();
