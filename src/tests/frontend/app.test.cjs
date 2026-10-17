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

function editingPage(request, polling = false) {
    const listeners = new Map();
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
        querySelector: selector => selector === '[data-refresh-region="episode-state"]' ? episodeState :
            selector === '[data-refresh-region="episode-title"]' ? oldTitle :
                polling && selector.startsWith('[data-refresh-region="overview"]') ? new Element() : null,
        querySelectorAll: selector => selector === '[data-action]' ? [remove] : selector === '[data-form]' ? [form] : [],
        addEventListener: (name, callback) => listeners.set(name, callback),
        createElement: () => new Element(),
    };
    const context = {
        window: {PodcastHTTP: {request, RequestError}, location: {href: 'http://localhost/episodes/42/'}, addEventListener() {}},
        document, URL, navigator: {},
        setTimeout: (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; },
        clearTimeout: id => timers.delete(id),
        DOMParser: class { parseFromString() { return {querySelectorAll: () => [new Element({refreshRegion: 'episode-title'})]}; } },
        FormData: class {
            constructor(target) { this.values = Object.fromEntries(target.elements.map(field => [field.name, field.value])); }
            get(name) { return this.values[name] ?? null; }
        },
    };
    vm.runInNewContext(script, context);
    listeners.get('DOMContentLoaded')();
    return {
        form, fields, submit, remove, feedback, notifications,
        document, navigator: context.navigator, location: context.window.location,
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

test('a terminal download refresh deferred by editing is retried on the next poll', async () => {
    const fragment = deferred(), mutation = deferred();
    let polls = 0, fragments = 0;
    const page = editingPage((url, options = {}) => {
        if (options.method === 'PATCH') return mutation.promise;
        if (options.html) return ++fragments === 1 ? fragment.promise : Promise.resolve('<div data-refresh-region="episode-title"></div>');
        if (url === '/web-api/progress/') {
            const progressItems = polls++ === 0 ? [{episode: {id: 42, status: 'DOWNLOADING', title: 'Episode'}, completed: 0, total_file_size: 0}] : [];
            return Promise.resolve({progressItems});
        }
        return Promise.resolve({id: 42, status: 'PUBLISHED', title: 'Episode'});
    }, true);
    await page.poll();
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

test('progress is not requested from lists or idle episode pages', async () => {
    let progressRequests = 0;
    const page = editingPage(url => {
        if (url === '/web-api/progress/') progressRequests++;
        return Promise.resolve({progressItems: []});
    });
    await tick();
    assert.equal(progressRequests, 0);
    assert.equal(page.timers, 0);
});

test('polling pauses while hidden and backs off after network failures', async () => {
    let attempts = 0;
    const page = editingPage(url => {
        if (url !== '/web-api/progress/') return Promise.resolve({id: 42, status: 'PUBLISHED'});
        attempts++;
        return attempts <= 2 ? Promise.reject(new RequestError('Network unavailable.')) : Promise.resolve({progressItems: []});
    }, true);
    page.visibility(true);
    assert.equal(page.timers, 0);
    assert.equal(attempts, 0);
    page.visibility(false);
    assert.equal(page.nextPollDelay, 0);
    await page.poll();
    assert.equal(page.nextPollDelay, 4000);
    await page.poll();
    assert.equal(page.nextPollDelay, 8000);
    await page.poll();
    assert.equal(page.nextPollDelay, 2000);
    assert.equal(attempts, 3);
});

test('polling never starts an overlapping progress request', async () => {
    const progress = deferred();
    let attempts = 0;
    const page = editingPage(() => { attempts++; return progress.promise; }, true);
    const running = page.poll();
    page.visibility(true);
    page.visibility(false);
    assert.equal(page.timers, 0);
    assert.equal(attempts, 1);
    progress.resolve({progressItems: []});
    await running;
    assert.equal(page.timers, 1);
    assert.equal(page.nextPollDelay, 0);
});

test('old fragments cannot overwrite the page after its filter URL changes', async () => {
    const fragment = deferred();
    let polls = 0, fragments = 0;
    const page = editingPage((url, options = {}) => {
        if (options.html) return ++fragments === 1 ? fragment.promise : Promise.resolve('<div data-refresh-region="episode-title"></div>');
        if (url === '/web-api/progress/') {
            return Promise.resolve({progressItems: polls++ === 0 ? [{episode: {id: 42, status: 'DOWNLOADING'}}] : []});
        }
        return Promise.resolve({id: 42, status: 'PUBLISHED'});
    }, true);
    await page.poll();
    const running = page.poll();
    await tick();
    page.location.href = 'http://localhost/episodes/42/?search=changed';
    fragment.resolve('<div data-refresh-region="episode-title"></div>');
    await running;
    assert.equal(page.replacements, 0);
    await page.poll();
    assert.equal(page.replacements, 1);
});
