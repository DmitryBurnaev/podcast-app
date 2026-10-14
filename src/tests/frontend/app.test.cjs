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
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name] ?? null; }
    focus() {}
    matches(selector) { return selector === '[data-feedback]' && 'feedback' in this.dataset; }
    closest(selector) { return selector === '[data-busy="true"]' && this.dataset.busy === 'true' ? this : null; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
}

function editingPage(request, polling = false) {
    const listeners = new Map();
    const form = new Element({form: 'edit-episode', id: '42'});
    const feedback = new Element({feedback: ''});
    const pageFeedback = new Element({feedback: ''});
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
    let replacements = 0;
    const timers = new Map();
    let timerId = 0;
    oldTitle.replaceWith = () => { replacements++; };
    const document = {
        readyState: 'loading', hidden: false, activeElement: null,
        body: {style: {}},
        getElementById: id => ({'edit-episode-form': form, 'page-feedback': pageFeedback})[id] || null,
        querySelector: selector => selector === '[data-refresh-region="episode-state"]' ? {dataset: {status: 'NEW'}} :
            selector === '[data-refresh-region="episode-title"]' ? oldTitle :
                polling && selector.startsWith('[data-refresh-region="overview"]') ? new Element() : null,
        querySelectorAll: selector => selector === '[data-action]' ? [remove] : selector === '[data-form]' ? [form] : [],
        addEventListener: (name, callback) => listeners.set(name, callback),
        createElement: () => new Element(),
    };
    const context = {
        window: {PodcastHTTP: {request, RequestError}, location: {href: 'http://localhost/episodes/42/'}, addEventListener() {}},
        document, URL,
        setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
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
        form, fields, submit, remove, feedback,
        get replacements() { return replacements; },
        send: () => listeners.get('submit')({target: form, preventDefault() {}}),
        poll: () => {
            const [id, callback] = [...timers.entries()].at(-1);
            timers.delete(id);
            return callback();
        },
    };
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
    assert.equal(page.feedback.children[0].textContent, 'Title is invalid.');
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
    assert.equal(page.feedback.children[1].href, '/login');
    assert.equal(page.submit.disabled, true);
    assert.equal(page.remove.disabled, true);
    page.send();
    await tick();
    assert.equal(attempts, 1);
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
