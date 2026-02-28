// Backend URL — change to match your environment
const BACKEND_URL = 'http://localhost:8000';
const AI_URL      = 'http://localhost:8001';

// Translation cache: originalText → EN text (persists for the session)
const transCache = {};

async function translateBatch(strings) {
    const unique = [...new Set(strings.filter(s => s && !transCache[s]))];
    if (!unique.length) return;
    // Send in chunks of 15 to avoid LLM truncating large arrays
    const CHUNK = 15;
    for (let i = 0; i < unique.length; i += CHUNK) {
        const chunk = unique.slice(i, i + CHUNK);
        try {
            const resp = await fetch(`${AI_URL}/translate`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ texts: chunk, target_lang: 'en' }),
            });
            const { translations } = await resp.json();
            chunk.forEach((s, j) => { if (translations[j]) transCache[s] = translations[j]; });
        } catch { /* silent — fall back to originals */ }
    }
}

async function maybeTranslate(groups) {
    if (currentLang !== 'en') return groups;
    // Collect every string that needs translating
    const strs = [];
    groups.forEach(g => {
        strs.push(g.name);
        (g.ideas || []).forEach(i => strs.push(i.text || i));
        (g.subgroups || []).forEach(sg => {
            strs.push(sg.name);
            (sg.ideas || []).forEach(i => strs.push(i.text || i));
        });
    });
    Object.values(summariesMap).forEach(s => strs.push(s));
    await translateBatch(strs);
    const tr = s => (s && transCache[s]) ? transCache[s] : s;
    return groups.map(g => ({
        _orig:     g.name,
        name:      tr(g.name),
        ideas:     (g.ideas || []).map(i => ({ text: tr(i.text || i), url: (i && i.url) || null })),
        subgroups: (g.subgroups || []).map(sg => ({
            _orig: sg.name,
            name:  tr(sg.name),
            ideas: (sg.ideas || []).map(i => ({ text: tr(i.text || i), url: (i && i.url) || null })),
        })),
    }));
}

// Language state: 'es' | 'en'
let currentLang = localStorage.getItem('brain_lang') || 'es';

// ── UI strings (ES / EN) ─────────────────────────────────────────────────────
const STRINGS = {
    es: {
        remindersTitle:    'Alertas pendientes',
        searchPlaceholder: 'Buscar o crear grupo/idea...',
        emptyGroups:       'Sin grupos todavía. Escribe tu primera idea arriba.',
        emptyDetail:       'Sin contenido todavía.',
        summaryLabel:      '✨ Resumen IA',
        groupsLabel:       'Grupos',
        processing:        'Procesando con IA...',
        ignored:           '⚠️ La IA no entendió esa nota. Intenta de nuevo.',
        deleted:           '🗑️ Eliminado de',
        saved:             '✓ Guardado en',
        more:              'más',
        errorServer:       '❌ Error conectando con el servidor',
        recording:         '🎤 Grabando... toca de nuevo para parar',
        transcribing:      'Transcribiendo audio con IA...',
        noMic:             '❌ El navegador no permite el micro en HTTP. Prueba en Chrome con localhost.',
        requestingMic:     'Solicitando permiso de micrófono...',
        noSpeech:          '⚠️ No se detectó habla. Intenta de nuevo.',
        transcribeError:   '❌ Error al transcribir el audio',
        micError:          '❌ No se pudo acceder al micrófono: ',
        reminderSet:       '⏰ Recordatorio guardado para',
        processingDoc:    '📄 Leyendo documento con IA...',
        docDone:          'ideas extraídas del documento',
        docError:         '❌ Error al procesar el documento',
        docNoIdeas:       '⚠️ No se encontraron ideas en el documento',
    },
    en: {
        remindersTitle:    'Pending alerts',
        searchPlaceholder: 'Search or create group / idea...',
        emptyGroups:       'No groups yet. Write your first idea above.',
        emptyDetail:       'No content yet.',
        summaryLabel:      '✨ AI Summary',
        groupsLabel:       'Groups',
        processing:        'Processing with AI...',
        ignored:           '⚠️ The AI didn\'t understand that note. Try again.',
        deleted:           '🗑️ Deleted from',
        saved:             '✓ Saved in',
        more:              'more',
        errorServer:       '❌ Error connecting to server',
        recording:         '🎤 Recording... tap again to stop',
        transcribing:      'Transcribing audio with AI...',
        noMic:             '❌ Browser doesn\'t allow mic on HTTP. Try Chrome on localhost.',
        requestingMic:     'Requesting microphone permission...',
        noSpeech:          '⚠️ No speech detected. Try again.',
        transcribeError:   '❌ Error transcribing the audio',
        micError:          '❌ Could not access microphone: ',
        reminderSet:       '⏰ Reminder set for',
        processingDoc:    '📄 Reading document with AI...',
        docDone:          'ideas extracted from document',
        docError:         '❌ Error processing document',
        docNoIdeas:       '⚠️ No ideas found in document',
    },
};
function t(key) { return (STRINGS[currentLang] || STRINGS.es)[key] || STRINGS.es[key]; }
const MAX_BUBBLES = 12;

// ── YouTube helpers ──────────────────────────────────────────────────────────
const YT_RE = /(?:https?:\/\/)?(?:www\.)?(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|shorts\/|embed\/))([a-zA-Z0-9_-]{11})/;
function ytVideoId(url) {
    if (!url) return null;
    const m = url.match(YT_RE);
    return m ? m[1] : null;
}

// ── Super-bubble state (persisted in localStorage) ──────────────────────────
// Set of group names that are super-bubbles (created from large documents)
let superBubbles = new Set(JSON.parse(localStorage.getItem('brain_superbubbles') || '[]'));
function saveSuperBubble(name) {
    superBubbles.add(name);
    localStorage.setItem('brain_superbubbles', JSON.stringify([...superBubbles]));
}
function removeSuperBubble(name) {
    superBubbles.delete(name);
    localStorage.setItem('brain_superbubbles', JSON.stringify([...superBubbles]));
}
function isSuperBubble(name) { return superBubbles.has(name); }

// ── Pin state (persisted in localStorage) ────────────────────────────────────
// { groupName: timestamp }  — lower timestamp = pinned first
let pinnedGroups = JSON.parse(localStorage.getItem('brain_pins') || '{}');

function savePin(name) {
    pinnedGroups[name] = pinnedGroups[name] || Date.now();
    localStorage.setItem('brain_pins', JSON.stringify(pinnedGroups));
}
function removePin(name) {
    delete pinnedGroups[name];
    localStorage.setItem('brain_pins', JSON.stringify(pinnedGroups));
}
function isPinned(name) { return Object.prototype.hasOwnProperty.call(pinnedGroups, name); }

function sortByPin(groups) {
    const pinned   = groups.filter(g => isPinned(g._orig || g.name))
                           .sort((a, b) => pinnedGroups[a._orig || a.name] - pinnedGroups[b._orig || b.name]);
    const unpinned = groups.filter(g => !isPinned(g._orig || g.name));
    return [...pinned, ...unpinned];
}

// Cache of summaries keyed by "group" or "group/subgroup"
let summariesMap = {};
let urlMap = {};  // idea text → source_url

// ── Navigation state ──────────────────────────────────────────────────────────
// Each frame: { title, items: [{type:'subgroup'|'idea', ...}], parentLabel }
let navStack = [];

document.addEventListener('DOMContentLoaded', () => {
    const searchInput   = document.getElementById('main-search-input');
    const projectsGrid  = document.getElementById('projects-grid');
    const projectsView  = document.getElementById('projects-view');
    const detailView    = document.getElementById('detail-view');
    const detailTitle   = document.getElementById('detail-title');
    const detailContent = document.getElementById('detail-content');
    const backButton    = document.getElementById('back-button');
    const backLabel     = document.getElementById('back-label');
    const langToggle    = document.getElementById('lang-toggle');

    // ── Language toggle ───────────────────────────────────────────────────────
    langToggle.textContent = currentLang.toUpperCase();
    if (currentLang === 'en') langToggle.classList.add('active-en');
    langToggle.addEventListener('click', () => {
        currentLang = currentLang === 'es' ? 'en' : 'es';
        localStorage.setItem('brain_lang', currentLang);
        langToggle.textContent = currentLang.toUpperCase();
        langToggle.classList.toggle('active-en', currentLang === 'en');
        searchInput.placeholder = t('searchPlaceholder');
        loadGroups();
        loadReminders();
    });

    // ── Load groups from backend ──────────────────────────────────────────────
    function loadGroups() {
        // Fetch entries and summaries in parallel
        Promise.all([
            fetch(`${BACKEND_URL}/inbox?status=processed`).then(r => r.json()),
            fetch(`${BACKEND_URL}/summaries`).then(r => r.json()).catch(() => []),
        ]).then(([entries, summaries]) => {
            // Build summaries map: "group" or "group/subgroup" → summary text
            summariesMap = {};
            urlMap = {};
            for (const s of (summaries || [])) {
                const key = s.subgroup ? `${s.group}/${s.subgroup}` : s.group;
                summariesMap[key] = s.summary;
            }

            const map = {};
            for (const entry of entries) {
                const parts  = (entry.tags || '').split(',').map(t => t.trim()).filter(Boolean);
                const gname  = parts[0] || 'Sin grupo';
                const spname = parts[1] || null;
                const idea   = entry.summary || entry.content || '';
                const ideaObj = { text: idea, url: entry.source_url || null, id: entry.id };

                if (!map[gname]) map[gname] = { name: gname, ideas: [], subgroups: {} };
                if (spname) {
                    if (!map[gname].subgroups[spname]) map[gname].subgroups[spname] = [];
                    if (idea) map[gname].subgroups[spname].push(ideaObj);
                } else {
                    if (idea) map[gname].ideas.push(ideaObj);
                }
            }
            const groups = Object.values(map).map(g => ({
                name:      g.name,
                ideas:     g.ideas,
                subgroups: Object.entries(g.subgroups).map(([k, v]) => ({ name: k, ideas: v })),
            }));
            return maybeTranslate(groups.slice(0, MAX_BUBBLES));
        }).then(tGroups => renderMainGrid(tGroups))
        .catch(() => renderMainGrid([]));
    }

    // ── Reminders ─────────────────────────────────────────────────────────────
    // Strip LLM-appended date suffixes from reminder text
    // e.g. "gimnasio dom 1 mar 20:00" → "gimnasio"
    function stripDateSuffix(text) {
        return text
            .replace(/\s+(?:lun|mar|mi[eé]|jue|vie|s[aá]b|dom)\s+\d{1,2}\s+\w{3}(?:\s+\d{1,2}:\d{2})?\s*$/i, '')
            .replace(/\s+(?:a\s+las?\s+)?\d{1,2}:\d{2}\s*$/i, '')
            .trim();
    }

    function loadReminders() {
        fetch(`${BACKEND_URL}/reminders?sent=false`)
            .then(r => r.json())
            .then(renderReminders)
            .catch(() => renderReminders([]));
    }

    async function renderReminders(reminders) {
        const section = document.getElementById('reminders-section');
        const list    = document.getElementById('reminders-list');
        const title   = document.getElementById('reminders-title');
        title.textContent = t('remindersTitle');
        if (!reminders.length) { section.style.display = 'none'; return; }
        if (currentLang === 'en') {
            await translateBatch(reminders.map(r => r.message));
        }
        section.style.display = 'block';
        list.innerHTML = '';
        reminders.forEach(r => {
            const rawMsg = (currentLang === 'en' && transCache[r.message]) ? transCache[r.message] : r.message;
            const msg = stripDateSuffix(rawMsg);
            const fireDate = new Date(r.fire_at);
            const timeStr  = fireDate.toLocaleString(
                currentLang === 'en' ? 'en-GB' : 'es-ES',
                { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
            );
            const item = document.createElement('div');
            item.className = 'reminder-item';

            const timeSpan = document.createElement('span');
            timeSpan.className = 'reminder-time';
            timeSpan.textContent = timeStr;

            const msgSpan = document.createElement('span');
            msgSpan.className = 'reminder-msg';
            msgSpan.textContent = msg;

            const delBtn = document.createElement('button');
            delBtn.className = 'reminder-delete';
            delBtn.innerHTML = `<svg viewBox="0 0 24 24" width="13" height="13" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg>${currentLang === 'en' ? 'Delete' : 'Eliminar'}`;
            delBtn.addEventListener('click', async () => {
                item.style.opacity = '0.4';
                item.style.pointerEvents = 'none';
                try {
                    await fetch(`${BACKEND_URL}/reminders/${r.id}`, { method: 'DELETE' });
                } catch { /* silent */ }
                loadReminders();
                loadGroups(); // elimina también la burbuja vinculada
            });

            item.appendChild(timeSpan);
            item.appendChild(msgSpan);
            item.appendChild(delBtn);
            list.appendChild(item);
        });
    }

    // ── Context menu (pin/unpin) ──────────────────────────────────────────────
    let _ctxMenu = null;
    function closeCtxMenu() {
        if (_ctxMenu) { _ctxMenu.remove(); _ctxMenu = null; }
    }
    document.addEventListener('click', closeCtxMenu);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCtxMenu(); });

    function showPinMenu(e, groupName, isSuper) {
        e.preventDefault();
        closeCtxMenu();
        const menu = document.createElement('div');
        menu.className = 'pin-context-menu';
        const pinned = isPinned(groupName);
        menu.innerHTML = `
            ${isSuper ? '' : `<button class="pin-menu-item">${pinned ? '\uD83D\uDCCC Desanclar' : '\uD83D\uDCCC Anclar'}</button>`}
            <button class="pin-menu-item">\u270F\uFE0F Editar</button>
            <div class="pin-menu-separator"></div>
            <button class="pin-menu-item pin-menu-item--danger">\uD83D\uDDD1\uFE0F Eliminar</button>
        `;
        menu.style.left = `${e.clientX}px`;
        menu.style.top  = `${e.clientY}px`;
        const btns   = [...menu.querySelectorAll('button')];
        let   btnIdx = 0;
        if (!isSuper) {
            btns[btnIdx++].addEventListener('click', ev => {
                ev.stopPropagation();
                if (pinned) removePin(groupName); else savePin(groupName);
                closeCtxMenu();
                loadGroups();
            });
        }
        btns[btnIdx++].addEventListener('click', ev => {   // edit
            ev.stopPropagation();
            closeCtxMenu();
            showEditModal(e.clientX, e.clientY, groupName, newName => {
                fetch(`${BACKEND_URL}/groups/${encodeURIComponent(groupName)}`, {
                    method:  'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ new_name: newName }),
                }).then(() => {
                    if (isPinned(groupName)) {
                        const ts = pinnedGroups[groupName];
                        removePin(groupName);
                        pinnedGroups[newName] = ts;
                        localStorage.setItem('brain_pins', JSON.stringify(pinnedGroups));
                    }
                    if (isSuper) { removeSuperBubble(groupName); saveSuperBubble(newName); }
                    loadGroups();
                }).catch(() => {});
            });
        });
        btns[btnIdx++].addEventListener('click', ev => {   // delete
            ev.stopPropagation();
            closeCtxMenu();
            fetch(`${BACKEND_URL}/groups/${encodeURIComponent(groupName)}`, { method: 'DELETE' })
                .then(() => {
                    removePin(groupName);
                    if (isSuper) removeSuperBubble(groupName);
                    loadGroups();
                })
                .catch(() => {});
        });
        document.body.appendChild(menu);
        _ctxMenu = menu;
        const r = menu.getBoundingClientRect();
        if (r.right  > window.innerWidth)  menu.style.left = `${e.clientX - r.width}px`;
        if (r.bottom > window.innerHeight) menu.style.top  = `${e.clientY - r.height}px`;
    }

    // ── Edit modal ────────────────────────────────────────────────────────────
    let _editModal = null;
    function closeEditModal() {
        if (_editModal) { _editModal.remove(); _editModal = null; }
    }
    function showEditModal(x, y, currentValue, onSave) {
        closeEditModal();
        const modal = document.createElement('div');
        modal.className = 'edit-modal';
        const safeVal = currentValue.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
        modal.innerHTML = `
            <input class="edit-modal-input" type="text" value="${safeVal}" />
            <div class="edit-modal-actions">
                <button class="edit-modal-btn edit-modal-save">\u2713 Guardar</button>
                <button class="edit-modal-btn edit-modal-cancel">\u2715 Cancelar</button>
            </div>
        `;
        modal.style.left = `${x}px`;
        modal.style.top  = `${y}px`;
        modal.addEventListener('click', ev => ev.stopPropagation());
        document.body.appendChild(modal);
        _editModal = modal;
        const input     = modal.querySelector('.edit-modal-input');
        const saveBtn   = modal.querySelector('.edit-modal-save');
        const cancelBtn = modal.querySelector('.edit-modal-cancel');
        input.focus();
        input.select();
        const doSave = () => {
            const val = input.value.trim();
            if (val && val !== currentValue) onSave(val);
            closeEditModal();
        };
        saveBtn.addEventListener('click', doSave);
        cancelBtn.addEventListener('click', closeEditModal);
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter')  { e.preventDefault(); doSave(); }
            if (e.key === 'Escape') { e.preventDefault(); closeEditModal(); }
            e.stopPropagation();
        });
        requestAnimationFrame(() => {
            const r = modal.getBoundingClientRect();
            if (r.right  > window.innerWidth)  modal.style.left = `${x - r.width}px`;
            if (r.bottom > window.innerHeight) modal.style.top  = `${y - r.height}px`;
        });
        setTimeout(() => {
            const closeOut = ev => { closeEditModal(); };
            document.addEventListener('click', closeOut, { once: true });
        }, 0);
    }

    // ── Context menu for subgroup bubbles ─────────────────────────────────────
    function showSubCtxMenu(e, item) {
        e.preventDefault();
        closeCtxMenu();
        const parentFrame  = navStack[navStack.length - 1];
        const parentOrig   = parentFrame._orig || parentFrame.title;
        const subgroupOrig = item._orig || item.name;
        const menu = document.createElement('div');
        menu.className = 'pin-context-menu';
        menu.innerHTML = `
            <button class="pin-menu-item">\u270F\uFE0F Editar</button>
            <div class="pin-menu-separator"></div>
            <button class="pin-menu-item pin-menu-item--danger">\uD83D\uDDD1\uFE0F Eliminar</button>
        `;
        menu.style.left = `${e.clientX}px`;
        menu.style.top  = `${e.clientY}px`;
        const [editBtn, delBtn] = menu.querySelectorAll('button');
        editBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            closeCtxMenu();
            showEditModal(e.clientX, e.clientY, item.name, newName => {
                fetch(`${BACKEND_URL}/groups/${encodeURIComponent(parentOrig)}/subgroups/${encodeURIComponent(subgroupOrig)}`, {
                    method:  'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ new_name: newName }),
                }).then(() => { navStack = []; showView('projects-view'); loadGroups(); }).catch(() => {});
            });
        });
        delBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            closeCtxMenu();
            fetch(`${BACKEND_URL}/groups/${encodeURIComponent(parentOrig)}/subgroups/${encodeURIComponent(subgroupOrig)}`, { method: 'DELETE' })
                .then(() => { navStack = []; showView('projects-view'); loadGroups(); })
                .catch(() => {});
        });
        document.body.appendChild(menu);
        _ctxMenu = menu;
        const r = menu.getBoundingClientRect();
        if (r.right  > window.innerWidth)  menu.style.left = `${e.clientX - r.width}px`;
        if (r.bottom > window.innerHeight) menu.style.top  = `${e.clientY - r.height}px`;
    }

    // ── Context menu for idea rows ─────────────────────────────────────────────
    function showIdeaCtxMenu(e, item) {
        e.preventDefault();
        closeCtxMenu();
        const menu = document.createElement('div');
        menu.className = 'pin-context-menu';
        menu.innerHTML = `
            <button class="pin-menu-item">\u270F\uFE0F Editar</button>
            <div class="pin-menu-separator"></div>
            <button class="pin-menu-item pin-menu-item--danger">\uD83D\uDDD1\uFE0F Eliminar</button>
        `;
        menu.style.left = `${e.clientX}px`;
        menu.style.top  = `${e.clientY}px`;
        const [editBtn, delBtn] = menu.querySelectorAll('button');
        editBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            closeCtxMenu();
            if (!item.id) return;
            showEditModal(e.clientX, e.clientY, item.text, newText => {
                fetch(`${BACKEND_URL}/inbox/${item.id}`, {
                    method:  'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ summary: newText }),
                }).then(() => { navStack = []; showView('projects-view'); loadGroups(); }).catch(() => {});
            });
        });
        delBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            closeCtxMenu();
            if (!item.id) return;
            fetch(`${BACKEND_URL}/inbox/${item.id}`, { method: 'DELETE' })
                .then(() => { navStack = []; showView('projects-view'); loadGroups(); })
                .catch(() => {});
        });
        document.body.appendChild(menu);
        _ctxMenu = menu;
        const r = menu.getBoundingClientRect();
        if (r.right  > window.innerWidth)  menu.style.left = `${e.clientX - r.width}px`;
        if (r.bottom > window.innerHeight) menu.style.top  = `${e.clientY - r.height}px`;
    }

    // ── Main grid ─────────────────────────────────────────────────────────────
    function renderMainGrid(groups) {
        projectsGrid.innerHTML = '';
        if (!groups.length) {
            projectsGrid.innerHTML =
                `<p style="opacity:.4;text-align:center;grid-column:1/-1">${t('emptyGroups')}</p>`;
            return;
        }
        sortByPin(groups).slice(0, MAX_BUBBLES).forEach(group => {
            const el  = document.createElement('div');
            el.classList.add('grid-item');
            // Always use original name as pin key so translated names don't corrupt state
            const pinKey   = group._orig || group.name;
            const isSuper  = isSuperBubble(pinKey);
            if (isPinned(pinKey)) el.classList.add('grid-item--pinned');
            if (isSuper)          el.classList.add('grid-item--super');

            const txt = document.createElement('span');
            txt.classList.add('item-text');
            txt.textContent = group.name;
            el.appendChild(txt);

            if (isSuper) {
                const badge = document.createElement('span');
                badge.className = 'super-badge';
                badge.textContent = '\uD83D\uDCC4';
                el.appendChild(badge);
            } else if (isPinned(pinKey)) {
                const pin = document.createElement('span');
                pin.className = 'pin-badge';
                pin.textContent = '\uD83D\uDCCC';
                el.appendChild(pin);
            }

            el.addEventListener('click', () => {
                navStack = [];
                const origName   = group._orig || group.name;
                const rawSummary = summariesMap[origName] || null;
                const summary    = rawSummary ? (transCache[rawSummary] || rawSummary) : null;
                pushDetail(group.name, buildItems(group), t('groupsLabel'), summary, origName);
            });
            el.addEventListener('contextmenu', e => showPinMenu(e, group._orig || group.name, isSuper));
            projectsGrid.appendChild(el);
        });
    }

    // Convert group/subgroup node to mixed items array
    // Subgroups first (as navigable bubbles), then direct ideas as text rows
    function buildItems(node) {
        const items = [];
        for (const sg of (node.subgroups || [])) {
            items.push({ type: 'subgroup', name: sg.name, ideas: sg.ideas || [] });
        }
        for (const idea of (node.ideas || [])) {
            const text = (idea && typeof idea === 'object') ? idea.text : idea;
            const url  = (idea && typeof idea === 'object') ? (idea.url  || null) : null;
            const id   = (idea && typeof idea === 'object') ? (idea.id   || null) : null;
            items.push({ type: 'idea', text, url, id });
        }
        return items;
    }

    // ── View switching ────────────────────────────────────────────────────
    function showView(id) {
        projectsView.classList.toggle('active-view', id === 'projects-view');
        projectsView.classList.toggle('hidden-view', id !== 'projects-view');
        detailView.classList.toggle('active-view', id === 'detail-view');
        detailView.classList.toggle('hidden-view', id !== 'detail-view');
        backButton.style.display = (id === 'detail-view') ? 'flex' : 'none';
    }

    // ── Detail view navigation ────────────────────────────────────────────────
    function pushDetail(title, items, parentLabel, summary, origTitle) {
        navStack.push({ title, _orig: origTitle || title, items, parentLabel, summary: summary || null });
        renderDetail();
        showView('detail-view');
    }

    function renderDetail() {
        const frame = navStack[navStack.length - 1];
        detailTitle.textContent = frame.title;
        backLabel.textContent   = frame.parentLabel;
        renderDetailContent(frame.items, frame.summary);
    }

    function renderDetailContent(items, summary) {
        detailContent.innerHTML = '';

        // Summary card (only when AI has generated one)
        if (summary) {
            const card = document.createElement('div');
            card.classList.add('summary-card');
            const label = document.createElement('span');
            label.classList.add('summary-card-label');
            label.textContent = t('summaryLabel');
            const text = document.createElement('p');
            text.classList.add('summary-card-text');
            text.textContent = summary;
            card.appendChild(label);
            card.appendChild(text);
            detailContent.appendChild(card);
        }

        if (!items.length) {
            detailContent.innerHTML = `<p class="detail-empty">${t('emptyDetail')}</p>`;
            return;
        }
        items.forEach(item => {
            if (item.type === 'subgroup') {
                // ── Sub-bubble ────────────────────────────────────────────────
                const bubble = document.createElement('div');
                bubble.classList.add('sub-bubble');

                const txt = document.createElement('span');
                txt.classList.add('sub-bubble-text');
                txt.textContent = item.name;
                bubble.appendChild(txt);

                if (item.ideas.length) {
                    const cnt = document.createElement('span');
                    cnt.classList.add('sub-bubble-count');
                    cnt.textContent = `${item.ideas.length} idea${item.ideas.length > 1 ? 's' : ''}`;
                    bubble.appendChild(cnt);
                }

                bubble.addEventListener('click', () => {
                    const parentFrame = navStack[navStack.length - 1];
                    const parentOrig  = parentFrame._orig || parentFrame.title;
                    const itemOrig    = item._orig  || item.name;
                    const subItems    = item.ideas.map(idea => ({
                        type: 'idea',
                        text: (idea && typeof idea === 'object') ? idea.text : idea,
                        url:  (idea && typeof idea === 'object') ? (idea.url  || null) : null,
                        id:   (idea && typeof idea === 'object') ? (idea.id   || null) : null,
                    }));
                    const rawSummary  = summariesMap[`${parentOrig}/${itemOrig}`] || null;
                    const subSummary  = rawSummary ? (transCache[rawSummary] || rawSummary) : null;
                    pushDetail(item.name, subItems, parentFrame.title, subSummary, itemOrig);
                });
                bubble.addEventListener('contextmenu', e => showSubCtxMenu(e, item));
                detailContent.appendChild(bubble);
            } else {
                // ── YouTube card ──────────────────────────────────────────────
                if (item.url && ytVideoId(item.url)) {
                    const videoId = ytVideoId(item.url);
                    const card = document.createElement('div');
                    card.className = 'yt-card';
                    card.innerHTML = `
                        <div class="yt-card-header">
                            <svg class="yt-card-icon" viewBox="0 0 24 24" width="16" height="16" fill="#ff0000"><path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.6 3.6 12 3.6 12 3.6s-7.6 0-9.4.5A3 3 0 0 0 .5 6.2C0 8 0 12 0 12s0 4 .5 5.8a3 3 0 0 0 2.1 2.1C4.4 20.4 12 20.4 12 20.4s7.6 0 9.4-.5a3 3 0 0 0 2.1-2.1C24 16 24 12 24 12s0-4-.5-5.8z"/><polygon points="9.6,15.6 15.8,12 9.6,8.4" fill="white"/></svg>
                            <span class="yt-card-provider">YouTube</span>
                        </div>
                        <div class="yt-card-channel yt-meta-loading">&#8203;</div>
                        <a class="yt-card-title yt-meta-loading" href="${item.url}" target="_blank" rel="noopener">Cargando...</a>
                        <a class="yt-card-thumb-wrap" href="${item.url}" target="_blank" rel="noopener">
                            <img class="yt-card-thumb" src="https://i.ytimg.com/vi/${videoId}/hqdefault.jpg" alt="thumbnail" loading="lazy" />
                            <div class="yt-card-play"><svg viewBox="0 0 24 24" width="44" height="44" fill="white"><path d="M8 5v14l11-7z"/></svg></div>
                        </a>
                    `;
                    card.addEventListener('contextmenu', e => showIdeaCtxMenu(e, item));
                    detailContent.appendChild(card);
                    fetch(`https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${videoId}&format=json`)
                        .then(r => r.json())
                        .then(meta => {
                            const ch = card.querySelector('.yt-card-channel');
                            const ti = card.querySelector('.yt-card-title');
                            if (ch) { ch.textContent = meta.author_name || ''; ch.classList.remove('yt-meta-loading'); }
                            if (ti) { ti.textContent = meta.title || item.url;   ti.classList.remove('yt-meta-loading'); }
                        })
                        .catch(() => {
                            const ti = card.querySelector('.yt-card-title');
                            if (ti) { ti.textContent = item.url; ti.classList.remove('yt-meta-loading'); }
                        });
                    return;
                }

                // ── Idea row ──────────────────────────────────────────────────
                const row = document.createElement('div');
                row.classList.add('idea-item');

                const bullet = document.createElement('span');
                bullet.classList.add('idea-bullet');

                const txt = document.createElement('span');
                txt.classList.add('idea-text');
                txt.textContent = item.text;

                row.appendChild(bullet);
                row.appendChild(txt);
                row.addEventListener('contextmenu', e => showIdeaCtxMenu(e, item));

                if (item.url) {
                    const link = document.createElement('a');
                    link.className = 'idea-link';
                    link.href = item.url;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.title = item.url;
                    link.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>`;
                    row.appendChild(link);
                }

                detailContent.appendChild(row);
            }
        });
    }

    backButton.addEventListener('click', () => {
        navStack.pop();
        if (navStack.length === 0) {
            showView('projects-view');
        } else {
            renderDetail();
        }
    });

    // ── Submit note ───────────────────────────────────────────────────────────
    const sendButton = document.getElementById('send-button');

    function setLoading(on) {
        searchInput.disabled = on;
        sendButton.disabled  = on;
        sendButton.classList.toggle('is-loading', on);
        if (on) searchInput.placeholder = t('processing');
    }

    function submitNote(text) {
        if (!text.trim()) return;

        // ── YouTube fast-save (bypass AI) ─────────────────────────────────────
        const ytId = ytVideoId(text.trim());
        if (ytId) {
            setLoading(true);
            const cleanUrl = `https://www.youtube.com/watch?v=${ytId}`;
            fetch(`${BACKEND_URL}/batch-save`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    items:  [{ idea: cleanUrl, group: 'youtube', source_url: cleanUrl }],
                    origin: 'frontend',
                }),
            }).then(() => {
                searchInput.value = '';
                setLoading(false);
                searchInput.placeholder = '✓ Guardado en "youtube"';
                setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
                loadGroups();
            }).catch(() => {
                setLoading(false);
                searchInput.placeholder = t('errorServer');
                setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
            });
            return;
        }

        setLoading(true);

        fetch(`${BACKEND_URL}/note`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ content: text.trim(), origin: 'frontend', lang: currentLang }),
        })
        .then(r => r.json())
        .then(dataArr => {
            // El backend devuelve siempre una lista
            const items = Array.isArray(dataArr) ? dataArr : [dataArr];
            const data  = items.find(d => d.action !== 'ignored') || items[0];

            searchInput.value = '';
            setLoading(false);

            if (data.action === 'ignored') {
                searchInput.placeholder = t('ignored');
            } else if (data.action === 'delete') {
                const n = data.deleted_count || 1;
                let scope;
                if (data.idea) {
                    scope = currentLang === 'en'
                        ? `"${data.idea}" from "${data.group}"`
                        : `"${data.idea}" de "${data.group}"`;
                } else if (data.subgroup) {
                    scope = currentLang === 'en'
                        ? `subgroup "${data.subgroup}" of "${data.group}"`
                        : `subgrupo "${data.subgroup}" de "${data.group}"`;
                } else {
                    scope = currentLang === 'en'
                        ? `group "${data.group}"`
                        : `grupo "${data.group}"`;
                }
                const plural = currentLang === 'en'
                    ? (n === 1 ? `${n} item` : `${n} items`)
                    : (n === 1 ? `${n} idea` : `${n} ideas`);
                searchInput.placeholder = `🗑️ ${currentLang === 'en' ? 'Deleted' : 'Eliminado'} ${scope} (${plural})`;
            } else if (data.action === 'remind') {
                const when = data.remind_at
                    ? new Date(data.remind_at).toLocaleString(
                        currentLang === 'en' ? 'en-GB' : 'es-ES',
                        { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
                      )
                    : '';
                searchInput.placeholder = `${t('reminderSet')} ${when} — ${data.idea}`;
            } else {
                const sp    = data.subgroup ? ` \u203a ${data.subgroup}` : '';
                const extra = items.length > 1 ? ` (+${items.length - 1} ${t('more')})` : '';
                if (data.group) {
                    searchInput.placeholder = `${t('saved')} "${data.group}${sp}"${extra}`;
                } else {
                    searchInput.placeholder = currentLang === 'en' ? `✓ Saved${extra}` : `✓ Guardado${extra}`;
                }
            }
            // Refresh reminders bar if any result created/is a reminder
            if (items.some(d => d.action === 'remind')) loadReminders();
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
            loadGroups();
        })
        .catch(() => {
            setLoading(false);
            searchInput.placeholder = t('errorServer');
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
        });
    }

    document.getElementById('send-button').addEventListener('click', () => {
        submitNote(searchInput.value);
    });

    searchInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !searchInput.disabled) submitNote(searchInput.value);
    });

    // ── Microphone recording ──────────────────────────────────────────────────
    const micButton  = document.getElementById('mic-button');
    let mediaRecorder = null;
    let audioChunks   = [];

    function setMicState(state) {
        micButton.classList.toggle('is-recording',    state === 'recording');
        micButton.classList.toggle('is-transcribing', state === 'transcribing');
        micButton.disabled    = (state === 'transcribing');
        sendButton.disabled   = (state === 'recording' || state === 'transcribing');
        searchInput.disabled  = (state === 'transcribing');
        if (state === 'recording')    searchInput.placeholder = t('recording');
        if (state === 'transcribing') searchInput.placeholder = t('transcribing');
    }

    micButton.addEventListener('click', async () => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
            return;
        }

        // Check secure context / API availability
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            searchInput.placeholder = t('noMic');
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 4000);
            return;
        }

        // Give immediate visual feedback before async permission prompt
        micButton.classList.add('is-recording');
        searchInput.placeholder = t('requestingMic');

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunks  = [];
            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : MediaRecorder.isTypeSupported('audio/webm')
                    ? 'audio/webm'
                    : '';
            mediaRecorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);

            mediaRecorder.addEventListener('dataavailable', e => {
                if (e.data.size > 0) audioChunks.push(e.data);
            });

            mediaRecorder.addEventListener('stop', async () => {
                stream.getTracks().forEach(t => t.stop());
                setMicState('transcribing');
                const blob     = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                const formData = new FormData();
                formData.append('audio', blob, 'recording.webm');
                try {
                    const resp = await fetch(`${BACKEND_URL}/transcribe`, { method: 'POST', body: formData });
                    if (!resp.ok) throw new Error('transcription error');
                    const { transcribed_text } = await resp.json();
                    setMicState('idle');
                    if (transcribed_text && transcribed_text.trim()) {
                        submitNote(transcribed_text.trim());
                    } else {
                        searchInput.placeholder = t('noSpeech');
                        setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
                    }
                } catch {
                    setMicState('idle');
                    searchInput.placeholder = t('transcribeError');
                    setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
                }
            });

            mediaRecorder.start();
            setMicState('recording');
        } catch (err) {
            micButton.classList.remove('is-recording');
            searchInput.placeholder = t('micError') + (err.message || err);
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 4000);
        }
    });

    // ── Document upload ───────────────────────────────────────────────────────
    const docButton = document.getElementById('doc-button');
    const docInput  = document.getElementById('doc-input');

    docButton.addEventListener('click', () => {
        if (docButton.classList.contains('is-processing')) return;
        docInput.value = '';
        docInput.click();
    });

    docInput.addEventListener('change', async () => {
        const file = docInput.files[0];
        if (!file) return;

        docButton.classList.add('is-processing');
        docButton.disabled   = true;
        sendButton.disabled  = true;
        searchInput.disabled = true;
        searchInput.placeholder = t('processingDoc');

        try {
            // 1. Send file to AI service for extraction
            const formData = new FormData();
            formData.append('file', file);
            formData.append('lang', currentLang);
            // 30-minute timeout — large docs with many chunks can take a long time
            const aiController = new AbortController();
            const aiTimeout    = setTimeout(() => aiController.abort(), 30 * 60 * 1000);
            let aiResp;
            try {
                aiResp = await fetch(`${AI_URL}/extract-document`, {
                    method: 'POST',
                    body:   formData,
                    signal: aiController.signal,
                });
            } finally {
                clearTimeout(aiTimeout);
            }
            if (!aiResp.ok) {
                const detail = await aiResp.json().catch(() => ({}));
                throw new Error(detail.detail || `AI error ${aiResp.status}`);
            }
            const { extractions } = await aiResp.json();

            if (!extractions || extractions.length === 0) {
                searchInput.placeholder = t('docNoIdeas');
                setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3500);
                return;
            }

            // 2. If doc produces > 3 groups → wrap all into a super-bubble
            const distinctGroups = [...new Set(extractions.map(e => e.group).filter(Boolean))];
            let itemsToSave = extractions;
            if (distinctGroups.length > 2) {
                const baseName  = file.name.replace(/\.[^.]+$/, '');
                const superName = `\uD83D\uDCC4 ${baseName}`;
                saveSuperBubble(superName);
                itemsToSave = extractions.map(item => ({
                    idea:       item.subgroup ? `[${item.subgroup}] ${item.idea}` : item.idea,
                    group:      superName,
                    subgroup:   item.group,
                    source_url: item.source_url || undefined,
                }));
            }

            // 3. Save pre-classified ideas directly to backend
            const saveResp = await fetch(`${BACKEND_URL}/batch-save`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ items: itemsToSave, origin: 'document' }),
            });
            if (!saveResp.ok) throw new Error(`Backend error ${saveResp.status}`);
            const { saved } = await saveResp.json();

            searchInput.placeholder = `📄 ${saved} ${t('docDone')}`;
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 4000);
            loadGroups();
        } catch (err) {
            console.error('Document upload error:', err);
            searchInput.placeholder = t('docError');
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3500);
        } finally {
            docButton.classList.remove('is-processing');
            docButton.disabled   = false;
            sendButton.disabled  = false;
            searchInput.disabled = false;
        }
    });

    loadGroups();
    loadReminders();
    // Poll reminders every 30 s to reflect emails sent by the scheduler
    setInterval(loadReminders, 30000);
});
