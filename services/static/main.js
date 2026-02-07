/**
 * Friday Web Interface - Main Entry Point
 */

import { API } from './api.js';
import { ChatUI } from './chat.js';
import { VoiceRecorder } from './voice.js';
import { FeatureToggles } from './toggles.js';

// Initialize components
const api = new API();

// Voice recorder needs to be created first since chat needs reference to it
const voice = new VoiceRecorder(null, api, null);  // Chat UI will be set later

// Create chat UI with voice recorder reference for TTS playback
const chat = new ChatUI(document.getElementById('centerPanel'), api, voice);

// Set chat UI reference in voice recorder
voice.chatUI = chat;


// Feature toggle callbacks
const featureCallbacks = {
  speech: (enabled) => {
    // Update voice recorder when speech toggle changes
    // Pass true for fromUserToggle since this is a user interaction
    voice.setSpeechEnabled(enabled, true);
    console.log(`Speech ${enabled ? 'enabled' : 'disabled'}`);
  }
};

const toggles = new FeatureToggles(
  document.getElementById('featureToggles'),
  api,
  featureCallbacks
);

// Initialize on page load
window.addEventListener('DOMContentLoaded', async () => {
  console.log('Friday Web Interface initializing...');

  // Check browser support
  if (!VoiceRecorder.isSupported()) {
    console.warn('Voice recording not supported in this browser');
  }

  // Load initial status
  try {
    await toggles.loadStatus();
    console.log('Service status loaded');

    // Set initial speech state on voice recorder
    const status = await api.getStatus();
    if (status && status.features) {
      voice.setSpeechEnabled(status.features.speech !== false);
    }
  } catch (error) {
    console.error('Failed to load initial status:', error);
  }

  // Setup event listeners
  setupEventListeners();

  console.log('Friday Web Interface ready');
});

/**
 * Setup global event listeners
 */
function setupEventListeners() {
  // Status indicator (initial check only)
  updateStatusIndicator();

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd + K: Focus message input
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      document.getElementById('messageInput').focus();
    }

    // Ctrl/Cmd + L: Clear chat
    if ((e.ctrlKey || e.metaKey) && e.key === 'l') {
      e.preventDefault();
      if (confirm('Clear chat history?')) {
        chat.clearHistory();
      }
    }
  });
}

/**
 * Update status indicator
 */
async function updateStatusIndicator() {
  const statusDot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');

  try {
    const health = await api.health();

    if (health.status === 'healthy') {
      statusDot.className = 'status-dot online';
      statusText.textContent = 'Online';
    } else {
      statusDot.className = 'status-dot offline';
      statusText.textContent = 'Offline';
    }
  } catch (error) {
    statusDot.className = 'status-dot offline';
    statusText.textContent = 'Offline';
    console.error('Health check failed:', error);
  }
}

// Expose to global scope for debugging
window.friday = {
  api,
  chat,
  voice,
  toggles
};

console.log('Friday modules loaded. Access via window.friday for debugging.');
