const {test, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const {request, RequestError, responseError} = require('../../static/js/http.js');
const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

test('mutations send cookies, the AJAX header and JSON without retries', async () => {
    let calls = 0;
    global.fetch = async (url, options) => {
        calls++;
        assert.equal(url, '/web-api/podcasts/');
        assert.equal(options.credentials, 'same-origin');
        assert.equal(options.headers['X-Requested-With'], 'XMLHttpRequest');
        assert.equal(options.headers['Content-Type'], 'application/json');
        assert.deepEqual(JSON.parse(options.body), {name: 'A "quoted" podcast'});
        return new Response(JSON.stringify({id: 1}), {status: 201, headers: {'Content-Type': 'application/json'}});
    };
    assert.deepEqual(await request('/web-api/podcasts/', {method: 'POST', data: {name: 'A "quoted" podcast'}}), {id: 1});
    assert.equal(calls, 1);
});

test('successful deletion accepts an empty 204 response', async () => {
    global.fetch = async () => new Response(null, {status: 204});
    assert.equal(await request('/web-api/episodes/1/', {method: 'DELETE'}), null);
});

test('server error details are preserved as plain text', async () => {
    global.fetch = async () => new Response(JSON.stringify({error: {code: 'CONFLICT', message: 'Cannot delete.', details: '<script>bad()</script>'}}), {status: 409, headers: {'Content-Type': 'application/json'}});
    await assert.rejects(request('/web-api/episodes/1/', {method: 'DELETE'}), error => {
        assert.ok(error instanceof RequestError);
        assert.equal(error.status, 409);
        assert.equal(error.message, 'Cannot delete.\n<script>bad()</script>');
        return true;
    });
});

test('validation arrays identify the invalid field', () => {
    assert.equal(responseError({error: {message: 'Invalid input.', details: [{key: 'name', message: 'Name is required'}, {loc: ['data', 'title'], msg: 'Too long'}]}}, 400).message,
        'Invalid input.\nname: Name is required\ndata.title: Too long');
});

test('creation failure exposes its existing episode ID for recovery', () => {
    const error = responseError({error: {message: 'Tasks unavailable.', details: {episode_id: 123}}}, 503);
    assert.equal(error.details.episode_id, 123);
    assert.equal(error.message, 'Tasks unavailable.');
});

test('HTML failures produce a readable fallback', async () => {
    global.fetch = async () => new Response('<html>Proxy error</html>', {status: 502, headers: {'Content-Type': 'text/html'}});
    await assert.rejects(request('/web-api/progress/'), /The server could not complete/);
});

test('a login redirect is not treated as a successful response', async () => {
    global.fetch = async () => ({redirected: true});
    await assert.rejects(request('/web-api/podcasts/'), error => error.status === 401 && /session has expired/.test(error.message));
});

test('malformed JSON cannot be mistaken for a successful mutation', async () => {
    global.fetch = async () => new Response('{broken', {headers: {'Content-Type': 'application/json'}});
    await assert.rejects(request('/web-api/podcasts/', {method: 'POST', data: {name: 'New'}}), /check the current state before retrying/);
});

test('network errors remain recoverable and do not retry mutations', async () => {
    let calls = 0;
    global.fetch = async () => { calls++; throw new TypeError('Failed to fetch'); };
    await assert.rejects(request('/web-api/podcasts/', {method: 'POST', data: {name: 'New'}}), /Unable to reach the server/);
    assert.equal(calls, 1);
});

test('timeouts abort requests and warn against blindly repeating a mutation', async () => {
    global.fetch = (_url, {signal}) => new Promise((_, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    });
    await assert.rejects(request('/web-api/podcasts/', {method: 'POST', timeout: 5}), /check the current state before retrying/);
});

test('fragment requests return HTML and request same-origin credentials', async () => {
    global.fetch = async (_url, options) => {
        assert.equal(options.headers.Accept, 'text/html');
        assert.equal(options.credentials, 'same-origin');
        return new Response('<div data-refresh-region="episodes"></div>', {headers: {'Content-Type': 'text/html'}});
    };
    assert.match(await request('/episodes/?fragment=1', {html: true}), /data-refresh-region/);
});

test('JSON in place of a fragment is rejected', async () => {
    global.fetch = async () => new Response('{}', {headers: {'Content-Type': 'application/json'}});
    await assert.rejects(request('/episodes/?fragment=1', {html: true}), /unexpected page response/);
});
