const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {RequestError} = require('../../static/js/http.js');

const script = fs.readFileSync(path.join(__dirname, '../../static/js/app.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}

// Minimal DOM boundary: exercise the real event handlers and asynchronous requests.
class Element {
    constructor(dataset = {}) {
        this.dataset = dataset;
        this.children = [];
        this.disabled = false;
        this.hidden = false;
        this.isConnected = true;
        this.classList = {add() {}, remove() {}, toggle() {}};
    }
    append(child) { child.parentElement = this; this.children.push(child); }
    replaceChildren() { this.children = []; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    getAttribute(name) { return this[name] ?? null; }
    focus() {}
    matches(selector) { return selector === '[data-feedback]' && 'feedback' in this.dataset; }
    closest(selector) {
        if (selector === '[data-busy="true"]' && this.dataset.busy === 'true') return this;
        if (selector === '[data-notification]') return this.dataset.notification ? this : this.parentElement?.closest(selector) || null;
        return null;
    }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    remove() {
        this.parentElement?.children.splice(this.parentElement.children.indexOf(this), 1);
        this.isConnected = false;
    }
}

function notificationContent(page) {
    return page.notifications.children.at(-1)?._content;
}

function editingPage(request, polling = false, enableSSE = false) {
    const listeners = new Map();
    let parsedEpisode = null;
    let parsedCard = null;
    let parsedStatuses = null;
    const eventSources = [];
    class FakeEventSource {
        constructor(url) { this.url = url; this.closed = false; eventSources.push(this); }
        addEventListener(name, callback) { if (name === 'progress') this.listener = callback; }
        close() { this.closed = true; }
    }
    const form = new Element({form: 'edit-episode', id: '42'});
    const feedback = new Element({feedback: ''});
    const pageFeedback = new Element({feedback: ''});
    const notifications = new Element();
    const submit = new Element();
    const remove = new Element({action: 'delete-episode', id: '42'});
    const fields = ['title', 'author', 'description'].map(name => Object.assign(new Element(), {name, value: name === 'title' ? 'Unsaved "title"' : ''}));
    form.elements = Object.assign(fields, {
        namedItem: name => fields.find(field => field.name === name),
    });
    form.checkValidity = () => true;
    form.matches = selector => selector === 'form, [data-modal]';
    form.closest = selector => selector === '[data-form]' ? form : Element.prototype.closest.call(form, selector);
    form.querySelector = () => feedback;
    form.querySelectorAll = selector => selector === 'button[type="submit"]' ? [submit] : [...fields, submit];
    const oldTitle = new Element();
    const episodeState = new Element({status: polling ? 'DOWNLOADING' : 'NEW', episodeId: '42'});
    let replacements = 0;
    const timers = new Map();
    let timerId = 0;
    oldTitle.replaceWith = () => { replacements++; };
    const document = {
        readyState: 'loading', hidden: false, activeElement: null,
        body: {style: {}},
        getElementById: id => ({'edit-episode-form': form, 'page-feedback': pageFeedback, 'notification-stack': notifications})[id] || null,
        querySelector: selector => enableSSE && selector === '[data-episode-card], #episode-rows, #addEpisodeModal' ? new Element() :
            selector === '[data-refresh-region="episode-state"]' ? episodeState :
            selector === '[data-refresh-region="episode-title"]' ? oldTitle :
                polling && selector.startsWith('[data-refresh-region="overview"]') ? new Element() : null,
        querySelectorAll: selector => selector === '[data-action]' ? [remove] : selector === '[data-form]' ? [form] : [],
        addEventListener: (name, callback) => listeners.set(name, callback),
        createElement: () => new Element(),
    };
    const context = {
        window: {PodcastHTTP: {request, RequestError}, location: {href: 'http://localhost/episodes/42/'}, addEventListener() {},
            EventSource: enableSSE ? FakeEventSource : undefined, htmx: enableSSE ? {swap() {}, process() {}} : undefined},
        EventSource: FakeEventSource,
        document, URL, navigator: {},
        setTimeout: (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; },
        clearTimeout: id => timers.delete(id),
        DOMParser: class { parseFromString() { return {querySelectorAll: selector => selector === '[data-live-status]' ? parsedStatuses || [] : [new Element({refreshRegion: 'episode-title'})], querySelector: selector => selector === '[data-created-episode]' ? parsedEpisode : parsedCard}; } },
        FormData: class {
            constructor(target) { this.values = Object.fromEntries(target.elements.map(field => [field.name, field.value])); }
            get(name) { return this.values[name] ?? null; }
        },
    };
    vm.runInNewContext(script, context);
    listeners.get('DOMContentLoaded')();
    return {
        form, fields, submit, remove, feedback, notifications,
        document, window: context.window, navigator: context.navigator, location: context.window.location,
        eventSources,
        dispatch: (name, detail) => listeners.get(name)({detail}),
        setParsedEpisode: episode => { parsedEpisode = episode; },
        setParsedCard: card => { parsedCard = card; },
        sendProgress: (statuses, source = eventSources.at(-1)) => { parsedStatuses = statuses; source.listener({data: ''}); },
        click: button => listeners.get('click')({target: {closest: () => button}, preventDefault() {}, stopPropagation() {}}),
        get nextPollDelay() { return [...timers.values()].at(-1)?.delay; },
        get replacements() { return replacements; },
        send: () => listeners.get('submit')({target: form, preventDefault() {}}),
        poll: () => {
            const [id, entry] = [...timers.entries()].at(-1);
            timers.delete(id);
            return entry.callback();
        },
        visibility: hidden => { document.hidden = hidden; listeners.get('visibilitychange')(); },
        get timers() { return timers.size; },
        expireNotifications: () => {
            for (const [id, timer] of [...timers]) {
                if (timer.delay !== 10000) continue;
                timers.delete(id);
                timer.callback();
            }
        },
    };
}

test('episode creation keeps the panel open and shows metadata and cover status', () => {
    const page = editingPage(() => assert.fail('HTMX sends the request'));
    const ids = ['addEpisodeModal', 'episode-creation-progress', 'episode-creation-status',
        'episode-creation-details', 'episode-creation-title', 'episode-creation-description',
        'episode-creation-meta', 'episode-creation-link',
        'episode-creation-cover', 'episode-creation-submit', 'episode-creation-close',
        'episode-creation-cancel', 'episode-creation-spinner', 'episode-creation-download',
        'episode-creation-download-label', 'episode-creation-download-bar', 'episode-creation-start-download',
        'episode-creation-download-state'];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
    const originalGet = page.document.getElementById;
    page.document.getElementById = id => nodes[id] || originalGet(id);
    const form = Object.assign(new Element(), {id: 'add-episode-form'});
    page.dispatch('htmx:beforeRequest', {elt: form});
    assert.equal(nodes['episode-creation-status'].textContent, 'Getting information from source...');
    assert.equal(nodes['episode-creation-spinner'].hidden, false);
    assert.equal(nodes.addEpisodeModal.dataset.busy, 'true');
    assert.equal(nodes['episode-creation-submit'].hidden, true);
    assert.equal(nodes['episode-creation-cancel'].hidden, false);
    page.setParsedEpisode(new Element({id: '42', title: 'Episode title', author: 'Author',
        description: 'Description', length: '125', status: 'DOWNLOADING'}));
    page.dispatch('htmx:afterRequest', {elt: form, successful: true, xhr: {responseText: ''}});
    assert.equal(nodes.addEpisodeModal.hidden, false);
    assert.equal(nodes.addEpisodeModal.dataset.busy, 'false');
    assert.equal(nodes['episode-creation-submit'].hidden, true);
    assert.equal(nodes['episode-creation-close'].textContent, 'Close');
    assert.equal(nodes['episode-creation-link'].hidden, false);
    assert.equal(nodes['episode-creation-link'].href, '/episodes/42/');
    assert.equal(nodes['episode-creation-title'].textContent, 'Episode title');
    assert.equal(nodes['episode-creation-meta'].textContent, 'Author · 2:05');
    assert.equal(nodes['episode-creation-description'].textContent, 'Description');
    assert.equal(nodes['episode-creation-download-label'].textContent, 'Downloading');
    assert.equal(nodes['episode-creation-download'].hidden, false);
    assert.equal(nodes['episode-creation-start-download'].hidden, true);
    assert.equal(nodes['episode-creation-cover'].src, '/episodes/42/cover/');
    nodes['episode-creation-cover'].onload();
    assert.equal(nodes['episode-creation-status'].textContent, 'Episode information and cover preview loaded.');
    nodes['episode-creation-cover'].onerror();
    assert.equal(nodes['episode-creation-status'].textContent, 'Episode information loaded. Cover preview is unavailable.');
    page.dispatch('htmx:beforeRequest', {elt: form});
    assert.equal(nodes['episode-creation-link'].hidden, true);
    page.dispatch('htmx:afterRequest', {elt: form, successful: false});
    assert.equal(nodes.addEpisodeModal.dataset.busy, 'false');
    assert.equal(nodes['episode-creation-status'].textContent, 'Unable to create the episode. Check the URL and try again.');
    assert.equal(nodes['episode-creation-submit'].hidden, false);
    page.dispatch('htmx:beforeRequest', {elt: form});
    let aborted = false;
    page.window.htmx = {trigger: (_form, event) => { aborted = event === 'htmx:abort'; }};
    page.click(new Element({action: 'cancel-episode-creation'}));
    assert.equal(aborted, true);
    page.dispatch('htmx:afterRequest', {elt: form, successful: false});
    assert.match(nodes['episode-creation-status'].textContent, /may still be created/);
    assert.equal(nodes['episode-creation-cancel'].hidden, true);
});

test('episode creation loads details by id when the response has only a card', async () => {
    const requested = [];
    const page = editingPage(async url => {
        requested.push(url);
        if (url.endsWith('/download/')) return {id: 42, status: 'DOWNLOADING'};
        return {id: 42, title: 'Recovered title', description: 'Recovered description', status: 'NEW'};
    }, false, true);
    const ids = ['addEpisodeModal', 'episode-creation-progress', 'episode-creation-status',
        'episode-creation-details', 'episode-creation-title', 'episode-creation-description',
        'episode-creation-meta', 'episode-creation-link',
        'episode-creation-cover', 'episode-creation-submit', 'episode-creation-close',
        'episode-creation-cancel', 'episode-creation-spinner', 'episode-creation-download',
        'episode-creation-download-label', 'episode-creation-download-bar', 'episode-creation-start-download',
        'episode-creation-download-state'];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
    const originalGet = page.document.getElementById;
    page.document.getElementById = id => nodes[id] || originalGet(id);
    const form = Object.assign(new Element(), {id: 'add-episode-form'});
    page.setParsedCard(new Element({id: '42'}));
    page.dispatch('htmx:beforeRequest', {elt: form});
    await page.dispatch('htmx:afterRequest', {elt: form, successful: true, xhr: {responseText: ''}});
    assert.deepEqual(requested, ['/web-api/episodes/42/']);
    assert.equal(nodes['episode-creation-title'].textContent, 'Recovered title');
    assert.equal(nodes['episode-creation-description'].textContent, 'Recovered description');
    assert.equal(nodes['episode-creation-start-download'].hidden, false);
    assert.equal(nodes['episode-creation-download'].hidden, true);
    page.sendProgress([new Element({episodeId: '42', status: 'NEW'})]);
    assert.equal(nodes['episode-creation-start-download'].hidden, false);
    nodes['episode-creation-start-download'].dataset.action = 'start-created-episode-download';
    page.click(nodes['episode-creation-start-download']);
    await tick();
    const liveStatus = new Element({episodeId: '42', status: 'DOWNLOADING'});
    liveStatus.querySelector = () => ({textContent: 'Downloading audio 45%'});
    page.sendProgress([liveStatus]);
    assert.equal(nodes['episode-creation-start-download'].hidden, true);
    assert.equal(nodes['episode-creation-download'].hidden, false);
    assert.equal(nodes['episode-creation-download-label'].textContent, 'Downloading audio 45%');
    assert.equal(nodes['episode-creation-download-bar'].value, 45);
    page.sendProgress([new Element({episodeId: '42', status: 'PUBLISHED'})]);
    assert.equal(nodes['episode-creation-download'].hidden, true);
    assert.equal(nodes['episode-creation-start-download'].hidden, true);
    assert.equal(nodes['episode-creation-download-state'].textContent, 'Already downloaded');
    assert.deepEqual(requested, ['/web-api/episodes/42/', '/web-api/episodes/42/download/']);
    assert.equal(page.eventSources.at(-1).url, '/episodes/progress/events/?episode_id=42');
});

test('creation panel reconnects to SSE and ignores the previous connection', async () => {
    const page = editingPage(() => assert.fail('Panel progress must not poll'), false, true);
    page.document.querySelector = () => null;
    const ids = ['addEpisodeModal', 'episode-creation-progress', 'episode-creation-status',
        'episode-creation-details', 'episode-creation-title', 'episode-creation-description',
        'episode-creation-meta', 'episode-creation-link', 'episode-creation-cover',
        'episode-creation-submit', 'episode-creation-close', 'episode-creation-cancel',
        'episode-creation-spinner', 'episode-creation-download', 'episode-creation-download-label',
        'episode-creation-download-bar', 'episode-creation-start-download', 'sse-sink'];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
    const originalGet = page.document.getElementById;
    page.document.getElementById = id => nodes[id] || originalGet(id);
    const form = Object.assign(new Element(), {id: 'add-episode-form'});
    const oldSource = page.eventSources[0];
    page.setParsedEpisode(new Element({id: '42', title: 'Episode', status: 'DOWNLOADING'}));
    page.dispatch('htmx:beforeRequest', {elt: form});
    await page.dispatch('htmx:afterRequest', {elt: form, successful: true, xhr: {responseText: ''}});
    assert.equal(oldSource.closed, true);
    assert.equal(page.eventSources.at(-1).url, '/episodes/progress/events/?episode_id=42');
    const liveStatus = new Element({episodeId: '42', status: 'DOWNLOADING'});
    liveStatus.querySelector = () => ({textContent: 'Downloading audio 70%'});
    page.sendProgress([liveStatus]);
    const oldStatus = new Element({episodeId: '42', status: 'DOWNLOADING'});
    oldStatus.querySelector = () => ({textContent: 'Waiting for download'});
    page.sendProgress([oldStatus], oldSource);
    assert.equal(nodes['episode-creation-download-label'].textContent, 'Downloading audio 70%');
    assert.equal(nodes['episode-creation-download-bar'].value, 70);
    const completed = new Element({episodeId: '42', status: 'PUBLISHED'});
    page.sendProgress([completed]);
    assert.equal(nodes['episode-creation-download'].hidden, true);
    assert.equal(page.timers, 0);
});

for (const [action, modalId] of [['add-podcast', 'addPodcastModal'], ['add-episode', 'addEpisodeModal']]) {
    test(`${action} focuses the autofocus field before the close button`, async () => {
        const page = editingPage(() => assert.fail('Opening a panel must not send a request'));
        const modal = new Element();
        const field = new Element();
        const close = new Element();
        field.focus = () => { page.document.activeElement = field; };
        close.focus = () => { page.document.activeElement = close; };
        modal.querySelector = selector => selector === '[autofocus]' ? field : close;
        const nodes = {
            [modalId]: modal,
            'add-episode-form': new Element(),
            'episode-creation-progress': new Element(),
            'episodePodcastTitle': new Element(),
        };
        const originalGet = page.document.getElementById;
        page.document.getElementById = id => nodes[id] || originalGet(id);
        const button = new Element({action, id: '7', name: 'Podcast'});
        page.click(button);
        await tick();
        assert.equal(modal.hidden, false);
        assert.equal(page.document.activeElement, field);
    });
}

function rssPage() {
    const page = editingPage(() => assert.fail('Copying must not send a request'));
    const input = new Element();
    input.value = 'http://example.test/podcasts/42/rss.xml';
    input.focus = () => { page.document.activeElement = input; };
    input.select = () => { input.selectionStart = 0; input.selectionEnd = input.value.length; };
    const feedback = new Element({feedback: ''});
    const button = new Element({action: 'copy-rss'});
    button.focus = () => { page.document.activeElement = button; };
    page.document.activeElement = button;
    const getElementById = page.document.getElementById;
    page.document.getElementById = id => ({'podcast-rss-link': input, 'rss-feedback': feedback})[id] || getElementById(id);
    return {page, input, feedback, button};
}

test('RSS copy reports success only after the Clipboard API resolves', async () => {
    const {page, input, feedback, button} = rssPage();
    const copying = deferred();
    page.navigator.clipboard = {writeText: text => {
        assert.equal(text, input.value);
        return copying.promise;
    }};
    page.document.execCommand = () => assert.fail('The legacy fallback must not run');
    page.click(button);
    await tick();
    assert.equal(page.notifications.children.length, 0);
    copying.resolve();
    await tick();
    assert.equal(notificationContent(page).children[0].textContent, 'RSS link copied.');
});

for (const unavailable of ['missing', 'missing writeText', 'rejected']) {
    test(`RSS copy falls back when the Clipboard API is ${unavailable}`, async () => {
        const {page, input, feedback, button} = rssPage();
        if (unavailable === 'missing writeText') page.navigator.clipboard = {};
        if (unavailable === 'rejected') {
            page.navigator.clipboard = {writeText: () => Promise.reject(new Error('Permission denied'))};
        }
        let copied;
        page.document.execCommand = command => {
            assert.equal(command, 'copy');
            assert.equal(page.document.activeElement, input);
            copied = input.value.slice(input.selectionStart, input.selectionEnd);
            return true;
        };
        page.click(button);
        await tick();
        assert.equal(copied, input.value);
        assert.equal(notificationContent(page).children[0].textContent, 'RSS link copied.');
        assert.equal(page.document.activeElement, button);
    });
}

for (const failure of ['false', 'throws', 'missing']) {
    test(`RSS copy leaves the link selected for manual copying when fallback ${failure}`, async () => {
        const {page, input, feedback, button} = rssPage();
        if (failure !== 'missing') page.document.execCommand = () => {
            if (failure === 'throws') throw new Error('Copy denied');
            return false;
        };
        page.click(button);
        await tick();
        assert.equal(notificationContent(page).children[0].textContent, 'Could not copy the link. Select and copy it manually.');
        assert.equal(page.notifications.children.at(-1).className, 'ui-notification ui-feedback-error');
        assert.equal(page.document.activeElement, input);
        assert.equal(input.value.slice(input.selectionStart, input.selectionEnd), input.value);
    });
}

test('editing stays locked through fragment refresh and rejects repeated submission', async () => {
    const mutation = deferred(), fragment = deferred();
    const calls = [];
    const page = editingPage((url, options = {}) => {
        calls.push({url, options});
        return options.html ? fragment.promise : mutation.promise;
    });
    page.send();
    assert.equal(page.submit.disabled, true);
    assert.equal(page.remove.disabled, true);
    page.send();
    assert.equal(calls.length, 1);
    mutation.resolve({title: 'Saved "title"', author: 'Author', description: 'Description'});
    await tick();
    assert.equal(calls.length, 2);
    assert.equal(page.submit.disabled, true);
    assert.equal(page.remove.disabled, true);
    page.send();
    assert.equal(calls.length, 2);
    fragment.resolve('<div data-refresh-region="episode-title"></div>');
    await tick();
    assert.equal(page.replacements, 1);
    assert.equal(page.submit.disabled, false);
    assert.equal(page.remove.disabled, false);
    assert.equal(page.fields[0].value, 'Saved "title"');
});

test('failed editing keeps input and allows correction and retry', async () => {
    let attempts = 0;
    const page = editingPage(() => {
        attempts++;
        return Promise.reject(new RequestError('Title is invalid.', 422));
    });
    page.send();
    await tick();
    assert.equal(page.fields[0].value, 'Unsaved "title"');
    assert.equal(notificationContent(page).children[0].textContent, 'Title is invalid.');
    assert.equal(page.submit.disabled, false);
    assert.equal(page.remove.disabled, false);
    page.fields[0].value = 'Corrected title';
    page.send();
    await tick();
    assert.equal(attempts, 2);
    assert.equal(page.fields[0].value, 'Corrected title');
});

test('expired session preserves edits and disables further mutations with a login link', async () => {
    let attempts = 0;
    const page = editingPage(() => {
        attempts++;
        return Promise.reject(new RequestError('Session expired.', 401));
    });
    page.send();
    await tick();
    assert.equal(page.fields[0].value, 'Unsaved "title"');
    assert.equal(notificationContent(page).children[1].href, '/login');
    assert.equal(page.submit.disabled, true);
    assert.equal(page.remove.disabled, true);
    page.send();
    await tick();
    assert.equal(attempts, 1);
});

test('notifications stack, can be dismissed, and expire after ten seconds', async () => {
    const {page, button} = rssPage();
    page.document.execCommand = () => true;
    page.click(button);
    await tick();
    const notification = page.notifications.children[0];
    assert.equal(page.notifications.children.length, 1);
    const close = notification.children[1];
    page.click(close);
    assert.equal(page.notifications.children.length, 0);
    page.click(button);
    await tick();
    page.expireNotifications();
    assert.equal(page.notifications.children.length, 0);
});

test('a terminal SSE refresh deferred by editing is retried without losing edits', async () => {
    const fragment = deferred(), mutation = deferred();
    let fragments = 0;
    const page = editingPage((url, options = {}) => {
        if (options.method === 'PATCH') return mutation.promise;
        if (options.html) return ++fragments === 1 ? fragment.promise : Promise.resolve('<div data-refresh-region="episode-title"></div>');
        throw new Error(`Unexpected request: ${url}`);
    }, true, true);
    page.sendProgress([new Element({episodeId: '42', status: 'PUBLISHED'})]);
    const terminalPoll = page.poll();
    await tick();
    assert.equal(fragments, 1);
    page.send();
    fragment.resolve('<div data-refresh-region="episode-title"></div>');
    await terminalPoll;
    assert.equal(page.replacements, 0);
    mutation.reject(new RequestError('Try again.', 503));
    await tick();
    await page.poll();
    assert.equal(fragments, 2);
    assert.equal(page.replacements, 1);
    assert.equal(page.fields[0].value, 'Unsaved "title"');
});

test('progress is not polled on an active episode page', async () => {
    let progressRequests = 0;
    const page = editingPage(url => {
        if (url === '/web-api/progress/') progressRequests++;
        return Promise.resolve({progressItems: []});
    }, true, true);
    await tick();
    assert.equal(progressRequests, 0);
    assert.equal(page.timers, 0);
});

test('old fragments cannot overwrite the page after its filter URL changes', async () => {
    const fragment = deferred();
    let fragments = 0;
    const page = editingPage((url, options = {}) => {
        if (options.html) return ++fragments === 1 ? fragment.promise : Promise.resolve('<div data-refresh-region="episode-title"></div>');
        throw new Error(`Unexpected request: ${url}`);
    }, true, true);
    page.sendProgress([new Element({episodeId: '42', status: 'PUBLISHED'})]);
    const running = page.poll();
    await tick();
    page.location.href = 'http://localhost/episodes/42/?search=changed';
    fragment.resolve('<div data-refresh-region="episode-title"></div>');
    await running;
    assert.equal(page.replacements, 0);
    await page.poll();
    assert.equal(page.replacements, 1);
});

test('SSE updates the disabled player slider for each processing stage', () => {
    const page = editingPage(() => assert.fail('Progress must not be polled'), true, true);
    const slider = new Element();
    const label = new Element();
    const styles = new Map();
    slider.style = {setProperty: (name, value) => styles.set(name, value)};
    slider.removeAttribute = name => { delete slider[name]; };
    const getElementById = page.document.getElementById;
    page.document.getElementById = id => ({'progress-slider': slider, 'player-status': label})[id] || getElementById(id);

    const status = new Element({episodeId: '42', status: 'DOWNLOADING'});
    status.querySelector = () => ({textContent: 'Downloading audio 45%'});
    page.sendProgress([status]);
    assert.equal(slider.disabled, true);
    assert.equal(slider.max, '100');
    assert.equal(slider.value, '45');
    assert.equal(styles.get('--player-progress'), '45%');
    assert.equal(label.textContent, 'Downloading audio 45%');
    assert.equal(slider.getAttribute('aria-valuenow'), '45');

    status.querySelector = () => ({textContent: 'Processing audio'});
    page.sendProgress([status]);
    assert.equal(label.textContent, 'Processing audio');
    assert.equal(styles.get('--player-progress'), '0%');
    assert.equal(slider.getAttribute('aria-valuenow'), null);
    assert.equal(slider.getAttribute('aria-valuetext'), 'Processing audio');

    status.querySelector = () => ({textContent: 'Uploading audio 100%'});
    page.sendProgress([status]);
    assert.equal(slider.disabled, true);
    assert.equal(slider.value, '100');
    assert.equal(styles.get('--player-progress'), '100%');
    assert.equal(label.textContent, 'Uploading audio 100%');
});
