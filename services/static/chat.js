/**
 * Chat UI - Manages message display and user interactions
 */

export class ChatUI {
  constructor(panelElement, api, voiceRecorder = null) {
    this.panel = panelElement;
    this.api = api;
    this.voiceRecorder = voiceRecorder;  // Optional voice recorder for TTS playback
    this.messagesContainer = document.getElementById('messages');
    this.messageInput = document.getElementById('messageInput');
    this.sendBtn = document.getElementById('sendBtn');
    this.conversationHistory = [];

    this.setupEventListeners();
  }

  setupEventListeners() {
    // Send button click
    this.sendBtn.addEventListener('click', () => this.sendMessage());

    // Enter key to send (Shift+Enter for new line)
    this.messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });
  }

  /**
   * Send a text message
   */
  async sendMessage() {
    const content = this.messageInput.value.trim();

    // Text-only - no attachments
    if (!content) return;

    // Add user message to UI
    this.addMessage('user', content);
    this.conversationHistory.push({ role: 'user', content });

    // Clear input
    this.messageInput.value = '';

    // Show typing indicator
    const typingId = this.showTypingIndicator();

    try {
      // Send to API
      const messages = await this.api.chat(this.conversationHistory);

      // Remove typing indicator
      this.removeTypingIndicator(typingId);

      // Only display the LAST assistant message (final response, not intermediate routing)
      const assistantMessages = messages.filter(msg => msg.role === 'assistant');
      if (assistantMessages.length > 0) {
        const finalMessage = assistantMessages[assistantMessages.length - 1];
        this.addMessage('assistant', finalMessage.content);
        this.conversationHistory.push(finalMessage);

        // Speak response via TTS if voice is enabled
        await this.speakResponse(finalMessage.content);
      }
    } catch (error) {
      this.removeTypingIndicator(typingId);
      this.showError('Failed to send message. Please try again.');
      console.error('Send message error:', error);
    }
  }

  /**
   * Add a message to the chat
   * @param {string} role - 'user' or 'assistant'
   * @param {string} content - Message content
   * @param {boolean} isHTML - Whether content is HTML (default: false)
   */
  addMessage(role, content, isHTML = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';

    if (isHTML) {
      contentDiv.innerHTML = content;
    } else {
      // Convert markdown to HTML (simple conversion for now)
      contentDiv.innerHTML = this.markdownToHTML(content);
    }

    // Add timestamp
    const timestamp = document.createElement('span');
    timestamp.className = 'timestamp';
    timestamp.textContent = new Date().toLocaleTimeString();
    contentDiv.appendChild(timestamp);

    messageDiv.appendChild(contentDiv);
    this.messagesContainer.appendChild(messageDiv);

    // Auto-scroll to bottom
    this.scrollToBottom();
  }

  /**
   * Show typing indicator
   * @returns {string} - Indicator ID
   */
  showTypingIndicator() {
    const id = `typing-${Date.now()}`;
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message assistant';
    typingDiv.id = id;

    const indicatorDiv = document.createElement('div');
    indicatorDiv.className = 'typing-indicator';
    indicatorDiv.innerHTML = '<span></span><span></span><span></span>';

    typingDiv.appendChild(indicatorDiv);
    this.messagesContainer.appendChild(typingDiv);

    this.scrollToBottom();
    return id;
  }

  /**
   * Remove typing indicator
   * @param {string} id - Indicator ID
   */
  removeTypingIndicator(id) {
    const element = document.getElementById(id);
    if (element) {
      element.remove();
    }
  }

  /**
   * Show error message
   * @param {string} message - Error message
   */
  showError(message) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.textContent = message;
    this.messagesContainer.appendChild(errorDiv);
    this.scrollToBottom();
  }

  /**
   * Scroll to bottom of messages
   */
  scrollToBottom() {
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
  }

  /**
   * Simple markdown to HTML converter
   * (For production, use a library like marked.js)
   * @param {string} markdown - Markdown text
   * @returns {string} - HTML string
   */
  markdownToHTML(markdown) {
    let html = markdown;

    // Code blocks
    html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
      return `<pre><code class="language-${lang || 'text'}">${this.escapeHTML(code.trim())}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

    // Line breaks
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');

    // Wrap in paragraph if not already wrapped
    if (!html.startsWith('<')) {
      html = `<p>${html}</p>`;
    }

    return html;
  }

  /**
   * Escape HTML special characters
   * @param {string} text - Text to escape
   * @returns {string} - Escaped text
   */
  escapeHTML(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Add transcribed text to input
   * @param {string} text - Transcribed text
   */
  addTranscriptionToInput(text) {
    const currentValue = this.messageInput.value;
    this.messageInput.value = currentValue ? `${currentValue} ${text}` : text;
    this.messageInput.focus();
  }

  /**
   * Speak assistant response via TTS if voice is enabled
   * @param {string} text - Text to speak
   */
  async speakResponse(text) {
    // Only speak if voice recorder is available and VAD is enabled
    if (this.voiceRecorder && this.voiceRecorder.vadEnabled) {
      try {
        await this.voiceRecorder.speakText(text);
      } catch (error) {
        console.error('TTS playback error:', error);
        // Don't block on TTS errors
      }
    }
  }

  /**
   * Clear conversation history
   */
  clearHistory() {
    this.conversationHistory = [];
    this.messagesContainer.innerHTML = '';
  }
}
