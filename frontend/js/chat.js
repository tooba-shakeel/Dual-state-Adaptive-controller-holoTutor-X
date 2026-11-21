/**
 * Chat component — handles conversation history and SSE streaming.
 */

class ChatComponent {
    constructor() {
        this.currentConversationId = null;
        this.isGenerating = false;
        
        // DOM Elements
        this.messagesContainer = document.getElementById('chat-messages');
        this.inputField = document.getElementById('chat-input');
        this.submitBtn = document.getElementById('chat-submit');
        this.form = document.getElementById('chat-form');
        this.historyList = document.getElementById('chat-history-list');
        this.newChatBtn = document.getElementById('new-chat-btn');
        this.exportBtn = document.getElementById('export-chat-btn');
        
        this.bindEvents();
        this.loadConversations();
    }
    
    bindEvents() {
        this.inputField.addEventListener('input', () => {
            this.submitBtn.disabled = this.inputField.value.trim().length === 0 || this.isGenerating;
        });
        
        this.form.addEventListener('submit', (e) => {
            e.preventDefault();
            this.sendMessage();
        });
        
        this.newChatBtn.addEventListener('click', () => {
            this.startNewConversation();
        });
        
        if (this.exportBtn) {
            this.exportBtn.addEventListener('click', () => {
                this.exportConversation();
            });
        }
    }
    
    async loadConversations() {
        try {
            const conversations = await API.getConversations();
            this.historyList.innerHTML = '';
            
            if (conversations.length === 0) {
                this.historyList.innerHTML = '<li class="chat-history-item" style="opacity:0.5;pointer-events:none">No history</li>';
                return;
            }
            
            conversations.forEach(conv => {
                const li = document.createElement('li');
                li.className = `chat-history-item ${conv.id === this.currentConversationId ? 'active' : ''}`;
                li.textContent = conv.title || 'Conversation';
                li.onclick = () => this.loadConversation(conv.id);
                this.historyList.appendChild(li);
            });
        } catch (e) {
            console.error('Failed to load conversations', e);
        }
    }
    
    async startNewConversation() {
        this.currentConversationId = null;
        this.messagesContainer.innerHTML = `
            <div class="chat-message chat-message--system">
                <div class="chat-message__content">
                    Started a new conversation. What would you like to ask?
                </div>
            </div>
        `;
        this.loadConversations();
    }
    
    async loadConversation(id) {
        try {
            const conv = await API.getConversation(id);
            this.currentConversationId = id;
            this.loadConversations(); // update active state
            
            this.messagesContainer.innerHTML = '';
            conv.messages.forEach(msg => {
                this.appendMessage(msg.role, msg.content);
            });
            this.scrollToBottom();
        } catch (e) {
            console.error('Failed to load conversation details', e);
        }
    }
    
    appendMessage(role, content) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `chat-message chat-message--${role}`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'chat-message__content';
        
        // Simple markdown parsing
        let html = content
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
            
        contentDiv.innerHTML = html;
        msgDiv.appendChild(contentDiv);
        
        this.messagesContainer.appendChild(msgDiv);
        this.scrollToBottom();
        return { msgDiv, contentDiv };
    }
    
    appendCitations(msgDiv, citations) {
        if (!citations || citations.length === 0) return;
        
        const citationsDiv = document.createElement('div');
        citationsDiv.className = 'chat-message__citations';
        
        citations.forEach(c => {
            const scorePercent = Math.round(c.score * 100);
            citationsDiv.innerHTML += `
                <div class="citation-card">
                    <div class="citation-card__header">
                        <strong>${c.document_name} (Page ${c.page_number})</strong>
                        <span>Relevance: ${scorePercent}%</span>
                    </div>
                    <div class="citation-card__text">${c.text}</div>
                </div>
            `;
        });
        
        msgDiv.appendChild(citationsDiv);
        this.scrollToBottom();
    }
    
    // True when the "answer" is really an error message (e.g. the AI wait limit or a usage limit)
    static isFailedAnswer(text) {
        const t = String(text || '').trim();
        return t.startsWith('Error:') || t.includes('[LLM Error:');
    }

    // Retry button under a failed answer: removes the failed pair and asks the same question again
    appendRetryButton(msgDiv, userDiv, question) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn--sm chat-retry';
        btn.textContent = 'Retry';
        btn.addEventListener('click', () => {
            if (this.isGenerating) return;
            userDiv.remove();
            msgDiv.remove();
            this.sendMessage(question);
        });
        msgDiv.appendChild(btn);
        this.scrollToBottom();
    }

    async sendMessage(retryText) {
        const text = (typeof retryText === 'string' ? retryText : this.inputField.value).trim();
        if (!text || this.isGenerating) return;
        
        // UI reset
        this.inputField.value = '';
        this.submitBtn.disabled = true;
        this.isGenerating = true;
        
        // Add user message
        const { msgDiv: userDiv } = this.appendMessage('user', text);
        
        // Create empty assistant message for streaming
        const { msgDiv, contentDiv } = this.appendMessage('assistant', '<span class="status-dot status-dot--loading"></span> Thinking...');
        let fullResponse = '';
        let answerTopic = null;
        
        try {
            await API.chatStream(text, this.currentConversationId, (chunk, citations, data) => {
                // Keep the conversation the server created, so follow-up questions share its history
                if (data && data.conversation_id && !this.currentConversationId) {
                    this.currentConversationId = data.conversation_id;
                }
                if (fullResponse === '') {
                    contentDiv.innerHTML = ''; // Clear loading
                }
                
                if (chunk) {
                    fullResponse += chunk;
                    // Re-render full markdown
                    contentDiv.innerHTML = fullResponse
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\*(.*?)\*/g, '<em>$1</em>')
                        .replace(/\n/g, '<br>');
                }
                
                if (citations && citations.length > 0) {
                    this.appendCitations(msgDiv, citations);
                }
                
                // First event also carries the Tutor State (learner state, evidence, action)
                if (data && data.tutor_state && !msgDiv.querySelector('.tutor-state')) {
                    answerTopic = data.topic || data.tutor_state.topic || null;
                    this.appendTutorState(msgDiv, data.tutor_state);
                }
                
                this.scrollToBottom();
            });
            
            // Rating buttons under the finished answer; a Retry button if the answer is an error
            if (ChatComponent.isFailedAnswer(fullResponse)) {
                this.appendRetryButton(msgDiv, userDiv, text);
            } else if (fullResponse && this.currentConversationId) {
                this.appendFeedbackButtons(msgDiv, this.currentConversationId, answerTopic);
            }
            
            // Reload history to get new ID if it was created
            await this.loadConversations();
            
        } catch (e) {
            console.error('Chat error:', e);
            contentDiv.innerHTML = '';
            const errorText = document.createElement('span');
            errorText.style.color = 'var(--danger)';
            errorText.textContent = `Error: ${e.message}`;
            contentDiv.appendChild(errorText);
            this.appendRetryButton(msgDiv, userDiv, text);
        } finally {
            this.isGenerating = false;
            this.inputField.focus();
        }
    }
    
    // ── Tutor State panel + learner rating (LSE) ────────────────────

    static prettyLabel(value) {
        const text = String(value || '').replace(/_/g, ' ');
        return text.charAt(0).toUpperCase() + text.slice(1);
    }

    static tone(value) {
        if (['understanding', 'strong'].includes(value)) return 'good';
        if (['uncertain', 'partial'].includes(value)) return 'warn';
        if (['confused', 'repeated_failure', 'conflicting', 'insufficient'].includes(value)) return 'bad';
        return 'neutral';
    }

    appendTutorState(msgDiv, tutorState) {
        const panel = document.createElement('div');
        panel.className = 'tutor-state';

        const title = document.createElement('div');
        title.className = 'tutor-state__title';
        title.textContent = 'Tutor State';
        panel.appendChild(title);

        const row = document.createElement('div');
        row.className = 'tutor-state__row';

        let evidenceText = ChatComponent.prettyLabel(tutorState.evidence_state);
        if (typeof tutorState.evidence_score === 'number') {
            evidenceText += ` (${Math.round(tutorState.evidence_score * 100)}%)`;
        }

        const chips = [
            ['Learner', ChatComponent.prettyLabel(tutorState.learner_state), ChatComponent.tone(tutorState.learner_state)],
            ['Evidence', evidenceText, ChatComponent.tone(tutorState.evidence_state)],
            ['Action', ChatComponent.prettyLabel(tutorState.teaching_action), 'neutral'],
        ];
        chips.forEach(([label, value, tone]) => {
            const chip = document.createElement('span');
            chip.className = `tutor-chip tutor-chip--${tone}`;
            const l = document.createElement('span');
            l.className = 'tutor-chip__label';
            l.textContent = label;
            const v = document.createElement('span');
            v.className = 'tutor-chip__value';
            v.textContent = value;
            chip.appendChild(l);
            chip.appendChild(v);
            row.appendChild(chip);
        });

        panel.appendChild(row);
        msgDiv.appendChild(panel);
        this.scrollToBottom();
    }

    appendFeedbackButtons(msgDiv, conversationId, topic) {
        const wrap = document.createElement('div');
        wrap.className = 'feedback';

        const prompt = document.createElement('span');
        prompt.className = 'feedback__prompt';
        prompt.textContent = 'Did you understand this?';
        wrap.appendChild(prompt);

        const buttons = [
            { signal: 1,  label: 'Got it',   cls: 'feedback__btn--good' },
            { signal: 0,  label: 'Not sure', cls: 'feedback__btn--warn' },
            { signal: -1, label: 'Confused', cls: 'feedback__btn--bad' },
        ];
        const status = document.createElement('span');
        status.className = 'feedback__status';

        const btnEls = buttons.map(({ signal, label, cls }) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `btn btn--sm feedback__btn ${cls}`;
            btn.textContent = label;
            btn.addEventListener('click', async () => {
                btnEls.forEach(b => { b.disabled = true; });
                btn.classList.add('feedback__btn--selected');
                status.textContent = 'Saving…';
                try {
                    const res = await API.sendFeedback(conversationId, signal, topic);
                    status.textContent = res && res.learner_state
                        ? `Noted. Your level is now: ${ChatComponent.prettyLabel(res.learner_state)}. The next answer will adapt.`
                        : 'Noted.';
                } catch (e) {
                    console.error('Feedback error:', e);
                    status.textContent = 'Could not save your rating. Try again.';
                    btn.classList.remove('feedback__btn--selected');
                    btnEls.forEach(b => { b.disabled = false; });
                    if (typeof App !== 'undefined' && App.toast) App.toast('Could not save your rating', 'error');
                }
            });
            wrap.appendChild(btn);
            return btn;
        });

        wrap.appendChild(status);
        msgDiv.appendChild(wrap);
        this.scrollToBottom();
    }
    
    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
    
    async exportConversation() {
        if (!this.currentConversationId) {
            App.toast('No conversation selected', 'error');
            return;
        }
        App.toast('Starting export...', 'info');
        try {
            await API.downloadFile(`/api/export/chat/${this.currentConversationId}/txt`, 'chat.txt');
        } catch (e) {
            console.error('Export error:', e);
            App.toast(`Export failed: ${e.message}`, 'error');
        }
    }
}
