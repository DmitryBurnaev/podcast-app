// Run explicitly against src.tests.browser_app, never against a production server.
// This verifies live HTTP contracts; it does not execute the frontend in a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = new URL(process.env.BROWSER_URL || 'http://127.0.0.1:8021/');
assert.equal(base.hostname, '127.0.0.1');
assert.equal(base.protocol, 'http:');
assert.ok(Number(base.port) >= 1024);
let cookie = '';
const checks = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function send(route, {method = 'GET', data, form, status = 200} = {}) {
    const url = new URL(route, base);
    assert.equal(url.origin, base.origin);
    const headers = {'X-Requested-With': 'XMLHttpRequest', Origin: base.origin};
    if (cookie) headers.Cookie = cookie;
    if (data) headers['Content-Type'] = 'application/json';
    if (form) headers['Content-Type'] = 'application/x-www-form-urlencoded';
    const response = await fetch(url, {
        method, headers, redirect: 'manual', signal: AbortSignal.timeout(15000),
        body: data ? JSON.stringify(data) : form ? new URLSearchParams(form).toString() : undefined,
    });
    const content = await response.text();
    assert.equal(response.status, status, `${method} ${url.pathname}: ${content.slice(0, 500)}`);
    return {response, content, json: response.headers.get('Content-Type')?.includes('json') ? JSON.parse(content) : null};
}

async function until(route, predicate) {
    for (let attempt = 0; attempt < 12; attempt++) {
        const {json} = await send(route);
        if (predicate(json)) return json;
        await delay(500);
    }
    throw new Error(`Timed out waiting for ${route}`);
}

async function main() {
    const fixture = await send('/__browser__/feed.xml');
    assert.equal(fixture.content, '<rss version="2.0"><channel><title>Browser feed</title></channel></rss>', 'Only the isolated browser app may be used.');
    await send('/login');
    const login = await send('/login', {method: 'POST', form: {email: 'browser@podcast.example', password: 'browser-check'}, status: 302});
    cookie = login.response.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
    assert.ok(cookie);
    checks.push('real login and session cookie');
    for (const route of ['/', '/podcasts/', '/episodes/']) {
        const page = await send(route);
        assert.match(page.content, /\/static\/js\/app.js/);
    }
    checks.push('main HTML pages render');

    const invalid = await send('/web-api/podcasts/', {method: 'POST', data: {name: ' '}, status: 400});
    assert.equal(invalid.json.error.code, 'INVALID_PARAMETERS');
    const name = `Stage 3 "quoted" <podcast> ${Date.now()}`;
    const created = await send('/web-api/podcasts/', {method: 'POST', data: {name, description: 'Initial'}, status: 201});
    const podcastId = created.json.id;
    assert.equal(created.json.download_automatically, false);
    const podcastRoute = `/web-api/podcasts/${podcastId}/`;
    const list = await send('/podcasts/?fragment=1');
    assert.match(list.content, /data-refresh-region="podcasts"/);
    assert.ok(list.content.includes('&lt;podcast&gt;'));
    assert.ok(list.content.includes('&#34;quoted&#34;') || list.content.includes('&quot;quoted&quot;'));
    const updated = await send(podcastRoute, {method: 'PATCH', data: {name, description: 'Changed', download_automatically: true}});
    assert.equal(updated.json.download_automatically, true);
    checks.push('podcast validation, creation, quoted name escaping and editing');

    const sourceURL = 'https://www.youtube.com/watch?v=stage3demo0';
    const start = Date.now();
    const episode = await send(`${podcastRoute}episodes/`, {method: 'POST', data: {sourceURL}, status: 201});
    assert.ok(Date.now() - start >= 3000, 'The delayed media source must be exercised.');
    assert.equal(episode.json.status, 'DOWNLOADING');
    const episodeId = episode.json.id;
    const episodeRoute = `/web-api/episodes/${episodeId}/`;
    const conflict = await send(podcastRoute, {method: 'DELETE', status: 409});
    assert.equal(conflict.json.error.code, 'CONFLICT');
    await send(`${episodeRoute}cancel-downloading/`, {method: 'PUT'});
    await until(episodeRoute, value => value.status === 'NEW');
    const duplicate = await send(`${podcastRoute}episodes/`, {method: 'POST', data: {sourceURL}, status: 201});
    assert.equal(duplicate.json.id, episodeId);
    const edited = await send(episodeRoute, {method: 'PATCH', data: {title: 'Episode "quotes" <text>', author: 'Edited author', description: 'Edited description'}});
    assert.equal(edited.json.author, 'Edited author');
    const detail = await send(`/episodes/${episodeId}/?fragment=1`);
    assert.match(detail.content, /data-refresh-region="episode-title"/);
    assert.ok(detail.content.includes('&lt;text&gt;'));
    checks.push('delayed episode creation, autodownload, cancellation, conflict, duplicate and editing');

    await send(`${episodeRoute}download/`, {method: 'PUT'});
    const published = await until(episodeRoute, value => value.status === 'PUBLISHED');
    assert.ok(published.audio_url);
    const progress = await send('/web-api/progress/');
    assert.ok(!progress.json.progressItems.some(item => item.episode.id === episodeId));
    const media = await send(published.audio_url, {status: 307});
    const audioURL = new URL(media.response.headers.get('location'));
    assert.equal(audioURL.origin, base.origin);
    const audio = await fetch(audioURL);
    assert.equal(audio.status, 200);
    assert.equal(audio.headers.get('Content-Type'), 'audio/wav');
    const bytes = Buffer.from(await audio.arrayBuffer());
    assert.equal(bytes.subarray(0, 4).toString(), 'RIFF');
    const player = await send(`/episodes/${episodeId}/?fragment=1`);
    assert.match(player.content, /id="episode-audio"/);
    checks.push('terminal download state, token redirect and available WAV payload');

    const task = await send(`${podcastRoute}generate-rss/`, {method: 'PUT'});
    assert.ok(task.json.job_id);
    await until(`${podcastRoute}rss-task/`, value => value.status === 'success');
    const podcast = await send(podcastRoute);
    assert.equal(podcast.json.stat.episodes_count, 1);
    const feedRedirect = await send(podcast.json.rss_url, {status: 307});
    const feedURL = new URL(feedRedirect.response.headers.get('location'));
    assert.equal(feedURL.origin, base.origin);
    const feed = await fetch(feedURL);
    assert.equal(feed.status, 200);
    assert.match(await feed.text(), /<rss version="2.0">/);
    checks.push('RSS task completion, feed availability and episode count');

    await send(episodeRoute, {method: 'DELETE', status: 204});
    const empty = await send(`/podcasts/${podcastId}/?fragment=1`);
    assert.match(empty.content, /No episodes available/);
    assert.equal((await send(podcastRoute)).json.stat.episodes_count, 0);
    await send(podcastRoute, {method: 'DELETE', status: 204});
    await send(podcastRoute, {status: 404});
    checks.push('episode and podcast deletion, empty list and zero count');
    cookie = '';
    const expired = await send('/web-api/podcasts/', {status: 401});
    assert.equal(expired.json.error.code, 'AUTH_MISSING');
    assert.equal(expired.response.headers.get('location'), null);
    checks.push('missing session returns JSON without redirect');

    const output = path.join(process.cwd(), 'output/playwright/stage3-live-http.json');
    fs.mkdirSync(path.dirname(output), {recursive: true});
    fs.writeFileSync(output, JSON.stringify({kind: 'live-http-only', browserExecuted: false, base: base.origin, checks}, null, 2) + '\n');
    console.log(`${checks.length} live HTTP checks passed; browser execution remains unverified.`);
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
