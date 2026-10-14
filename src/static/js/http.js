(function (root) {
    'use strict';

    class RequestError extends Error {
        constructor(message, status = 0, details = null) {
            super(message);
            this.name = 'RequestError';
            this.status = status;
            this.details = details;
        }
    }

    function detailText(details) {
        if (typeof details === 'string') return details;
        if (!Array.isArray(details)) return '';
        return details.map(item => {
            if (typeof item === 'string') return item;
            if (!item || typeof item !== 'object') return '';
            const message = item.message || item.msg || item.detail;
            if (typeof message !== 'string') return '';
            const field = item.key || (Array.isArray(item.loc) ? item.loc.join('.') : '');
            return field ? `${field}: ${message}` : message;
        }).filter(Boolean).join('\n');
    }

    function responseError(payload, status) {
        const error = payload && typeof payload === 'object' ? payload.error : null;
        const details = error?.details ?? payload?.detail ?? null;
        const fallback = status === 401
            ? 'Your session has expired. Sign in again to continue.'
            : 'The server could not complete the request. Please try again.';
        const message = typeof error?.message === 'string' ? error.message : fallback;
        const extra = detailText(details);
        return new RequestError(extra && extra !== message ? `${message}\n${extra}` : message, status, details);
    }

    async function request(url, {method = 'GET', data, html = false, timeout = 120000} = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
            const headers = {
                'Accept': html ? 'text/html' : 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            };
            if (data !== undefined) headers['Content-Type'] = 'application/json';
            const response = await root.fetch(url, {
                method, headers, credentials: 'same-origin', cache: 'no-store',
                body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal,
            });
            if (response.redirected) throw responseError(null, 401);
            const isJSON = (response.headers.get('Content-Type') || '').includes('json');
            const payload = isJSON ? await response.json().catch(() => null) : null;
            if (!response.ok) throw responseError(payload, response.status);
            if (response.status === 204) return null;
            if (html) {
                if (isJSON) throw new RequestError('The server returned an unexpected page response.', response.status);
                return await response.text();
            }
            if (!isJSON || payload === null) throw new RequestError('The server returned an unexpected response. Please check the current state before retrying.', response.status);
            return payload;
        } catch (error) {
            if (error instanceof RequestError) throw error;
            if (controller.signal.aborted) {
                throw new RequestError('The server took too long to respond. Please check the current state before retrying.');
            }
            throw new RequestError('Unable to reach the server. Check your connection and try again.');
        } finally {
            clearTimeout(timer);
        }
    }

    const api = {RequestError, detailText, responseError, request};
    root.PodcastHTTP = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
