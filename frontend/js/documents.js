/**
 * Documents component — renders the document list, handles
 * status polling, deletion, and chunk inspection.
 */

const Documents = (() => {
    let _grid, _empty;
    let _pollTimer = null;

    // ── Init ────────────────────────────────────────────────────────

    function init() {
        _grid  = document.getElementById('doc-grid');
        _empty = document.getElementById('empty-state');

        document.getElementById('refresh-btn').addEventListener('click', refresh);

        // Modal close
        document.getElementById('modal-close').addEventListener('click', _closeModal);
        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'modal-overlay') _closeModal();
        });
    }

    // ── Refresh ─────────────────────────────────────────────────────

    async function refresh() {
        try {
            const data = await API.listDocuments();
            _render(data.documents);

            // Auto-poll if any docs are still processing
            const processing = data.documents.some(
                (d) => !['completed', 'failed'].includes(d.status)
            );

            if (processing && !_pollTimer) {
                _pollTimer = setInterval(refresh, 3000);
            } else if (!processing && _pollTimer) {
                clearInterval(_pollTimer);
                _pollTimer = null;
            }
        } catch (err) {
            console.error('Failed to load documents:', err);
        }
    }

    // ── Render ──────────────────────────────────────────────────────

    function _render(docs) {
        if (!docs.length) {
            _empty.hidden = false;
            _grid.hidden = true;
            return;
        }

        _empty.hidden = true;
        _grid.hidden = false;

        _grid.innerHTML = docs.map((doc, i) => {
            const sizeMB = (doc.file_size / 1024 / 1024).toFixed(1);
            const time = new Date(doc.upload_time).toLocaleString();
            const statusClass = doc.status;

            return `
                <div class="doc-card" style="animation-delay: ${i * 60}ms" data-id="${doc.id}">
                    <div class="doc-card__header">
                        <span class="doc-card__name" title="${_esc(doc.original_name)}">${_esc(doc.original_name)}</span>
                        <span class="badge badge--${statusClass}">
                            <span class="badge__dot"></span>
                            ${_statusLabel(doc.status)}
                        </span>
                    </div>
                    <div class="doc-card__meta">
                        <span>📐 ${sizeMB} MB</span>
                        <span>📄 ${doc.page_count || '—'} pages</span>
                        <span>🧩 ${doc.chunk_count || '—'} chunks</span>
                        <span>🕐 ${time}</span>
                    </div>
                    ${doc.error_message ? `<p style="font-size:0.78rem;color:var(--status-failed);">⚠ ${_esc(doc.error_message)}</p>` : ''}
                    <div class="doc-card__actions">
                        ${doc.status === 'completed'
                            ? `<button class="btn btn--sm" onclick="Documents.showChunks('${doc.id}','${_esc(doc.original_name)}')">Chunks</button>
                               <button class="btn btn--sm btn--primary" onclick="Documents.showInsights('${doc.id}','${_esc(doc.original_name)}')">Insights</button>`
                            : ''
                        }
                        <button class="btn btn--sm btn--danger" onclick="Documents.deleteDoc('${doc.id}')">Delete</button>
                    </div>
                    ${doc.status === 'completed' 
                        ? `<div class="doc-card__actions" style="margin-top: 0.5rem; justify-content: flex-start; gap: 0.5rem;">
                             <span style="font-size: 0.8rem; color: var(--text-muted); line-height: 28px;">Export:</span>
                             <button class="btn btn--sm" onclick="Documents.exportInsights('${doc.id}', 'pdf')">PDF</button>
                             <button class="btn btn--sm" onclick="Documents.exportInsights('${doc.id}', 'word')">Word</button>
                             <button class="btn btn--sm" onclick="Documents.exportInsights('${doc.id}', 'excel')">Excel</button>
                           </div>`
                        : ''}
                </div>
            `;
        }).join('');
    }

    // ── Delete ──────────────────────────────────────────────────────

    async function deleteDoc(id) {
        if (!confirm('Delete this document and all associated data?')) return;

        try {
            await API.deleteDocument(id);
            App.toast('Document deleted', 'success');
            refresh();
        } catch (err) {
            App.toast(err.message || 'Delete failed', 'error');
        }
    }

    // ── Chunk inspection modal ──────────────────────────────────────

    async function showChunks(docId, docName) {
        const overlay = document.getElementById('modal-overlay');
        const body    = document.getElementById('modal-body');
        const title   = document.getElementById('modal-title');

        title.textContent = `Chunks — ${docName}`;
        body.innerHTML = '<div style="text-align:center;padding:2rem;"><span class="spinner"></span> Loading chunks…</div>';
        overlay.hidden = false;

        try {
            const chunks = await API.getChunks(docId);

            if (!chunks.length) {
                body.innerHTML = '<p style="text-align:center;color:var(--text-muted)">No chunks found.</p>';
                return;
            }

            body.innerHTML = chunks.map((c) => `
                <div class="chunk-card">
                    <div class="chunk-card__header">
                        <span>Chunk #${c.chunk_index} · Page ${c.page_number}</span>
                        <span>~${c.token_count} tokens</span>
                    </div>
                    <div class="chunk-card__text">${_esc(c.text)}</div>
                </div>
            `).join('');
        } catch (err) {
            body.innerHTML = `<p style="color:var(--status-failed)">Failed to load chunks: ${_esc(err.message)}</p>`;
        }
    }

    function _closeModal() {
        document.getElementById('modal-overlay').hidden = true;
    }

    // ── Insights & Export ───────────────────────────────────────────

    async function showInsights(docId, docName) {
        const overlay = document.getElementById('modal-overlay');
        const body    = document.getElementById('modal-body');
        const title   = document.getElementById('modal-title');

        title.textContent = `Insights — ${docName}`;
        body.innerHTML = `
            <div style="margin-bottom: 1rem; display: flex; gap: 0.5rem;">
                <select id="insight-type-select" style="flex: 1; padding: 0.5rem; border-radius: 4px; border: 1px solid var(--border-color); background: var(--bg-surface); color: var(--text-main);">
                    <option value="executive_summary">Executive Summary</option>
                    <option value="key_insights">Key Insights</option>
                    <option value="action_items">Action Items</option>
                    <option value="risks">Risks</option>
                    <option value="recommendations">Recommendations</option>
                    <option value="kpi_metrics">KPIs & Metrics</option>
                    <option value="important_dates">Important Dates</option>
                    <option value="named_entities">Named Entities</option>
                    <option value="financial_metrics">Financial Metrics</option>
                    <option value="table_insights">Table Insights</option>
                </select>
                <button class="btn btn--sm btn--primary" onclick="Documents.loadInsight('${docId}')">Generate</button>
            </div>
            <div id="insight-content" style="padding: 1rem; background: var(--bg-surface-hover); border-radius: 8px; min-height: 100px; white-space: pre-wrap;">
                Select an insight type and click Generate. Note: First generation may take a few moments.
            </div>
        `;
        overlay.hidden = false;
    }

    async function loadInsight(docId) {
        const select = document.getElementById('insight-type-select');
        const type = select.value;
        const contentDiv = document.getElementById('insight-content');
        
        contentDiv.innerHTML = '<div style="text-align:center;padding:2rem;"><span class="spinner"></span> Generating insight...</div>';
        
        try {
            const token = (typeof Auth !== 'undefined' && Auth.getToken) ? Auth.getToken() : null;
            const res = await fetch(`/api/documents/${docId}/insights/${type}`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!res.ok) throw new Error('Failed to load insight');
            const data = await res.json();
            
            contentDiv.innerHTML = _esc(data.content).replace(/\\n/g, '<br>');
        } catch (err) {
            contentDiv.innerHTML = `<span style="color:var(--status-failed)">Error: ${_esc(err.message)}</span>`;
        }
    }

    async function exportInsights(docId, format) {
        App.toast('Starting export...', 'info');
        const extension = { pdf: 'pdf', word: 'docx', excel: 'xlsx' }[format] || format;
        try {
            await API.downloadFile(`/api/export/insights/${docId}/${format}`, `insights_${docId}.${extension}`);
        } catch (err) {
            console.error('Export error:', err);
            App.toast(`Export failed: ${err.message}`, 'error');
        }
    }

    // ── Helpers ─────────────────────────────────────────────────────

    function _statusLabel(s) {
        const labels = {
            pending: 'Pending', extracting: 'Extracting', chunking: 'Chunking',
            embedding: 'Embedding', storing: 'Storing', completed: 'Completed', failed: 'Failed',
        };
        return labels[s] || s;
    }

    function _esc(str) {
        const d = document.createElement('div');
        d.textContent = str || '';
        return d.innerHTML;
    }

    return { init, refresh, deleteDoc, showChunks, showInsights, loadInsight, exportInsights };
})();
