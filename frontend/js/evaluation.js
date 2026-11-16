/**
 * Evaluation Mode screen (SRS 3.2.9, FR9). Shown only to evaluators and administrators.
 *
 *  - upload the test questions (CSV)                      POST /api/evaluation/questions
 *  - choose the versions to compare, the document, the pause between AI calls
 *  - start / continue a run, stop it                      POST /api/evaluation/run, /stop
 *  - watch the progress                                   GET  /api/evaluation/status
 *  - the side-by-side comparison table                    GET  /api/evaluation/results
 *  - download the results and the summary as CSV          GET  /api/evaluation/download/{results|summary}
 *
 * The run itself happens on the server, one run at a time.
 */

const Evaluation = (() => {
    const ORIGIN = (typeof window !== 'undefined' && (
        window.API_BASE ||
        ((document.querySelector('meta[name="api-base"]') || {}).content) ||
        (window.location && window.location.origin))) || '';
    const BASE = `${ORIGIN}/api/evaluation`;

    const VERSIONS = [
        { id: 'full',       label: 'Full tutor',                           on: true },
        { id: 'baseline_a', label: 'Baseline A: normal RAG',               on: true },
        { id: 'baseline_b', label: 'Baseline B: learner state only',       on: true },
        { id: 'baseline_c', label: 'Baseline C: evidence confidence only', on: true },
        { id: 'plain_llm',  label: 'Extra: plain AI, no retrieval',        on: false },
    ];
    const STATUS_LABEL = {
        idle: 'Not started', running: 'Running', stopping: 'Stopping', stopped: 'Stopped',
        finished: 'Finished', failed: 'Failed',
    };
    const EXAMPLE_CSV = [
        'id,question,answerable,reference_page,learner_state',
        'Q01,What does the document say about <a topic it covers>?,yes,3,uncertain',
        'Q02,Explain <a concept from the document>.,yes,5,confused',
        'Q03,What is <something the document never mentions>?,no,,uncertain',
        '',
    ].join('\n');

    const ui = {};
    let status = null;
    let timer = null;
    let me = null;

    // ── small helpers ───────────────────────────────────────────────

    function el(tag, props, ...kids) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(props || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k === 'class') node.className = v;
            else if (k === 'text') node.textContent = v;
            else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
            else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden') node[k] = v;
            else node.setAttribute(k, v === true ? '' : String(v));
        }
        for (const kid of kids.flat()) {
            if (kid === null || kid === undefined || kid === false) continue;
            node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
        }
        return node;
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function notify(message, type) {
        if (typeof App !== 'undefined' && App && App.toast) App.toast(message, type || 'info');
    }

    function isEvaluator(user) {
        const r = String((user && user.role) || '').toLowerCase();
        return r === 'evaluator' || r === 'administrator' || r === 'admin';
    }

    function when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return isNaN(d.getTime()) ? '' : d.toLocaleString();
    }

    async function readError(res) {
        let body = null;
        try { body = await res.json(); } catch (_e) { /* no JSON */ }
        const detail = body && body.detail;
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail) && detail.length) {
            return detail.map(d => (d && (d.msg || d.message)) || String(d)).join(' ');
        }
        if (res.status === 403) return 'Only evaluators and administrators can use the evaluation screen.';
        return `Something went wrong (${res.status}).`;
    }

    /** A request that carries the login token. A 401 sends the user back to the login screen. */
    async function api(path, options) {
        const opts = Object.assign({}, options || {});
        const headers = Object.assign({}, opts.headers || {});
        const token = Auth.getToken && Auth.getToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        opts.headers = headers;
        let res;
        try {
            res = await fetch(BASE + path, opts);
        } catch (_e) {
            throw new Error('Cannot reach the server. Check that it is running.');
        }
        if (res.status === 401 && Auth.expire) Auth.expire();
        if (!res.ok) {
            const err = new Error(await readError(res));
            err.status = res.status;
            throw err;
        }
        return res;
    }

    async function getJson(path, options) {
        const res = await api(path, options);
        try { return await res.json(); } catch (_e) { return null; }
    }

    function postJson(path, body) {
        return getJson(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
    }

    function saveBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    // ── layout ──────────────────────────────────────────────────────

    function buildLayout(root) {
        ui.file = el('input', { type: 'file', accept: '.csv,text/csv', class: 'eval-file', onchange: uploadQuestions });
        ui.qInfo = el('p', { class: 'eval-muted eval-info', text: 'No questions uploaded yet.' });
        ui.versionBoxes = VERSIONS.map(v => ({
            id: v.id,
            box: el('input', { type: 'checkbox', checked: v.on, id: `eval-ver-${v.id}` }),
        }));
        ui.doc = el('select', { class: 'eval-select' }, el('option', { value: '', text: 'All my completed documents' }));
        ui.docHint = el('p', { class: 'eval-muted', hidden: true });
        ui.delay = el('input', { type: 'number', min: 0, max: 120, step: 1, value: 20, class: 'eval-number' });
        ui.fresh = el('input', { type: 'checkbox', id: 'eval-fresh' });
        ui.startBtn = el('button', { class: 'btn btn--primary', type: 'button', text: 'Start / continue', onclick: startRun });
        ui.stopBtn = el('button', { class: 'btn', type: 'button', text: 'Stop', onclick: stopRun, disabled: true });
        ui.setupError = el('p', { class: 'eval-error', hidden: true });

        const setup = el('section', { class: 'section eval-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, el('span', { class: 'section__icon', text: '🧪' }), 'Evaluation Mode'),
                el('p', { class: 'section__desc', text:
                    'Run a set of test questions through the Full tutor and the baselines, then compare the results.' })),
            el('div', { class: 'eval-grid' },
                el('div', { class: 'eval-field' },
                    el('label', { class: 'eval-label', text: '1. Questions file (CSV)' }),
                    ui.file,
                    ui.qInfo,
                    el('p', { class: 'eval-muted' },
                        'Columns: id, question, answerable (yes/no), optional reference_page and learner_state. ',
                        el('button', { type: 'button', class: 'eval-linkbtn', text: 'Download an example', onclick: downloadExample }))),
                el('div', { class: 'eval-field' },
                    el('label', { class: 'eval-label', text: '2. Versions to compare' }),
                    el('div', { class: 'eval-checks' },
                        ui.versionBoxes.map((vb, i) =>
                            el('label', { class: 'eval-check', for: `eval-ver-${vb.id}` }, vb.box, ' ', VERSIONS[i].label)))),
                el('div', { class: 'eval-field' },
                    el('label', { class: 'eval-label', text: '3. Document to search' }),
                    ui.doc,
                    ui.docHint,
                    el('p', { class: 'eval-muted', text: 'Only your own uploaded documents are used.' })),
                el('div', { class: 'eval-field' },
                    el('label', { class: 'eval-label', text: '4. Pause between AI calls (seconds)' }),
                    ui.delay,
                    el('p', { class: 'eval-muted', text: 'Free AI plans have request limits. A longer pause (20 to 30 s) avoids "429" errors.' }))),
            el('div', { class: 'eval-options' },
                el('label', { class: 'eval-check', for: 'eval-fresh' }, ui.fresh,
                    ' Start over: discard the earlier results (leave unticked to continue where the last run stopped)')),
            el('div', { class: 'eval-actions' }, ui.startBtn, ui.stopBtn),
            ui.setupError);

        // Progress
        ui.statusBadge = el('span', { class: 'eval-badge eval-badge--idle', text: STATUS_LABEL.idle });
        ui.progressFill = el('div', { class: 'eval-progress__fill' });
        ui.progressText = el('p', { class: 'eval-muted', text: 'No evaluation has been run yet.' });
        ui.last = el('p', { class: 'eval-muted eval-last', hidden: true });
        ui.docs = el('p', { class: 'eval-muted', hidden: true });
        ui.runError = el('p', { class: 'eval-error', hidden: true });
        ui.resultsBtn = el('button', { class: 'btn btn--sm', type: 'button', text: 'Download results (CSV)', onclick: () => download('results') });
        ui.summaryBtn = el('button', { class: 'btn btn--sm', type: 'button', text: 'Download summary (CSV)', onclick: () => download('summary') });
        ui.downloadError = el('p', { class: 'eval-error', hidden: true });

        const progress = el('section', { class: 'section eval-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, 'Progress ', ui.statusBadge),
                el('div', { class: 'eval-actions' }, ui.resultsBtn, ui.summaryBtn)),
            el('div', { class: 'eval-progress' }, ui.progressFill),
            ui.progressText, ui.last, ui.docs, ui.runError, ui.downloadError);

        // Comparison
        ui.summary = el('div', { class: 'eval-tablewrap' });
        const comparison = el('section', { class: 'section eval-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, 'Comparison'),
                el('div', { class: 'eval-actions' },
                    el('button', { class: 'btn btn--sm', type: 'button', text: 'Refresh', onclick: () => loadResults() }))),
            ui.summary,
            el('p', { class: 'eval-muted eval-legend' },
                'Unsupported answers: the tutor answered a question the material cannot answer (lower is better). ',
                'Correct refusals: it refused an unanswerable question (higher is better). ',
                'Wrong refusals: it refused a question the material can answer (lower is better). ',
                'Source page matches: answered questions whose reference page was among the sources. ',
                'The refusal check reads the answer text, so please verify the answers in the results CSV.'));

        root.appendChild(setup);
        root.appendChild(progress);
        root.appendChild(comparison);
    }

    // ── data ────────────────────────────────────────────────────────

    async function load() {
        loadDocuments();
        loadQuestionInfo();
        await loadStatus();
        await loadResults();
    }

    async function loadDocuments() {
        try {
            const data = await Auth.request('GET', '/api/documents');
            const list = Array.isArray(data) ? data : ((data && (data.documents || data.items)) || []);
            const ready = list.filter(d => ['completed', 'ready'].includes(String(d.status || '').toLowerCase()));
            const keep = ui.doc.value;
            clear(ui.doc);
            ui.doc.appendChild(el('option', { value: '', text: 'All my completed documents' }));
            ready.forEach(d => ui.doc.appendChild(
                el('option', { value: d.id, text: d.original_name || d.filename || d.name || d.id })));
            ui.doc.value = ready.some(d => d.id === keep) ? keep : '';
            ui.docHint.hidden = ready.length > 0;
            ui.docHint.textContent = ready.length ? '' : 'No completed PDF yet. Upload one in the Documents tab first.';
        } catch (_e) {
            ui.docHint.hidden = true;
        }
    }

    function showQuestionInfo(info) {
        ui.qInfo.textContent = info
            ? `${info.questions} questions uploaded (${info.answerable} answerable, ${info.unanswerable} not answerable).`
            : 'No questions uploaded yet.';
    }

    async function loadQuestionInfo() {
        try {
            showQuestionInfo(await getJson('/questions'));
        } catch (_e) {
            showQuestionInfo(null);   // 404: nothing uploaded yet
        }
    }

    async function loadStatus() {
        try {
            showStatus(await getJson('/status'));
        } catch (err) {
            showRunError(err.message);
        }
    }

    async function loadResults() {
        try {
            const data = await getJson('/results');
            renderSummary((data && data.summary) || []);
        } catch (err) {
            clear(ui.summary);
            ui.summary.appendChild(el('p', { class: 'eval-error', text: err.message }));
        }
    }

    // ── actions ─────────────────────────────────────────────────────

    function showSetupError(text) {
        ui.setupError.hidden = !text;
        ui.setupError.textContent = text || '';
    }

    function showRunError(text) {
        ui.runError.hidden = !text;
        ui.runError.textContent = text || '';
    }

    async function uploadQuestions() {
        showSetupError('');
        const file = ui.file.files && ui.file.files[0];
        if (!file) return;
        ui.qInfo.textContent = 'Uploading…';
        const form = new FormData();
        form.append('file', file);
        try {
            showQuestionInfo(await getJson('/questions', { method: 'POST', body: form }));
            notify('Questions uploaded.', 'success');
        } catch (err) {
            showSetupError(err.message);
            loadQuestionInfo();
        }
    }

    async function startRun() {
        showSetupError('');
        const chosen = ui.versionBoxes.filter(vb => vb.box.checked).map(vb => vb.id);
        if (!chosen.length) return showSetupError('Choose at least one version.');
        const delay = Number(ui.delay.value);
        if (Number.isNaN(delay) || delay < 0 || delay > 120) return showSetupError('The pause must be between 0 and 120 seconds.');
        const body = { versions: chosen, delay, fresh: !!ui.fresh.checked };
        if (ui.doc.value) body.document_ids = [ui.doc.value];

        ui.startBtn.disabled = true;
        try {
            showStatus(await postJson('/run', body));
            ui.fresh.checked = false;
            notify('Evaluation started.', 'success');
        } catch (err) {
            showSetupError(err.message);
            ui.startBtn.disabled = false;
        }
    }

    async function stopRun() {
        ui.stopBtn.disabled = true;
        try {
            showStatus(await postJson('/stop'));
        } catch (err) {
            showSetupError(err.message);
        }
    }

    async function download(kind) {
        ui.downloadError.hidden = true;
        try {
            const res = await api(`/download/${kind}`);
            saveBlob(await res.blob(), `evaluation_${kind}.csv`);
        } catch (err) {
            ui.downloadError.textContent = err.status === 404 ? 'There are no results yet.' : err.message;
            ui.downloadError.hidden = false;
        }
    }

    function downloadExample() {
        saveBlob(new Blob([EXAMPLE_CSV], { type: 'text/csv' }), 'evaluation_questions_example.csv');
    }

    // ── polling ─────────────────────────────────────────────────────

    function stopPolling() {
        if (timer) { clearInterval(timer); timer = null; }
    }

    function startPolling() {
        if (timer) return;
        timer = setInterval(async () => {
            try {
                const next = await getJson('/status');
                const before = status && status.status;
                showStatus(next);
                await loadResults();
                const after = next && next.status;
                if ((before === 'running' || before === 'stopping') && after !== 'running' && after !== 'stopping') {
                    notify(after === 'finished' ? 'Evaluation finished.' : `Evaluation ${STATUS_LABEL[after] || after}.`,
                           after === 'failed' ? 'error' : 'info');
                }
            } catch (_e) { /* try again at the next tick */ }
        }, 3000);
    }

    // ── rendering ───────────────────────────────────────────────────

    function showStatus(s) {
        if (!s) return;
        status = s;
        const state = s.status || 'idle';
        const busy = state === 'running' || state === 'stopping';

        ui.statusBadge.className = `eval-badge eval-badge--${state}`;
        ui.statusBadge.textContent = STATUS_LABEL[state] || state;

        const total = Number(s.total) || 0;
        const done = Number(s.done) || 0;
        const pct = total ? Math.min(100, Math.round((100 * done) / total)) : 0;
        ui.progressFill.style.width = `${pct}%`;
        ui.progressText.textContent = total
            ? `${done} of ${total} done (${pct}%)`
            : 'No evaluation has been run yet.';

        ui.last.hidden = !s.last;
        ui.last.textContent = s.last ? `Last: ${s.last}` : '';

        const names = (s.documents || []).join(', ');
        const times = [s.started_at ? `Started ${when(s.started_at)}` : '', s.finished_at ? `ended ${when(s.finished_at)}` : '']
            .filter(Boolean).join(', ');
        const info = [names ? `Documents: ${names}` : '', times].filter(Boolean).join(' · ');
        ui.docs.hidden = !info;
        ui.docs.textContent = info;

        showRunError(s.error ? `The run stopped with an error: ${s.error}. Press Start / continue to try again.` : '');

        ui.startBtn.disabled = busy;
        ui.stopBtn.disabled = state !== 'running';
        if (busy) startPolling(); else stopPolling();
    }

    function th(text) { return el('th', { text }); }

    function renderSummary(summary) {
        clear(ui.summary);
        if (!summary.length) {
            ui.summary.appendChild(el('p', { class: 'eval-muted', text: 'No results yet. Upload the questions and start a run.' }));
            return;
        }
        const head = ['Version', 'Questions', 'Unsupported answers', 'Correct refusals', 'Wrong refusals',
                      'Clarifying questions', 'Source page matches', 'Avg time (s)', 'Errors'];
        const body = summary.map(s => el('tr', {},
            el('td', { class: 'eval-version', text: s.version }),
            el('td', { text: String(s.questions) }),
            el('td', { text: `${s.unsupported_answers} of ${s.unanswerable} (${s.unsupported_rate})` }),
            el('td', { text: `${s.correct_refusals} of ${s.unanswerable} (${s.correct_refusal_rate})` }),
            el('td', { text: `${s.wrong_refusals} of ${s.answerable} (${s.wrong_refusal_rate})` }),
            el('td', { text: String(s.clarifying_questions) }),
            el('td', { text: `${s.source_page_matches} (${s.source_match_rate})` }),
            el('td', { text: s.avg_response_time_s === '' || s.avg_response_time_s === null ? '-' : String(s.avg_response_time_s) }),
            el('td', { class: s.errors ? 'eval-bad' : '', text: String(s.errors) })));
        ui.summary.appendChild(el('table', { class: 'eval-table' },
            el('thead', {}, el('tr', {}, head.map(th))), el('tbody', {}, body)));
    }

    // ── init ────────────────────────────────────────────────────────

    /** Call after login and before the tabs are wired up. Does nothing for students. */
    function init() {
        me = (typeof Auth !== 'undefined' && Auth.getUser) ? Auth.getUser() : null;
        if (!isEvaluator(me)) return;
        const nav = document.querySelector('.header__nav');
        const main = document.getElementById('app-main');
        if (!nav || !main) return;

        const button = el('button', { class: 'nav-btn', 'data-view': 'evaluation', text: 'Evaluation' });
        button.addEventListener('click', load);
        nav.appendChild(button);

        const view = el('div', { id: 'view-evaluation', class: 'view', hidden: true });
        const root = el('div', { class: 'eval-root' });
        view.appendChild(root);
        main.appendChild(view);
        buildLayout(root);
    }

    return { init };
})();
