/**
 * API client — handles all HTTP communication with the backend.
 * Supports multipart uploads with progress tracking.
 */

const API = (() => {
    // Determine API base URL in this order:
    // 1. `window.API_BASE` (settable by dev tools or hosting env)
    // 2. <meta name="api-base" content="..."> in HTML
    // 3. fallback to current origin so static-hosted frontend works when mounted by backend
    const BASE_URL = (
        window.API_BASE ||
        document.querySelector('meta[name="api-base"]')?.content ||
        window.location.origin
    );

    // ── Helpers ──────────────────────────────────────────────────────

    // The login token (from auth.js) is sent with every request: "Authorization: Bearer <token>".
    function _authHeaders(headers = {}) {
        const token = (typeof Auth !== 'undefined' && Auth.getToken) ? Auth.getToken() : null;
        if (token) headers['Authorization'] = `Bearer ${token}`;
        return headers;
    }

    // 401 = the token is missing or has expired: go back to the login screen.
    function _handleUnauthorized(status) {
        if (status === 401 && typeof Auth !== 'undefined' && Auth.expire) Auth.expire();
    }

    async function _request(method, path, { body, params } = {}) {
        let url = `${BASE_URL}${path}`;
        if (params) {
            const qs = new URLSearchParams(params).toString();
            url += `?${qs}`;
        }

        const headers = _authHeaders({});
        let fetchBody = body;

        // If body is plain object, JSON-encode it
        if (body && !(body instanceof FormData) && typeof body === 'object') {
            headers['Content-Type'] = 'application/json';
            fetchBody = JSON.stringify(body);
        }

        const res = await fetch(url, { method, headers, body: fetchBody });

        if (!res.ok) {
            _handleUnauthorized(res.status);
            let error;
            try {
                error = await res.json();
            } catch {
                error = { error: res.statusText };
            }
            const err = new Error(error.error || 'Request failed');
            err.status = res.status;
            err.detail = error.detail;
            throw err;
        }

        return res.json();
    }

    // ── Upload with XHR for progress ────────────────────────────────

    function uploadFiles(files, onProgress) {
        return new Promise((resolve, reject) => {
            const form = new FormData();
            for (const file of files) {
                form.append('files', file);
            }

            const xhr = new XMLHttpRequest();
            xhr.open('POST', `${BASE_URL}/api/documents/upload`);
            const authHeaders = _authHeaders({});
            Object.keys(authHeaders).forEach(k => xhr.setRequestHeader(k, authHeaders[k]));

            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable && onProgress) {
                    onProgress(Math.round((e.loaded / e.total) * 100));
                }
            });

            xhr.addEventListener('load', () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    _handleUnauthorized(xhr.status);
                    let err;
                    try {
                        err = JSON.parse(xhr.responseText);
                    } catch {
                        err = { error: xhr.statusText };
                    }
                    reject(new Error(err.error || 'Upload failed'));
                }
            });

            xhr.addEventListener('error', () => reject(new Error('Network error')));
            xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));

            xhr.send(form);
        });
    }

    // ── Chat & RAG ──────────────────────────────────────────────────
    async function chatStream(message, conversationId, onUpdate) {
        const payload = { message };
        if (conversationId) {
            payload.conversation_id = conversationId;
        }

        const response = await fetch(`${BASE_URL}/api/chat`, {
            method: 'POST',
            headers: _authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            _handleUnauthorized(response.status);
            const error = await response.json();
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            
            // SSE lines are separated by \n\n
            const parts = buffer.split('\n\n');
            buffer = parts.pop(); // last part might be incomplete

            for (const part of parts) {
                // The server reports a failed answer as "event: error" (e.g. the AI wait limit)
                if (part.startsWith('event: error')) {
                    const line = part.split('\n').find(l => l.startsWith('data: '));
                    let message = 'The answer could not be generated.';
                    try { message = JSON.parse(line.slice(6)).error || message; } catch (_e) { /* keep default */ }
                    throw new Error(message);
                }
                if (part.startsWith('data: ')) {
                    const dataStr = part.slice(6);
                    if (dataStr.trim() === '{}') continue;
                    
                    try {
                        const data = JSON.parse(dataStr);
                        if (data.error) throw new Error(data.error);
                        onUpdate(data.text, data.citations, data);
                    } catch (e) {
                        console.error('Error parsing SSE data:', dataStr, e);
                    }
                }
            }
        }
    }

    // ── Download a file that needs the login token ──────────────────
    // A plain link (window.location.href) cannot send "Authorization: Bearer <token>",
    // so the file is fetched with the token and saved from a blob.
    async function downloadFile(path, fallbackName = 'download') {
        const res = await fetch(`${BASE_URL}${path}`, { headers: _authHeaders({}) });
        if (!res.ok) {
            _handleUnauthorized(res.status);
            let message = 'Export failed';
            try {
                const body = await res.json();
                const found = body.error || body.detail;
                if (typeof found === 'string' && found) message = found;
            } catch (_e) { /* no JSON body */ }
            throw new Error(message);
        }
        // The file name comes from the server's Content-Disposition header
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
        const filename = match ? decodeURIComponent(match[1]) : fallbackName;

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    // ── Public API ──────────────────────────────────────────────────

    return {
        healthCheck:    () => _request('GET', '/api/health'),
        listDocuments:  (limit = 50, offset = 0) =>
                            _request('GET', '/api/documents', { params: { limit, offset } }),
        getDocument:    (id) => _request('GET', `/api/documents/${id}`),
        deleteDocument: (id) => _request('DELETE', `/api/documents/${id}`),
        getChunks:      (id, limit = 100) =>
                            _request('GET', `/api/documents/${id}/chunks`, { params: { limit } }),
        uploadFiles,
        downloadFile,
        chatStream,
        getConversations: () => _request('GET', '/api/conversations'),
        getConversation:  (id) => _request('GET', `/api/conversations/${id}`),
        // Learner rating: Got it = 1, Not sure = 0, Confused = -1
        sendFeedback: (conversationId, signal, topic) => {
            const body = { conversation_id: conversationId, signal };
            if (topic) body.topic = topic;
            return _request('POST', '/api/feedback', { body });
        },
    };
})();
