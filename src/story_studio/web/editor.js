'use strict';
(() => {
  const DATA = window.STORY_EDITOR_DATA;
  const BASE_KEY = 'illustrated-story-studio:v1';
  // Each story's recovery copy is namespaced by the story id from /s/<id>/. UI preferences and "Hide images" stay global.
  // The file:// manual mode (and any page outside /s/<id>/) keeps the old un-namespaced key.
  const storyId = decodeURIComponent(location.pathname.match(/^\/s\/([^/]+)\//)?.[1] || '');
  const KEY = storyId ? BASE_KEY + ':' + storyId : BASE_KEY;
  const UI_KEY = BASE_KEY + ':ui', HIDE_KEY = BASE_KEY + ':hide-images';
  const SIZES = ['small', 'medium', 'large', 'wide'];
  const LAYOUTS = ['row', 'grid'], MAX_GROUP = 12;
  const SERVER_VERSION = 8; // 7: text blocks can carry comments. 8: PDF export. The editor runs under /s/<id>/ with relative API URLs.
  const COMMENT_LABELS = {note:'Note', change:'Change', question:'Question', keep:'Keep'}, MAX_COMMENTS = 500;
  const VIDEO_PATTERN = /\.(mp4|m4v|mov|webm|ogv)$/i;
  const VIDEO_PLACEHOLDER = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" fill="#2b302d"/><path d="M6 4.5v7l5.5-3.5z" fill="#fff"/></svg>');
  const SIZE_LABELS = {small:'S', medium:'M', large:'L', wide:'Wide'};
  const SIZE_NAMES = {small:'Small', medium:'Medium', large:'Large (default)', wide:'Wide, beyond the text column'};
  const $ = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const uid = () => 'id-' + crypto.randomUUID();
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fileURL = name => encodeURIComponent(name);
  const autosizeNative = typeof CSS !== 'undefined' && CSS.supports('field-sizing', 'content');
  const writer = uid();
  const library = new Map(DATA.images.map(image => [image.file, image]));
  let project, activeChapter, undoStack = [], redoStack = [], editGroup = null;
  let fileHandle = null, diskTimer, diskQueue = Promise.resolve(), diskPending = 0;
  let browserSaved = false, blocked = false, recoveryRaw = '', recoveryMode = '';
  let previewing = false, noticeTimer, selected = new Set(), drag = null;
  let diskSavedVersion = '', libraryCards = [];
  const serverMode = location.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(location.hostname);
  let server = null, serverDirty = false, serverPending = 0, serverQueue = Promise.resolve(), imageSignature = '';
  let polling = false, reloadingForUpdate = false;
  // Editing-surface state (not part of the project).
  let view = 'chapter';            // 'chapter' or 'title'
  let caret = null;                // {chapter, block, pos}: last cursor position in a text block
  let target = null;               // {chapter, index}: an explicitly marked insertion gap
  let pick = null;                 // {mode:'insert'|'replace', block, opened}
  let pendingFocus = null, pendingScroll = null;
  let lightbox = {list:[], index:0, from:'library'};
  let fileState = 'idle', savedAt = '';
  const ui = Object.assign({chapters:true, library:true, comments:false, commentScope:'chapter', commentMarks:true, readComments:false,
    thumb:'m', sort:'newest', libraryWidth:400}, readUI());
  let activeComment = null, showResolved = false, commentsTimer = null;

  function readUI() { try { return JSON.parse(localStorage.getItem(UI_KEY)) || {}; } catch { return {}; } }
  function saveUI() { try { localStorage.setItem(UI_KEY, JSON.stringify(ui)); } catch {} }
  function imageURL(file) {
    const stamp = metadata(file).stamp;
    return fileURL(file) + (serverMode && stamp ? '?v=' + encodeURIComponent(stamp) : '');
  }

  /* ---------- Status display ---------- */
  function fileStatus(text, state) {
    $('file-status').textContent = text;
    $('file-status').classList.toggle('error', state === 'error');
    fileState = state;
    if (state === 'ok') savedAt = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    renderSaveIndicator();
  }
  function browserStatus(text, error = false) {
    $('save-status').textContent = text;
    $('save-status').classList.toggle('error', error);
    renderSaveIndicator();
  }
  function renderSaveIndicator() {
    let state, label;
    const browserError = $('save-status').classList.contains('error');
    if (blocked) { state = 'error'; label = 'Saving paused'; }
    else if (serverMode || fileHandle) {
      state = fileState;
      label = {ok:'Saved' + (savedAt ? ' · ' + savedAt : ''), busy:'Saving…', error:'Not saved — details', idle:'Connecting…'}[state];
    } else if (browserError) { state = 'error'; label = 'Not saved — details'; }
    else { state = browserSaved ? 'ok' : 'idle'; label = browserSaved ? 'Saved in this browser only' : 'Not saved yet'; }
    $('save-indicator').dataset.state = state;
    $('save-short').textContent = label;
    $('save-indicator').title = $('file-status').textContent + '\n' + $('save-status').textContent;
  }

  /* ---------- Server persistence (unchanged behaviour) ---------- */
  async function responseJSON(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(body.error || 'Local server request failed.'), {status:response.status});
    return body;
  }
  function queueServerSave() {
    if (!server || blocked) return serverQueue;
    const payload = serialize();
    serverPending++;
    serverQueue = serverQueue.then(async () => {
      if (blocked || diskSavedVersion === payload) { serverPending--; return; }
      fileStatus('Saving automatically to story-project.json…', 'busy');
      try {
        const result = await responseJSON(await fetch('api/project', {
          method:'POST', headers:{'Content-Type':'application/json', 'X-Story-Token':server.token},
          body:JSON.stringify({project:JSON.parse(payload), revision:server.revision}), signal:AbortSignal.timeout(10000)
        }));
        server.revision = result.revision; diskSavedVersion = payload;
        serverDirty = payload !== serialize();
        if (serverDirty) fileStatus('Changes pending · story-project.json', 'busy');
        else fileStatus('Automatically saved · story-project.json', 'ok');
        saveBrowser();
      } catch (error) {
        serverDirty = true;
        if (error.status === 409) {
          recovery('server-conflict', 'The project file changed in another tab, program or computer. Saving is paused to protect both versions. Download this tab’s recovery copy, then reload the saved project. You can import the recovery JSON if you want to replace it.', serialize());
        } else if (error.status === 403) {
          try {
            const fresh = await responseJSON(await fetch('api/bootstrap'));
            if (fresh.revision !== server.revision) throw Error('The project changed while the server restarted.');
            server.token = fresh.token;
          } catch {
            recovery('server-conflict', 'The local server restarted and its project changed. Download a recovery copy before reloading the saved project.', serialize());
          }
        } else {
          fileStatus('File save failed — retrying automatically; keep this tab open', 'error');
        }
      } finally { serverPending--; }
    });
    return serverQueue;
  }
  async function pollServer() {
    if (polling || !server || blocked) return;
    polling = true;
    try {
      const result = await responseJSON(await fetch('api/images', {signal:AbortSignal.timeout(8000)}));
      if (result.signature !== imageSignature) {
        const before = new Set(DATA.images.map(image => image.file));
        DATA.images = result.images; imageSignature = result.signature; buildLibrary();
        document.querySelectorAll('[data-source-file]').forEach(image => {
          const next = imageURL(image.dataset.sourceFile);
          if (image.getAttribute('src') !== next) image.src = next;
        });
        const added = result.images.filter(image => !before.has(image.file)).length;
        if (added && before.size) notify(`${added} new file${added === 1 ? '' : 's'} appeared in the library.`);
      }
      $('folder-status').textContent = 'Watching this folder · new images and videos appear automatically';
      if (serverDirty && !serverPending) queueServerSave();
    } catch {
      $('folder-status').textContent = 'Folder connection lost — reconnecting automatically';
    } finally { polling = false; }
  }

  /* ---------- Project validation ---------- */
  function validFile(name) {
    return typeof name === 'string' && name.length <= 500 && !/[\\/\x00-\x1f]/.test(name) && (/\.(png|jpe?g|gif|webp|bmp)$/i.test(name) || VIDEO_PATTERN.test(name));
  }
  function isVideo(file) { return VIDEO_PATTERN.test(file); }
  // Same rules as story.py: offsets that no longer fit the text are clamped, never rejected, so a comment is not lost.
  function validComments(list, body, text, id) {
    if (!Array.isArray(list) || list.length > MAX_COMMENTS) throw Error(`A text block can hold at most ${MAX_COMMENTS} comments.`);
    return list.map(c => {
      if (!c || typeof c !== 'object' || !Number.isInteger(c.start) || !Number.isInteger(c.end)) throw Error('The project contains an invalid comment.');
      const label = c.label ?? 'note';
      if (typeof label !== 'string' || !Object.hasOwn(COMMENT_LABELS, label)) throw Error('Unknown comment label.');
      const end = Math.min(Math.max(c.end, 0), body.length), start = Math.min(Math.max(c.start, 0), end);
      const clean = {id:id(c.id), start, end, label, note:text(c.note ?? '', 100000), quote:text(c.quote ?? '', 100000)};
      if (c.suggestion !== undefined) clean.suggestion = text(c.suggestion, 300000);
      if (c.resolved !== undefined) {
        if (typeof c.resolved !== 'boolean') throw Error('The project contains an invalid comment.');
        if (c.resolved) clean.resolved = true;
      }
      ['created', 'updated'].forEach(key => { if (c[key] !== undefined) clean[key] = text(c[key], 100); });
      return clean;
    });
  }
  function validate(raw) {
    if (!raw || raw.format !== 'illustrated-story-project' || raw.version !== 1) throw Error('This is not a supported Story Studio project (version 1).');
    const text = (value, max = 2000000) => {
      if (typeof value !== 'string' || value.length > max) throw Error('The project contains invalid or oversized text.');
      return value;
    };
    const ids = new Set();
    const id = value => {
      if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(value) || ids.has(value)) throw Error('The project has invalid or duplicate content IDs.');
      ids.add(value); return value;
    };
    if (!Array.isArray(raw.chapters) || raw.chapters.length < 1 || raw.chapters.length > 500) throw Error('A project must have between 1 and 500 chapters.');
    let count = 0;
    const clean = {
      format: 'illustrated-story-project', version: 1,
      id: typeof raw.id === 'string' ? raw.id.slice(0, 100) : uid(),
      title: text(raw.title, 10000), subtitle: text(raw.subtitle, 100000),
      chapters: raw.chapters.map(chapter => {
        if (!chapter || !Array.isArray(chapter.blocks)) throw Error('Invalid chapter.');
        return {
          id: id(chapter.id), title: text(chapter.title, 10000), notes: text(chapter.notes ?? '', 300000),
          blocks: chapter.blocks.map(block => {
            if (++count > 10000 || !block) throw Error('Too many blocks, or an invalid block.');
            const blockId = id(block.id);
            if (block.type === 'text') {
              const clean = {id: blockId, type: 'text', text: text(block.text)};
              if (block.comments !== undefined) {
                const list = validComments(block.comments, clean.text, text, id);
                if (list.length) clean.comments = list;
              }
              return clean;
            }
            let picture;
            if (block.type === 'gallery') {
              if (!Array.isArray(block.items) || block.items.length < 2 || block.items.length > MAX_GROUP) throw Error(`A picture group must contain 2–${MAX_GROUP} pictures.`);
              if (!LAYOUTS.includes(block.layout)) throw Error('Unknown picture group layout.');
              const items = block.items.map(item => {
                if (!item || !validFile(item.file)) throw Error('Image references must be top-level image filenames, not paths or URLs.');
                return {file: item.file};
              });
              picture = {id: blockId, type: 'gallery', layout: block.layout, items, caption: text(block.caption, 300000)};
            } else {
              if (block.type !== 'image' || !validFile(block.file)) throw Error('Image references must be top-level image filenames, not paths or URLs.');
              picture = {id: blockId, type: 'image', file: block.file, caption: text(block.caption, 300000)};
            }
            if (block.size !== undefined) {
              if (!SIZES.includes(block.size)) throw Error('Unknown image size.');
              picture.size = block.size;
            }
            return picture;
          })
        };
      })
    };
    if (raw.extraImages !== undefined) {
      if (!Array.isArray(raw.extraImages) || raw.extraImages.length > 10000) throw Error('Invalid added-image library.');
      clean.extraImages = raw.extraImages.map(image => {
        if (!image || !validFile(image.file)) throw Error('Added images must use top-level filenames.');
        return {file:image.file, title:text(image.title, 10000), category:text(image.category, 1000)};
      });
    }
    if (raw.disclaimer !== undefined) clean.disclaimer = text(raw.disclaimer, 100000);
    if (raw.disclaimerGate !== undefined) {
      if (typeof raw.disclaimerGate !== 'boolean') throw Error('Invalid disclaimer setting.');
      clean.disclaimerGate = raw.disclaimerGate;
    }
    if (raw.outline !== undefined) clean.outline = text(raw.outline);
    if (typeof raw.updatedAt === 'string') clean.updatedAt = raw.updatedAt;
    return clean;
  }

  /* ---------- Helpers ---------- */
  function chapter() { return project.chapters.find(c => c.id === activeChapter) || project.chapters[0]; }
  function chapterName(c) { return c.title || 'Untitled chapter'; }
  function metadata(file) { return library.get(file) || {file, title: file, category: 'Imported image reference'}; }
  function isPicture(b) { return b?.type === 'image' || b?.type === 'gallery'; }
  function blockFiles(b) { return b.type === 'image' ? [b.file] : b.type === 'gallery' ? b.items.map(x => x.file) : []; }
  function groupColumns(b) { const n = b.items.length; return b.layout === 'row' ? n : n === 2 || n === 4 ? 2 : 3; }
  function imageCount(c) { return c.blocks.reduce((n, b) => n + blockFiles(b).length, 0); }
  function wordCount(text) { return text.trim() ? text.trim().split(/\s+/u).length : 0; }
  function chapterWords(c) { return c.blocks.reduce((sum, b) => sum + wordCount(b.type === 'text' ? b.text : b.caption), 0); }
  function notify(message, error = false, action = null) {
    clearTimeout(noticeTimer); $('notice-text').textContent = message;
    $('notice').classList.toggle('error', error); $('notice').hidden = false;
    $('notice-action').hidden = !action;
    if (action) { $('notice-action').textContent = action.label; $('notice-action').onclick = () => { $('notice').hidden = true; action.run(); }; }
    if (!error) noticeTimer = setTimeout(() => { $('notice').hidden = true; }, action ? 9000 : 5000);
  }
  const undoAction = {label:'Undo', run:() => stepHistory(undoStack, redoStack)};
  function history(group = null) {
    if (!group || group !== editGroup) {
      undoStack.push({project: clone(project), activeChapter});
      if (undoStack.length > 100) undoStack.shift();
    }
    editGroup = group; redoStack = []; updateHistory();
  }
  function updateHistory() {
    $('undo').disabled = blocked || !undoStack.length;
    $('redo').disabled = blocked || !redoStack.length;
  }
  function saveBrowser() {
    if (blocked || !project) return false;
    try {
      localStorage.setItem(KEY, JSON.stringify({project, writer, activeChapter, savedAt: new Date().toISOString(),
        pendingServer:serverMode && (serverDirty || diskSavedVersion !== serialize()), serverRevision:server?.revision}));
      browserSaved = true;
      browserStatus('Recovery copy saved in this browser · ' + new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}));
      return true;
    } catch {
      browserSaved = false;
      browserStatus('Browser save failed — download a backup or link a project file', true);
      return false;
    }
  }
  function serialize() { return JSON.stringify(project, null, 2) + '\n'; }
  function queueDiskSave() {
    if (serverMode) return queueServerSave();
    if (!fileHandle || blocked) return diskQueue;
    const handle = fileHandle, payload = serialize();
    diskPending++;
    fileStatus('Saving to ' + handle.name + '…', 'busy');
    diskQueue = diskQueue.then(async () => {
      try {
        const writable = await handle.createWritable();
        try { await writable.write(payload); await writable.close(); }
        catch (error) { try { await writable.abort(); } catch {} throw error; }
        diskSavedVersion = payload;
        if (fileHandle === handle) {
          if (payload === serialize()) fileStatus('File saved · ' + handle.name, 'ok');
          else fileStatus('File changes pending · ' + handle.name, 'busy');
        }
      } catch (error) {
        if (fileHandle === handle) {
          fileHandle = null; diskSavedVersion = '';
          fileStatus('File save failed — reconnect file or download a backup', 'error');
          notify('Could not write the project file: ' + error.message + '. Your browser draft is still available if browser autosave succeeded.', true);
        }
      } finally { diskPending--; }
    });
    return diskQueue;
  }
  function changed(render = true) {
    project.updatedAt = new Date().toISOString();
    if (serverMode) serverDirty = true;
    saveBrowser(); clearTimeout(diskTimer);
    if (serverMode && server) {
      fileStatus('Changes pending · story-project.json', 'busy');
      diskTimer = setTimeout(queueServerSave, 350);
    } else if (fileHandle) {
      fileStatus('File changes pending · ' + fileHandle.name, 'busy');
      diskTimer = setTimeout(queueDiskSave, 650);
    }
    if (render) renderEditor();
    else refreshLight();
  }
  async function flushDisk() {
    clearTimeout(diskTimer);
    if (serverMode) { if (serverDirty) queueServerSave(); await serverQueue; }
    else { if (fileHandle) queueDiskSave(); await diskQueue; }
  }
  function mutate(action) { if (blocked) return; history(); action(); changed(); }

  /* ---------- Thumbnails: small JPEGs cached in IndexedDB ---------- */
  const thumbs = {db:null, urls:new Map(), waiting:[], running:0, observer:null};
  function thumbKey(file) { return (storyId ? storyId + '|' : '') + file + '|' + (metadata(file).stamp || ''); }
  function thumbDB() {
    thumbs.db ||= new Promise(resolve => {
      try {
        const request = indexedDB.open('story-studio-thumbnails', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('thumbs');
        request.onsuccess = () => resolve(request.result);
        request.onerror = request.onblocked = () => resolve(null);
      } catch { resolve(null); }
    });
    return thumbs.db;
  }
  async function thumbStore(mode, action) {
    const db = await thumbDB(); if (!db) return null;
    return new Promise(resolve => {
      try {
        const tx = db.transaction('thumbs', mode), request = action(tx.objectStore('thumbs'));
        tx.oncomplete = () => resolve(request.result ?? null); tx.onerror = tx.onabort = () => resolve(null);
      } catch { resolve(null); }
    });
  }
  async function makeThumb(file) {
    if (!serverMode) throw Error('file:// pages cannot read image pixels; use the full image');
    const key = thumbKey(file);
    let blob = await thumbStore('readonly', store => store.get(key));
    if (!blob) {
      let canvas;
      if (isVideo(file)) canvas = await videoFrame(file);
      else {
        const response = await fetch(imageURL(file));
        if (!response.ok) throw Error('Image unavailable');
        const bitmap = await createImageBitmap(await response.blob(), {resizeWidth:360, resizeQuality:'medium'});
        canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        canvas.getContext('2d').drawImage(bitmap, 0, 0); bitmap.close();
      }
      blob = await canvas.convertToBlob({type:'image/jpeg', quality:.82});
      thumbStore('readwrite', store => store.put(blob, key));
    }
    return URL.createObjectURL(blob);
  }
  // A frame about a second in (the very first is often black); the server's range support keeps this from downloading the whole video.
  async function videoFrame(file) {
    const video = document.createElement('video');
    video.muted = true; video.preload = 'auto'; video.src = imageURL(file);
    const once = (ok, timeout = 15000) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Video timed out')), timeout);
      video.addEventListener(ok, () => { clearTimeout(timer); resolve(); }, {once:true});
      video.addEventListener('error', () => { clearTimeout(timer); reject(Error('Video unavailable')); }, {once:true});
    });
    try {
      await once('loadeddata');
      video.currentTime = Math.min(1, (video.duration || 0) / 3);
      await once('seeked');
      const width = Math.min(360, video.videoWidth), height = Math.round(video.videoHeight * width / video.videoWidth);
      const canvas = new OffscreenCanvas(width, height);
      canvas.getContext('2d').drawImage(video, 0, 0, width, height);
      return canvas;
    } finally { video.removeAttribute('src'); video.load(); }
  }
  function pumpThumbs() {
    while (thumbs.running < 4 && thumbs.waiting.length) {
      const file = thumbs.waiting.shift(), key = thumbKey(file);
      if (thumbs.urls.has(key)) { applyThumb(file); continue; }
      thumbs.running++;
      makeThumb(file).then(url => thumbs.urls.set(key, url), () => thumbs.urls.set(key, isVideo(file) ? VIDEO_PLACEHOLDER : imageURL(file)))
        .finally(() => { thumbs.running--; applyThumb(file); pumpThumbs(); });
    }
  }
  function applyThumb(file) {
    const url = thumbs.urls.get(thumbKey(file)); if (!url) return;
    document.querySelectorAll(`img[data-thumb="${CSS.escape(file)}"]`).forEach(img => { if (img.getAttribute('src') !== url) img.src = url; });
  }
  function observeThumbs(root) {
    thumbs.observer ||= new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      thumbs.observer.unobserve(entry.target);
      const file = entry.target.dataset.thumb;
      if (!thumbs.waiting.includes(file)) thumbs.waiting.push(file);
      pumpThumbs();
    }), {rootMargin:'400px'});
    root.querySelectorAll('img[data-thumb]').forEach(img => {
      const url = thumbs.urls.get(thumbKey(img.dataset.thumb));
      if (url) img.src = url; else thumbs.observer.observe(img);
    });
  }

  /* ---------- Rendering ---------- */
  function renderCounts() {
    const total = project.chapters.reduce((n, x) => n + chapterWords(x), 0);
    const images = project.chapters.reduce((n, x) => n + imageCount(x), 0);
    $('story-stats').textContent = `${project.chapters.length} chapters · ${images} images · ${total.toLocaleString()} words · about ${Math.max(1, Math.round(total / 220))} min read`;
  }
  function chapterStats(c) {
    const open = openComments(c);
    return `${imageCount(c)} images · ${chapterWords(c)} words` + (open ? ` · ${open} comment${open === 1 ? '' : 's'}` : '');
  }
  function renderChapters() {
    $('chapter-list').innerHTML = project.chapters.map((c, index) => {
      const files = c.blocks.flatMap(blockFiles).slice(0, 5);
      return `<li class="chapter-item ${view === 'chapter' && c.id === activeChapter ? 'active' : ''}" draggable="true" data-chapter-item="${esc(c.id)}">
        <button type="button" class="chapter-select" data-chapter="${esc(c.id)}" ${c.id === activeChapter ? 'aria-current="true"' : ''}>
          <span class="chapter-number">Chapter ${index + 1}</span><span class="chapter-name">${esc(chapterName(c))}</span>
          <span class="chapter-stats">${chapterStats(c)}</span>
          ${files.length ? `<span class="chapter-strip">${files.map(f => `<img data-thumb="${esc(f)}" alt="">`).join('')}</span>` : ''}
        </button>
        <div class="chapter-order"><button type="button" data-chapter-move="-1" data-id="${esc(c.id)}" aria-label="Move chapter ${index + 1} earlier" ${index === 0 ? 'disabled' : ''}>↑</button><button type="button" data-chapter-move="1" data-id="${esc(c.id)}" aria-label="Move chapter ${index + 1} later" ${index === project.chapters.length - 1 ? 'disabled' : ''}>↓</button></div>
      </li>`;
    }).join('');
    observeThumbs($('chapter-list'));
  }
  // Cheap refresh while typing: no DOM rebuilds, so focus and thumbnails stay put.
  function refreshLight() {
    $('chapter-list').querySelectorAll('[data-chapter-item]').forEach(item => {
      const c = project.chapters.find(x => x.id === item.dataset.chapterItem); if (!c) return;
      item.querySelector('.chapter-name').textContent = chapterName(c);
      item.querySelector('.chapter-stats').textContent = chapterStats(c);
    });
    renderTitles(); renderCounts();
    $('notes-toggle').classList.toggle('has-notes', !!chapter().notes.trim());
  }
  function renderTitles() {
    $('topbar-title').textContent = project.title || 'Untitled story';
    $('title-page-name').textContent = project.title || 'Untitled story';
    const disclaimer = (project.disclaimer || '').trim();
    $('disclaimer-item-name').textContent = disclaimer ? disclaimer.split('\n')[0] : 'None — click to add one';
    const words = wordCount(project.outline || '');
    $('outline-item-stats').textContent = words ? `${words.toLocaleString()} words` : 'Empty — plan the story here';
    $('outline-count').textContent = words ? `${words.toLocaleString()} words · saved automatically · private` : '';
    document.title = 'Story Studio — ' + (project.title || 'Untitled story');
  }
  function gapHTML(index, count) {
    const end = index === count, active = target && target.chapter === activeChapter && target.index === index;
    return `<div class="gap ${end ? 'gap-end' : ''} ${active ? 'active' : ''}" data-gap="${index}">
      <div class="gap-line"></div>
      ${end && !count ? '<p class="chapter-empty">This chapter is empty. Start writing, or add pictures.</p>' : ''}
      <div class="gap-actions"><button type="button" data-gap-text title="Insert text here">+ Text</button><button type="button" data-gap-image title="Choose a picture for this spot">+ Image</button></div>
      <span class="gap-label">New images go here <button type="button" data-gap-clear aria-label="Clear the marked spot">×</button></span>
      ${end ? '<p class="gap-hint hint">…or drag pictures from the library to any spot in the chapter.</p>' : ''}
    </div>`;
  }
  // Videos stay muted in the editor and play while hovered; the viewer and the finished story give them full controls.
  function mediaHTML(file, attrs) {
    const title = esc(metadata(file).title), src = esc(imageURL(file));
    return (isVideo(file)
      ? `<video data-source-file="${esc(file)}" src="${src}" aria-label="${title}" title="${title} — hover to play, click to enlarge" muted loop playsinline preload="metadata" data-hover-play ${attrs}></video>`
      : `<img data-source-file="${esc(file)}" src="${src}" alt="${title}" title="${title} — click to enlarge" loading="lazy" decoding="async" ${attrs}>`) + hiddenMediaHTML(file);
  }
  // Stands in for a picture while "Hide all images and videos" is on (CSS shows it only then).
  function hiddenMediaHTML(file) {
    return `<span class="media-hidden" data-hidden-for="${esc(file)}"><span class="media-hidden-kind">${isVideo(file) ? 'Video' : 'Picture'} hidden</span> ${esc(metadata(file).title)}</span>`;
  }
  function groupHTML(block) {
    const n = block.items.length;
    return `<figure><div class="gallery-cells layout-${block.layout}" style="--cols:${groupColumns(block)}">${block.items.map((item, i) => {
      return `<div class="gallery-item">${mediaHTML(item.file, `data-view-item="${i}"`)}
        <div class="item-tools"><button type="button" data-item-action="left" data-item="${i}" aria-label="Move picture ${i + 1} earlier" title="Move earlier" ${i === 0 ? 'disabled' : ''}>←</button><button type="button" data-item-action="right" data-item="${i}" aria-label="Move picture ${i + 1} later" title="Move later" ${i === n - 1 ? 'disabled' : ''}>→</button><button type="button" data-item-action="replace" data-item="${i}" title="Choose a different picture">Replace</button><button type="button" data-item-action="remove" data-item="${i}" aria-label="Remove picture ${i + 1} from the group" title="Remove from the group">×</button></div></div>`;
    }).join('')}</div><textarea class="caption" data-edit="caption" rows="1" placeholder="Add one caption for the whole group…" aria-label="Group caption">${esc(block.caption)}</textarea></figure>`;
  }
  function blockHTML(block, index, blocks) {
    const image = block.type === 'image', gallery = block.type === 'gallery', picture = image || gallery;
    const info = image ? metadata(block.file) : null, size = block.size || 'large';
    const kind = gallery ? 'group' : image ? (isVideo(block.file) ? 'video' : 'picture') : 'text';
    const others = project.chapters.filter(c => c.id !== activeChapter);
    const ratio = image && info.width && info.height ? `style="aspect-ratio:${info.width}/${info.height}"` : '';
    const caretHere = !target && caret && caret.chapter === activeChapter && caret.block === block.id;
    const sep = '<span class="sep"></span>';
    return `<article class="block block-${block.type} size-${size} ${caretHere ? 'insert-here' : ''}" data-block="${esc(block.id)}">
      <div class="block-tools" role="toolbar" aria-label="${gallery ? 'Picture group' : image ? 'Image' : 'Text'} block ${index + 1}">
        <button type="button" class="drag-handle" draggable="true" data-drag-block="${esc(block.id)}" data-focus="handle" aria-label="Drag to reorder" title="Drag to reorder${picture ? ', or onto the side of another picture to group them' : ''}">⠿</button>
        <button type="button" data-action="up" aria-label="Move up" title="Move up (Alt+↑)" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button type="button" data-action="down" aria-label="Move down" title="Move down (Alt+↓)" ${index === blocks.length - 1 ? 'disabled' : ''}>↓</button>
        ${gallery ? sep + LAYOUTS.map(l => `<button type="button" class="size-btn" data-layout="${l}" aria-pressed="${block.layout === l}" title="${l === 'row' ? 'All pictures side by side in one row' : 'Pictures arranged in a grid'}">${l === 'row' ? 'Row' : 'Grid'}</button>`).join('') : ''}
        ${picture ? sep + SIZES.map(s => `<button type="button" class="size-btn" data-size="${s}" aria-pressed="${s === size}" title="${SIZE_NAMES[s]}">${SIZE_LABELS[s]}</button>`).join('') : ''}
        ${image ? sep + '<button type="button" data-action="replace" title="Choose a different picture">Replace</button>' : ''}
        ${gallery ? sep + `<button type="button" data-action="group-add" title="Add pictures to this group" ${block.items.length >= MAX_GROUP ? 'disabled' : ''}>+ Add</button>` : ''}
        ${picture ? '' : `<button type="button" data-action="comment" title="Comment on the selected words, or on the paragraph at the cursor (Alt+C)">Comment${commentsOf(block).some(c => !c.resolved) ? ` <span class="count">${commentsOf(block).filter(c => !c.resolved).length}</span>` : ''}</button>`}
        ${sep}
        <div class="menu-wrap"><button type="button" data-menu aria-expanded="false" aria-label="More block actions">⋯</button>
          <div class="menu" hidden>
            <button type="button" data-action="duplicate">Duplicate</button>
            ${picture && isPicture(blocks[index - 1]) ? '<button type="button" data-action="group-prev">Group with the picture above</button>' : ''}
            ${picture && isPicture(blocks[index + 1]) ? '<button type="button" data-action="group-next">Group with the picture below</button>' : ''}
            ${gallery ? '<button type="button" data-action="ungroup">Ungroup into separate pictures</button>' : ''}
            ${others.length ? `<span class="menu-label">Move to chapter</span>${others.map(c => `<button type="button" data-move-to="${esc(c.id)}">${esc(chapterName(c))}</button>`).join('')}` : ''}
            <hr><button type="button" data-action="remove" class="danger">Delete ${kind}</button>
          </div>
        </div>
      </div>
      <div class="block-body">${gallery ? groupHTML(block) : image ? `<figure><div class="frame">${mediaHTML(block.file, `${ratio} data-view-image`)}</div><textarea class="caption" data-edit="caption" rows="1" placeholder="Add a caption…" aria-label="Caption">${esc(block.caption)}</textarea></figure>`
        : `<div class="prose-wrap"><div class="prose-marks" aria-hidden="true">${marksHTML(block)}</div><textarea class="prose" data-edit="text" rows="3" aria-label="Story text, block ${index + 1}" placeholder="Write here. Leave a blank line between paragraphs…">${esc(block.text)}</textarea></div>`}</div>
    </article>`;
  }
  function captureFocus() {
    const active = document.activeElement, article = active?.closest?.('#blocks [data-block]');
    if (!article) return null;
    const key = active.dataset.action ? `[data-action="${active.dataset.action}"]` : active.dataset.edit ? `[data-edit="${active.dataset.edit}"]`
      : active.dataset.size ? `[data-size="${active.dataset.size}"]` : active.dataset.layout ? `[data-layout="${active.dataset.layout}"]`
      : active.dataset.itemAction ? `[data-item-action="${active.dataset.itemAction}"][data-item="${active.dataset.item}"]` : '.drag-handle';
    return {block:article.dataset.block, selector:key, start:active.selectionStart, end:active.selectionEnd};
  }
  function renderEditor() {
    activeChapter = chapter().id;
    const c = chapter(), focus = pendingFocus || captureFocus();
    pendingFocus = null;
    if (target && (target.chapter !== c.id || target.index > c.blocks.length)) target = null;
    $('title-page').hidden = view !== 'title'; $('chapter-editor').hidden = view !== 'chapter';
    $('outline-page').hidden = view !== 'outline'; $('disclaimer-page').hidden = view !== 'disclaimer';
    $('title-page-item').classList.toggle('active', view === 'title');
    $('outline-item').classList.toggle('active', view === 'outline');
    $('disclaimer-item').classList.toggle('active', view === 'disclaimer');
    if (document.activeElement !== $('outline-text')) $('outline-text').value = project.outline || '';
    if (document.activeElement !== $('disclaimer-text')) $('disclaimer-text').value = project.disclaimer || '';
    $('disclaimer-gate').checked = !!project.disclaimerGate;
    if (document.activeElement !== $('project-title')) $('project-title').value = project.title;
    if (document.activeElement !== $('project-subtitle')) $('project-subtitle').value = project.subtitle;
    $('chapter-title').value = c.title; $('chapter-notes').value = c.notes;
    const index = project.chapters.indexOf(c);
    $('chapter-position').textContent = `Chapter ${index + 1} of ${project.chapters.length}`;
    $('blocks').innerHTML = c.blocks.map((b, i) => gapHTML(i, c.blocks.length) + blockHTML(b, i, c.blocks)).join('') + gapHTML(c.blocks.length, c.blocks.length);
    $('delete-chapter').disabled = project.chapters.length <= 1;
    const prev = project.chapters[index - 1], next = project.chapters[index + 1];
    $('prev-chapter').textContent = prev ? '← ' + chapterName(prev) : '';
    $('next-chapter').textContent = next ? chapterName(next) + ' →' : '+ New chapter';
    $('notes-toggle').classList.toggle('has-notes', !!c.notes.trim());
    renderChapters(); renderTitles(); renderCounts(); updateLibrary(); updateHistory(); autosizeAll(); renderComments();
    if (focus) {
      const article = $('blocks').querySelector(`[data-block="${CSS.escape(focus.block)}"]`);
      let element = article?.querySelector(focus.selector);
      if (element?.disabled) element = article.querySelector('.drag-handle');
      if (element) {
        element.focus({preventScroll:true});
        if (focus.start !== undefined && element.setSelectionRange) element.setSelectionRange(focus.start, focus.end ?? focus.start);
        if (focus.reveal) article.scrollIntoView({block:'center', behavior:'smooth'});
        else article.scrollIntoView({block:'nearest'});
      }
    }
    if (pendingScroll) {
      const ids = pendingScroll; pendingScroll = null;
      const articles = ids.map(id => $('blocks').querySelector(`[data-block="${CSS.escape(id)}"]`)).filter(Boolean);
      articles.forEach(a => a.classList.add('flash'));
      articles[0]?.scrollIntoView({block:'center', behavior:'smooth'});
    }
  }
  function autosize(textarea) {
    if (autosizeNative || !textarea) return;
    textarea.style.height = 'auto'; textarea.style.height = textarea.scrollHeight + 'px';
  }
  function autosizeAll() { if (!autosizeNative) document.querySelectorAll('.prose, .caption, #project-subtitle, #chapter-notes, #outline-text, #disclaimer-text').forEach(autosize); }

  /* ---------- Library ---------- */
  function countsByFile() {
    const counts = new Map();
    project.chapters.forEach(c => c.blocks.forEach(b => blockFiles(b).forEach(file => counts.set(file, (counts.get(file) || 0) + 1))));
    return counts;
  }
  function buildLibrary() {
    library.clear();
    DATA.images.forEach(image => library.set(image.file, image));
    (project.extraImages || []).forEach(image => { if (!serverMode || library.has(image.file)) library.set(image.file, {...library.get(image.file), ...image}); });
    project.chapters.forEach(c => c.blocks.forEach(b => { if (b.type === 'image' && !library.has(b.file)) library.set(b.file, metadata(b.file)); }));
    selected = new Set([...selected].filter(file => library.has(file)));
    const categories = [...new Set([...library.values()].map(x => x.category))].sort();
    const old = $('image-category').value;
    $('image-category').innerHTML = '<option value="">All scenes</option>' + categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    $('image-category').value = categories.includes(old) ? old : '';
    $('scene-list').innerHTML = categories.map(c => `<option value="${esc(c)}">`).join('');
    $('library-total').textContent = library.size;
    $('image-library').innerHTML = [...library.values()].map(info => `
      <article class="library-card" data-file="${esc(info.file)}" data-category="${esc(info.category)}">
        <div class="library-thumb" draggable="true" data-drag-file="${esc(info.file)}" data-view-file tabindex="0" role="button" aria-label="View ${esc(info.title)}" title="${esc(info.title)}\nClick to enlarge · drag into the story">
          <img data-thumb="${esc(info.file)}" alt="">${isVideo(info.file) ? '<span class="video-badge" aria-label="Video">▶</span>' : ''}
          <label class="library-check" title="Select for a batch insert"><input type="checkbox" data-select-image aria-label="Select ${esc(info.title)}"></label><span class="used-badge" hidden></span>
          <button type="button" class="thumb-insert" data-add-image>+ Insert</button>
        </div><p class="library-title">${esc(info.title)}</p>
      </article>`).join('') + '<p id="library-empty" class="no-results" hidden>No images match. Clear the search or change the scene filter.</p>';
    libraryCards = [...$('image-library').querySelectorAll('.library-card')];
    sortLibrary(); updateLibrary(); observeThumbs($('image-library'));
  }
  function searchText(file) { const info = metadata(file); return (file + ' ' + info.title + ' ' + info.category).toLowerCase(); }
  function sortLibrary() {
    const by = ui.sort, stamp = file => (metadata(file).stamp || '').split('-')[0].padStart(24, '0');
    const sorted = [...libraryCards].sort((a, b) => {
      const x = a.dataset.file, y = b.dataset.file;
      if (by === 'name') return metadata(x).title.localeCompare(metadata(y).title, undefined, {numeric:true});
      const order = stamp(x).localeCompare(stamp(y)) || x.localeCompare(y, undefined, {numeric:true});
      return by === 'oldest' ? order : -order;
    });
    sorted.forEach(card => $('image-library').insertBefore(card, $('library-empty')));
    libraryCards = sorted;
  }
  function updateLibrary() {
    const counts = countsByFile(), terms = $('image-search').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const category = $('image-category').value, unused = $('unused-only').checked;
    const insertLabel = pick?.mode === 'replace' ? 'Use this' : pick?.mode === 'group-add' ? '+ Add to group' : '+ Insert';
    let visible = 0;
    libraryCards.forEach(card => {
      const file = card.dataset.file, n = counts.get(file) || 0, haystack = searchText(file);
      card.hidden = !terms.every(t => haystack.includes(t)) || (!!category && card.dataset.category !== category) || (unused && n > 0);
      if (!card.hidden) visible++;
      const badge = card.querySelector('.used-badge'); badge.hidden = n === 0; badge.textContent = n === 1 ? 'Used' : `Used ×${n}`;
      card.querySelector('input').checked = selected.has(file);
      card.classList.toggle('is-selected', selected.has(file));
      card.querySelector('[data-add-image]').textContent = insertLabel;
    });
    $('library-count').textContent = visible === library.size ? `${library.size} files` : `${visible} of ${library.size} shown`;
    $('library-empty').hidden = visible !== 0;
    $('add-selected').textContent = pick?.mode === 'group-add' ? `Add ${selected.size} to the group` : `Insert ${selected.size} separately`;
    $('add-selected').hidden = selected.size === 0 || pick?.mode === 'replace';
    $('add-selected-group').hidden = selected.size < 2 || !!pick && pick.mode !== 'insert';
    $('add-selected').classList.toggle('primary', pick?.mode === 'group-add' || selected.size < 2);
    $('clear-selected').hidden = selected.size === 0;
    updateInsertInfo();
  }
  function updateCardInfo(file) {
    const card = libraryCards.find(x => x.dataset.file === file), info = metadata(file);
    if (card) {
      card.dataset.category = info.category;
      card.querySelector('.library-title').textContent = info.title;
      card.querySelector('.library-thumb').title = info.title + '\nClick to enlarge · drag into the story';
    }
    document.querySelectorAll(`#blocks img[data-source-file="${CSS.escape(file)}"]`).forEach(img => { img.alt = info.title; });
    document.querySelectorAll(`.media-hidden[data-hidden-for="${CSS.escape(file)}"]`).forEach(span => { span.lastChild.textContent = ' ' + info.title; });
  }
  function renderLibraryLayout() {
    document.body.classList.toggle('no-chapters', !ui.chapters);
    document.body.classList.toggle('no-library', !ui.library && !document.body.classList.contains('library-expanded'));
    document.body.classList.toggle('show-comments', ui.comments);
    document.body.classList.toggle('no-comment-marks', !ui.commentMarks);
    $('toggle-chapters').setAttribute('aria-pressed', String(ui.chapters));
    $('toggle-library').setAttribute('aria-pressed', String(ui.library));
    $('toggle-comments').setAttribute('aria-pressed', String(ui.comments));
    $('focus-toggle').setAttribute('aria-pressed', String(!ui.chapters && !ui.library && !ui.comments));
    document.documentElement.style.setProperty('--lib-w', ui.libraryWidth + 'px');
    const base = {s:88, m:120, l:180}[ui.thumb] || 120;
    $('image-library').style.setProperty('--thumb-base', base + 'px');
    document.querySelectorAll('[data-thumb-size]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.thumbSize === ui.thumb)));
    $('image-sort').value = ui.sort;
  }
  function toggleComments(on = !ui.comments) {
    ui.comments = on;
    if (on) { ui.library = false; if (document.body.classList.contains('library-expanded')) setLibraryExpanded(false); }
    saveUI(); renderLibraryLayout(); renderComments();
  }
  function setLibraryExpanded(on) {
    document.body.classList.toggle('library-expanded', on);
    $('library-backdrop').hidden = !on;
    $('library-expand').setAttribute('aria-pressed', String(on));
    $('library-expand').title = on ? 'Shrink back to the side panel' : 'Enlarge the library';
    renderLibraryLayout();
    if (!on && pick) { pick = null; renderPick(); updateLibrary(); }
  }

  /* ---------- Insertion point ---------- */
  function insertionPoint() {
    const c = chapter();
    if (target && target.chapter === c.id) return {index:Math.min(target.index, c.blocks.length), kind:'gap'};
    if (caret && caret.chapter === c.id) {
      const i = c.blocks.findIndex(b => b.id === caret.block);
      if (i >= 0 && c.blocks[i].type === 'text') {
        const text = c.blocks[i].text, pos = Math.min(caret.pos, text.length);
        if (!text.slice(0, pos).trim()) return {index:i, kind:'before', block:i};
        if (!text.slice(pos).trim()) return {index:i + 1, kind:'after', block:i};
        return {index:i + 1, kind:'split', block:i, split:{id:c.blocks[i].id, pos}};
      }
    }
    return {index:c.blocks.length, kind:'end'};
  }
  function describeInsertion() {
    const point = insertionPoint(), c = chapter(), where = `in “${chapterName(c)}”`;
    if (point.kind === 'gap') return point.index === c.blocks.length ? `at the end of “${chapterName(c)}”` : `at the marked spot ${where}`;
    if (point.kind === 'split') return `at your text cursor ${where} (splitting the paragraph)`;
    if (point.kind === 'before') return `before the text you were editing ${where}`;
    if (point.kind === 'after') return `after the text you were editing ${where}`;
    return `at the end of “${chapterName(c)}”`;
  }
  function updateInsertInfo() {
    const text = describeInsertion();
    $('insert-where').textContent = text;
    $('insert-reset').hidden = insertionPoint().kind === 'end';
    const notInserting = pick?.mode === 'replace' || pick?.mode === 'group-add';
    $('insert-info').hidden = notInserting;
    $('lb-where').textContent = notInserting ? '' : 'Inserts ' + text + '.';
  }
  function clearInsertion() { target = null; caret = null; document.querySelectorAll('.gap.active, .block.insert-here').forEach(x => x.classList.remove('active', 'insert-here')); updateInsertInfo(); }
  function setTarget(index) {
    target = {chapter:activeChapter, index}; caret = null;
    document.querySelectorAll('.block.insert-here').forEach(x => x.classList.remove('insert-here'));
    document.querySelectorAll('#blocks .gap').forEach(g => g.classList.toggle('active', Number(g.dataset.gap) === index));
    updateInsertInfo();
  }
  function trackCaret(textarea) {
    const article = textarea.closest('[data-block]');
    if (!article || textarea.dataset.edit !== 'text') return;
    const changedBlock = !caret || caret.block !== article.dataset.block || target;
    caret = {chapter:activeChapter, block:article.dataset.block, pos:textarea.selectionStart};
    if (target) { target = null; document.querySelectorAll('.gap.active').forEach(g => g.classList.remove('active')); }
    if (changedBlock) {
      document.querySelectorAll('.block.insert-here').forEach(x => x.classList.remove('insert-here'));
      article.classList.add('insert-here');
    }
    updateInsertInfo();
  }

  /* ---------- Editing actions ---------- */
  function insertImages(files, asGroup = false) {
    files = files.filter(file => library.has(file));
    if (!files.length || blocked) return;
    if (view !== 'chapter') view = 'chapter';
    if (asGroup && files.length > MAX_GROUP) { notify(`A group holds at most ${MAX_GROUP} pictures, so only the first ${MAX_GROUP} were grouped.`, true); files = files.slice(0, MAX_GROUP); }
    const point = insertionPoint(), made = asGroup && files.length > 1
      ? [{id:uid(), type:'gallery', layout:files.length > 4 ? 'grid' : 'row', items:files.map(file => ({file})), caption:''}]
      : files.map(file => ({id:uid(), type:'image', file, caption:''}));
    mutate(() => {
      const blocks = chapter().blocks;
      if (point.split) {
        const block = blocks.find(b => b.id === point.split.id);
        blocks.splice(point.index, 0, splitTextBlock(block, point.split.pos));
      }
      blocks.splice(point.index, 0, ...made);
      target = {chapter:activeChapter, index:point.index + made.length}; caret = null;
      pendingScroll = made.map(b => b.id);
    });
    notify(`Inserted ${made[0].type === 'gallery' ? `a group of ${files.length} pictures` : made.length === 1 ? '1 picture' : made.length + ' pictures'} into “${chapterName(chapter())}”.`, false, undoAction);
  }
  function replaceImage(blockId, file, item = null) {
    const c = project.chapters.find(x => x.blocks.some(b => b.id === blockId)); if (!c || !library.has(file)) return;
    activeChapter = c.id; view = 'chapter';
    mutate(() => {
      const block = c.blocks.find(b => b.id === blockId);
      if (block.type === 'gallery' && block.items[item]) block.items[item].file = file; else block.file = file;
      pendingScroll = [blockId];
    });
    notify('Picture replaced. The caption was kept.', false, undoAction);
  }
  function chooseImage(file) {
    if (pick?.mode === 'replace') replaceImage(pick.block, file, pick.item);
    else if (pick?.mode === 'group-add') addToGroup(pick.block, [file]);
    else insertImages([file]);
    if (pick) endPick();
  }
  // Picture groups ("gallery" blocks): 2–12 pictures shown as a row or grid with one caption.
  function asItems(block) { return block.type === 'gallery' ? block.items.map(x => ({file:x.file})) : [{file:block.file}]; }
  function joinCaptions(...captions) { return captions.map(x => (x || '').trim()).filter(Boolean).join('\n'); }
  function makeGroup(id, items, from) {
    const group = {id, type:'gallery', layout:from.find(b => b?.layout)?.layout || (items.length > 4 ? 'grid' : 'row'), items, caption:joinCaptions(...from.map(b => b?.caption))};
    const size = from.find(b => b?.size)?.size; if (size) group.size = size;
    return group;
  }
  function mergeBlocks(first) {
    const blocks = chapter().blocks, a = blocks[first], b = blocks[first + 1];
    if (!isPicture(a) || !isPicture(b)) return;
    const items = [...asItems(a), ...asItems(b)];
    if (items.length > MAX_GROUP) { notify(`A group holds at most ${MAX_GROUP} pictures.`, true); return; }
    const group = makeGroup(a.type === 'gallery' ? a.id : b.type === 'gallery' ? b.id : uid(), items, [a, b]);
    mutate(() => { blocks.splice(first, 2, group); pendingScroll = [group.id]; });
    notify('Grouped. Use Row or Grid to change the layout.', false, undoAction);
  }
  function ungroup(id) {
    const blocks = chapter().blocks, index = blocks.findIndex(b => b.id === id), group = blocks[index];
    if (group?.type !== 'gallery') return;
    const singles = group.items.map((x, i) => ({id:uid(), type:'image', file:x.file, caption:i === group.items.length - 1 ? group.caption : '', ...(group.size ? {size:group.size} : {})}));
    mutate(() => { blocks.splice(index, 1, ...singles); pendingScroll = singles.map(b => b.id); });
    notify('Ungrouped. The group caption moved to the last picture.', false, undoAction);
  }
  function addToGroup(blockId, files) {
    const c = project.chapters.find(x => x.blocks.some(b => b.id === blockId)), group = c?.blocks.find(b => b.id === blockId);
    if (group?.type !== 'gallery') return;
    files = files.filter(file => library.has(file));
    const room = MAX_GROUP - group.items.length;
    if (files.length > room) { notify(`A group holds at most ${MAX_GROUP} pictures${room ? `, so only ${room} more could be added` : ''}.`, true); files = files.slice(0, room); }
    if (!files.length) return;
    activeChapter = c.id; view = 'chapter';
    mutate(() => { group.items.push(...files.map(file => ({file}))); pendingScroll = [blockId]; });
  }
  function groupItemAction(block, action, i) {
    if (action === 'replace') { startPick('replace', block.id, i); return; }
    if (action === 'left' || action === 'right') {
      const to = i + (action === 'left' ? -1 : 1); if (to < 0 || to >= block.items.length) return;
      pendingFocus = {block:block.id, selector:`[data-item-action="${action}"][data-item="${to}"]`};
      mutate(() => { const [x] = block.items.splice(i, 1); block.items.splice(to, 0, x); });
      return;
    }
    if (action === 'remove') {
      mutate(() => {
        block.items.splice(i, 1);
        if (block.items.length === 1) {
          const blocks = chapter().blocks, single = {id:block.id, type:'image', file:block.items[0].file, caption:block.caption};
          if (block.size) single.size = block.size;
          blocks.splice(blocks.indexOf(block), 1, single);
        }
      });
      notify('Picture taken out of the group. Original image files are never deleted.', false, undoAction);
    }
  }
  // Drop a picture onto the left or right edge of another picture to group them.
  function groupOnDrop(item, targetId, side) {
    const blocks = chapter().blocks, targetBlock = blocks.find(b => b.id === targetId); if (!isPicture(targetBlock)) return;
    const source = item.type === 'block' ? project.chapters.find(c => c.id === item.chapter) : null;
    const moving = source?.blocks.find(b => b.id === item.id);
    if (item.type === 'block' && !isPicture(moving)) return;
    const incoming = moving ? asItems(moving) : [{file:item.file}], existing = asItems(targetBlock);
    if (incoming.length + existing.length > MAX_GROUP) { notify(`A group holds at most ${MAX_GROUP} pictures.`, true); return; }
    mutate(() => {
      if (moving) source.blocks.splice(source.blocks.indexOf(moving), 1);
      const items = side === 'left' ? [...incoming, ...existing] : [...existing, ...incoming];
      const group = makeGroup(targetBlock.id, items, [targetBlock, moving]);
      blocks.splice(blocks.indexOf(targetBlock), 1, group);
      pendingScroll = [group.id]; target = null;
    });
    notify('Grouped side by side. Use Row or Grid to change the layout.', false, undoAction);
  }
  function startPick(mode, blockId = null, item = null) {
    pick = {mode, block:blockId, item, opened:!document.body.classList.contains('library-expanded')};
    setLibraryExpanded(true); renderPick(); updateLibrary();
    $('image-search').focus();
  }
  function endPick() {
    const wasOpened = pick?.opened; pick = null; renderPick(); updateLibrary();
    if (wasOpened) setLibraryExpanded(false);
  }
  function renderPick() {
    $('pick-banner').hidden = !pick;
    if (pick) $('pick-text').textContent = pick.mode === 'replace' ? 'Choose a replacement picture'
      : pick.mode === 'group-add' ? 'Choose pictures to add to the group — tick several to add them at once' : 'Choose a picture — it goes ' + describeInsertion();
  }
  function addText(at, text = '') {
    const id = uid();
    view = 'chapter';
    pendingFocus = {block:id, selector:'textarea', start:0, reveal:true};
    mutate(() => { chapter().blocks.splice(at, 0, {id, type:'text', text}); target = null; });
  }
  function moveBlock(id, delta) {
    const blocks = chapter().blocks, index = blocks.findIndex(b => b.id === id), to = index + delta;
    if (index < 0 || to < 0 || to >= blocks.length) return;
    mutate(() => { const [b] = blocks.splice(index, 1); blocks.splice(to, 0, b); target = null; });
  }
  function removeBlock(id) {
    const blocks = chapter().blocks, index = blocks.findIndex(b => b.id === id); if (index < 0) return;
    const kind = blocks[index].type === 'image' && isVideo(blocks[index].file) ? 'Video' : {image:'Picture', gallery:'Picture group', text:'Text'}[blocks[index].type];
    const notes = commentsOf(blocks[index]).length;
    mutate(() => { blocks.splice(index, 1); if (caret?.block === id) caret = null; target = null; });
    notify(notes ? `${kind} deleted, with its ${notes} comment${notes === 1 ? '' : 's'}.` : `${kind} deleted. Original image files are never deleted.`, false, undoAction);
  }
  function splitText(id, pos) {
    const blocks = chapter().blocks, index = blocks.findIndex(b => b.id === id); if (index < 0) return;
    const block = blocks[index];
    mutate(() => {
      const after = splitTextBlock(block, pos);
      blocks.splice(index + 1, 0, after);
      pendingFocus = {block:after.id, selector:'textarea', start:0};
    });
  }
  function joinWithPrevious(id) {
    const blocks = chapter().blocks, index = blocks.findIndex(b => b.id === id); if (index < 0) return false;
    const block = blocks[index], previous = blocks[index - 1];
    if (previous?.type === 'text') {
      const head = previous.text.replace(/\s+$/, ''), joiner = head && block.text ? '\n\n' : '';
      pendingFocus = {block:previous.id, selector:'textarea', start:head.length + joiner.length};
      mutate(() => {
        const shift = head.length + joiner.length;
        const kept = commentsOf(previous).map(c => { const end = Math.min(c.end, head.length); return {...c, start:Math.min(c.start, end), end}; });
        const joined = [...kept, ...commentsOf(block).map(c => ({...c, start:c.start + shift, end:c.end + shift}))];
        previous.text = head + joiner + block.text; blocks.splice(index, 1);
        if (joined.length) previous.comments = joined;
      });
      return true;
    }
    if (!block.text) {
      pendingFocus = previous ? {block:previous.id, selector:'textarea', start:previous.caption.length} : null;
      mutate(() => blocks.splice(index, 1));
      return true;
    }
    return false;
  }
  function selectChapter(id, nextView = 'chapter') {
    if (id) activeChapter = id;
    view = nextView; editGroup = null; target = null; caret = null;
    renderEditor(); saveBrowser();
    window.scrollTo({top:0, behavior:'instant'});
    if (window.innerWidth <= 760 && ui.chapters) { ui.chapters = false; renderLibraryLayout(); }
  }
  function addChapter(at = project.chapters.length) {
    mutate(() => { const c = {id:uid(), title:'Untitled chapter', notes:'', blocks:[]}; project.chapters.splice(at, 0, c); activeChapter = c.id; view = 'chapter'; });
    window.scrollTo({top:0, behavior:'instant'});
    $('chapter-title').focus(); $('chapter-title').select();
  }
  function setImageInfo(file, field, value) {
    if (blocked || !library.has(file)) return;
    history('info:' + file + field);
    project.extraImages ||= [];
    let entry = project.extraImages.find(x => x.file === file);
    if (!entry) { const info = metadata(file); entry = {file, title:info.title, category:info.category}; project.extraImages.push(entry); }
    entry[field] = value;
    library.set(file, {...library.get(file), [field]:value});
    updateCardInfo(file); changed(false); updateLibrary();
    if (field === 'category') {
      const categories = [...new Set([...library.values()].map(x => x.category))].sort();
      $('scene-list').innerHTML = categories.map(c => `<option value="${esc(c)}">`).join('');
    }
  }

  /* ---------- Lightbox ---------- */
  function openLightbox(list, index, from) {
    lightbox = {list, index, from};
    if (!$('lightbox').open) $('lightbox').showModal();
    renderLightbox();
  }
  function renderLightbox() {
    const entry = lightbox.list[lightbox.index]; if (!entry) { $('lightbox').close(); return; }
    const info = metadata(entry.file), counts = countsByFile(), video = isVideo(entry.file);
    $('lb-image').hidden = video; $('lb-video').hidden = !video;
    if (video) {
      if ($('lb-video').dataset.sourceFile !== entry.file) {
        $('lb-video').src = imageURL(entry.file); $('lb-video').dataset.sourceFile = entry.file;
        if (!document.body.classList.contains('hide-images')) $('lb-video').play().catch(() => {});
      }
      $('lb-video').setAttribute('aria-label', info.title);
    } else {
      stopLightboxVideo();
      $('lb-image').src = imageURL(entry.file); $('lb-image').alt = info.title;
      $('lb-image').dataset.sourceFile = entry.file;
    }
    $('lb-position').textContent = `${lightbox.index + 1} of ${lightbox.list.length}` + (lightbox.from === 'chapter' ? ' in this chapter' : '');
    $('lb-title').value = info.title; $('lb-category').value = info.category;
    $('lb-file').textContent = entry.file;
    const uses = [];
    project.chapters.forEach((c, i) => c.blocks.forEach(b => { if (blockFiles(b).includes(entry.file)) uses.push({c, i, b}); }));
    $('lb-used').innerHTML = uses.length ? 'Used in: ' + uses.map(u => `<button type="button" data-goto-block="${esc(u.b.id)}" data-goto-chapter="${esc(u.c.id)}">Ch. ${u.i + 1} · ${esc(chapterName(u.c))}</button>`).join('') : `<span class="hint">Not used in the story yet.</span>`;
    $('lb-prev').disabled = lightbox.index === 0; $('lb-next').disabled = lightbox.index >= lightbox.list.length - 1;
    const replacing = pick?.mode === 'replace';
    $('lb-insert').hidden = lightbox.from === 'chapter';
    $('lb-insert').textContent = replacing ? 'Use this picture' : pick?.mode === 'group-add' ? 'Add to the group' : counts.get(entry.file) ? 'Insert again' : 'Insert';
    $('lb-replace').hidden = lightbox.from !== 'chapter';
    $('lb-where').hidden = lightbox.from === 'chapter';
    updateInsertInfo();
  }
  // "Hide all images and videos" (Settings). Global, shared with the home page through localStorage.
  function applyHideImages(on) {
    $('setting-hide-images').checked = on;
    document.body.classList.toggle('hide-images', on);
    if (on) document.querySelectorAll('video').forEach(video => video.pause());
  }
  function stopLightboxVideo() {
    const video = $('lb-video'); if (!video.dataset.sourceFile) return;
    video.pause(); video.removeAttribute('src'); delete video.dataset.sourceFile; video.load();
  }
  function stepLightbox(delta) {
    const next = lightbox.index + delta;
    if (next >= 0 && next < lightbox.list.length) { lightbox.index = next; renderLightbox(); }
  }

  /* ---------- Comments ----------
     A comment belongs to one text block: {id, start, end, label, note, quote, suggestion?, resolved?, created, updated?}.
     start/end are string offsets into block.text and follow the words as the text is edited. A range whose words
     were all deleted shrinks to nothing; the comment stays, showing its original quote. */
  function commentsOf(b) { return b?.type === 'text' ? b.comments || [] : []; }
  function openComments(c) { return c.blocks.reduce((n, b) => n + commentsOf(b).filter(x => !x.resolved).length, 0); }
  function findComment(id) {
    for (const c of project.chapters) for (const b of c.blocks) {
      const comment = commentsOf(b).find(x => x.id === id);
      if (comment) return {chapter:c, block:b, comment};
    }
    return null;
  }
  function dropEmptyComments(block) { if (block.comments && !block.comments.length) delete block.comments; }
  // The single edit that turns `before` into `after`. The caret (where typing ended) settles ambiguous cases such as "aa" → "aaa".
  function textChange(before, after, caret = after.length) {
    let from = 0, tail = 0;
    const most = Math.min(before.length, after.length, caret);
    while (from < most && before[from] === after[from]) from++;
    const room = Math.min(before.length - from, after.length - Math.max(from, caret));
    while (tail < room && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail++;
    return {from, removedEnd:before.length - tail, inserted:after.length - tail - from};
  }
  const wordChar = ch => !!ch && /[\p{L}\p{N}'’-]/u.test(ch);
  // Moves every comment of `block` for a change at [from, removedEnd) replaced by `inserted` characters of `after`.
  // Typing right after (or right before) a commented word grows the comment only when it continues that word.
  function shiftComments(block, {from, removedEnd, inserted}, after) {
    const delta = inserted - (removedEnd - from), insertOnly = removedEnd === from && inserted > 0;
    commentsOf(block).forEach(c => {
      const empty = c.start === c.end;
      let start = c.start < from ? c.start : c.start >= removedEnd ? c.start + delta : from;
      let end = c.end <= from ? c.end : c.end >= removedEnd ? c.end + delta : from + inserted;
      if (insertOnly && !empty) {
        if (c.end === from && wordChar(after[from - 1]) && wordChar(after[from])) end = from + inserted;
        if (c.start === from && wordChar(after[from + inserted - 1]) && wordChar(after[from + inserted])) start = from;
      }
      c.start = start; c.end = Math.max(start, end);
    });
  }
  // Splits a text block's text at pos the way Ctrl+Enter does; comments go with their words.
  function splitTextBlock(block, pos) {
    const head = block.text.slice(0, pos).replace(/\s+$/, ''), rest = block.text.slice(pos);
    const skip = rest.length - rest.replace(/^\s+/, '').length, offset = pos + skip;
    const after = {id:uid(), type:'text', text:rest.slice(skip)}, keep = [], moved = [];
    commentsOf(block).forEach(c => {
      if (c.start >= pos) {
        const start = Math.max(0, c.start - offset); moved.push({...c, start, end:Math.max(start, c.end - offset)});
      } else {
        const end = Math.min(c.end, head.length); keep.push({...c, start:Math.min(c.start, end), end});
      }
    });
    block.text = head;
    if (block.comments) { block.comments = keep; dropEmptyComments(block); }
    if (moved.length) after.comments = moved;
    return after;
  }
  // The paragraph (text between blank lines) around pos, without its surrounding whitespace.
  function paragraphAt(text, pos) {
    const gap = /\n[ \t]*\n/g; let start = 0, end = text.length, m;
    while ((m = gap.exec(text))) {
      if (m.index >= pos) { end = m.index; break; }
      start = m.index + m[0].length;
    }
    while (start < end && /\s/.test(text[start])) start++;
    while (end > start && /\s/.test(text[end - 1])) end--;
    return [start, end];
  }
  // Highlights for one text block. Overlapping comments nest; the narrowest one decides the colour.
  function marksHTML(block) {
    const text = block.text, list = commentsOf(block).filter(c => !c.resolved && c.end > c.start);
    if (!list.length) return esc(text);
    const cuts = [...new Set([0, text.length, ...list.flatMap(c => [c.start, c.end])])].sort((a, b) => a - b);
    let html = '';
    for (let i = 0; i < cuts.length - 1; i++) {
      const a = cuts[i], b = cuts[i + 1], piece = esc(text.slice(a, b));
      const over = list.filter(c => c.start <= a && c.end >= b).sort((x, y) => (x.end - x.start) - (y.end - y.start));
      if (!over.length) { html += piece; continue; }
      const ids = over.map(c => c.id).join(' '), active = over.some(c => c.id === activeComment);
      html += `<mark class="cm cm-${over[0].label}${over.length > 1 ? ' cm-overlap' : ''}${active ? ' cm-active' : ''}" data-comments="${esc(ids)}">${piece}</mark>`;
    }
    return html;
  }
  function renderMarks(article, block) {
    const mirror = article?.querySelector('.prose-marks');
    if (mirror) mirror.innerHTML = marksHTML(block);
  }
  function commentAtCaret(block, pos) {
    const here = commentsOf(block).filter(c => !c.resolved && c.start <= pos && pos <= c.end && c.end > c.start);
    return here.sort((x, y) => (x.end - x.start) - (y.end - y.start))[0]?.id || null;
  }
  function setActiveComment(id, reveal = true) {
    if (activeComment === id) return;
    activeComment = id;
    document.querySelectorAll('mark.cm-active').forEach(m => m.classList.remove('cm-active'));
    document.querySelectorAll('.comment-card.active').forEach(card => card.classList.remove('active'));
    if (!id) return;
    document.querySelectorAll(`mark[data-comments~="${CSS.escape(id)}"]`).forEach(m => m.classList.add('cm-active'));
    const card = $('comment-list').querySelector(`[data-comment-id="${CSS.escape(id)}"]`);
    card?.classList.add('active');
    if (reveal && card) card.scrollIntoView({block:'nearest'});
  }
  function addComment(blockId, start, end, inDialog = false) {
    const found = project.chapters.flatMap(c => c.blocks.map(b => ({c, b}))).find(x => x.b.id === blockId);
    if (!found || found.b.type !== 'text' || blocked) return;
    const block = found.b;
    if (start === end) [start, end] = paragraphAt(block.text, start);
    if (start === end) { notify('Write some text first, then comment on it.'); return; }
    if (commentsOf(block).length >= MAX_COMMENTS) { notify(`A text block can hold at most ${MAX_COMMENTS} comments.`, true); return; }
    const comment = {id:uid(), start, end, label:'note', note:'', quote:block.text.slice(start, end).slice(0, 2000), created:new Date().toISOString()};
    activeComment = comment.id;
    mutate(() => { (block.comments ||= []).push(comment); });
    if (inDialog) { openCommentDialog(comment.id); return; }
    if (!ui.comments) { ui.comments = true; ui.library = false; saveUI(); renderLibraryLayout(); renderComments(); }
    const note = $('comment-list').querySelector(`[data-comment-id="${CSS.escape(comment.id)}"] [data-comment-field="note"]`);
    note?.focus(); note?.scrollIntoView({block:'nearest'});
  }
  function commentFromTextarea(textarea) {
    const article = textarea?.closest('[data-block]'); if (!article) return;
    addComment(article.dataset.block, textarea.selectionStart, textarea.selectionEnd);
  }
  function goToComment(id) {
    const found = findComment(id); if (!found) return;
    if ($('comment-dialog').open) $('comment-dialog').close();
    if (previewing) showPreview(false);
    activeChapter = found.chapter.id; view = 'chapter'; target = null;
    activeComment = id;
    const {start, end} = found.comment;
    pendingFocus = {block:found.block.id, selector:'[data-edit="text"]', start, end};
    renderEditor();
    activeComment = null; setActiveComment(id);
    const article = $('blocks').querySelector(`[data-block="${CSS.escape(found.block.id)}"]`);
    (article?.querySelector(`mark[data-comments~="${CSS.escape(id)}"]`) || article)?.scrollIntoView({block:'center', behavior:'smooth'});
  }
  function commentAction(id, action) {
    const found = findComment(id); if (!found || blocked) return;
    const {block, comment} = found, now = new Date().toISOString();
    if (action === 'resolve') {
      mutate(() => { if (comment.resolved) delete comment.resolved; else comment.resolved = true; comment.updated = now; });
      if (comment.resolved && !showResolved) notify('Comment resolved. Tick “Show resolved” to see it again.', false, undoAction);
    } else if (action === 'delete') {
      mutate(() => { block.comments.splice(block.comments.indexOf(comment), 1); dropEmptyComments(block); if (activeComment === id) activeComment = null; });
      if ($('comment-dialog').open) $('comment-dialog').close();
      notify('Comment deleted.', false, undoAction);
    } else if (action === 'suggest') {
      mutate(() => { comment.suggestion = block.text.slice(comment.start, comment.end); comment.updated = now; });
      const field = document.querySelector(`[data-comment-id="${CSS.escape(id)}"] [data-comment-field="suggestion"]`);
      field?.focus(); field?.select();
    } else if (action === 'accept') {
      const replacement = comment.suggestion ?? '';
      mutate(() => {
        const after = block.text.slice(0, comment.start) + replacement + block.text.slice(comment.end);
        const change = {from:comment.start, removedEnd:comment.end, inserted:replacement.length};
        const others = block.comments.filter(c => c !== comment);
        shiftComments({type:'text', comments:others}, change, after);
        block.text = after;
        comment.end = comment.start + replacement.length; delete comment.suggestion;
        comment.resolved = true; comment.updated = now;
        if (!comment.note.trim()) block.comments.splice(block.comments.indexOf(comment), 1);
        dropEmptyComments(block);
      });
      notify('Change applied to the story text.', false, undoAction);
    } else if (action === 'reject') {
      mutate(() => { delete comment.suggestion; comment.updated = now; });
    }
  }
  function setCommentField(id, field, value) {
    const found = findComment(id); if (!found || blocked) return;
    history('comment:' + id + field);
    found.comment[field] = value; found.comment.updated = new Date().toISOString();
    changed(false);
  }
  function commentDate(iso) {
    const date = iso ? new Date(iso) : null;
    if (!date || isNaN(date)) return '';
    return date.toDateString() === new Date().toDateString()
      ? date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : date.toLocaleDateString([], {day:'numeric', month:'short'});
  }
  function commentCardHTML({chapter: c, block, comment}, withChapter = false) {
    const current = block.text.slice(comment.start, comment.end);
    const quote = current ? esc(current.length > 400 ? current.slice(0, 400) + '…' : current)
      : `<del>${esc(comment.quote.slice(0, 400))}</del> <span class="comment-gone">the commented words were deleted</span>`;
    const where = withChapter ? `Ch. ${project.chapters.indexOf(c) + 1} · ${esc(chapterName(c))}` : '';
    const suggestion = comment.suggestion !== undefined ? `<div class="comment-suggestion">
        <label><span class="comment-sub">Replace with</span><textarea data-comment-field="suggestion" rows="1" placeholder="New wording (leave empty to delete the words)…">${esc(comment.suggestion)}</textarea></label>
        <div class="comment-row"><button type="button" class="primary" data-comment-action="accept">Accept change</button><button type="button" data-comment-action="reject">Discard suggestion</button></div>
      </div>` : '';
    return `<article class="comment-card label-${comment.label}${comment.resolved ? ' resolved' : ''}${comment.id === activeComment ? ' active' : ''}" data-comment-id="${esc(comment.id)}">
      <button type="button" class="comment-quote" data-comment-go title="Show in the text">${quote}</button>
      <div class="comment-labels" role="group" aria-label="Label">${Object.entries(COMMENT_LABELS).map(([key, name]) => `<button type="button" class="label-chip label-${key}" data-comment-label="${key}" aria-pressed="${key === comment.label}">${name}</button>`).join('')}</div>
      <textarea class="comment-note" data-comment-field="note" rows="1" placeholder="Write a comment… (optional)" aria-label="Comment">${esc(comment.note)}</textarea>
      ${suggestion}
      <div class="comment-footer"><span class="comment-meta">${where ? where + ' · ' : ''}${commentDate(comment.updated || comment.created)}${comment.resolved ? ' · resolved' : ''}</span>
        <span class="comment-actions">${comment.suggestion === undefined && !comment.resolved ? '<button type="button" class="quiet" data-comment-action="suggest" title="Propose new wording you can accept later">Suggest wording</button>' : ''}<button type="button" data-comment-action="resolve">${comment.resolved ? 'Reopen' : 'Resolve'}</button><button type="button" class="quiet danger" data-comment-action="delete" aria-label="Delete comment" title="Delete comment">Delete</button></span></div>
    </article>`;
  }
  // Re-render keeping focus and the caret in whichever comment field was being edited.
  function keepCommentFocus(root, render) {
    const active = document.activeElement, card = root.contains(active) ? active.closest('[data-comment-id]') : null;
    const field = active?.dataset?.commentField, start = active?.selectionStart, end = active?.selectionEnd;
    render();
    if (!card) return;
    const again = root.querySelector(`[data-comment-id="${CSS.escape(card.dataset.commentId)}"] ${field ? `[data-comment-field="${field}"]` : '.comment-quote'}`);
    if (!again) return;
    again.focus({preventScroll:true});
    if (field && start !== undefined) again.setSelectionRange(start, end);
  }
  function renderComments() {
    if (!project) return;
    const open = project.chapters.reduce((n, c) => n + openComments(c), 0);
    $('comments-count').textContent = open; $('comments-count').hidden = !open;
    if ($('comment-dialog').open) renderCommentDialog();
    if (previewing) refreshPreviewMarks();
    if (!ui.comments) return;
    const story = ui.commentScope === 'story', chapters = story ? project.chapters : [chapter()];
    document.querySelectorAll('[data-comment-scope]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.commentScope === ui.commentScope)));
    $('comments-resolved').checked = showResolved;
    let shown = 0, resolvedHidden = 0;
    const html = chapters.map(c => {
      const items = c.blocks.flatMap(b => commentsOf(b).map(comment => ({chapter:c, block:b, comment})).sort((x, y) => x.comment.start - y.comment.start || x.comment.end - y.comment.end))
        .filter(x => { if (x.comment.resolved && !showResolved) { resolvedHidden++; return false; } return true; });
      shown += items.length;
      if (!items.length) return '';
      return (story ? `<h3 class="comment-chapter"><button type="button" class="link" data-comment-chapter="${esc(c.id)}">${project.chapters.indexOf(c) + 1}. ${esc(chapterName(c))}</button></h3>` : '') + items.map(x => commentCardHTML(x, story)).join('');
    }).join('');
    const where = story ? 'in this story' : 'in this chapter';
    keepCommentFocus($('comment-list'), () => {
      $('comment-list').innerHTML = html || `<p class="comment-empty">No ${showResolved ? '' : 'open '}comments ${where}.${resolvedHidden ? ` ${resolvedHidden} resolved comment${resolvedHidden === 1 ? ' is' : 's are'} hidden.` : ''}</p>`;
    });
    $('comments-total').textContent = story ? open : openComments(chapter());
    autosizeComments($('comment-list'));
  }
  function scheduleComments() { clearTimeout(commentsTimer); commentsTimer = setTimeout(renderComments, 250); }
  function autosizeComments(root) { if (!autosizeNative) root.querySelectorAll('textarea').forEach(autosize); }
  function openCommentDialog(id) {
    activeComment = id;
    $('comment-dialog').dataset.comment = id;
    renderCommentDialog();
    if (!$('comment-dialog').open) $('comment-dialog').showModal();
    $('comment-dialog').querySelector('[data-comment-field="note"]')?.focus();
  }
  function renderCommentDialog() {
    const found = findComment($('comment-dialog').dataset.comment);
    if (!found) { $('comment-dialog').close(); return; }
    keepCommentFocus($('comment-dialog-body'), () => {
      $('comment-dialog-body').innerHTML = `<span class="eyebrow">Comment · ${esc(chapterName(found.chapter))}</span>` + commentCardHTML(found)
        .replace('title="Show in the text"', 'title="Show this in the editor"');
    });
    autosizeComments($('comment-dialog-body'));
  }
  // Floating "Comment" button: above the selection in a text block, or in the story while reading.
  function placeCommentFloat(top, left) {
    const button = $('comment-float');
    button.hidden = false;
    const width = button.offsetWidth || 110;
    button.style.top = Math.max(window.scrollY + 60, top - 38) + 'px';
    button.style.left = Math.min(Math.max(8, left - width / 2), document.documentElement.clientWidth - width - 8) + 'px';
  }
  function hideCommentFloat() { $('comment-float').hidden = true; $('comment-float').dataset.mode = ''; }
  function updateCommentFloat() {
    const textarea = document.activeElement;
    if (blocked || previewing || !textarea?.matches?.('#blocks textarea[data-edit="text"]') || textarea.selectionStart === textarea.selectionEnd) {
      if ($('comment-float').dataset.mode !== 'read') hideCommentFloat();
      return;
    }
    // Measure where the selection starts with a probe in the highlight layer, which lays the text out exactly like the textarea.
    const mirror = textarea.parentElement.querySelector('.prose-marks'), text = textarea.value, pos = textarea.selectionStart;
    mirror.innerHTML = esc(text.slice(0, pos)) + '<span class="caret-probe">​</span>' + esc(text.slice(pos));
    const rect = mirror.querySelector('.caret-probe').getBoundingClientRect();
    const block = chapter().blocks.find(b => b.id === textarea.closest('[data-block]').dataset.block);
    if (block) mirror.innerHTML = marksHTML(block);
    $('comment-float').dataset.mode = 'edit';
    placeCommentFloat(rect.top + window.scrollY, rect.left + window.scrollX + 40);
  }
  // Read mode: the selection inside one paragraph block, as offsets into that block's text.
  function readSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0), prose = (range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement)?.closest('#preview .prose[data-block]');
    if (!prose) return null;
    const offset = (node, at) => { const r = document.createRange(); r.selectNodeContents(prose); r.setEnd(node, at); return r.toString().length; };
    const start = offset(range.startContainer, range.startOffset);
    const end = prose.contains(range.endContainer) ? offset(range.endContainer, range.endOffset) : prose.textContent.length;
    return end > start ? {block:prose.dataset.block, start, end, rect:range.getBoundingClientRect()} : null;
  }
  function updateReadFloat() {
    if (!previewing || !ui.readComments || blocked) { if ($('comment-float').dataset.mode === 'read') hideCommentFloat(); return; }
    const picked = readSelection();
    if (!picked) { hideCommentFloat(); return; }
    $('comment-float').dataset.mode = 'read';
    placeCommentFloat(picked.rect.top + window.scrollY, picked.rect.left + window.scrollX + picked.rect.width / 2);
  }
  function refreshPreviewMarks() {
    $('preview').querySelectorAll('.prose[data-block]').forEach(div => {
      const block = project.chapters.flatMap(c => c.blocks).find(b => b.id === div.dataset.block);
      if (block) div.innerHTML = ui.readComments ? marksHTML(block) : esc(block.text);
    });
  }

  /* ---------- Export / preview ---------- */
  function download(name, contents, type) {
    const url = URL.createObjectURL(new Blob([contents], {type}));
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  function projectName(extension) {
    const slug = project.title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').slice(0, 80) || 'my-story';
    return slug + extension;
  }
  const READER_CSS = `body{font:18px/1.8 Georgia,serif;color:#23372f;background:#f5f0e6;margin:0}main{max-width:800px;margin:auto;padding:3rem 1.5rem}h1,h2{font-weight:400;line-height:1.2;overflow-wrap:anywhere}h1{font-size:clamp(2.5rem,6vw,4.5rem);margin:0 0 1rem}h2{font-size:2.3rem}.subtitle{font:1.15rem/1.7 system-ui;color:#657166;white-space:pre-wrap}.preview-chapter{border-top:1px solid #d5d9cc;margin-top:3rem;padding-top:2rem}.eyebrow{font:.7rem system-ui;letter-spacing:.15em;text-transform:uppercase;color:#83502c}.prose{white-space:pre-wrap;overflow-wrap:anywhere;margin:1.5rem 0}figure{margin:2rem 0}:is(img,video){display:block;max-width:100%;max-height:850px;margin:auto}figure.size-small :is(img,video){max-width:45%;max-height:420px}figure.size-medium :is(img,video){max-width:70%;max-height:640px}figure.size-wide{width:min(calc(100vw - 2rem),1200px);position:relative;left:50%;transform:translateX(-50%)}figure.size-wide :is(img,video){width:100%;max-height:90vh;object-fit:contain}figcaption{font:italic .95rem/1.6 Georgia,serif;text-align:center;color:#657166;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:.8rem}nav{display:flex;gap:.7rem;flex-wrap:wrap;font:.9rem system-ui;margin:2rem 0}a{color:inherit}footer{font:.75rem system-ui;color:#657166;margin-top:3rem}.gallery-cells{display:grid;gap:.5rem;grid-template-columns:repeat(var(--cols),minmax(0,1fr))}figure.gallery .gallery-cells :is(img,video){width:100%;max-width:100%;max-height:none;margin:0}figure.layout-grid .gallery-cells :is(img,video){aspect-ratio:1;object-fit:cover}figure.gallery.size-small .gallery-cells{width:60%;margin:auto}figure.gallery.size-medium .gallery-cells{width:80%;margin:auto}@media(max-width:600px){.gallery-cells{grid-template-columns:repeat(2,minmax(0,1fr))}figure.gallery.size-small .gallery-cells,figure.gallery.size-medium .gallery-cells{width:100%}}.disclaimer{max-width:560px;margin:0 auto 3rem;padding:1.6rem 1.8rem;border:1px solid #bcc6b8;border-radius:10px;background:#fffdf8;text-align:center;font-size:1rem;white-space:pre-wrap;overflow-wrap:anywhere}#gate{position:fixed;inset:0;z-index:10;display:flex;align-items:center;justify-content:center;padding:1.5rem;background:#f5f0e6}#gate .disclaimer{margin:0}#gate button{display:block;margin:1.4rem auto 0;font:1rem system-ui;padding:.7rem 1.8rem;border-radius:8px;border:0;background:#23372f;color:#fff;cursor:pointer}body.gated{overflow:hidden}body.gated main{visibility:hidden}@media(max-width:600px){figure.size-small :is(img,video){max-width:70%}}@media print{body{background:white}.preview-chapter{break-before:page;border:0}figure{break-inside:avoid}:is(img,video){max-height:23cm}figure.size-wide{width:100%;left:0;transform:none}nav{display:none}}`;
  function disclaimerMarkup(forExport) {
    const text = (project.disclaimer || '').trim();
    if (!text) return '';
    const note = !forExport && project.disclaimerGate ? '<span class="disclaimer-note">In the exported story, readers must click Continue first.</span>' : '';
    return `<aside class="disclaimer" aria-label="Disclaimer">${esc(text)}${note}</aside>`;
  }
  // media (exports only): file -> {src, still}; a still is a video's first frame, used in PDFs. Without an entry,
  // the export links the file by name, which works only beside the story's files.
  function figureMarkup(b, forExport, media = null, forPdf = false) {
    const img = file => {
      const entry = forExport ? media?.get(file) : null;
      const src = esc(entry ? entry.src : forExport ? fileURL(file) : imageURL(file)), title = esc(metadata(file).title);
      if (forPdf && !entry) return `<div class="media-missing">${isVideo(file) ? 'Video' : 'Picture'}: ${title}</div>`;
      return (isVideo(file) && !entry?.still ? `<video src="${src}" aria-label="${title}" controls playsinline preload="metadata"></video>` : `<img src="${src}" alt="${title}"${forPdf ? '' : ' loading="lazy"'}>`) + (forExport ? '' : hiddenMediaHTML(file));
    };
    const classes = [b.size && b.size !== 'large' ? 'size-' + b.size : '', b.type === 'gallery' ? 'gallery layout-' + b.layout : ''].filter(Boolean).join(' ');
    const body = b.type === 'gallery' ? `<div class="gallery-cells" style="--cols:${groupColumns(b)}">${b.items.map(x => img(x.file)).join('')}</div>` : img(b.file);
    return `<figure${classes ? ` class="${esc(classes)}"` : ''}>${body}${b.caption ? `<figcaption>${esc(b.caption)}</figcaption>` : ''}</figure>`;
  }
  function storyMarkup(forExport = false, media = null, forPdf = false) {
    return disclaimerMarkup(forExport) + `<h1>${esc(project.title || 'Untitled story')}</h1>${project.subtitle ? `<p class="subtitle">${esc(project.subtitle)}</p>` : ''}<nav aria-label="Chapters">${project.chapters.map((c,i) => `<a href="#read-${esc(c.id)}">${i+1}. ${esc(chapterName(c))}</a>`).join(' · ')}</nav>` + project.chapters.map((c,index) => `<section class="preview-chapter" id="read-${esc(c.id)}"><div class="eyebrow">Chapter ${index+1}${forExport ? '' : `<button type="button" class="preview-edit" data-edit-chapter="${esc(c.id)}">Edit</button>`}</div><h2>${esc(chapterName(c))}</h2>${c.blocks.map(b => b.type === 'text' ? (b.text ? (forExport ? `<div class="prose">${esc(b.text)}</div>` : `<div class="prose" data-block="${esc(b.id)}">${ui.readComments ? marksHTML(b) : esc(b.text)}</div>`) : '') : figureMarkup(b, forExport, media, forPdf)).join('')}${!forExport && !c.blocks.length ? '<p class="preview-empty">This chapter is empty. Return to the editor to add text or images.</p>' : ''}</section>`).join('');
  }
  /* Exports embed every picture, so the file works anywhere. HTML gets WebP (sharp, small); PDF gets JPEG, which
     the browser's PDF writer stores as is, and a still frame for each video. Pictures are capped at maxSide pixels. */
  const EXPORT_FORMATS = {html:{type:'image/webp', quality:0.9, maxSide:2048}, pdf:{type:'image/jpeg', quality:0.88, maxSide:1600}};
  const MAX_EMBEDDED_VIDEO = 200e6;
  const PDF_CSS = '@page{size:A4;margin:16mm 14mm}.media-missing{margin:auto;max-width:70%;padding:2rem;border:1px dashed #bcc6b8;border-radius:8px;text-align:center;font:italic .95rem/1.6 Georgia,serif;color:#657166}';
  let exporting = false;
  function exportFiles() { return [...new Set(project.chapters.flatMap(c => c.blocks.flatMap(blockFiles)))]; }
  function blobDataURL(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result); reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }
  async function encodePicture(source, width, height, format) {
    const scale = Math.min(1, format.maxSide / Math.max(width, height, 1));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale)); canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d');
    if (format.type === 'image/jpeg') { context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height); }
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    const encode = type => new Promise(resolve => canvas.toBlob(resolve, type, format.quality));
    let blob = await encode(format.type);
    if (!blob || blob.type !== format.type) blob = await encode('image/jpeg'); // Browsers that cannot write WebP.
    if (!blob) throw Error('The browser could not encode the picture.');
    return {blob, scaled: scale < 1};
  }
  async function fetchMedia(file) {
    const response = await fetch(imageURL(file));
    if (!response.ok) throw Error(`${file} is not available (${response.status}).`);
    return response.blob();
  }
  async function embedPicture(file, format) {
    const original = await fetchMedia(file);
    if (/\.(gif|svg)$/i.test(file)) return {src: await blobDataURL(original)}; // Keeps animation and vectors.
    let bitmap;
    try { bitmap = await createImageBitmap(original); } catch { return {src: await blobDataURL(original)}; }
    try {
      const {blob, scaled} = await encodePicture(bitmap, bitmap.width, bitmap.height, format);
      const keepOriginal = !scaled && original.size <= blob.size && (format.type !== 'image/jpeg' || original.type === 'image/jpeg');
      return {src: await blobDataURL(keepOriginal ? original : blob)};
    } finally { bitmap.close(); }
  }
  function videoStill(file, format) {
    return new Promise((resolve, reject) => {
      const video = document.createElement('video');
      const done = () => { clearTimeout(timer); video.removeAttribute('src'); video.load(); };
      const timer = setTimeout(() => { done(); reject(Error(`${file}: the video did not load.`)); }, 30000);
      video.muted = true; video.playsInline = true; video.preload = 'auto';
      video.onerror = () => { done(); reject(Error(`${file}: the video could not be read.`)); };
      video.onloadeddata = () => { video.currentTime = Math.min(0.5, (video.duration || 1) / 10); };
      video.onseeked = async () => {
        try { resolve({src: await blobDataURL((await encodePicture(video, video.videoWidth, video.videoHeight, format)).blob), still: true}); }
        catch (error) { reject(error); } finally { done(); }
      };
      video.src = imageURL(file);
    });
  }
  async function embedMedia(kind, progress) {
    const format = EXPORT_FORMATS[kind], files = exportFiles(), media = new Map(), failed = [];
    let next = 0, finished = 0;
    async function worker() {
      while (next < files.length) {
        const file = files[next++];
        try {
          if (!isVideo(file)) media.set(file, await embedPicture(file, format));
          else if (kind === 'pdf') media.set(file, await videoStill(file, format));
          else {
            const original = await fetchMedia(file);
            if (original.size > MAX_EMBEDDED_VIDEO) failed.push(file); else media.set(file, {src: await blobDataURL(original)});
          }
        } catch (error) { console.warn('Export:', error); failed.push(file); }
        progress(++finished, files.length);
      }
    }
    await Promise.all(Array.from({length: Math.min(4, files.length)}, worker));
    return {media, failed};
  }
  function storyDocument(media, forPdf) {
    const gate = !forPdf && !!project.disclaimerGate && !!(project.disclaimer || '').trim();
    const story = storyMarkup(true, media, forPdf);
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(project.title || 'Untitled story')}</title><style>${READER_CSS}${forPdf ? PDF_CSS : ''}</style>${gate ? '<noscript><style>body.gated main{visibility:visible}#gate{display:none}</style></noscript>' : ''}</head><body${gate ? ' class="gated"' : ''}>${gate ? `<div id="gate" role="dialog" aria-modal="true" aria-label="Disclaimer"><div><aside class="disclaimer">${esc(project.disclaimer.trim())}</aside><button type="button" onclick="document.body.classList.remove('gated');document.getElementById('gate').remove();scrollTo(0,0)">Continue</button></div></div>` : ''}<main>${gate ? story.replace(disclaimerMarkup(true), '') : story}<footer>Illustrated story · exported ${new Date().toLocaleDateString()}</footer></main></body></html>`;
  }
  function printFromReadMode() { if (!previewing) showPreview(true); setTimeout(() => window.print(), 100); }
  async function exportStory(kind) {
    if (exporting) { notify('An export is already running. Wait for it to finish.'); return; }
    if (kind === 'pdf' && !server) { notify('PDF export needs the Story Studio app. Open Read and use Print / PDF instead.', true, {label:'Print', run:printFromReadMode}); return; }
    exporting = true; ['export-html', 'export-pdf'].forEach(id => { $(id).disabled = true; });
    const label = kind === 'pdf' ? 'PDF' : 'HTML';
    try {
      notify(`Preparing the ${label} export…`);
      const {media, failed} = await embedMedia(kind, (done, total) => notify(`Preparing the ${label} export… picture ${done} of ${total}`));
      const html = storyDocument(media, kind === 'pdf');
      const missing = failed.length ? ` ${failed.length} picture${failed.length === 1 ? ' was' : 's were'} not embedded and only show beside the story’s files: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}` : '';
      if (kind === 'html') {
        download(projectName('.html'), html, 'text/html;charset=utf-8');
        notify(`Story exported as one HTML file with the pictures inside. Private notes and comments are not included.${missing}`, !!missing);
        return;
      }
      notify('Creating the PDF…');
      const response = await fetch('api/export-pdf', {method:'POST', headers:{'Content-Type':'text/html;charset=utf-8', 'X-Story-Token':server.token}, body:html});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        if (response.status === 501) { notify(`${body.error || 'PDF export is not available here.'} You can still print to PDF from Read mode.`, true, {label:'Print', run:printFromReadMode}); return; }
        throw Error(body.error || `PDF export failed (${response.status}).`);
      }
      download(projectName('.pdf'), await response.blob(), 'application/pdf');
      notify(`Story exported as PDF. Private notes and comments are not included.${missing}`, !!missing);
    } catch (error) {
      notify(`${label} export failed: ${error.message}`, true);
    } finally {
      exporting = false; ['export-html', 'export-pdf'].forEach(id => { $(id).disabled = false; });
    }
  }
  function showPreview(show) {
    previewing = show; hideCommentFloat(); $('workspace').hidden = show; $('preview').hidden = !show;
    document.body.classList.toggle('previewing', show);
    $('preview-toggle').textContent = show ? '← Edit' : 'Read';
    $('preview-toggle').setAttribute('aria-pressed', String(show));
    if (show) {
      $('preview').innerHTML = `<div class="preview-actions"><span>Reading the live draft · private notes are left out</span><span class="preview-tools"><label title="Show comment highlights. Select words to comment on them; click a highlight to open its comment."><input type="checkbox" id="preview-comments" ${ui.readComments ? 'checked' : ''}> Show comments</label><button type="button" id="print-story">Print / PDF</button></span></div>` + storyMarkup();
      $('print-story').addEventListener('click', () => window.print());
      $('preview').classList.toggle('show-comments', ui.readComments);
      $('preview-comments').addEventListener('change', event => {
        ui.readComments = event.target.checked; saveUI();
        $('preview').classList.toggle('show-comments', ui.readComments); refreshPreviewMarks(); updateReadFloat();
      });
      const here = view === 'chapter' && $('read-' + activeChapter);
      if (here && project.chapters[0].id !== activeChapter) { here.scrollIntoView({block:'start', behavior:'instant'}); window.scrollBy(0, -110); return; }
    }
    window.scrollTo({top:0, behavior:'instant'});
  }
  function recovery(mode, message, raw) {
    blocked = true; recoveryMode = mode; recoveryRaw = raw;
    $('workspace').disabled = true; $('recovery').hidden = false; $('recovery-message').textContent = message;
    $('recovery-resolve').textContent = mode === 'corrupt' ? 'Use starting draft…' : 'Reload saved draft';
    browserStatus('Saving paused — resolve the warning above', true);
    clearTimeout(diskTimer); fileHandle = null; updateHistory(); renderSaveIndicator();
  }
  function loadBrowserInitial() {
    let restoredChapter = '';
    project = clone(DATA.seed);
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        try {
          const stored = JSON.parse(raw);
          project = validate(stored.project);
          if (project.chapters.some(c => c.id === stored.activeChapter)) restoredChapter = stored.activeChapter;
        }
        catch (error) { recovery('corrupt', 'The stored draft could not be loaded. Automatic saving is paused to avoid overwriting it. Download the recovery copy before choosing a starting draft, or open a valid JSON project. ' + error.message, raw); }
      }
    } catch { browserSaved = false; }
    activeChapter = restoredChapter || project.chapters[0].id;
    buildLibrary(); renderEditor(); if (!blocked) saveBrowser();
  }
  async function applyImport(raw, handle = null) {
    const parsed = validate(JSON.parse(raw));
    if (serverMode && blocked) throw Error('Reload the saved project before importing a replacement. Download the recovery copy first.');
    if (!confirm('Replace the current working draft with this project? Download a backup first if you need to keep both.')) return;
    await flushDisk();
    if (serverMode && blocked) throw Error('Saving paused because the disk project changed. Reload it before importing.');
    history(); fileHandle = null; diskSavedVersion = '';
    project = parsed; activeChapter = project.chapters[0].id; selected.clear(); target = null; caret = null;
    blocked = false; $('workspace').disabled = false; $('recovery').hidden = true;
    buildLibrary(); fileHandle = serverMode ? null : handle;
    if (!serverMode) fileStatus(handle ? 'Linked to ' + handle.name : 'No project file linked — browser autosave only', handle ? 'ok' : 'idle');
    changed(); if (previewing) showPreview(true);
    if (serverMode) {
      await flushDisk();
      notify(serverDirty || blocked ? 'Imported into this tab, but the file is not saved yet. Check the save warning and keep this page open.' : 'Project imported and automatically saved in story-project.json.', serverDirty || blocked);
      return;
    }
    notify(handle ? 'Project opened. Changes will also be written to ' + handle.name + ' during this session.' : 'Project opened and saved in this browser. Download backups regularly or link an autosave file.');
  }
  function stepHistory(from, to) {
    if (blocked || !from.length) return;
    to.push({project:clone(project), activeChapter}); const snapshot = from.pop(); project = snapshot.project; activeChapter = snapshot.activeChapter;
    editGroup = null; target = null; caret = null; buildLibrary(); changed(); if (previewing) showPreview(true);
    if ($('lightbox').open) renderLightbox();
  }
  function closeMenus(except = null) {
    document.querySelectorAll('.menu:not([hidden])').forEach(menu => {
      if (menu === except) return;
      menu.hidden = true; menu.previousElementSibling?.setAttribute('aria-expanded', 'false');
    });
  }
  function typing(target) { return target.matches?.('input, textarea, select, [contenteditable]'); }

  /* ---------- Events ---------- */
  function bindEvents() {
    const editField = (id, target, field) => $(id).addEventListener('input', event => {
      if (blocked) return;
      history(id + (target === 'chapter' ? activeChapter : ''));
      (target === 'chapter' ? chapter() : project)[field] = event.target.value;
      autosize(event.target); changed(false);
    });
    editField('project-title', 'project', 'title'); editField('project-subtitle', 'project', 'subtitle');
    editField('chapter-title', 'chapter', 'title'); editField('chapter-notes', 'chapter', 'notes');
    document.addEventListener('focusout', () => { editGroup = null; });

    // Menus
    document.addEventListener('click', event => {
      const toggle = event.target.closest('[data-menu]');
      if (toggle) {
        const menu = toggle.nextElementSibling, open = menu.hidden;
        closeMenus(menu); menu.hidden = !open; toggle.setAttribute('aria-expanded', String(open));
        return;
      }
      if (!event.target.closest('.menu') || event.target.closest('.menu > button, .menu > a')) closeMenus();
    });

    // Top bar
    $('toggle-chapters').addEventListener('click', () => { ui.chapters = !ui.chapters; saveUI(); renderLibraryLayout(); });
    $('toggle-library').addEventListener('click', () => {
      if (document.body.classList.contains('library-expanded')) { setLibraryExpanded(false); ui.library = false; }
      else ui.library = !ui.library;
      if (ui.library) ui.comments = false; // The library and the comments share the right-hand column.
      saveUI(); renderLibraryLayout();
    });
    $('toggle-comments').addEventListener('click', () => toggleComments());
    $('comments-close').addEventListener('click', () => toggleComments(false));
    $('focus-toggle').addEventListener('click', () => {
      const focusing = ui.chapters || ui.library || ui.comments;
      if (focusing) { ui.beforeFocus = {chapters:ui.chapters, library:ui.library, comments:ui.comments}; ui.chapters = ui.library = ui.comments = false; }
      else { ui.chapters = ui.beforeFocus?.chapters ?? true; ui.library = ui.beforeFocus?.library ?? true; ui.comments = !ui.library && !!ui.beforeFocus?.comments; }
      saveUI(); renderLibraryLayout(); renderComments();
    });
    $('topbar-title').addEventListener('click', () => { if (previewing) showPreview(false); selectChapter(null, 'title'); $('project-title').focus(); });
    $('title-page-item').addEventListener('click', () => selectChapter(null, 'title'));
    $('title-page-continue').addEventListener('click', () => selectChapter(null, 'chapter'));
    $('outline-item').addEventListener('click', () => { selectChapter(null, 'outline'); $('outline-text').focus({preventScroll:true}); });
    $('disclaimer-item').addEventListener('click', () => { selectChapter(null, 'disclaimer'); $('disclaimer-text').focus({preventScroll:true}); });
    $('disclaimer-continue').addEventListener('click', () => selectChapter(null, 'title'));
    editField('outline-text', 'project', 'outline'); editField('disclaimer-text', 'project', 'disclaimer');
    $('disclaimer-gate').addEventListener('change', event => {
      const on = event.target.checked;
      mutate(() => { if (on) project.disclaimerGate = true; else delete project.disclaimerGate; });
    });
    $('outline-chapters').addEventListener('click', () => {
      const list = project.chapters.map((c, i) => `${i + 1}. ${chapterName(c)}\n   `).join('\n');
      const current = (project.outline || '').replace(/\s+$/, '');
      mutate(() => { project.outline = (current ? current + '\n\n' : '') + 'Chapters\n\n' + list; });
      const area = $('outline-text'); area.focus(); area.setSelectionRange(area.value.length, area.value.length); area.scrollIntoView({block:'end'});
    });
    $('outline-download').addEventListener('click', () => {
      download(projectName('-outline.md'), `# ${project.title || 'Untitled story'} — outline\n\n${(project.outline || '').trim()}\n`, 'text/markdown;charset=utf-8');
      notify('Outline downloaded. It is also saved inside the project automatically.');
    });

    // Chapters
    $('chapter-list').addEventListener('click', event => {
      const select = event.target.closest('[data-chapter]'), move = event.target.closest('[data-chapter-move]');
      if (select) selectChapter(select.dataset.chapter);
      if (move) mutate(() => {
        const index = project.chapters.findIndex(c => c.id === move.dataset.id), to = index + Number(move.dataset.chapterMove);
        if (to >= 0 && to < project.chapters.length) { const [c] = project.chapters.splice(index, 1); project.chapters.splice(to, 0, c); }
      });
    });
    $('add-chapter').addEventListener('click', () => addChapter());
    $('notes-toggle').addEventListener('click', () => {
      const open = $('notes-panel').hidden; $('notes-panel').hidden = !open;
      $('notes-toggle').setAttribute('aria-expanded', String(open));
      if (open) { autosize($('chapter-notes')); $('chapter-notes').focus(); }
    });
    $('delete-chapter').addEventListener('click', () => {
      if (project.chapters.length <= 1) return;
      const name = chapterName(chapter());
      mutate(() => { const index = project.chapters.indexOf(chapter()); project.chapters.splice(index, 1); activeChapter = project.chapters[Math.min(index, project.chapters.length - 1)].id; });
      notify(`Deleted “${name}”. Original image files are never deleted.`, false, undoAction);
    });
    $('prev-chapter').addEventListener('click', () => { const i = project.chapters.indexOf(chapter()); if (i > 0) selectChapter(project.chapters[i - 1].id); });
    $('next-chapter').addEventListener('click', () => {
      const i = project.chapters.indexOf(chapter());
      if (i < project.chapters.length - 1) selectChapter(project.chapters[i + 1].id); else addChapter();
    });

    // Blocks
    $('blocks').addEventListener('input', event => {
      const field = event.target.dataset.edit, article = event.target.closest('[data-block]');
      if (!field || !article || blocked) return;
      const block = chapter().blocks.find(b => b.id === article.dataset.block); if (!block) return;
      history(block.id + field);
      const hadComments = commentsOf(block).length && field === 'text';
      if (hadComments) shiftComments(block, textChange(block.text, event.target.value, event.target.selectionEnd), event.target.value);
      block[field] = event.target.value; autosize(event.target); changed(false);
      if (field === 'text') renderMarks(article, block);
      if (hadComments) scheduleComments();
      trackCaret(event.target);
    });
    ['focusin', 'click', 'keyup', 'select', 'pointerup'].forEach(type => $('blocks').addEventListener(type, event => {
      if (!event.target.matches?.('textarea[data-edit="text"]')) return;
      trackCaret(event.target);
      const block = chapter().blocks.find(b => b.id === event.target.closest('[data-block]')?.dataset.block);
      if (block && type !== 'focusin') setActiveComment(commentAtCaret(block, event.target.selectionStart));
      updateCommentFloat();
    }));
    $('blocks').addEventListener('focusout', event => { if (event.target.matches?.('textarea[data-edit="text"]')) setTimeout(updateCommentFloat, 0); });
    window.addEventListener('scroll', () => { if ($('comment-float').dataset.mode === 'read') hideCommentFloat(); }, {passive:true});
    $('blocks').addEventListener('keydown', event => {
      const textarea = event.target.closest('textarea'), article = event.target.closest('[data-block]');
      if (!article || blocked) return;
      const id = article.dataset.block;
      if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault(); moveBlock(id, event.key === 'ArrowUp' ? -1 : 1); return;
      }
      if (!textarea || textarea.dataset.edit !== 'text') return;
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); splitText(id, textarea.selectionStart); return; }
      if (event.key === 'Backspace' && textarea.selectionStart === 0 && textarea.selectionEnd === 0 && !event.ctrlKey && !event.altKey) {
        if (joinWithPrevious(id)) event.preventDefault();
      }
    });
    $('blocks').addEventListener('click', event => {
      if (event.target.closest('[data-menu]')) return;
      const gap = event.target.closest('[data-gap]');
      if (gap) {
        const index = Number(gap.dataset.gap);
        if (event.target.closest('[data-gap-text]')) addText(index);
        else if (event.target.closest('[data-gap-image]')) { setTarget(index); startPick('insert'); }
        else if (event.target.closest('[data-gap-clear]')) clearInsertion();
        else if (!gap.classList.contains('gap-end')) setTarget(index);
        return;
      }
      const article = event.target.closest('[data-block]'); if (!article) return;
      const id = article.dataset.block, blocks = chapter().blocks, block = blocks.find(b => b.id === id); if (!block) return;
      const hit = event.target.closest('.media-hidden')?.previousElementSibling || event.target;
      const viewItem = hit.closest('[data-view-item]');
      if (viewItem || hit.closest('[data-view-image]')) {
        const list = blocks.flatMap(b => b.type === 'image' ? [{file:b.file, block:b.id}] : b.type === 'gallery' ? b.items.map((x, i) => ({file:x.file, block:b.id, item:i})) : []);
        openLightbox(list, list.findIndex(x => x.block === id && (!viewItem || x.item === Number(viewItem.dataset.viewItem))), 'chapter'); return;
      }
      const layout = event.target.closest('[data-layout]');
      if (layout) { mutate(() => { block.layout = layout.dataset.layout; }); return; }
      const itemButton = event.target.closest('[data-item-action]');
      if (itemButton) { groupItemAction(block, itemButton.dataset.itemAction, Number(itemButton.dataset.item)); return; }
      const size = event.target.closest('[data-size]');
      if (size) { mutate(() => { if (size.dataset.size === 'large') delete block.size; else block.size = size.dataset.size; }); return; }
      const moveTo = event.target.closest('[data-move-to]');
      if (moveTo) {
        const destination = project.chapters.find(c => c.id === moveTo.dataset.moveTo); if (!destination) return;
        mutate(() => { const index = blocks.indexOf(block); if (index >= 0) destination.blocks.push(...blocks.splice(index, 1)); });
        notify('Moved to the end of “' + chapterName(destination) + '”.', false, {label:'Go there', run:() => { selectChapter(destination.id); pendingScroll = [id]; renderEditor(); }});
        return;
      }
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (action === 'up' || action === 'down') moveBlock(id, action === 'up' ? -1 : 1);
      else if (action === 'remove') removeBlock(id);
      else if (action === 'replace') startPick('replace', id);
      else if (action === 'group-add') startPick('group-add', id);
      else if (action === 'group-prev') mergeBlocks(blocks.indexOf(block) - 1);
      else if (action === 'group-next') mergeBlocks(blocks.indexOf(block));
      else if (action === 'ungroup') ungroup(id);
      else if (action === 'comment') commentFromTextarea(article.querySelector('textarea[data-edit="text"]'));
      else if (action === 'duplicate') {
        const copy = {...clone(block), id:uid()}; delete copy.comments;
        mutate(() => { blocks.splice(blocks.indexOf(block) + 1, 0, copy); pendingScroll = [copy.id]; });
      }
    });

    // Library
    const viewLibraryImage = card => {
      const list = libraryCards.filter(c => !c.hidden).map(c => ({file:c.dataset.file}));
      openLightbox(list, Math.max(0, list.findIndex(x => x.file === card.dataset.file)), 'library');
    };
    $('image-library').addEventListener('click', event => {
      const card = event.target.closest('[data-file]'); if (!card) return;
      if (event.target.closest('[data-add-image]')) { chooseImage(card.dataset.file); return; }
      if (event.target.closest('.library-check')) return;
      if (event.target.closest('[data-view-file]')) viewLibraryImage(card);
    });
    $('image-library').addEventListener('keydown', event => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-view-file]')) { event.preventDefault(); viewLibraryImage(event.target.closest('[data-file]')); }
    });
    $('image-library').addEventListener('change', event => {
      if (!event.target.matches('[data-select-image]')) return;
      const file = event.target.closest('[data-file]').dataset.file;
      if (event.target.checked) selected.add(file); else selected.delete(file); updateLibrary();
    });
    const selectedFiles = () => { const files = libraryCards.map(c => c.dataset.file).filter(f => selected.has(f)); selected.clear(); return files; };
    $('add-selected').addEventListener('click', () => {
      const files = selectedFiles();
      if (pick?.mode === 'group-add') addToGroup(pick.block, files); else insertImages(files);
      if (pick) endPick(); else updateLibrary();
    });
    $('add-selected-group').addEventListener('click', () => {
      insertImages(selectedFiles(), true);
      if (pick) endPick(); else updateLibrary();
    });
    $('clear-selected').addEventListener('click', () => { selected.clear(); updateLibrary(); });
    $('insert-reset').addEventListener('click', clearInsertion);
    $('library-expand').addEventListener('click', () => setLibraryExpanded(!document.body.classList.contains('library-expanded')));
    $('library-close').addEventListener('click', () => {
      if (document.body.classList.contains('library-expanded')) setLibraryExpanded(false);
      else { ui.library = false; saveUI(); renderLibraryLayout(); }
    });
    $('library-backdrop').addEventListener('click', () => setLibraryExpanded(false));
    $('pick-cancel').addEventListener('click', endPick);
    $('add-image-files').addEventListener('click', () => $('image-files').click());
    $('image-files').addEventListener('change', event => {
      if (blocked) return;
      const files = [...event.target.files].filter(file => validFile(file.name) && !library.has(file.name));
      event.target.value = '';
      if (!files.length) { notify('Those files are already listed, or are not supported image or video files.'); return; }
      mutate(() => {
        project.extraImages ||= [];
        files.forEach(file => project.extraImages.push({file:file.name, title:file.name.replace(/\.[^.]+$/, ''), category:isVideo(file.name) ? 'Videos' : 'Added images'}));
        buildLibrary();
      });
      notify(`Added ${files.length} image filenames to this project. Their original files must be beside editor.html.`);
    });
    $('image-search').addEventListener('input', updateLibrary);
    $('image-category').addEventListener('change', updateLibrary);
    $('image-sort').addEventListener('change', event => { ui.sort = event.target.value; saveUI(); sortLibrary(); });
    $('unused-only').addEventListener('change', updateLibrary);
    document.querySelectorAll('[data-thumb-size]').forEach(button => button.addEventListener('click', () => { ui.thumb = button.dataset.thumbSize; saveUI(); renderLibraryLayout(); }));
    $('settings-button').addEventListener('click', () => {
      $('setting-hide-images').checked = document.body.classList.contains('hide-images');
      $('setting-comment-marks').checked = ui.commentMarks;
      $('settings').showModal();
    });
    $('setting-hide-images').addEventListener('change', event => {
      try { localStorage.setItem(HIDE_KEY, String(event.target.checked)); } catch {}
      applyHideImages(event.target.checked);
    });
    $('setting-comment-marks').addEventListener('change', event => { ui.commentMarks = event.target.checked; saveUI(); renderLibraryLayout(); });
    try { applyHideImages(localStorage.getItem(HIDE_KEY) === 'true'); } catch {}

    // Comments: the panel and the dialog share one set of controls.
    const commentRoots = [$('comment-list'), $('comment-dialog-body')];
    commentRoots.forEach(root => {
      root.addEventListener('input', event => {
        const field = event.target.dataset.commentField, id = event.target.closest('[data-comment-id]')?.dataset.commentId;
        if (!field || !id) return;
        setCommentField(id, field, event.target.value); autosize(event.target);
      });
      root.addEventListener('click', event => {
        const card = event.target.closest('[data-comment-id]'), id = card?.dataset.commentId;
        const chapterLink = event.target.closest('[data-comment-chapter]');
        if (chapterLink) { selectChapter(chapterLink.dataset.commentChapter); return; }
        if (!id) return;
        const label = event.target.closest('[data-comment-label]'), action = event.target.closest('[data-comment-action]');
        if (label) {
          const found = findComment(id);
          if (found && found.comment.label !== label.dataset.commentLabel) mutate(() => { found.comment.label = label.dataset.commentLabel; found.comment.updated = new Date().toISOString(); });
        } else if (action) commentAction(id, action.dataset.commentAction);
        else if (event.target.closest('[data-comment-go]')) goToComment(id);
        else if (root === $('comment-list') && !event.target.closest('textarea, button')) setActiveComment(id, false);
      });
    });
    document.querySelectorAll('[data-comment-scope]').forEach(button => button.addEventListener('click', () => { ui.commentScope = button.dataset.commentScope; saveUI(); renderComments(); }));
    $('comments-resolved').addEventListener('change', event => { showResolved = event.target.checked; renderComments(); });
    $('comment-dialog').addEventListener('close', () => { if (previewing) refreshPreviewMarks(); });
    $('comment-float').addEventListener('pointerdown', event => event.preventDefault()); // Keep the text selection.
    $('comment-float').addEventListener('click', () => {
      const mode = $('comment-float').dataset.mode; hideCommentFloat();
      if (mode === 'read') {
        const picked = readSelection(); window.getSelection()?.removeAllRanges();
        if (picked) addComment(picked.block, picked.start, picked.end, true);
      } else commentFromTextarea(document.activeElement);
    });
    $('library-resizer').addEventListener('pointerdown', event => {
      event.preventDefault();
      const resizer = event.currentTarget; resizer.setPointerCapture(event.pointerId); resizer.classList.add('active');
      const move = e => { ui.libraryWidth = Math.round(Math.min(Math.max(window.innerWidth - e.clientX, 280), window.innerWidth * .6)); renderLibraryLayout(); };
      const up = () => { resizer.classList.remove('active'); resizer.removeEventListener('pointermove', move); resizer.removeEventListener('pointerup', up); saveUI(); };
      resizer.addEventListener('pointermove', move); resizer.addEventListener('pointerup', up);
    });

    // Lightbox
    $('lb-prev').addEventListener('click', () => stepLightbox(-1));
    $('lb-next').addEventListener('click', () => stepLightbox(1));
    $('lb-close').addEventListener('click', () => $('lightbox').close());
    $('lightbox').addEventListener('close', stopLightboxVideo);
    $('lightbox').addEventListener('click', event => { if (event.target === $('lightbox') || event.target.classList.contains('lb-stage')) $('lightbox').close(); });
    $('lightbox').addEventListener('keydown', event => {
      if (typing(event.target)) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); stepLightbox(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); stepLightbox(1); }
      if (event.key === 'Enter' && !$('lb-insert').hidden && !event.target.matches('button')) { event.preventDefault(); $('lb-insert').click(); }
    });
    $('lb-title').addEventListener('input', event => setImageInfo(lightbox.list[lightbox.index].file, 'title', event.target.value));
    $('lb-category').addEventListener('input', event => setImageInfo(lightbox.list[lightbox.index].file, 'category', event.target.value));
    $('lb-insert').addEventListener('click', () => { const file = lightbox.list[lightbox.index].file; $('lightbox').close(); chooseImage(file); });
    $('lb-replace').addEventListener('click', () => { const entry = lightbox.list[lightbox.index]; $('lightbox').close(); startPick('replace', entry.block, entry.item ?? null); });
    $('lb-used').addEventListener('click', event => {
      const button = event.target.closest('[data-goto-block]'); if (!button) return;
      $('lightbox').close(); if (previewing) showPreview(false);
      if (document.body.classList.contains('library-expanded')) setLibraryExpanded(false);
      activeChapter = button.dataset.gotoChapter; view = 'chapter'; target = null; caret = null;
      pendingScroll = [button.dataset.gotoBlock]; renderEditor();
    });

    // Drag and drop: library images, blocks and chapters
    document.addEventListener('dragstart', event => {
      const block = event.target.closest?.('[data-drag-block]'), image = event.target.closest?.('[data-drag-file]'), chapterItem = event.target.closest?.('[data-chapter-item]');
      if (blocked || (!block && !image && !chapterItem)) return;
      drag = block ? {type:'block', id:block.dataset.dragBlock, chapter:activeChapter}
        : image ? {type:'image', file:image.dataset.dragFile} : {type:'chapter', id:chapterItem.dataset.chapterItem};
      event.dataTransfer.setData('application/x-story-studio', JSON.stringify(drag)); event.dataTransfer.effectAllowed = drag.type === 'image' ? 'copy' : 'move';
      if (block) { const article = block.closest('.block'); article.classList.add('dragging'); event.dataTransfer.setDragImage(article, 20, 20); }
      if (chapterItem && !block && !image) chapterItem.classList.add('dragging');
      if (image && document.body.classList.contains('library-expanded') && !pick) setTimeout(() => setLibraryExpanded(false), 0);
    });
    const DROP_MARKS = ['drag-over', 'drop-before', 'drop-after', 'drop-beside-left', 'drop-beside-right'];
    const clearDropMarks = () => document.querySelectorAll(DROP_MARKS.map(c => '.' + c).join(',')).forEach(el => el.classList.remove(...DROP_MARKS));
    // Over the outer quarter of a picture, a dragged picture groups with it instead of going above/below.
    function besideSide(el, event, item = drag) {
      if (!item || (item.type !== 'image' && item.type !== 'block') || !el.matches('#blocks .block-image, #blocks .block-gallery')) return null;
      if (item.type === 'block') {
        if (item.id === el.dataset.block) return null;
        if (!isPicture(project.chapters.find(c => c.id === item.chapter)?.blocks.find(b => b.id === item.id))) return null;
      }
      const rect = el.getBoundingClientRect(), x = (event.clientX - rect.left) / rect.width;
      return x < .25 ? 'left' : x > .75 ? 'right' : null;
    }
    function dropTarget(event) {
      if (drag?.type === 'chapter') return event.target.closest('#chapter-list [data-chapter-item]');
      return event.target.closest('#blocks .gap, #blocks .block, #chapter-list [data-chapter-item]');
    }
    const lowerHalf = (el, event) => { const rect = el.getBoundingClientRect(); return event.clientY > rect.top + rect.height / 2; };
    const externalFiles = event => !drag && event.dataTransfer?.types?.includes('Files');
    document.addEventListener('dragover', event => {
      const dropEl = dropTarget(event); if (!dropEl || (!drag && !externalFiles(event))) return;
      if (externalFiles(event) && dropEl.matches('[data-chapter-item]')) return;
      event.preventDefault(); event.dataTransfer.dropEffect = drag?.type === 'image' || !drag ? 'copy' : 'move';
      clearDropMarks();
      const side = besideSide(dropEl, event);
      if (side) dropEl.classList.add('drop-beside-' + side);
      else if (dropEl.matches('.block') || (drag?.type === 'chapter')) dropEl.classList.add(lowerHalf(dropEl, event) ? 'drop-after' : 'drop-before');
      else dropEl.classList.add('drag-over');
    });
    document.addEventListener('dragleave', event => { const dropEl = dropTarget(event); if (dropEl && !dropEl.contains(event.relatedTarget)) dropEl.classList.remove(...DROP_MARKS); });
    document.addEventListener('dragend', () => { drag = null; clearDropMarks(); document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging')); });
    document.addEventListener('drop', event => {
      const dropEl = dropTarget(event);
      if (!dropEl || blocked) return;
      if (!drag && externalFiles(event)) {
        event.preventDefault(); clearDropMarks();
        const names = [...event.dataTransfer.files].map(f => f.name), known = names.filter(n => library.has(n)), unknown = names.filter(n => !library.has(n));
        if (known.length) { setTarget(dropEl.matches('.gap') ? Number(dropEl.dataset.gap) : chapter().blocks.findIndex(b => b.id === dropEl.dataset.block) + (lowerHalf(dropEl, event) ? 1 : 0)); insertImages(known); }
        if (unknown.length) notify(`${unknown.length} file(s) are not in the story folder yet. Copy them into the folder first — they then appear in the library automatically.`, true);
        return;
      }
      if (!drag) return;
      event.preventDefault(); const item = drag, after = lowerHalf(dropEl, event), side = besideSide(dropEl, event); drag = null; clearDropMarks();
      if (side) { groupOnDrop(item, dropEl.dataset.block, side); return; }
      if (item.type === 'chapter') {
        const from = project.chapters.findIndex(c => c.id === item.id); let to = project.chapters.findIndex(c => c.id === dropEl.dataset.chapterItem);
        if (from < 0 || to < 0 || from === to) return;
        if (after) to++;
        if (from < to) to--;
        mutate(() => { const [c] = project.chapters.splice(from, 1); project.chapters.splice(to, 0, c); });
        return;
      }
      const toChapter = dropEl.dataset.chapterItem;
      const destination = toChapter ? project.chapters.find(c => c.id === toChapter) : chapter();
      if (!destination) return;
      let at = dropEl.matches('.gap') ? Number(dropEl.dataset.gap)
        : dropEl.dataset.block ? destination.blocks.findIndex(b => b.id === dropEl.dataset.block) + (after ? 1 : 0) : destination.blocks.length;
      if (at < 0) return;
      if (item.type === 'block' && dropEl.dataset.block === item.id && destination.id === item.chapter) return;
      mutate(() => {
        if (item.type === 'image' && library.has(item.file)) {
          const block = {id:uid(), type:'image', file:item.file, caption:''};
          destination.blocks.splice(at, 0, block); if (!toChapter) pendingScroll = [block.id];
        } else if (item.type === 'block') {
          const source = project.chapters.find(c => c.id === item.chapter); if (!source) return;
          const index = source.blocks.findIndex(b => b.id === item.id); if (index < 0) return;
          const [block] = source.blocks.splice(index, 1); if (source === destination && index < at) at--; destination.blocks.splice(at, 0, block);
        }
        target = null;
      });
      if (toChapter && toChapter !== activeChapter) notify(`Placed at the end of “${chapterName(destination)}”.`, false, undoAction);
    });

    // History and keyboard
    $('undo').addEventListener('click', () => stepHistory(undoStack, redoStack));
    $('redo').addEventListener('click', () => stepHistory(redoStack, undoStack));
    document.addEventListener('keydown', event => {
      if (!project) return; // Nothing loaded, e.g. the story's folder is missing.
      const mod = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
      if (mod && key === 's') {
        event.preventDefault();
        if (blocked) { notify('Saving is paused. Resolve the recovery warning first.', true); return; }
        const saved = saveBrowser(); flushDisk();
        notify(serverMode ? 'Saving to story-project.json. (It also saves automatically as you type.)' : fileHandle ? 'Saving the browser draft and linked project file.' : saved ? 'Draft saved in this browser. Download a backup for a portable copy.' : 'Browser saving failed. Download a backup now.', !saved && !fileHandle);
        return;
      }
      if (mod && (key === 'z' || key === 'y') && !typing(event.target)) {
        event.preventDefault();
        const redo = key === 'y' || event.shiftKey;
        stepHistory(redo ? redoStack : undoStack, redo ? undoStack : redoStack); return;
      }
      if (event.key === 'Escape') {
        if (document.querySelector('.menu:not([hidden])')) { closeMenus(); return; }
        if (document.body.classList.contains('library-expanded') && !$('lightbox').open) { if (pick) endPick(); else setLibraryExpanded(false); return; }
      }
      if (event.altKey && !mod && (event.key === 'PageUp' || event.key === 'PageDown')) {
        event.preventDefault();
        const i = project.chapters.indexOf(chapter()) + (event.key === 'PageUp' ? -1 : 1);
        if (project.chapters[i]) selectChapter(project.chapters[i].id);
        return;
      }
      if (event.altKey && !mod && key === 'i' && !previewing) { event.preventDefault(); startPick('insert'); return; }
      if (event.altKey && !mod && !event.shiftKey && event.code === 'KeyC' && !document.querySelector('dialog[open]')) {
        event.preventDefault();
        if (previewing) { const picked = ui.readComments && readSelection(); if (picked) { hideCommentFloat(); addComment(picked.block, picked.start, picked.end, true); } else notify('Turn on “Show comments” and select some words in the story to comment on them.'); }
        else if (event.target.matches?.('#blocks textarea[data-edit="text"]')) commentFromTextarea(event.target);
        else if (!typing(event.target)) toggleComments();
        return;
      }
      if (event.key === '/' && !mod && !typing(event.target) && !previewing && !document.querySelector('dialog[open]')) {
        event.preventDefault();
        if (!ui.library && !document.body.classList.contains('library-expanded')) { ui.library = true; ui.comments = false; saveUI(); renderLibraryLayout(); }
        $('image-search').focus(); $('image-search').select();
      }
    });
    window.addEventListener('resize', () => { clearTimeout(window.__autosize); window.__autosize = setTimeout(autosizeAll, 150); });

    // Preview, export and project files
    $('preview-toggle').addEventListener('click', () => showPreview(!previewing));
    $('preview').addEventListener('click', event => {
      const mark = event.target.closest('mark[data-comments]');
      if (mark && ui.readComments && window.getSelection()?.isCollapsed !== false) { openCommentDialog(mark.dataset.comments.split(' ')[0]); return; }
      const edit = event.target.closest('[data-edit-chapter]'); if (!edit) return;
      showPreview(false); selectChapter(edit.dataset.editChapter);
    });
    ['pointerup', 'keyup'].forEach(type => $('preview').addEventListener(type, () => setTimeout(updateReadFloat, 0)));
    document.addEventListener('selectionchange', () => { if ($('comment-float').dataset.mode === 'read' && window.getSelection()?.isCollapsed) hideCommentFloat(); });
    $('export-html').addEventListener('click', () => exportStory('html'));
    $('export-pdf').addEventListener('click', () => exportStory('pdf'));
    $('download-project').addEventListener('click', () => { download(projectName('-project.json'), serialize(), 'application/json'); notify('Backup downloaded. Keep the JSON file with your images; use Open project to restore it.'); });
    $('link-file').addEventListener('click', async () => {
      if (blocked) { notify('Resolve the recovery warning before linking a file.', true); return; }
      if (!window.showSaveFilePicker) { notify('Direct file autosave is not available in this browser. Use Download backup regularly, or open the editor in Chrome / Edge.', true); return; }
      try {
        const handle = await window.showSaveFilePicker({suggestedName:projectName('-project.json'), types:[{description:'Story Studio project', accept:{'application/json':['.json']}}]});
        if (!/\.json$/i.test(handle.name)) throw Error('Choose a .json project file, not an image or HTML file.');
        await flushDisk(); fileHandle = handle; diskSavedVersion = ''; await queueDiskSave();
        if (fileHandle) notify('Disk autosave is linked to ' + handle.name + '. Reopen that project file with Open project next time to reconnect.');
      } catch (error) { if (error.name !== 'AbortError') notify('Could not link the project file: ' + error.message, true); }
    });
    $('open-project').addEventListener('click', async () => {
      if (serverMode || !window.showOpenFilePicker) { $('import-file').click(); return; }
      try {
        const [handle] = await window.showOpenFilePicker({types:[{description:'Story Studio project',accept:{'application/json':['.json']}}], multiple:false});
        let writableHandle = handle;
        if (handle.requestPermission && await handle.requestPermission({mode:'readwrite'}) !== 'granted') writableHandle = null;
        const file = await handle.getFile(); if (file.size > 10000000) throw Error('Project files must be smaller than 10 MB.');
        await applyImport(await file.text(), writableHandle);
      } catch (error) { if (error.name !== 'AbortError') notify('Could not open the project: ' + error.message, true); }
    });
    $('import-file').addEventListener('change', async event => {
      const file = event.target.files[0]; if (!file) return;
      try { if (file.size > 10000000) throw Error('Project files must be smaller than 10 MB.'); await applyImport(await file.text()); }
      catch (error) { notify('Could not open the project: ' + error.message, true); }
      finally { event.target.value = ''; }
    });
    $('blank-project').addEventListener('click', () => {
      if (blocked || !confirm('Start a blank story in the current working draft? Download a backup to keep the existing version. You can Undo this. If a file is linked, it will also be updated.')) return;
      mutate(() => { project = {format:'illustrated-story-project',version:1,id:uid(),title:'Untitled story',subtitle:'',chapters:[{id:uid(),title:'Chapter one',notes:'',blocks:[]}]}; activeChapter = project.chapters[0].id; selected.clear(); target = null; caret = null; buildLibrary(); });
    });
    $('help-button').addEventListener('click', () => { closeMenus(); $('help').showModal(); });
    $('help-button-2').addEventListener('click', () => $('help').showModal());
    $('shortcuts-button').addEventListener('click', () => $('shortcuts').showModal());
    $('notice-close').addEventListener('click', () => { $('notice').hidden = true; });
    $('recovery-download').addEventListener('click', () => download('story-studio-recovery.json', recoveryRaw, 'application/json'));
    $('recovery-resolve').addEventListener('click', () => {
      if (recoveryMode !== 'corrupt') { location.reload(); return; }
      if (!confirm('Replace the unreadable browser draft with the starting story? Download the recovery copy first if you need it.')) return;
      blocked = false; $('workspace').disabled = false; $('recovery').hidden = true; changed();
    });
    window.addEventListener('storage', event => {
      if (event.key === HIDE_KEY) { applyHideImages(event.newValue === 'true'); return; }
      if (serverMode) return; // The server's revision check arbitrates disk writes.
      if (event.key === KEY && event.newValue) {
        try { if (JSON.parse(event.newValue).writer === writer) return; } catch {}
        recovery('conflict', 'Another tab changed the saved draft. Editing and file autosave in this tab are paused to prevent overwriting it. Download a recovery copy to keep this tab’s version, then reload the saved draft.', serialize());
      }
    });
    window.addEventListener('beforeunload', event => {
      saveBrowser();
      if (reloadingForUpdate || !project) return; // The recovery copy above carries any unsaved change across the update.
      if ((serverMode && !blocked && (serverPending || serverDirty)) || (!browserSaved && diskSavedVersion !== serialize()) || (fileHandle && (diskPending || diskSavedVersion !== serialize()))) { event.preventDefault(); event.returnValue = ''; }
    });
    const media = target => target.tagName === 'IMG' || target.tagName === 'VIDEO';
    const clearMissing = event => { if (media(event.target)) event.target.parentElement?.querySelector('.missing-image')?.remove(); };
    document.addEventListener('load', clearMissing, true);
    document.addEventListener('loadeddata', clearMissing, true);
    document.addEventListener('error', event => {
      if (!media(event.target) || event.target.closest('.chapter-strip') || ['lb-image', 'lb-video'].includes(event.target.id)) return;
      if (event.target.tagName === 'VIDEO' && !event.target.getAttribute('src')) return;
      const parent = event.target.parentElement;
      if (!parent || parent.querySelector('.missing-image')) return;
      const warning = document.createElement('div'); warning.className = 'missing-image';
      warning.textContent = event.target.tagName === 'VIDEO' ? 'Video unavailable or in a format this browser cannot play (MP4/H.264 and WebM work best).' : serverMode ? 'Image unavailable. Keep its original file in this story’s folder.' : 'Image unavailable. Keep its original file beside editor.html.';
      parent.append(warning);
    }, true);
    document.addEventListener('mouseenter', event => { if (event.target.matches?.('video[data-hover-play]') && !document.body.classList.contains('hide-images')) event.target.play().catch(() => {}); }, true);
    document.addEventListener('mouseleave', event => { if (event.target.matches?.('video[data-hover-play]')) event.target.pause(); }, true);
  }

  // Legacy adoption: the single-story editor kept its recovery copy under the un-namespaced key. Copy it
  // over (raw, so nothing is lost even if loading it pauses saving) when it belongs to this project.
  function adoptLegacyRecovery(projectId) {
    if (KEY === BASE_KEY) return;
    try {
      const raw = localStorage.getItem(BASE_KEY);
      if (localStorage.getItem(KEY) !== null || !raw || JSON.parse(raw)?.project?.id !== projectId) return;
      localStorage.setItem(KEY, raw); localStorage.removeItem(BASE_KEY);
    } catch { /* Unreadable legacy data stays where it is. */ }
  }
  function startUpdates() {
    window.StoryUpdate?.start({banner:$('update-banner'), token:() => server?.token, setToken:value => { if (server) server.token = value; },
      notify, beforeInstall:() => { saveBrowser(); return flushDisk(); }, beforeReload:() => { saveBrowser(); reloadingForUpdate = true; }});
  }
  function showMissing() {
    blocked = true; clearTimeout(diskTimer);
    $('workspace').hidden = true; $('missing-story').hidden = false;
    document.querySelector('.actions').hidden = true; $('toggle-chapters').hidden = true; $('topbar-title').hidden = true;
    document.title = 'Story Studio — story not found';
    let raw = null;
    try { raw = localStorage.getItem(KEY); } catch {}
    $('missing-recovery').hidden = $('missing-recovery-note').hidden = !raw;
    $('missing-recovery').addEventListener('click', () => download('story-studio-recovery.json', raw, 'application/json'));
  }
  async function loadInitial() {
    if (window.innerWidth <= 1100) ui.library = false;
    if (window.innerWidth <= 760) ui.chapters = false;
    renderLibraryLayout();
    const icon = document.querySelector('.brand-icon'), showIcon = () => { icon.hidden = !icon.naturalWidth; };
    icon.addEventListener('load', showIcon); if (icon.complete) showIcon(); // icon.svg may be absent in development.
    if (serverMode && storyId) { $('all-stories').hidden = false; $('brand').href = '/'; $('brand').title = 'All stories'; }
    if (!serverMode) {
      $('folder-status').textContent = 'Manual mode — open the story from the Story Studio app for automatic folder watching and saving.';
      fileStatus('No project file linked — browser autosave only', 'idle');
      loadBrowserInitial(); return;
    }
    $('workspace').disabled = true;
    $('link-file').hidden = true; $('add-image-files').hidden = true;
    $('open-project').textContent = 'Import project…';
    $('help-content').innerHTML = '<p><strong>Everything saves automatically.</strong> Story Studio creates and updates <code>story-project.json</code> in this story’s folder, beside your images. Opening the story again from the Story Studio home page resumes that file without selecting or reconnecting anything. The dot in the top bar turns green once a change is on disk.</p><p>New top-level image and video files in the story folder appear within a few seconds. Subfolders, symbolic links and ZIP archives are not scanned. Image files are referenced, never copied or uploaded. Image descriptions you write are stored in the project, not in the image files.</p><p>Changes are saved shortly after you stop typing. A browser recovery copy is kept too; if Story Studio stops, leave the page open and start Story Studio again. Saving retries automatically.</p><p><code>story-project.json.bak</code> holds the previous saved version. Dropbox can sync these files, but do not edit on two computers simultaneously. Conflicting file changes pause saving instead of overwriting.</p><p><strong>Previous browser-only edits:</strong> open the old file-based editor, download a JSON backup, then import it here once.</p><p>Download backup exports editable JSON, including private notes. Export story HTML makes one read-only file with the pictures inside, without notes or comments. Export PDF does the same as a printable PDF. Nothing is sent to an internet service.</p>';
    try {
      server = await responseJSON(await fetch('api/bootstrap', {signal:AbortSignal.timeout(10000)}));
      if (server.version !== SERVER_VERSION) throw Object.assign(Error('outdated server'), {outdated:true});
      DATA.images = server.images; imageSignature = server.signature;
      project = validate(server.project); diskSavedVersion = serialize();
      activeChapter = project.chapters[0].id;
      adoptLegacyRecovery(project.id);
      try {
        const raw = localStorage.getItem(KEY), stored = raw ? JSON.parse(raw) : null;
        if (stored) {
          const cached = validate(stored.project);
          if (stored.pendingServer && JSON.stringify(cached) !== JSON.stringify(project)) {
            project = cached;
            if (stored.serverRevision === server.revision) serverDirty = true;
            else recovery('server-conflict', 'This browser has an unsaved recovery draft, but the project file changed too. Download the recovery copy before reloading the saved project.', JSON.stringify(cached, null, 2));
          }
          if (project.chapters.some(c => c.id === stored.activeChapter)) activeChapter = stored.activeChapter;
        }
      } catch { /* A corrupt browser cache never replaces the validated disk project. */ }
      buildLibrary(); renderEditor();
      if (!blocked) {
        $('workspace').disabled = false; saveBrowser();
        fileStatus('Automatically saved · story-project.json', 'ok');
        if (serverDirty) queueServerSave();
      }
      $('folder-status').textContent = 'Watching this folder · new images and videos appear automatically';
      setInterval(pollServer, 2000);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) pollServer(); else if (serverDirty) queueServerSave(); });
      if (storyId) startUpdates();
    } catch (error) {
      if (error.status === 404 && storyId) { $('workspace').disabled = false; showMissing(); startUpdates(); return; }
      if (storyId && error.status) startUpdates(); // Story Studio answered, so updates can still be offered.
      project ||= clone(DATA.seed); activeChapter ||= project.chapters[0].id;
      buildLibrary(); renderEditor();
      recovery('server-error', error.outdated
        ? 'This page and the running Story Studio are different versions. Reload this page; if the warning stays, quit Story Studio (tray icon → Quit) and start it again. Nothing has been saved or changed.'
        : 'Could not open the story: ' + error.message + '. Make sure Story Studio is running, then reload. The project file has not been overwritten.', serialize());
    }
  }
  bindEvents(); loadInitial();
})();
