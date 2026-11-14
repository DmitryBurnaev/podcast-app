(function () {
    'use strict';
    const {request, RequestError} = window.PodcastHTTP;
    const operations = new Set();
    const finishingOperations = new Set();
    const rssTasks = new Map();
    const busyControls = new WeakMap();
    const notifications = new WeakMap();
    const notificationTimers = new WeakMap();
    const regions = new Set(['overview', 'podcasts', 'episodes', 'podcast-summary', 'episode-title',
        'episode-state', 'episode-metadata', 'episode-description', 'episode-player', 'app-statistics', 'breadcrumbs']);
    let activeModal = null;
    let modalOpener = null;
    let suspendedModal = null;
    let pendingClose = null;
    let confirmation = null;
    let refreshChain = Promise.resolve();
    let pollTimer = null;
    let pollRunning = false;
    let pollAgain = false;
    let pollDelay = 2000;
    let authExpired = false;
    let rssDiscovered = false;
    let refreshPending = false;
    let episodeCreationController = null;
    let episodeCreationCancelled = false;
    let episodeCreationSequence = 0;
    let progressSource = null;

    const byId = id => document.getElementById(id);
    const episodeForm = () => byId('edit-episode-form');
    const podcastForm = () => byId('edit-podcast-form');
    const activeStatus = status => ['DOWNLOADING', 'CANCELING'].includes(String(status).toUpperCase());
    const actionButtons = () => document.querySelectorAll('[data-action]');

    function detailEpisodeId() {
        const detail = document.querySelector('[data-refresh-region="episode-state"]');
        const id = Number(detail?.dataset.episodeId);
        return Number.isSafeInteger(id) && id > 0 ? id : null;
    }

    function shouldPoll() {
        return refreshPending || rssTasks.size > 0 ||
            (Boolean(podcastForm()?.dataset.id) && !rssDiscovered);
    }

    async function copyText(input) {
        if (navigator.clipboard?.writeText) {
            try {
                await navigator.clipboard.writeText(input.value);
                return;
            } catch {
                // Try selection-based copying if clipboard access is denied.
            }
        }
        const previousFocus = document.activeElement;
        input.focus({preventScroll: true});
        input.select();
        // Legacy fallback for browsers without the Clipboard API (e.g. over HTTP).
        // noinspection JSDeprecatedSymbols
        if (!document.execCommand('copy')) throw new Error('Copy failed');
        previousFocus?.focus({preventScroll: true});
    }

    function statusLabel(status) {
        return ({
            NEW: 'Pending',
            ERROR: 'Error',
            PUBLISHED: 'Published',
            ARCHIVED: 'Archived',
            DOWNLOADING: 'Downloading',
            CANCELING: 'Canceling',
            DL_PENDING: 'Waiting for download',
            DL_EPISODE_DOWNLOADING: 'Downloading audio',
            DL_EPISODE_POSTPROCESSING: 'Processing audio',
            DL_EPISODE_UPLOADING: 'Uploading audio',
            DL_COVER_DOWNLOADING: 'Downloading cover',
            DL_COVER_UPLOADING: 'Uploading cover'
        })[status] || 'Processing';
    }

    function setCreationStatus(message, loading = false) {
        const progress = byId('episode-creation-progress');
        if (!progress) return;
        progress.hidden = false;
        byId('episode-creation-status').textContent = message;
        const spinner = byId('episode-creation-spinner');
        if (spinner) spinner.hidden = !loading;
    }

    function setCreatedEpisodeDownload(status, label, percentage = null) {
        const download = byId('episode-creation-download');
        const button = byId('episode-creation-start-download');
        if (!download || !button) return;
        const normalized = String(status).toUpperCase();
        const active = activeStatus(normalized);
        const state = byId('episode-creation-download-state');
        if (state) {
            state.hidden = !['PUBLISHED', 'ARCHIVED'].includes(normalized);
            if (!state.hidden) state.textContent = normalized === 'PUBLISHED' ? 'Already downloaded' : 'Episode archived';
        }
        download.hidden = !active;
        button.hidden = !['NEW', 'ERROR'].includes(normalized);
        if (!active) return;
        byId('episode-creation-download-label').textContent = label || statusLabel(status);
        const bar = byId('episode-creation-download-bar');
        if (percentage === null || !Number.isFinite(percentage)) bar.removeAttribute('value');
        else bar.value = Math.max(0, Math.min(100, percentage));
    }

    function connectProgressStream(episodeId = null) {
        if (!window.EventSource) return;
        progressSource?.close();
        const url = '/episodes/progress/events/' + (episodeId ? `?episode_id=${episodeId}` : '');
        const source = new EventSource(url);
        progressSource = source;
        source.addEventListener('progress', event => {
            if (source !== progressSource) return;
            const fragment = new DOMParser().parseFromString(event.data, 'text/html');
            fragment.querySelectorAll('[data-live-status]').forEach(status => {
                const detail = document.querySelector('[data-refresh-region="episode-state"]');
                if (detail?.dataset.episodeId === status.dataset.episodeId) {
                    const wasActive = activeStatus(detail.dataset.status);
                    const active = activeStatus(status.dataset.status);
                    detail.dataset.status = status.dataset.status;
                    if (wasActive !== active) {
                        refreshPending = true;
                        wakePoll();
                    }
                    if (active) {
                        const label = status.querySelector('[data-progress-label]')?.textContent.trim() || statusLabel(status.dataset.status);
                        const match = label.match(/(\d+(?:\.\d+)?)%/);
                        const percentage = match ? Math.max(0, Math.min(100, Number(match[1]))) : null;
                        const playerLabel = byId('player-status');
                        const slider = byId('progress-slider');
                        if (playerLabel) playerLabel.textContent = label;
                        if (slider && playerLabel) {
                            slider.disabled = true;
                            slider.max = '100';
                            slider.value = String(percentage ?? 0);
                            slider.style.setProperty('--player-progress', `${percentage ?? 0}%`);
                            slider.setAttribute('aria-valuetext', label);
                            if (percentage === null) slider.removeAttribute('aria-valuenow');
                            else slider.setAttribute('aria-valuenow', String(percentage));
                        }
                    }
                }
                if (byId('episode-creation-progress')?.dataset.episodeId === status.dataset.episodeId) {
                    const label = activeStatus(status.dataset.status)
                        ? status.querySelector('[data-progress-label]')?.textContent.trim()
                        : statusLabel(status.dataset.status);
                    const match = label?.match(/(\d+(?:\.\d+)?)%/);
                    setCreatedEpisodeDownload(status.dataset.status, label, match ? Number(match[1]) : null);
                }
                document.querySelectorAll('[data-episode-card]').forEach(card => {
                    if (card.dataset.id === status.dataset.episodeId) card.dataset.status = status.dataset.status;
                });
            });
            window.htmx.swap(byId('sse-sink'), event.data, {swapStyle: 'innerHTML'});
            syncButtons();
        });
    }

    function setCreationActions(state) {
        const submit = byId('episode-creation-submit');
        const close = byId('episode-creation-close');
        const cancel = byId('episode-creation-cancel');
        if (!submit || !close || !cancel) return;
        submit.hidden = state !== 'ready';
        cancel.hidden = state !== 'loading';
        close.hidden = state === 'loading';
        close.textContent = state === 'complete' ? 'Close' : 'Cancel';
    }

    function beginEpisodeCreation() {
        const progress = byId('episode-creation-progress');
        if (!progress) return;
        progress.dataset.episodeId = '';
        episodeCreationSequence++;
        episodeCreationCancelled = false;
        setCreationActions('loading');
        byId('episode-creation-details').hidden = true;
        const download = byId('episode-creation-download');
        if (download) download.hidden = true;
        const downloadState = byId('episode-creation-download-state');
        if (downloadState) downloadState.hidden = true;
        const start = byId('episode-creation-start-download');
        if (start) start.hidden = true;
        const link = byId('episode-creation-link');
        if (link) link.hidden = true;
        setCreationStatus('Getting information from source...', true);
    }

    function showCreatedEpisode(episode) {
        const progress = byId('episode-creation-progress');
        if (!progress) return;
        const id = Number(episode.id);
        if (!Number.isSafeInteger(id) || id < 1) return;
        progress.dataset.episodeId = String(id);
        setCreationActions('complete');
        byId('episode-creation-title').textContent = episode.title || '';
        byId('episode-creation-description').textContent = episode.description || '';
        const seconds = Math.max(0, Number(episode.length) || 0);
        const duration = seconds ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : '';
        byId('episode-creation-meta').textContent = [episode.author, duration].filter(Boolean).join(' · ');
        const link = byId('episode-creation-link');
        link.href = `/episodes/${id}/`;
        link.hidden = false;
        byId('episode-creation-details').hidden = false;
        setCreatedEpisodeDownload(episode.status, statusLabel(episode.status));
        connectProgressStream(id);
        setCreationStatus('Episode created. Loading cover preview…', true);
        const cover = byId('episode-creation-cover');
        cover.alt = `Cover for ${episode.title || 'episode'}`;
        cover.onload = () => {
            if (progress.dataset.episodeId === String(id)) setCreationStatus('Episode information and cover preview loaded.');
        };
        cover.onerror = () => {
            if (progress.dataset.episodeId === String(id)) {
                setCreationStatus('Episode information loaded. Cover preview is unavailable.');
                cover.onload = null;
                cover.onerror = null;
                cover.alt = 'Cover unavailable';
                cover.src = '/static/img/episode-default.jpg';
            }
        };
        cover.src = `/episodes/${id}/cover/`;
    }

    function dismissNotification(notification) {
        if (!notification) return;
        clearTimeout(notificationTimers.get(notification));
        notificationTimers.delete(notification);
        notification.remove();
    }

    function notificationContent(notification) {
        return notification?._content || null;
    }

    function appendNotificationControl(notification, control) {
        notificationContent(notification)?.append(control);
    }

    function feedback(scope, text, kind = 'success', error = null, source = 'operation') {
        if (!scope) return;
        const box = scope.matches('[data-feedback]') ? scope : scope.querySelector('[data-operation-feedback], [data-feedback]:not(#rss-feedback)');
        if (!box) return;
        const existing = notifications.get(box);
        dismissNotification(existing);
        box.replaceChildren();
        box.hidden = true;
        box.dataset.source = source;
        if (!text) return null;
        const stack = byId('notification-stack');
        if (!stack) return null;
        const notification = document.createElement('article');
        notification.className = `ui-notification ui-feedback-${kind}`;
        notification.dataset.notification = 'true';
        notification.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        const content = document.createElement('div');
        content.className = 'ui-notification-content';
        notification._content = content;
        notification.append(content);
        if (kind === 'pending') {
            const spinner = document.createElement('span');
            spinner.className = 'ui-spinner';
            spinner.setAttribute('aria-hidden', 'true');
            content.append(spinner);
        }
        const message = document.createElement('span');
        message.className = 'ui-notification-message';
        message.textContent = text;
        content.append(message);
        const close = document.createElement('button');
        close.type = 'button';
        close.dataset.action = 'dismiss-notification';
        close.className = 'ui-notification-close';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.textContent = '×';
        notification.append(close);
        stack.append(notification);
        notifications.set(box, notification);
        notificationTimers.set(notification, setTimeout(() => dismissNotification(notification), 10000));
        if (error?.status === 401) {
            authExpired = true;
            const login = document.createElement('a');
            login.href = '/login';
            login.textContent = 'Sign in';
            login.className = 'ui-feedback-link';
            appendNotificationControl(notification, login);
            syncButtons();
        }
        return notification;
    }

    function pageMessage(text, error = null, source = 'operation', retry = false) {
        const box = byId('page-feedback');
        if (!box) return;
        const notification = feedback(box, text, 'error', error, source);
        if (retry && error?.status !== 401) {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.action = 'refresh-page';
            button.textContent = 'Refresh data';
            button.className = 'ui-feedback-link';
            appendNotificationControl(notification, button);
        }
    }

    function setBusy(scope, busy) {
        scope.dataset.busy = String(busy);
        scope.setAttribute('aria-busy', String(busy));
        scope.querySelectorAll('button, input, textarea, select').forEach(control => {
            if (control.dataset.cancelCreation !== undefined) return;
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
        const busy = key => operations.has(key) || finishingOperations.has(key);
        const requestBusy = operations.size > 0 || finishingOperations.size > 0;
        actionButtons().forEach(button => {
            if (button.closest('[data-busy="true"]')) return;
            const action = button.dataset.action;
            const id = Number(button.dataset.id);
            const episodeBusy = busy(`episode:${id}`);
            const podcastBusy = busy(`podcast:${id}`);
            if (['download-episode', 'cancel-episode', 'delete-episode'].includes(action)) {
                const card = button.closest('[data-episode-card]');
                const status = card?.dataset.status || detail?.dataset.status || '';
                const active = activeStatus(status);
                button.disabled = authExpired || episodeBusy || (action === 'cancel-episode' ? status !== 'DOWNLOADING' : active);
                if (action === 'cancel-episode') button.hidden = !active;
                if (action === 'download-episode') button.hidden = active || ['PUBLISHED', 'ARCHIVED'].includes(status);
            }
            if (action === 'generate-rss') button.disabled = authExpired || podcastBusy || rssTasks.has(id);
            if (['add-episode', 'delete-podcast'].includes(action)) button.disabled = authExpired || podcastBusy;
            if (action === 'add-podcast') button.disabled = authExpired || busy('create-podcast');
            if (['add-podcast', 'add-episode', 'delete-podcast', 'delete-episode', 'download-episode',
                'cancel-episode', 'generate-rss'].includes(action)) button.disabled ||= requestBusy;
        });
        document.querySelectorAll('[data-form]').forEach(form => {
            if (form.closest('[data-busy="true"]')) return;
            form.querySelectorAll('button[type="submit"]').forEach(button => {
                button.disabled = authExpired || requestBusy;
            });
        });
    }

    function openModal(id, opener = document.activeElement) {
        if (pendingClose) closeModal(true, true);
        const modal = byId(id);
        if (!modal || activeModal?.dataset.busy === 'true') return;
        if (activeModal && id === 'actionModal' && activeModal.dataset.responsiveEdit !== undefined) {
            suspendedModal = {modal: activeModal, opener: modalOpener};
            activeModal.inert = true;
            activeModal = null;
        } else if (activeModal) closeModal(true, true);
        modalOpener = {element: opener, action: opener?.dataset?.action, id: opener?.dataset?.id};
        activeModal = modal;
        modal.hidden = false;
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        if (modal.dataset.responsiveEdit !== undefined) {
            modal.classList.add('is-open');
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
        }
        document.body.style.overflow = 'hidden';
        const focus = modal.querySelector('[autofocus]') ||
            (modal.dataset.responsiveEdit !== undefined && modal.querySelector('input:not([readonly]), textarea')) ||
            modal.querySelector('input, textarea, button');
        focus?.focus();
    }

    function closeModal(force = false, immediate = false) {
        if (!activeModal || (!force && activeModal.dataset.busy === 'true')) return;
        const modal = activeModal;
        if (pendingClose) {
            if (!immediate) return;
            clearTimeout(pendingClose);
            pendingClose = null;
        }
        const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        const isDrawer = modal.dataset.drawer !== undefined ||
            (modal.dataset.responsiveEdit !== undefined && window.matchMedia?.('(max-width: 767px)').matches);
        if (!immediate && isDrawer && !reducedMotion) {
            modal.classList.add('is-closing');
            pendingClose = setTimeout(() => {
                pendingClose = null;
                closeModal(true, true);
            }, 125);
            return;
        }
        modal.classList.remove('is-closing');
        modal.hidden = true;
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        activeModal = null;
        confirmation = null;
        document.body.style.overflow = '';
        modal.querySelector('form')?.reset();
        feedback(modal, '');
        if (modal.dataset.responsiveEdit !== undefined) {
            modal.hidden = false;
            modal.classList.remove('hidden', 'is-open');
            modal.removeAttribute('role');
            modal.removeAttribute('aria-modal');
        }
        const opener = modalOpener;
        modalOpener = null;
        if (modal.id === 'actionModal' && suspendedModal) {
            activeModal = suspendedModal.modal;
            activeModal.inert = false;
            modalOpener = suspendedModal.opener;
            suspendedModal = null;
            document.body.style.overflow = 'hidden';
        }
        const target = opener?.element?.isConnected ? opener.element :
            [...actionButtons()].find(button => button.dataset.action === opener?.action && button.dataset.id === opener?.id);
        target?.focus({preventScroll: true});
    }

    function showError(error) {
        const modal = byId('actionModal');
        if (activeModal) {
            return feedback(activeModal, error.message, 'error', error);
        }
        byId('action-modal-title').textContent = 'Unable to complete action';
        byId('action-modal-description').textContent = '';
        byId('action-modal-confirm').hidden = true;
        openModal('actionModal');
        return feedback(modal, error.message, 'error', error);
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

    async function refreshPage(url = new URL(window.location.href), allowFilters = false) {
        const location = new URL(url);
        location.searchParams.set('fragment', '1');
        const perform = async () => {
            const html = await request(location.pathname + location.search, {html: true});
            const parsed = new DOMParser().parseFromString(html, 'text/html');
            const incoming = [...parsed.querySelectorAll('[data-refresh-region]')];
            if (!incoming.length) throw new RequestError('The server returned an unexpected page response.');
            if (operations.size && !(allowFilters && operations.size === 1 && operations.has('filters'))) return false;
            const current = new URL(window.location.href);
            current.searchParams.delete('fragment');
            const requested = new URL(location);
            requested.searchParams.delete('fragment');
            if (!allowFilters && requested.pathname + requested.search !== current.pathname + current.search) return false;
            const scroll = document.querySelector('main')?.scrollTop;
            const focused = document.activeElement;
            const focusKey = focused?.dataset?.action;
            const focusId = focused?.dataset?.id;
            incoming.forEach(node => {
                const name = node.dataset.refreshRegion;
                if (!regions.has(name)) return;
                const previous = document.querySelector(`[data-refresh-region="${name}"]`);
                if (!previous) return;
                if (name === 'episode-player' && previous.dataset.sourceUrl === node.dataset.sourceUrl && previous.dataset.status === node.dataset.status) return;
                node.querySelectorAll('script').forEach(script => script.remove());
                previous.replaceWith(node);
                window.htmx?.process(node);
            });
            if (scroll !== undefined) document.querySelector('main').scrollTop = scroll;
            if (focusKey && !focused.isConnected) {
                [...actionButtons()].find(button => button.dataset.action === focusKey && button.dataset.id === focusId)?.focus({preventScroll: true});
            }
            initPlayer();
            syncFilterForm();
            syncButtons();
            return true;
        };
        refreshChain = refreshChain.catch(() => {
        }).then(perform);
        return refreshChain;
    }

    async function refreshAfterSuccess() {
        try {
            refreshPending = !(await refreshPage());
            wakePoll();
            return true;
        } catch (error) {
            refreshPending = true;
            pageMessage('The operation succeeded, but the page data could not be refreshed. ' + error.message, error, 'operation', true);
            wakePoll();
            return false;
        }
    }

    async function mutate(key, scope, label, send, success) {
        if (operations.size || finishingOperations.size || authExpired) return;
        operations.add(key);
        setBusy(scope, true);
        feedback(scope, label, 'pending');
        let data;
        let failure;
        try {
            data = await send();
        } catch (error) {
            failure = error;
        }
        operations.delete(key);
        if (failure) {
            setBusy(scope, false);
            if (failure.cancelled) {
                setCreationActions('ready');
                setCreationStatus(failure.message);
                wakePoll();
                return;
            }
            if (key.startsWith('podcast:') && scope.id === 'addEpisodeModal') setCreationActions('ready');
            let notification;
            if (scope.matches('form, [data-modal]')) notification = feedback(scope, failure.message, 'error', failure);
            else {
                feedback(scope, '');
                notification = showError(failure);
            }
            if (failure.details?.episode_id) {
                const link = document.createElement('a');
                link.href = `/episodes/${Number(failure.details.episode_id)}/`;
                link.textContent = 'Open the created episode';
                link.className = 'ui-feedback-link';
                appendNotificationControl(notification, link);
                await refreshAfterSuccess();
            }
            wakePoll();
            return;
        }
        finishingOperations.add(key);
        syncButtons();
        try {
            await success(data);
        } finally {
            finishingOperations.delete(key);
            setBusy(scope, false);
            wakePoll();
        }
    }

    function savedForm(form, values) {
        for (const [key, value] of Object.entries(values)) {
            const field = form.elements.namedItem(key);
            if (!field || field.readOnly) continue;
            if (field.type === 'checkbox') field.defaultChecked = field.checked = Boolean(value);
            else field.defaultValue = field.value = value ?? '';
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
                    () => request('/web-api/podcasts/', {
                        method: 'POST',
                        data: {
                            name: String(data.get('name')).trim(),
                            description: data.get('description'),
                            download_automatically: data.has('download_automatically')
                        }
                    }),
                    async () => {
                        closeModal(true);
                        await refreshAfterSuccess();
                    });
                break;
            case 'create-episode':
                beginEpisodeCreation();
                episodeCreationController = new AbortController();
                await mutate(`podcast:${id}`, scope, 'Getting episode information…',
                    async () => {
                        try {
                            return await request(`/web-api/podcasts/${id}/episodes/`, {
                                method: 'POST',
                                signal: episodeCreationController.signal,
                                data: {sourceURL: String(data.get('sourceURL')).trim()}
                            });
                        } catch (error) {
                            setCreationStatus(error.message || 'Unable to create the episode.');
                            throw error;
                        }
                    },
                    async episode => {
                        showCreatedEpisode(episode);
                        await refreshAfterSuccess();
                    });
                episodeCreationController = null;
                break;
            case 'edit-podcast':
                await mutate(`podcast:${id}`, activeModal === scope ? scope : form, 'Saving podcast…',
                    () => request(`/web-api/podcasts/${id}/`, {
                        method: 'PATCH',
                        data: {
                            name: String(data.get('name')).trim(),
                            description: data.get('description'),
                            download_automatically: data.has('download_automatically')
                        }
                    }),
                    async podcast => {
                        savedForm(form, podcast);
                        await refreshAfterSuccess();
                        if (activeModal === scope) closeModal(true);
                    });
                break;
            case 'edit-episode':
                await mutate(`episode:${id}`, activeModal === scope ? scope : form, 'Saving episode…',
                    () => request(`/web-api/episodes/${id}/`, {
                        method: 'PATCH',
                        data: {
                            title: String(data.get('title')).trim(),
                            author: data.get('author'),
                            description: data.get('description')
                        }
                    }),
                    async episode => {
                        savedForm(form, episode);
                        await refreshAfterSuccess();
                        if (activeModal === scope) closeModal(true);
                    });
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
        if (operations.size || finishingOperations.size || authExpired) return;
        operations.add('filters');
        setBusy(scope, true);
        feedback(scope, 'Updating episodes…', 'pending');
        try {
            await refreshPage(url, true);
            window.history.pushState({}, '', url.pathname + url.search);
            syncFilterForm();
            const search = byId('search-input');
            if (search) search.value = url.searchParams.get('search') || '';
            feedback(scope, '');
            if (activeModal === scope) closeModal(true);
        } catch (error) {
            feedback(scope, error.message, 'error', error);
        } finally {
            operations.delete('filters');
            setBusy(scope, false);
        }
    }

    function syncFilterForm() {
        const form = document.querySelector('[data-form="filters"]');
        if (!form || activeModal?.contains(form)) return;
        const params = new URL(window.location.href).searchParams;
        for (const field of form.elements) {
            if (!field.name) continue;
            if (field.name === 'status') field.checked = (params.get('status') || '').split(',').includes(field.value);
            else if (['search', 'podcast', 'size_min', 'size_max'].includes(field.name)) field.value = params.get(field.name) || '';
            if (field.type === 'checkbox') field.defaultChecked = field.checked;
            else field.defaultValue = field.value;
        }
    }

    async function handleAction(button) {
        const id = Number(button.dataset.id);
        const scope = button.closest('[data-episode-card]') || episodeForm() || podcastForm() || byId('page-feedback');
        switch (button.dataset.action) {
            case 'open-detail-edit':
                openModal('detailEditModal', button);
                break;
            case 'dismiss-notification':
                dismissNotification(button.closest('[data-notification]'));
                break;
            case 'edit-episode':
                window.location.assign(`/episodes/${id}/`);
                break;
            case 'add-podcast':
                openModal('addPodcastModal', button);
                break;
            case 'add-episode': {
                const form = byId('add-episode-form');
                episodeCreationSequence++;
                byId('episode-creation-progress').hidden = true;
                byId('episode-creation-progress').dataset.episodeId = '';
                for (const id of ['episode-creation-details', 'episode-creation-download', 'episode-creation-download-state', 'episode-creation-start-download', 'episode-creation-link']) {
                    const node = byId(id);
                    if (node) node.hidden = true;
                }
                setCreationActions('ready');
                form.dataset.id = String(id);
                if (byId('episode-rows')) {
                    form.setAttribute('hx-post', `/podcasts/${id}/episodes/create/`);
                    form.setAttribute('hx-target', '#episode-rows');
                    form.setAttribute('hx-swap', 'afterbegin');
                    form.setAttribute('hx-disabled-elt', 'find button[type=submit]');
                    window.htmx.process(form);
                }
                byId('episodePodcastTitle').textContent = button.dataset.name;
                openModal('addEpisodeModal', button);
                break;
            }
            case 'close-modal':
                closeModal();
                break;
            case 'cancel-episode-creation':
                episodeCreationCancelled = true;
                if (episodeCreationController) episodeCreationController.abort();
                else window.htmx.trigger(byId('add-episode-form'), 'htmx:abort');
                break;
            case 'start-created-episode-download': {
                const episodeId = Number(byId('episode-creation-progress')?.dataset.episodeId);
                if (!Number.isSafeInteger(episodeId) || episodeId < 1) break;
                const panel = byId('addEpisodeModal');
                await mutate(`episode:${episodeId}`, panel, 'Starting download…',
                    () => request(`/web-api/episodes/${episodeId}/download/`, {method: 'PUT'}),
                    async episode => {
                        if (byId('episode-creation-progress')?.dataset.episodeId !== String(episodeId)) return;
                        setCreatedEpisodeDownload(episode.status, statusLabel(episode.status));
                        connectProgressStream(episodeId);
                    });
                break;
            }
            case 'open-filters':
                syncFilterForm();
                openModal('filtersModal', button);
                break;
            case 'delete-podcast':
                confirmDelete('podcast', id, button);
                break;
            case 'delete-episode':
                confirmDelete('episode', id, button);
                break;
            case 'confirm-delete': {
                const {kind, id: confirmedId} = confirmation || {};
                if (!kind) return;
                await mutate(`${kind}:${confirmedId}`, activeModal, `Deleting ${kind}…`,
                    () => request(`/web-api/${kind === 'podcast' ? 'podcasts' : 'episodes'}/${confirmedId}/`, {method: 'DELETE'}),
                    async () => {
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
                        feedback(scope, cancel ? 'Cancellation requested.' : 'Download started.');
                        const card = button.closest('[data-episode-card]');
                        if (card) card.dataset.status = episode.status;
                        await refreshAfterSuccess();
                    });
                break;
            }
            case 'generate-rss': {
                const dialog = byId('rssConfirmModal');
                dialog.dataset.id = String(id);
                feedback(dialog, '');
                dialog.showModal();
                break;
            }
            case 'cancel-rss-confirm':
                if (byId('rssConfirmModal').dataset.busy !== 'true') byId('rssConfirmModal').close();
                break;
            case 'confirm-rss': {
                const dialog = byId('rssConfirmModal');
                const podcastId = Number(dialog.dataset.id);
                if (!Number.isSafeInteger(podcastId) || podcastId < 1) break;
                await mutate(`podcast:${podcastId}`, dialog, 'Starting RSS generation…',
                    () => request(`/web-api/podcasts/${podcastId}/generate-rss/`, {method: 'PUT'}),
                    async task => {
                        rssTasks.set(podcastId, task.job_id);
                        dialog.close();
                        feedback(byId('rss-feedback'), 'Generating RSS…', 'pending');
                        syncButtons();
                        wakePoll();
                    });
                break;
            }
            case 'copy-rss':
                try {
                    await copyText(byId('podcast-rss-link'));
                    feedback(byId('rss-feedback'), 'RSS link copied.');
                } catch {
                    feedback(byId('rss-feedback'), 'Could not copy the link. Select and copy it manually.', 'error');
                }
                break;
            case 'toggle-search': {
                const search = byId('search-block');
                if (!search) return;
                search.classList.toggle('hidden');
                if (!search.classList.contains('hidden')) byId('search-input')?.focus();
                break;
            }
            case 'toggle-user-menu':
                byId('user-dropdown-menu')?.classList.toggle('hidden');
                break;
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

    async function poll() {
        if (pollRunning || document.hidden || authExpired || !shouldPoll()) return;
        pollRunning = true;
        try {
            if (operations.size) return;
            let refresh = refreshPending;
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
            if (refresh) {
                refreshPending = true;
                refreshPending = !(await refreshPage());
            }
            syncButtons();
            pollDelay = 2000;
            if (byId('page-feedback')?.dataset.source === 'poll') feedback(byId('page-feedback'), '');
        } catch (error) {
            pageMessage(error.status === 401 ? error.message : 'Live updates are temporarily unavailable. Retrying… ' + error.message, error, 'poll');
            pollDelay = Math.min(30000, pollDelay * 2);
        } finally {
            pollRunning = false;
            if (!document.hidden && !authExpired && shouldPoll()) {
                pollTimer = setTimeout(poll, pollAgain ? 0 : pollDelay);
                pollAgain = false;
            }
        }
    }

    function wakePoll() {
        if (!shouldPoll()) {
            clearTimeout(pollTimer);
            pollAgain = false;
            return;
        }
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
            slider.style.setProperty('--player-progress', `${audio.duration > 0 ? Math.max(0, Math.min(100, Number(slider.value) / audio.duration * 100)) : 0}%`);
            byId('play-icon').classList.toggle('hidden', !audio.paused);
            byId('pause-icon').classList.toggle('hidden', audio.paused);
        };
        play.addEventListener('click', async () => {
            try {
                if (audio.paused) await audio.play(); else audio.pause();
                feedback(byId('player-feedback'), '');
            } catch {
                feedback(byId('player-feedback'), 'Audio could not be played. Please try again.', 'error');
            }
        });
        slider.addEventListener('pointerdown', event => {
            dragging = true;
            slider.setPointerCapture(event.pointerId);
        });
        for (const name of ['pointerup', 'pointercancel']) slider.addEventListener(name, () => {
            dragging = false;
            update();
        });
        slider.addEventListener('input', () => {
            if (Number.isFinite(audio.duration)) audio.currentTime = Number(slider.value);
        });
        for (const name of ['play', 'pause', 'timeupdate', 'loadedmetadata', 'ended']) audio.addEventListener(name, update);
        audio.addEventListener('error', () => feedback(byId('player-feedback'), 'The audio file could not be loaded. Please try again later.', 'error'));
        update();
    }

    function init() {
        byId('rssConfirmModal')?.addEventListener('cancel', event => {
            if (event.currentTarget.dataset.busy === 'true') event.preventDefault();
        });
        document.addEventListener('htmx:afterSwap', syncButtons);
        document.addEventListener('htmx:beforeRequest', event => {
            if (event.detail.elt.id !== 'add-episode-form') return;
            beginEpisodeCreation();
            setBusy(byId('addEpisodeModal'), true);
        });
        document.addEventListener('htmx:beforeSwap', event => {
            if (event.detail.elt.id !== 'add-episode-form' || !event.detail.shouldSwap) return;
            const incoming = new DOMParser().parseFromString(event.detail.serverResponse, 'text/html');
            const id = incoming.querySelector('[data-episode-card]')?.dataset.id;
            if (id) document.querySelectorAll('[data-episode-card]').forEach(card => {
                if (card.dataset.id === id) card.remove();
            });
        });
        document.addEventListener('htmx:afterRequest', async event => {
            const form = event.detail.elt;
            if (form.id !== 'add-episode-form') return;
            setBusy(byId('addEpisodeModal'), false);
            if (episodeCreationCancelled) {
                setCreationActions('ready');
                setCreationStatus('Request cancelled. The episode may still be created; check the list before retrying.');
                return;
            }
            if (event.detail.successful) {
                byId('empty-episodes')?.remove();
                const response = new DOMParser().parseFromString(event.detail.xhr.responseText, 'text/html');
                const episode = response.querySelector('[data-created-episode]');
                if (episode) showCreatedEpisode(episode.dataset);
                else {
                    const card = response.querySelector('[data-episode-card]');
                    const id = Number(card?.dataset.id);
                    const sequence = episodeCreationSequence;
                    setCreationActions('complete');
                    setCreationStatus('Getting episode details…', true);
                    try {
                        if (!Number.isSafeInteger(id) || id < 1) throw new Error('Missing episode id');
                        const details = await request(`/web-api/episodes/${id}/`);
                        if (sequence !== episodeCreationSequence) return;
                        showCreatedEpisode(details);
                    } catch {
                        if (sequence !== episodeCreationSequence) return;
                        setCreationActions('complete');
                        setCreationStatus('Episode created, but its details could not be displayed.');
                    }
                }
                syncButtons();
            } else {
                setCreationActions('ready');
                setCreationStatus('Unable to create the episode. Check the URL and try again.');
                feedback(form, 'Unable to create the episode. Check the URL and try again.', 'error');
            }
        });
        if (window.EventSource && (detailEpisodeId() || document.querySelector('[data-episode-card], #episode-rows, #addEpisodeModal'))) connectProgressStream(detailEpisodeId());
        document.addEventListener('click', event => {
            if (event.target === activeModal) {
                closeModal();
                return;
            }
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
            if (form.getAttribute('hx-post')) return;
            event.preventDefault();
            if (form.checkValidity()) submitForm(form).catch(error => feedback(form, error.message, 'error', error));
        });
        document.addEventListener('keydown', event => {
            if (byId('rssConfirmModal')?.open) {
                if (event.key === 'Escape' && byId('rssConfirmModal').dataset.busy === 'true') event.preventDefault();
                return;
            }
            if (activeModal) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    closeModal();
                }
                if (event.key === 'Tab') {
                    const elements = [...activeModal.querySelectorAll('button, a[href], input, textarea, select, [tabindex="0"]')].filter(element => !element.disabled && !element.hidden && element.getClientRects().length);
                    const first = elements[0], last = elements[elements.length - 1];
                    if (!first) {
                        event.preventDefault();
                        return;
                    }
                    if (event.shiftKey && document.activeElement === first) {
                        event.preventDefault();
                        last.focus();
                    } else if (!event.shiftKey && document.activeElement === last) {
                        event.preventDefault();
                        first.focus();
                    }
                }
                return;
            }
            if (event.key === 'Escape') byId('search-block')?.classList.add('hidden');
            if (event.key === '/' && !event.target.matches('input, textarea, select, [contenteditable="true"]')) {
                event.preventDefault();
                byId('search-block')?.classList.remove('hidden');
                byId('search-input')?.focus();
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
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) clearTimeout(pollTimer); else wakePoll();
        });
        window.addEventListener('popstate', () => refreshAfterSuccess());
        initPlayer();
        syncFilterForm();
        syncButtons();
        wakePoll();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
