/**
 * Administration screen (SRS 3.2.1, UAM). Shown only to administrators.
 *
 *  - lists all users (GET   /api/admin/users)
 *  - creates evaluator accounts (POST  /api/admin/evaluators)
 *  - enables or disables an account (PATCH /api/admin/users/{id}/status)
 */

const Admin = (() => {
    const ui = {};
    let users = [];
    let me = null;

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

    function notify(message, type) {
        if (typeof App !== 'undefined' && App && App.toast) App.toast(message, type || 'info');
    }

    function roleKey(user) {
        return String((user && user.role) || '').toLowerCase().replace(/[\s-]+/g, '_');
    }

    function isAdmin(user) {
        const r = roleKey(user);
        return r === 'administrator' || r === 'admin';
    }

    function prettyRole(role) {
        const text = String(role || '').replace(/_/g, ' ').trim();
        return text ? text.charAt(0).toUpperCase() + text.slice(1) : '-';
    }

    function statusOf(user) {
        return String(user.status || 'active').toLowerCase();
    }

    function nameOf(user) {
        return user.full_name || user.name || user.email || `User ${user.id}`;
    }

    // ── layout ──────────────────────────────────────────────────────

    function buildView(view) {
        // Create an evaluator account
        ui.evName = el('input', { type: 'text', class: 'admin-input', autocomplete: 'off' });
        ui.evEmail = el('input', { type: 'email', class: 'admin-input', autocomplete: 'off' });
        ui.evPassword = el('input', { type: 'password', class: 'admin-input', autocomplete: 'new-password' });
        ui.evError = el('p', { class: 'admin-error', hidden: true });
        ui.evSubmit = el('button', { type: 'submit', class: 'btn btn--primary', text: 'Create evaluator' });

        const evaluatorForm = el('form', { class: 'admin-form', onsubmit: createEvaluator },
            el('label', { class: 'admin-field' }, el('span', { class: 'admin-label', text: 'Full name' }), ui.evName),
            el('label', { class: 'admin-field' }, el('span', { class: 'admin-label', text: 'Email' }), ui.evEmail),
            el('label', { class: 'admin-field' }, el('span', { class: 'admin-label', text: 'Temporary password' }), ui.evPassword),
            el('div', { class: 'admin-field admin-field--button' }, ui.evSubmit),
            ui.evError);

        // Users
        ui.search = el('input', { type: 'search', class: 'admin-input admin-input--sm', placeholder: 'Search name or email', oninput: renderUsers });
        ui.roleFilter = el('select', { class: 'admin-select', onchange: renderUsers });
        ui.count = el('p', { class: 'admin-muted' });
        ui.table = el('div', { class: 'admin-tablewrap' });
        ui.listError = el('p', { class: 'admin-error', hidden: true });

        view.appendChild(el('section', { class: 'section admin-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, el('span', { class: 'section__icon', text: '🛡' }), 'Administration'),
                el('p', { class: 'section__desc', text: 'Manage accounts. Students sign up themselves; evaluators are created here.' })),
            el('h3', { class: 'admin-h3', text: 'Create an evaluator account' }),
            evaluatorForm));

        view.appendChild(el('section', { class: 'section admin-card' },
            el('div', { class: 'section__header' },
                el('h3', { class: 'admin-h3', text: 'Users' }),
                el('div', { class: 'admin-actions' }, ui.search, ui.roleFilter,
                    el('button', { type: 'button', class: 'btn btn--sm', text: 'Refresh', onclick: load }))),
            ui.count, ui.listError, ui.table));
    }

    // ── data ────────────────────────────────────────────────────────

    async function load() {
        ui.listError.hidden = true;
        try {
            const data = await Auth.request('GET', '/api/admin/users');
            users = Array.isArray(data) ? data : (data && (data.users || data.items)) || [];
            refreshRoleFilter();
            renderUsers();
        } catch (err) {
            ui.listError.textContent = err.status === 403
                ? 'Only administrators can see this list.'
                : err.message;
            ui.listError.hidden = false;
        }
    }

    async function createEvaluator(e) {
        if (e && e.preventDefault) e.preventDefault();
        ui.evError.hidden = true;
        const fail = (text) => { ui.evError.textContent = text; ui.evError.hidden = false; };
        const full_name = ui.evName.value.trim();
        const email = ui.evEmail.value.trim();
        const password = ui.evPassword.value;
        if (!full_name || !email || !password) return fail('Fill in the name, email and password.');
        ui.evSubmit.disabled = true;
        try {
            await Auth.request('POST', '/api/admin/evaluators', { full_name, email, password });
            ui.evName.value = '';
            ui.evEmail.value = '';
            ui.evPassword.value = '';
            notify(`Evaluator account created for ${email}.`, 'success');
            await load();
        } catch (err) {
            fail(err.message);
        } finally {
            ui.evSubmit.disabled = false;
        }
    }

    async function toggleStatus(user) {
        const next = statusOf(user) === 'disabled' ? 'active' : 'disabled';
        const verb = next === 'disabled' ? 'Disable' : 'Enable';
        if (typeof confirm === 'function' && !confirm(`${verb} the account of ${nameOf(user)}?`)) return;
        try {
            await Auth.request('PATCH', `/api/admin/users/${user.id}/status`, { status: next });
            notify(`${nameOf(user)} is now ${next}.`, 'success');
            await load();
        } catch (err) {
            notify(err.message, 'error');
        }
    }

    // ── rendering ───────────────────────────────────────────────────

    function refreshRoleFilter() {
        const keep = ui.roleFilter.value;
        const roles = [...new Set(users.map(roleKey).filter(Boolean))].sort();
        clear(ui.roleFilter);
        ui.roleFilter.appendChild(el('option', { value: '', text: 'All roles' }));
        roles.forEach(r => ui.roleFilter.appendChild(el('option', { value: r, text: prettyRole(r) })));
        ui.roleFilter.value = roles.includes(keep) ? keep : '';
    }

    function renderUsers() {
        clear(ui.table);
        const term = (ui.search.value || '').trim().toLowerCase();
        const role = ui.roleFilter.value;
        const shown = users.filter(u =>
            (!role || roleKey(u) === role) &&
            (!term || `${nameOf(u)} ${u.email || ''}`.toLowerCase().includes(term)));
        ui.count.textContent = `${shown.length} of ${users.length} account(s)`;
        if (!shown.length) {
            ui.table.appendChild(el('p', { class: 'admin-muted', text: users.length ? 'No account matches.' : 'No accounts yet.' }));
            return;
        }
        const head = ['Name', 'Email', 'Role', 'Status', ''];
        const rows = shown.map(u => {
            const mine = me && String(u.id) === String(me.id);
            const disabled = statusOf(u) === 'disabled';
            return el('tr', {},
                el('td', { class: 'admin-name' }, nameOf(u), mine ? el('span', { class: 'admin-you', text: ' (you)' }) : null),
                el('td', { text: u.email || '-' }),
                el('td', {}, el('span', { class: 'admin-badge', text: prettyRole(u.role) })),
                el('td', {}, el('span', { class: `admin-badge admin-badge--${disabled ? 'disabled' : 'active'}`, text: disabled ? 'Disabled' : 'Active' })),
                el('td', { class: 'admin-cell-action' }, mine ? null :
                    el('button', { type: 'button', class: 'btn btn--sm', text: disabled ? 'Enable' : 'Disable', onclick: () => toggleStatus(u) })));
        });
        ui.table.appendChild(el('table', { class: 'admin-table' },
            el('thead', {}, el('tr', {}, head.map(h => el('th', { text: h })))),
            el('tbody', {}, rows)));
    }

    // ── init ────────────────────────────────────────────────────────

    /** Call after login and before the tabs are wired up. Does nothing for non-administrators. */
    function init() {
        me = (typeof Auth !== 'undefined' && Auth.getUser) ? Auth.getUser() : null;
        if (!isAdmin(me)) return;
        const nav = document.querySelector('.header__nav');
        const main = document.getElementById('app-main');
        if (!nav || !main) return;

        const button = el('button', { class: 'nav-btn', 'data-view': 'admin', text: 'Admin' });
        button.addEventListener('click', load);
        nav.appendChild(button);

        const view = el('div', { id: 'view-admin', class: 'view admin-root', hidden: true });
        main.appendChild(view);
        buildView(view);
        load();
    }

    return { init };
})();
