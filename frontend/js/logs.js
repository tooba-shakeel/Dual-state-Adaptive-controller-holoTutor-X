/**
 * Logs and Unanswered screen (SRS LOG module). Shown only to administrators.
 *
 *  - every question students asked, with the Tutor State it produced  (GET /api/admin/logs)
 *  - only the questions the tutor could not answer                     (GET /api/admin/unanswered)
 *  - filters: student, from date, to date; 25 rows per page
 */

const Logs = (() => {
    const PAGE_SIZE = 25;
    const ui = {};
    let mode = 'all';      // 'all' | 'unanswered'
    let offset = 0;
    let total = 0;

    // ── helpers ─────────────────────────────────────────────────────

    function el(tag, props, ...kids) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(props || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k === 'class') node.className = v;
            else if (k === 'text') node.textContent = v;
            else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
            else if (k === 'value' || k === 'disabled' || k === 'hidden') node[k] = v;
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

    function pretty(value) {
        const text = String(value || '').replace(/_/g, ' ').trim();
        return text ? text.charAt(0).toUpperCase() + text.slice(1) : '-';
    }

    function when(iso) {
        const d = new Date(iso);
        return isNaN(d) ? (iso || '-') : d.toLocaleString();
    }

    function isAdmin(user) {
        const r = String((user && user.role) || '').toLowerCase();
        return r === 'administrator' || r === 'admin';
    }

    // ── layout ──────────────────────────────────────────────────────

    function buildView(view) {
        ui.allBtn = el('button', { type: 'button', class: 'btn btn--sm logs-tab logs-tab--on', text: 'All questions', onclick: () => setMode('all') });
        ui.unBtn = el('button', { type: 'button', class: 'btn btn--sm logs-tab', text: 'Unanswered', onclick: () => setMode('unanswered') });

        ui.user = el('select', { class: 'admin-select', onchange: () => reload() },
            el('option', { value: '', text: 'All students' }));
        ui.from = el('input', { type: 'date', class: 'admin-input admin-input--sm', onchange: () => reload() });
        ui.to = el('input', { type: 'date', class: 'admin-input admin-input--sm', onchange: () => reload() });

        ui.count = el('p', { class: 'admin-muted' });
        ui.error = el('p', { class: 'admin-error', hidden: true });
        ui.table = el('div', { class: 'admin-tablewrap' });
        ui.prev = el('button', { type: 'button', class: 'btn btn--sm', text: '← Newer', onclick: () => page(-1) });
        ui.next = el('button', { type: 'button', class: 'btn btn--sm', text: 'Older →', onclick: () => page(1) });

        view.appendChild(el('section', { class: 'section admin-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, el('span', { class: 'section__icon', text: '📋' }), 'Interaction log'),
                el('p', { class: 'section__desc', text: 'Every question students asked and how the tutor decided. Unanswered shows the questions the tutor could not answer from the study material.' })),
            el('div', { class: 'admin-actions logs-filters' },
                ui.allBtn, ui.unBtn,
                el('span', { class: 'logs-gap' }),
                ui.user,
                el('label', { class: 'logs-date' }, 'From ', ui.from),
                el('label', { class: 'logs-date' }, 'To ', ui.to),
                el('button', { type: 'button', class: 'btn btn--sm', text: 'Refresh', onclick: () => reload() })),
            ui.count, ui.error, ui.table,
            el('div', { class: 'admin-actions logs-pager' }, ui.prev, ui.next)));
    }

    // ── data ────────────────────────────────────────────────────────

    async function loadStudents() {
        try {
            const users = await Auth.request('GET', '/api/admin/users');
            const keep = ui.user.value;
            clear(ui.user);
            ui.user.appendChild(el('option', { value: '', text: 'All students' }));
            (Array.isArray(users) ? users : [])
                .filter(u => String(u.role).toLowerCase() === 'student')
                .forEach(u => ui.user.appendChild(el('option', { value: String(u.id), text: u.full_name ? `${u.full_name} (${u.email})` : u.email })));
            ui.user.value = keep;
        } catch (_e) { /* the log still works without the student list */ }
    }

    async function load() {
        ui.error.hidden = true;
        const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
        if (ui.user.value) params.set('user_id', ui.user.value);
        if (ui.from.value) params.set('date_from', ui.from.value);
        if (ui.to.value) params.set('date_to', ui.to.value);
        const path = mode === 'unanswered' ? '/api/admin/unanswered' : '/api/admin/logs';
        try {
            const data = await Auth.request('GET', `${path}?${params}`);
            total = data.total || 0;
            render(data.items || []);
        } catch (err) {
            ui.error.textContent = err.status === 403 ? 'Only administrators can see the log.' : err.message;
            ui.error.hidden = false;
        }
    }

    function reload() {
        offset = 0;
        load();
    }

    function page(direction) {
        const next = offset + direction * PAGE_SIZE;
        if (next < 0 || next >= total) return;
        offset = next;
        load();
    }

    function setMode(next) {
        mode = next;
        ui.allBtn.classList.toggle('logs-tab--on', mode === 'all');
        ui.unBtn.classList.toggle('logs-tab--on', mode === 'unanswered');
        reload();
    }

    // ── rendering ───────────────────────────────────────────────────

    function render(items) {
        clear(ui.table);
        const first = total ? offset + 1 : 0;
        const last = Math.min(offset + items.length, total);
        ui.count.textContent = `${first}–${last} of ${total} question(s)`;
        ui.prev.disabled = offset === 0;
        ui.next.disabled = offset + PAGE_SIZE >= total;

        if (!items.length) {
            ui.table.appendChild(el('p', { class: 'admin-muted', text:
                mode === 'unanswered' ? 'No unanswered questions.' : 'No questions logged yet.' }));
            return;
        }

        const head = ['Time', 'Student', 'Question', 'Learner state', 'Evidence', 'Action', 'Answered'];
        const rows = items.map(r => el('tr', {},
            el('td', { class: 'logs-time', text: when(r.created_at) }),
            el('td', { text: r.user_email || (r.user_id ? `User ${r.user_id}` : '-') }),
            el('td', { class: 'logs-question', text: r.question, title: r.question }),
            el('td', {}, el('span', { class: 'admin-badge', text: pretty(r.learner_state) })),
            el('td', {}, el('span', { class: 'admin-badge', text: `${pretty(r.evidence_state)} · ${Number(r.evidence_score).toFixed(2)}` })),
            el('td', {}, el('span', { class: 'admin-badge', text: pretty(r.teaching_action) })),
            el('td', {}, el('span', { class: `admin-badge admin-badge--${r.answered ? 'active' : 'disabled'}`, text: r.answered ? 'Yes' : 'No' }))));

        ui.table.appendChild(el('table', { class: 'admin-table' },
            el('thead', {}, el('tr', {}, head.map(h => el('th', { text: h })))),
            el('tbody', {}, rows)));
    }

    // ── init ────────────────────────────────────────────────────────

    /** Call after login and before the tabs are wired up. Does nothing for non-administrators. */
    function init() {
        const me = (typeof Auth !== 'undefined' && Auth.getUser) ? Auth.getUser() : null;
        if (!isAdmin(me)) return;
        const nav = document.querySelector('.header__nav');
        const main = document.getElementById('app-main');
        if (!nav || !main) return;

        const button = el('button', { class: 'nav-btn', 'data-view': 'logs', text: 'Logs' });
        button.addEventListener('click', () => { loadStudents(); load(); });
        nav.appendChild(button);

        const view = el('div', { id: 'view-logs', class: 'view admin-root', hidden: true });
        main.appendChild(view);
        buildView(view);
    }

    return { init };
})();
