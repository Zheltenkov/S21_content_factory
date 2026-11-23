import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/modules/translationArchive.js', import.meta.url), 'utf8');

for (const contentType of ['Content-Type', 'content-type', 'CONTENT-TYPE', null]) {
    test(`archive upload and start serialize multipart with ${contentType || 'cookie-only auth'}`, async () => {
        const sharedHeaders = contentType
            ? { [contentType]: 'application/json', Authorization: 'Bearer regression-test' }
            : {};
        const originalHeaders = { ...sharedHeaders };
        const requests = [];
        const nodes = {
            translationArchiveFile: { files: [new File(['archive bytes'], 'course.zip')] },
            translationArchiveLanguage: { value: 'en' },
            translationStatus: {},
        };
        const window = { getAuthHeaders: () => sharedHeaders };
        const context = vm.createContext({
            window, Headers, FormData,
            document: {
                getElementById: id => nodes[id] || null,
                querySelectorAll: () => [{ checked: true, disabled: false, dataset: { path: 'README.md' } }],
            },
            fetch: async (url, options) => {
                // Exercise actual Fetch serialization: FormData must carry its generated boundary.
                const request = new Request('https://example.invalid' + url, options);
                requests.push(request);
                return requests.length === 1
                    ? { ok: true, json: async () => ({ plan_id: 'test-plan', entries: [], totals: {} }) }
                    : { ok: false, status: 400, json: async () => ({ detail: 'test: no paid job started' }) };
            },
            clearInterval() {},
        });
        vm.runInContext(source, context);
        await window.inspectArchive();
        await window.startArchiveTranslation();
        assert.equal(requests.length, 2);
        for (const request of requests) {
            assert.match(request.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
            assert.equal(request.headers.get('Authorization'), sharedHeaders.Authorization || null);
        }
        const upload = await requests[0].formData();
        assert.equal(upload.get('file').name, 'course.zip');
        assert.equal(await upload.get('file').text(), 'archive bytes');
        const start = await requests[1].formData();
        assert.equal(start.get('target_language'), 'en');
        assert.deepEqual(JSON.parse(start.get('paths')), ['README.md']);
        assert.deepEqual(sharedHeaders, originalHeaders, 'shared auth headers must not be mutated');
    });
}
