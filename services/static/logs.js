/**
 * Log Stream - Manages real-time log display via WebSocket
 */

export class LogStream {
  constructor(outputElement, api) {
    this.output = outputElement;
    this.api = api;
    this.ws = null;
    this.maxLines = 500;
    this.autoScroll = true;

    // Detect manual scroll
    this.output.addEventListener('scroll', () => {
      const isAtBottom = this.output.scrollHeight - this.output.scrollTop <= this.output.clientHeight + 50;
      this.autoScroll = isAtBottom;
    });
  }

  /**
   * Connect to WebSocket log stream
   */
  connect() {
    try {
      this.ws = this.api.connectLogs(
        (message) => this.handleLogMessage(message),
        (error) => this.handleError(error)
      );

      console.log('Connected to log stream');
    } catch (error) {
      console.error('Failed to connect to log stream:', error);
      this.showConnectionError();
    }
  }

  /**
   * Disconnect from WebSocket
   */
  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
      console.log('Disconnected from log stream');
    }
  }

  /**
   * Handle incoming log message
   * @param {string} message - Log message
   */
  handleLogMessage(message) {
    // Parse log level
    const level = this.parseLogLevel(message);

    // Format and append message
    const formatted = this.formatLogLine(message, level);
    this.appendLine(formatted);

    // Limit buffer size
    this.trimBuffer();

    // Auto-scroll if at bottom
    if (this.autoScroll) {
      this.scrollToBottom();
    }
  }

  /**
   * Parse log level from message
   * @param {string} message - Log message
   * @returns {string} - Log level ('info', 'warning', 'error', 'debug')
   */
  parseLogLevel(message) {
    const lowerMsg = message.toLowerCase();

    if (lowerMsg.includes('[error]') || lowerMsg.includes('error:')) {
      return 'error';
    } else if (lowerMsg.includes('[warning]') || lowerMsg.includes('warning:') || lowerMsg.includes('[warn]')) {
      return 'warning';
    } else if (lowerMsg.includes('[debug]') || lowerMsg.includes('debug:')) {
      return 'debug';
    } else {
      return 'info';
    }
  }

  /**
   * Format log line with color coding
   * @param {string} message - Log message
   * @param {string} level - Log level
   * @returns {string} - Formatted HTML
   */
  formatLogLine(message, level) {
    const escaped = this.escapeHTML(message);
    return `<span class="log-level-${level}">${escaped}</span>`;
  }

  /**
   * Append a line to output
   * @param {string} html - Formatted HTML
   */
  appendLine(html) {
    const line = document.createElement('div');
    line.innerHTML = html;
    this.output.appendChild(line);
  }

  /**
   * Trim buffer to max lines
   */
  trimBuffer() {
    while (this.output.children.length > this.maxLines) {
      this.output.removeChild(this.output.firstChild);
    }
  }

  /**
   * Scroll to bottom
   */
  scrollToBottom() {
    this.output.scrollTop = this.output.scrollHeight;
  }


  /**
   * Handle WebSocket error
   * @param {Error} error - Error object
   */
  handleError(error) {
    console.error('WebSocket error:', error);
    this.showConnectionError();
  }

  /**
   * Show connection error message
   */
  showConnectionError() {
    const errorMsg = '<span class="log-level-error">Failed to connect to log stream. Retrying in 5 seconds...</span>';
    this.appendLine(errorMsg);

    // Retry connection after 5 seconds
    setTimeout(() => {
      this.connect();
    }, 5000);
  }

  /**
   * Load initial logs (fallback to REST endpoint)
   */
  async loadInitialLogs() {
    try {
      const logs = await this.api.getLogs(100);
      const lines = logs.split('\n');

      lines.forEach((line) => {
        if (line.trim()) {
          const level = this.parseLogLevel(line);
          const formatted = this.formatLogLine(line, level);
          this.appendLine(formatted);
        }
      });

      this.scrollToBottom();
    } catch (error) {
      console.error('Failed to load initial logs:', error);
    }
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
}
