/**
 * Login and signup (SRS 3.2.1, UAM) for the browser.
 *
 * - The app starts only after a valid login: App.init() waits for Auth.ensureLoggedIn().
 * - The login token is kept in the browser (localStorage) and sent by api.js as
 *   "Authorization: Bearer <token>".
 * - The header shows the user's name, role and a Logout button.
 * - If the server says 401 (token expired), api.js calls Auth.expire() and the
 *   login screen comes back.
 *
 * Talks to: POST /api/auth/signup, POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
 */

const Auth = (() => {
    const TOKEN_KEY = 'holotutor_token';
    const BASE = (
        window.API_BASE ||
        (document.querySelector('meta[name="api-base"]') || {}).content ||
        window.location.origin
    );

    let token = null;
    let user = null;
    let overlay = null;
    let headerUser = null;
    let onReady = null;

    // ── small helpers ───────────────────────────────────────────────

    function storeGet() {
        try { return localStorage.getItem(TOKEN_KEY); } catch (_e) { return null; }
    }

    function storeSet(value) {
        try {
            if (value) localStorage.setItem(TOKEN_KEY, value);
            else localStorage.removeItem(TOKEN_KEY);
        } catch (_e) { /* private mode: the login just will not be remembered */ }
    }

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

    /** A readable message from whatever shape the server's error has. */
    async function readError(res) {
        let body = null;
        try { body = await res.json(); } catch (_e) { /* no JSON */ }
        const detail = body && body.detail;
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail) && detail.length) {
            return detail.map(d => (d && (d.msg || d.message)) || String(d)).join(' ');
        }
        if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
        if (body && typeof body.error === 'string' && body.error) return body.error;
        if (body && typeof body.message === 'string' && body.message) return body.message;
        return `Something went wrong (${res.status}).`;
    }

    async function call(method, path, body, withToken) {
        const headers = {};
        if (body) headers['Content-Type'] = 'application/json';
        if (withToken && token) headers['Authorization'] = `Bearer ${token}`;
        let res;
        try {
            res = await fetch(`${BASE}${path}`, {
                method, headers, body: body ? JSON.stringify(body) : undefined,
            });
        } catch (_e) {
            throw new Error('Cannot reach the server. Check that it is running.');
        }
        if (!res.ok) {
            const err = new Error(await readError(res));
            err.status = res.status;
            throw err;
        }
        if (res.status === 204) return null;
        try { return await res.json(); } catch (_e) { return null; }
    }

    function prettyRole(role) {
        const text = String(role || '').replace(/_/g, ' ').trim();
        return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
    }

    // ── the login / signup screen ───────────────────────────────────

    function showOverlay(mode, notice) {
        if (!overlay) {
            overlay = el('div', { class: 'auth-overlay', role: 'dialog', 'aria-modal': 'true' });
            document.body.appendChild(overlay);
        }
        overlay.hidden = false;
        clear(overlay);
        overlay.appendChild(mode === 'signup' ? signupCard() : loginCard(notice));
    }

    function hideOverlay() {
        if (overlay) overlay.hidden = true;
    }

    function field(label, input) {
        return el('label', { class: 'auth-field' }, el('span', { class: 'auth-label', text: label }), input);
    }

    function cardShell(title, subtitle, form, switchText, switchLabel, switchTo) {
        return el('div', { class: 'auth-card' },
            el('h1', { class: 'auth-title', text: 'HoloTutor-X' }),
            el('h2', { class: 'auth-subtitle', text: title }),
            subtitle ? el('p', { class: 'auth-muted', text: subtitle }) : null,
            form,
            el('p', { class: 'auth-switch' }, switchText + ' ',
                el('a', { href: '#', class: 'auth-link', text: switchLabel,
                    onclick: (e) => { if (e && e.preventDefault) e.preventDefault(); showOverlay(switchTo); } })));
    }

    function loginCard(notice) {
        const email = el('input', { type: 'email', class: 'auth-input', autocomplete: 'username', required: true });
        const password = el('input', { type: 'password', class: 'auth-input', autocomplete: 'current-password', required: true });
        const message = el('p', { class: 'auth-message', hidden: !notice, text: notice || '' });
        const error = el('p', { class: 'auth-error', hidden: true });
        const submit = el('button', { type: 'submit', class: 'btn btn--primary auth-submit', text: 'Log in' });

        const form = el('form', {
            class: 'auth-form',
            onsubmit: async (e) => {
                if (e && e.preventDefault) e.preventDefault();
                error.hidden = true;
                if (!email.value.trim() || !password.value) {
                    error.textContent = 'Enter your email and password.';
                    error.hidden = false;
                    return;
                }
                submit.disabled = true;
                try {
                    await doLogin(email.value.trim(), password.value);
                } catch (err) {
                    error.textContent = err.message;
                    error.hidden = false;
                } finally {
                    submit.disabled = false;
                }
            },
        }, field('Email', email), field('Password', password), message, error, submit);

        return cardShell('Log in', 'Sign in to your study assistant.', form,
            'New here?', 'Create an account', 'signup');
    }

    function signupCard() {
        const name = el('input', { type: 'text', class: 'auth-input', autocomplete: 'name', required: true });
        const email = el('input', { type: 'email', class: 'auth-input', autocomplete: 'username', required: true });
        const password = el('input', { type: 'password', class: 'auth-input', autocomplete: 'new-password', required: true });
        const confirm = el('input', { type: 'password', class: 'auth-input', autocomplete: 'new-password', required: true });
        const consent = el('input', { type: 'checkbox', class: 'auth-consent__box' });
        const consentRow = el('label', { class: 'auth-consent' }, consent,
            el('span', { text: 'I agree that my questions and ratings are stored to adapt the tutoring.' }));
        const error = el('p', { class: 'auth-error', hidden: true });
        const submit = el('button', { type: 'submit', class: 'btn btn--primary auth-submit', text: 'Create account' });

        const form = el('form', {
            class: 'auth-form',
            onsubmit: async (e) => {
                if (e && e.preventDefault) e.preventDefault();
                error.hidden = true;
                const fail = (text) => { error.textContent = text; error.hidden = false; };
                if (!name.value.trim() || !email.value.trim() || !password.value) return fail('Fill in all the fields.');
                if (!consent.checked) return fail('Please agree to the storage of your questions and ratings to create an account.');
                const pwLength = Array.from(password.value).length;   // count characters like the server (an emoji is 1)
                if (pwLength < 8 || pwLength > 64) return fail('Password must be 8 to 64 characters.');
                if (password.value !== confirm.value) return fail('The two passwords do not match.');
                submit.disabled = true;
                try {
                    await call('POST', '/api/auth/signup', {
                        full_name: name.value.trim(), email: email.value.trim(), password: password.value,
                        consent: true,
                    });
                } catch (err) {
                    submit.disabled = false;
                    return fail(err.message);
                }
                try {
                    await doLogin(email.value.trim(), password.value);   // log in straight after signing up
                } catch (err) {
                    showOverlay('login', 'Account created. ' + err.message);
                } finally {
                    submit.disabled = false;
                }
            },
        }, field('Full name', name), field('Email', email), field('Password', password),
           field('Confirm password', confirm), consentRow, error, submit);

        return cardShell('Create your account', 'Students can sign up here.', form,
            'Already have an account?', 'Log in', 'login');
    }

    async function doLogin(email, password) {
        const data = await call('POST', '/api/auth/login', { email, password });
        if (!data || !data.token) throw new Error('The server did not return a login token.');
        token = data.token;
        user = data.user || null;
        storeSet(token);
        hideOverlay();
        showUser();
        if (onReady) { const done = onReady; onReady = null; done(user); }
    }

    // ── header: who is logged in ────────────────────────────────────

    function showUser() {
        const header = document.getElementById('app-header');
        if (!header) return;
        if (!headerUser) {
            headerUser = el('div', { class: 'auth-user' });
            header.appendChild(headerUser);
        }
        clear(headerUser);
        headerUser.hidden = false;
        const name = (user && (user.full_name || user.name || user.email)) || 'Account';
        const role = user && user.role ? prettyRole(user.role) : '';
        headerUser.appendChild(el('span', { class: 'auth-user__name', text: name }));
        if (role) headerUser.appendChild(el('span', { class: 'auth-user__role', text: role }));
        headerUser.appendChild(el('button', { type: 'button', class: 'btn btn--sm auth-user__logout', text: 'Log out', onclick: logout }));
    }

    // ── public functions ────────────────────────────────────────────

    /** Resolves when a valid login exists. Shows the login screen if there is none. */
    function ensureLoggedIn() {
        return new Promise(async (resolve) => {
            token = storeGet();
            let notice = '';
            if (token) {
                try {
                    user = await call('GET', '/api/auth/me', null, true);
                    showUser();
                    return resolve(user);
                } catch (err) {
                    if (err.status === 401 || err.status === 403) {
                        token = null;
                        storeSet(null);
                        notice = 'Your session has ended. Please log in again.';
                    } else {
                        notice = err.message;   // server not reachable: keep the token, show why
                    }
                }
            }
            onReady = resolve;
            showOverlay('login', notice);
        });
    }

    async function logout() {
        try { await call('POST', '/api/auth/logout', null, true); } catch (_e) { /* log out here anyway */ }
        token = null;
        user = null;
        storeSet(null);
        window.location.reload();
    }

    /** Called by api.js when the server answers 401 to a request that carried (or needed) a token. */
    function expire() {
        token = null;
        user = null;
        storeSet(null);
        window.location.reload();
    }

    /** A JSON request that carries the login token. A 401 sends the user back to the login screen. */
    async function request(method, path, body) {
        try {
            return await call(method, path, body, true);
        } catch (err) {
            if (err.status === 401) expire();
            throw err;
        }
    }

    return {
        ensureLoggedIn,
        logout,
        expire,
        request,
        getToken: () => token,
        getUser: () => user,
    };
})();
