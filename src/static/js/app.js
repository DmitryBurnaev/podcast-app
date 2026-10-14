(function () {
    'use strict';
    const {request, RequestError} = window.PodcastHTTP;
    const operations = new Set();
    const trackedEpisodes = new Map();
    const rssTasks = new Map();
    const busyControls = new WeakMap();
    const regions = new Set(['overview', 'podcasts', 'episodes', 'podcast-summary', 'episode-title',
        'episode-state', 'episode-metadata', 'episode-player', 'app-statistics', 'breadcrumbs']);
    let activeModal = null;
    let modalOpener = null;
    let confirmation = null;
    let refreshChain = Promise.resolve();
    let pollTimer = null;
    let pollRunning = false;
    let pollAgain = false;
    let pollDelay = 2000;
    let authExpired = false;
    let rssDiscovered = false;

    const byId = id => document.getElementById(id);
    const episodeForm = () => byId('edit-episode-form');
    const podcastForm = () => byId('edit-podcast-form');
    const activeStatus = status => ['DOWNLOADING', 'CANCELING'].includes(String(status).toUpperCase());
    const actionButtons = () => document.querySelectorAll('[data-action]');

    function statusLabel(status) {
        return ({NEW: 'Pending', ERROR: 'Error', PUBLISHED: 'Published', ARCHIVED: 'Archived',
            DOWNLOADING: 'Downloading', CANCELING: 'Canceling', DL_PENDING: 'Waiting for download',
            DL_EPISODE_DOWNLOADING: 'Downloading audio', DL_EPISODE_POSTPROCESSING: 'Processing audio',
            DL_EPISODE_UPLOADING: 'Uploading audio', DL_COVER_DOWNLOADING: 'Downloading cover',
            DL_COVER_UPLOADING: 'Uploading cover'})[status] || 'Processing';
    }

    function feedback(scope, text, kind = 'success', error = null, source = 'operation') {
        if (!scope) return;
        const box = scope.matches('[data-feedback]') ? scope : scope.querySelector('[data-operation-feedback], [data-feedback]:not(#rss-feedback)');
        if (!box) return;
        box.replaceChildren();
        box.hidden = !text;
        box.className = `ui-feedback ui-feedback-${kind}`;
        box.dataset.source = source;
        box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        if (!text) return;
        if (kind === 'pending') {
            const spinner = document.createElement('span');
            spinner.className = 'ui-spinner';
            spinner.setAttribute('aria-hidden', 'true');
            box.append(spinner);
        }
        const message = document.createElement('span');
        message.textContent = text;
        box.append(message);
        if (error?.status === 401) {
            authExpired = true;
            const login = document.createElement('a');
            login.href = '/login';
            login.textContent = 'Sign in';
            login.className = 'ui-feedback-link';
            box.append(login);
            syncButtons();
        }
    }

    function pageMessage(text, error = null, source = 'operation', retry = false) {
        const box = byId('page-feedback');
        if (!box) return;
        feedback(box, text, 'error', error, source);
        if (retry && error?.status !== 401) {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.action = 'refresh-page';
            button.textContent = 'Refresh data';
            button.className = 'ui-feedback-link';
            box.append(button);
        }
    }

    function setBusy(scope, busy) {
        scope.dataset.busy = String(busy);
        scope.setAttribute('aria-busy', String(busy));
        scope.querySelectorAll('button, input, textarea, select').forEach(control => {
            if (busy) {
                busyControls.set(control, control.disabled);
                control.disabled = true;
            } else if (busyControls.has(control)) {
                control.disabled = busyControls.get(control);
                busyControls.delete(control);
            }
        });
        syncButtons();
    }

    function syncButtons() {
        const detail = document.querySelector('[data-refresh-region="episode-state"]');
        actionButtons().forEach(button => {
            if (button.closest('[data-busy="true"]')) return;
            const action = button.dataset.action;
            if (['download-episode', 'cancel-episode', 'delete-episode'].includes(action)) {
                const card = button.closest('[data-episode-card]');
                const status = card?.dataset.status || detail?.dataset.status || '';
                const active = activeStatus(status);
                button.disabled = authExpired || (action === 'cancel-episode' ? status !== 'DOWNLOADING' : active);
                if (action === 'cancel-episode') button.hidden = !active;
                if (action === 'download-episode') button.hidden = active || Boolean(card && ['PUBLISHED', 'ARCHIVED'].includes(status));
            }
            if (action === 'generate-rss') button.disabled = authExpired || rssTasks.has(Number(button.dataset.id));
        });
    }

    function openModal(id, opener = document.activeElement) {
        const modal = byId(id);
        if (!modal || activeModal?.dataset.busy === 'true') return;
        if (activeModal) closeModal();
        modalOpener = {element: opener, action: opener?.dataset?.action, id: opener?.dataset?.id};
        activeModal = modal;
        modal.hidden = false;
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        document.body.style.overflow = 'hidden';
        const focus = modal.querySelector('[autofocus], input, textarea, button');
        focus?.focus();
    }

    function closeModal(force = false) {
        if (!activeModal || (!force && activeModal.dataset.busy === 'true')) return;
        const modal = activeModal;
        modal.hidden = true;
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        activeModal = null;
        confirmation = null;
        document.body.style.overflow = '';
        modal.querySelector('form')?.reset();
        feedback(modal, '');
        const opener = modalOpener;
        modalOpener = null;
        const target = opener?.element?.isConnected ? opener.element :
            [...actionButtons()].find(button => button.dataset.action === opener?.action && button.dataset.id === opener?.id);
        target?.focus({preventScroll: true});
    }

    function showError(error) {
        const modal = byId('actionModal');
        if (activeModal) {
            feedback(activeModal, error.message, 'error', error);
            return;
        }
        byId('action-modal-title').textContent = 'Unable to complete action';
        byId('action-modal-description').textContent = '';
        byId('action-modal-confirm').hidden = true;
        openModal('actionModal');
        feedback(modal, error.message, 'error', error);
    }

    function confirmDelete(kind, id, opener) {
        const card = opener.closest('[data-episode-card], [data-podcast-card]');
        const name = card?.dataset.name || (kind === 'podcast' ? podcastForm()?.elements.name.value : episodeForm()?.elements.title.value) || kind;
        byId('action-modal-title').textContent = `Delete ${kind}?`;
        byId('action-modal-description').textContent = `Delete “${name}”? ${kind === 'podcast' ? 'Its episodes will also be deleted. ' : ''}This action cannot be undone.`;
        const button = byId('action-modal-confirm');
        button.hidden = false;
        button.textContent = 'Delete';
        openModal('actionModal', opener);
        confirmation = {kind, id};
    }

    async function refreshPage(url = new URL(window.location.href)) {
        const location = new URL(url);
        location.searchParams.set('fragment', '1');
        const perform = async () => {
            const html = await request(location.pathname + location.search, {html: true});
            const parsed = new DOMParser().parseFromString(html, 'text/html');
            const incoming = [...parsed.querySelectorAll('[data-refresh-region]')];
            if (!incoming.length) throw new RequestError('The server returned an unexpected page response.');
            if (operations.size && !operations.has('filters')) return false;
            const scroll = document.querySelector('main')?.scrollTop;
            const focused = document.activeElement;
            const focusKey = focused?.dataset?.action;
            const focusId = focused?.dataset?.id;
            incoming.forEach(node => {
                const name = node.dataset.refreshRegion;
                if (!regions.has(name)) return;
                const previous = document.querySelector(`[data-refresh-region="${name}"]`);
                if (!previous) return;
                if (name === 'episode-player' && previous.dataset.sourceUrl === node.dataset.sourceUrl) return;
                node.querySelectorAll('script').forEach(script => script.remove());
                previous.replaceWith(node);
            });
            if (scroll !== undefined) document.querySelector('main').scrollTop = scroll;
            if (focusKey && !focused.isConnected) {
                [...actionButtons()].find(button => button.dataset.action === focusKey && button.dataset.id === focusId)?.focus({preventScroll: true});
            }
            initPlayer();
            syncButtons();
            return true;
        };
        refreshChain = refreshChain.catch(() => {}).then(perform);
        return refreshChain;
    }

    async function refreshAfterSuccess() {
        try {
            await refreshPage();
            wakePoll();
            return true;
        } catch (error) {
            pageMessage('The operation succeeded, but the page data could not be refreshed. ' + error.message, error, 'operation', true);
            wakePoll();
            return false;
        }
    }

    async function mutate(key, scope, label, send, success) {
        if (operations.has(key) || operations.has('filters')) return;
        operations.add(key);
        setBusy(scope, true);
        feedback(scope, label, 'pending');
        let data;
        let failure;
        try { data = await send(); } catch (error) { failure = error; }
        operations.delete(key);
        setBusy(scope, false);
        if (failure) {
            if (scope.matches('form, [data-modal]')) feedback(scope, failure.message, 'error', failure);
            else { feedback(scope, ''); showError(failure); }
            if (failure.details?.episode_id) {
                const box = scope.querySelector('[data-feedback]');
                const link = document.createElement('a');
                link.href = `/episodes/${Number(failure.details.episode_id)}/`;
                link.textContent = 'Open the created episode';
                link.className = 'ui-feedback-link';
                box?.append(link);
                await refreshAfterSuccess();
            }
            return;
        }
        feedback(scope, 'Done.');
        await success(data);
    }

    function savedForm(form, values) {
        for (const [key, value] of Object.entries(values)) {
            const field = form.elements.namedItem(key);
            if (!field || field.readOnly) continue;
            if (field.type === 'checkbox') field.checked = Boolean(value);
            else field.value = value ?? '';
        }
        feedback(form, 'Changes saved.');
        const title = values.name || values.title;
        if (title) document.title = 'Podcast App: ' + title.slice(0, 32);
    }

    async function submitForm(form) {
        const data = new FormData(form);
        const scope = form.closest('[data-modal]') || form;
        const id = Number(form.dataset.id);
        switch (form.dataset.form) {
            case 'create-podcast':
                await mutate('create-podcast', scope, 'Creating podcast…',
                    () => request('/web-api/podcasts/', {method: 'POST', data: {name: String(data.get('name')).trim(), description: data.get('description'), download_automatically: data.has('download_automatically')}}),
                    async () => { closeModal(true); await refreshAfterSuccess(); });
                break;
            case 'create-episode':
                await mutate(`podcast:${id}`, scope, 'Getting episode information…',
                    () => request(`/web-api/podcasts/${id}/episodes/`, {method: 'POST', data: {sourceURL: String(data.get('sourceURL')).trim()}}),
                    async episode => {
                        if (activeStatus(episode.status)) trackedEpisodes.set(episode.id, episode);
                        closeModal(true);
                        await refreshAfterSuccess();
                    });
                break;
            case 'edit-podcast':
                await mutate(`podcast:${id}`, form, 'Saving podcast…',
                    () => request(`/web-api/podcasts/${id}/`, {method: 'PATCH', data: {name: String(data.get('name')).trim(), description: data.get('description'), download_automatically: data.has('download_automatically')}}),
                    async podcast => { savedForm(form, podcast); await refreshAfterSuccess(); });
                break;
            case 'edit-episode':
                await mutate(`episode:${id}`, form, 'Saving episode…',
                    () => request(`/web-api/episodes/${id}/`, {method: 'PATCH', data: {title: String(data.get('title')).trim(), author: data.get('author'), description: data.get('description')}}),
                    async episode => { savedForm(form, episode); await refreshAfterSuccess(); });
                break;
            case 'filters': {
                const url = new URL(window.location.href);
                url.search = '';
                for (const key of ['search', 'podcast', 'size_min', 'size_max']) {
                    if (data.get(key)) url.searchParams.set(key, data.get(key));
                }
                const statuses = data.getAll('status');
                if (statuses.length) url.searchParams.set('status', statuses.join(','));
                await changeFilters(url, scope);
                break;
            }
        }
    }

    async function changeFilters(url, scope = byId('page-feedback')) {
        if (operations.size) return;
        operations.add('filters');
        setBusy(scope, true);
        feedback(scope, 'Updating episodes…', 'pending');
        try {
            await refreshPage(url);
            window.history.pushState({}, '', url.pathname + url.search);
            const search = byId('search-input');
            if (search) search.value = url.searchParams.get('search') || '';
            feedback(scope, '');
            if (activeModal === scope) closeModal(true);
        } catch (error) { feedback(scope, error.message, 'error', error); }
        finally { operations.delete('filters'); setBusy(scope, false); }
    }

    async function handleAction(button) {
        const id = Number(button.dataset.id);
        const scope = button.closest('[data-episode-card]') || episodeForm() || podcastForm() || byId('page-feedback');
        switch (button.dataset.action) {
            case 'edit-episode': window.location.assign(`/episodes/${id}/`); break;
            case 'add-podcast': openModal('addPodcastModal', button); break;
            case 'add-episode':
                byId('add-episode-form').dataset.id = String(id);
                byId('episodePodcastTitle').textContent = button.dataset.name;
                openModal('addEpisodeModal', button);
                break;
            case 'close-modal': closeModal(); break;
            case 'open-filters': openModal('filtersModal', button); break;
            case 'delete-podcast': confirmDelete('podcast', id, button); break;
            case 'delete-episode': confirmDelete('episode', id, button); break;
            case 'confirm-delete': {
                const {kind, id: confirmedId} = confirmation || {};
                if (!kind) return;
                await mutate(`${kind}:${confirmedId}`, activeModal, `Deleting ${kind}…`,
                    () => request(`/web-api/${kind === 'podcast' ? 'podcasts' : 'episodes'}/${confirmedId}/`, {method: 'DELETE'}),
                    async () => {
                        if (kind === 'episode') trackedEpisodes.delete(confirmedId);
                        if (kind === 'podcast') rssTasks.delete(confirmedId);
                        closeModal(true);
                        const detailForm = kind === 'podcast' ? podcastForm() : episodeForm();
                        if (Number(detailForm?.dataset.id) === confirmedId) {
                            window.location.assign(kind === 'podcast' ? '/podcasts/' : `/podcasts/${detailForm.dataset.podcastId}/`);
                        } else await refreshAfterSuccess();
                    });
                break;
            }
            case 'download-episode':
            case 'cancel-episode': {
                const cancel = button.dataset.action === 'cancel-episode';
                await mutate(`episode:${id}`, scope, cancel ? 'Requesting cancellation…' : 'Starting download…',
                    () => request(`/web-api/episodes/${id}/${cancel ? 'cancel-downloading' : 'download'}/`, {method: 'PUT'}),
                    async episode => {
                        trackedEpisodes.set(id, episode);
                        feedback(scope, cancel ? 'Cancellation requested.' : 'Download started.');
                        const card = button.closest('[data-episode-card]');
                        if (card) card.dataset.status = episode.status;
                        await refreshAfterSuccess();
                    });
                break;
            }
            case 'generate-rss':
                await mutate(`podcast:${id}`, scope, 'Starting RSS generation…',
                    () => request(`/web-api/podcasts/${id}/generate-rss/`, {method: 'PUT'}),
                    async task => { rssTasks.set(id, task.job_id); feedback(scope, 'RSS generation started.'); feedback(byId('rss-feedback'), 'Generating RSS…', 'pending'); syncButtons(); wakePoll(); });
                break;
            case 'copy-rss':
                try { await navigator.clipboard.writeText(byId('podcast-rss-link').value); feedback(byId('rss-feedback'), 'RSS link copied.'); }
                catch { feedback(byId('rss-feedback'), 'Could not copy the link. Select and copy it manually.', 'error'); }
                break;
            case 'toggle-search': {
                const search = byId('search-block');
                if (!search) return;
                search.classList.toggle('hidden');
                if (!search.classList.contains('hidden')) byId('search-input')?.focus();
                break;
            }
            case 'toggle-user-menu': byId('user-dropdown-menu')?.classList.toggle('hidden'); break;
            case 'remove-filter':
            case 'clear-filters': {
                const url = new URL(window.location.href);
                if (button.dataset.action === 'clear-filters') url.search = '';
                else button.dataset.filter.split(',').forEach(key => url.searchParams.delete(key));
                await changeFilters(url);
                break;
            }
            case 'refresh-page':
                authExpired = false;
                if (await refreshAfterSuccess()) feedback(byId('page-feedback'), '');
                break;
        }
    }

    function paintProgress(item) {
        const id = item.episode.id;
        const cards = [...document.querySelectorAll('[data-episode-card]')].filter(card => Number(card.dataset.id) === id);
        const detail = document.querySelector('[data-refresh-region="episode-state"]');
        if (Number(detail?.dataset.episodeId) === id) cards.push(detail);
        cards.forEach(card => {
            card.dataset.status = item.episode.status;
            const badge = card.querySelector('[data-status-label]');
            if (badge) badge.textContent = statusLabel(item.episode.status);
            const label = card.querySelector('[data-progress-label]');
            if (label) label.textContent = statusLabel(item.status) + (item.total_file_size > 0 ? ` ${item.completed}%` : '');
            const bar = card.querySelector('[data-progress-bar]');
            if (bar) bar.style.width = `${Math.max(0, Math.min(100, item.completed))}%`;
            const progress = card.querySelector('[data-progress]');
            if (progress) progress.hidden = false;
        });
    }

    async function poll() {
        if (pollRunning || document.hidden || authExpired) return;
        pollRunning = true;
        try {
            if (operations.size) return;
            const data = await request('/web-api/progress/');
            if (!Array.isArray(data.progressItems)) throw new RequestError('Live updates returned an unexpected response.');
            if (operations.size) return;
            const active = new Set();
            for (const item of data.progressItems) {
                active.add(item.episode.id);
                trackedEpisodes.set(item.episode.id, item.episode);
                paintProgress(item);
            }
            let refresh = false;
            for (const [id, previous] of trackedEpisodes) {
                if (active.has(id)) continue;
                const episode = await request(`/web-api/episodes/${id}/`).catch(error => {
                    if (error.status === 404) return null;
                    throw error;
                });
                if (episode && activeStatus(episode.status)) continue;
                trackedEpisodes.delete(id);
                refresh = true;
                if (episode?.status === 'ERROR' && document.querySelector(`[data-episode-card][data-id="${id}"], [data-episode-id="${id}"]`)) {
                    pageMessage(`Download failed for “${episode.title || previous.title}”. You can try downloading it again.`);
                }
            }
            const podId = Number(podcastForm()?.dataset.id);
            if (podId && !rssDiscovered) {
                const task = await request(`/web-api/podcasts/${podId}/rss-task/`);
                rssDiscovered = true;
                if (['queued', 'running'].includes(task.status)) rssTasks.set(podId, task.job_id);
            }
            for (const [id] of rssTasks) {
                const task = await request(`/web-api/podcasts/${id}/rss-task/`);
                if (['queued', 'running'].includes(task.status)) {
                    feedback(byId('rss-feedback'), task.status === 'queued' ? 'RSS generation queued…' : 'Generating RSS…', 'pending');
                } else if (task.status === 'success') {
                    const podcast = await request(`/web-api/podcasts/${id}/`);
                    updateRss(podcast.rss_url);
                    feedback(byId('rss-feedback'), 'RSS generated.');
                    rssTasks.delete(id);
                    refresh = true;
                } else {
                    rssTasks.delete(id);
                    feedback(byId('rss-feedback'), task.error || 'Unable to confirm the RSS task result. Check the feed or try again.', 'error');
                }
            }
            if (refresh) await refreshPage();
            syncButtons();
            pollDelay = 2000;
            if (byId('page-feedback')?.dataset.source === 'poll') feedback(byId('page-feedback'), '');
        } catch (error) {
            pageMessage(error.status === 401 ? error.message : 'Live updates are temporarily unavailable. Retrying… ' + error.message, error, 'poll');
            pollDelay = Math.min(30000, pollDelay * 2);
        } finally {
            pollRunning = false;
            if (!document.hidden && !authExpired) {
                pollTimer = setTimeout(poll, pollAgain ? 0 : pollDelay);
                pollAgain = false;
            }
        }
    }

    function wakePoll() {
        if (!document.querySelector('[data-refresh-region="overview"], [data-refresh-region="episodes"], [data-refresh-region="podcasts"], [data-refresh-region="episode-state"]')) return;
        clearTimeout(pollTimer);
        if (pollRunning) pollAgain = true;
        else if (!document.hidden && !authExpired) pollTimer = setTimeout(poll, 0);
    }

    function updateRss(url) {
        const input = byId('podcast-rss-link');
        if (!input) return;
        input.value = url || '';
        byId('rss-open-link').hidden = !url;
        byId('rss-open-link').href = url || '#';
        byId('rss-copy-button').hidden = !url;
    }

    function initPlayer() {
        const audio = byId('episode-audio');
        if (!audio || audio.dataset.bound) return;
        audio.dataset.bound = 'true';
        const slider = byId('progress-slider');
        const play = byId('play-button');
        let dragging = false;
        const update = () => {
            const seconds = Math.floor(Number.isFinite(audio.currentTime) ? audio.currentTime : 0);
            byId('time-display').textContent = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(value => String(value).padStart(2, '0')).join(':');
            slider.max = Number.isFinite(audio.duration) ? String(audio.duration) : '0';
            if (!dragging) slider.value = String(audio.currentTime);
            byId('play-icon').classList.toggle('hidden', !audio.paused);
            byId('pause-icon').classList.toggle('hidden', audio.paused);
        };
        play.addEventListener('click', async () => {
            try {
                if (audio.paused) await audio.play(); else audio.pause();
                feedback(byId('player-feedback'), '');
            } catch { feedback(byId('player-feedback'), 'Audio could not be played. Please try again.', 'error'); }
        });
        slider.addEventListener('pointerdown', event => { dragging = true; slider.setPointerCapture(event.pointerId); });
        for (const name of ['pointerup', 'pointercancel']) slider.addEventListener(name, () => { dragging = false; update(); });
        slider.addEventListener('input', () => {
            if (Number.isFinite(audio.duration)) audio.currentTime = Number(slider.value);
        });
        for (const name of ['play', 'pause', 'timeupdate', 'loadedmetadata', 'ended']) audio.addEventListener(name, update);
        audio.addEventListener('error', () => feedback(byId('player-feedback'), 'The audio file could not be loaded. Please try again later.', 'error'));
        update();
    }

    function init() {
        document.addEventListener('click', event => {
            if (event.target === activeModal) { closeModal(); return; }
            const button = event.target.closest('[data-action]');
            if (button) {
                event.preventDefault();
                event.stopPropagation();
                if (!button.disabled && button.getAttribute('aria-disabled') !== 'true') {
                    handleAction(button).catch(showError);
                }
            }
            if (!event.target.closest('#user-dropdown')) byId('user-dropdown-menu')?.classList.add('hidden');
        });
        document.addEventListener('submit', event => {
            const form = event.target.closest('[data-form]');
            if (!form) return;
            event.preventDefault();
            if (form.checkValidity()) submitForm(form).catch(error => feedback(form, error.message, 'error', error));
        });
        document.addEventListener('keydown', event => {
            if (activeModal) {
                if (event.key === 'Escape') { event.preventDefault(); closeModal(); }
                if (event.key === 'Tab') {
                    const elements = [...activeModal.querySelectorAll('button, a[href], input, textarea, select, [tabindex="0"]')].filter(element => !element.disabled && !element.hidden && element.getClientRects().length);
                    const first = elements[0], last = elements[elements.length - 1];
                    if (!first) { event.preventDefault(); return; }
                    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
                }
                return;
            }
            if (event.key === 'Escape') byId('search-block')?.classList.add('hidden');
            if (event.key === '/' && !event.target.matches('input, textarea, select, [contenteditable="true"]')) {
                event.preventDefault(); byId('search-block')?.classList.remove('hidden'); byId('search-input')?.focus();
            }
            if (event.key === 'Enter' && event.target.id === 'search-input') {
                event.preventDefault();
                const url = new URL(window.location.href);
                if (!url.pathname.startsWith('/episodes/')) {
                    window.location.assign('/episodes/?search=' + encodeURIComponent(event.target.value));
                } else {
                    if (event.target.value.trim()) url.searchParams.set('search', event.target.value.trim()); else url.searchParams.delete('search');
                    changeFilters(url);
                }
            }
        });
        document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(pollTimer); else wakePoll(); });
        window.addEventListener('popstate', () => refreshAfterSuccess());
        document.querySelectorAll('[data-episode-card], [data-refresh-region="episode-state"]').forEach(card => {
            if (activeStatus(card.dataset.status)) trackedEpisodes.set(Number(card.dataset.id || card.dataset.episodeId), {title: card.dataset.name});
        });
        initPlayer(); syncButtons(); wakePoll();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
