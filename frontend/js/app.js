/**
 * Main application controller.
 * Initialises components, runs health check, and provides global utilities.
 */

const App = (() => {
    // ── Init ────────────────────────────────────────────────────────

    let chatComponent = null;

    async function init() {
        // Login first (SRS 3.2.1): the rest of the app starts after a valid login.
        if (typeof Auth !== 'undefined') await Auth.ensureLoggedIn();

        Upload.init(_onUploadComplete);
        Documents.init();
        
        chatComponent = new ChatComponent();

        // Administration screen (only for administrators; SRS 3.2.1)
        if (typeof Admin !== 'undefined') Admin.init();
        // Logs / Unanswered screen (only for administrators; SRS LOG module)
        if (typeof Logs !== 'undefined') Logs.init();
        // Tutor Settings screen (only for administrators; SRS 3.2.5, 3.2.6)
        if (typeof TutorSettings !== 'undefined') TutorSettings.init();

        _initTabs();

        // Initial data load
        await Documents.refresh();

        // Health check
        _checkHealth();
    }

    function _initTabs() {
        const navBtns = document.querySelectorAll('.nav-btn');
        const views = document.querySelectorAll('.view');
        
        navBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const targetView = btn.getAttribute('data-view');
                
                // Update buttons
                navBtns.forEach(b => b.classList.remove('nav-btn--active'));
                btn.classList.add('nav-btn--active');
                
                // Update views
                views.forEach(v => {
                    if (v.id === `view-${targetView}`) {
                        v.removeAttribute('hidden');
                    } else {
                        v.setAttribute('hidden', '');
                    }
                });
            });
        });
    }

    // ── Health ──────────────────────────────────────────────────────

    async function _checkHealth() {
        const dot  = document.getElementById('status-dot');
        const text = document.getElementById('status-text');

        try {
            const health = await API.healthCheck();
            dot.className  = 'status-dot status-dot--healthy';
            text.textContent = `System ${health.status} · v${health.version}`;
        } catch {
            dot.className  = 'status-dot status-dot--error';
            text.textContent = 'System unreachable';
        }
    }

    // ── Upload callback ─────────────────────────────────────────────

    function _onUploadComplete(_result) {
        // Start polling for status updates
        setTimeout(() => Documents.refresh(), 800);
    }

    // ── Toast ───────────────────────────────────────────────────────

    function toast(message, type = 'info', duration = 4500) {
        const container = document.getElementById('toast-container');

        const el = document.createElement('div');
        el.className = `toast toast--${type}`;
        el.textContent = message;
        container.appendChild(el);

        setTimeout(() => {
            el.classList.add('toast-exit');
            el.addEventListener('animationend', () => el.remove());
        }, duration);
    }

    // ── Boot ────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', init);

    return { toast };
})();
