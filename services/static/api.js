/**
 * API Client - Handles all communication with Friday backend
 */

export class API {
  constructor(baseURL = '') {
    this.baseURL = baseURL;
  }

  /**
   * Send a chat message to Friday
   * @param {Array} messages - Array of {role, content} objects
   * @returns {Promise<Array>} - Response messages
   */
  async chat(messages) {
    try {
      const response = await fetch(`${this.baseURL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages })
      });

      if (!response.ok) {
        throw new Error(`Chat request failed: ${response.statusText}`);
      }

      const data = await response.json();
      return data.messages;
    } catch (error) {
      console.error('Chat API error:', error);
      throw error;
    }
  }

  /**
   * Transcribe audio to text using ASR
   * @param {Blob} audioBlob - Audio recording
   * @returns {Promise<string>} - Transcribed text
   */
  async transcribe(audioBlob) {
    try {
      const formData = new FormData();
      formData.append('file', audioBlob, 'recording.webm');

      const response = await fetch(`${this.baseURL}/asr`, {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        throw new Error(`Transcription failed: ${response.statusText}`);
      }

      const data = await response.json();
      return data.text;
    } catch (error) {
      console.error('Transcription error:', error);
      throw error;
    }
  }

  /**
   * Synthesize text to speech
   * @param {string} text - Text to synthesize
   * @param {string} voice - Voice ID (default: af_heart)
   * @param {number} speed - Speech speed (default: 1.0)
   * @returns {Promise<Blob>} - Audio blob
   */
  async synthesize(text, voice = 'af_heart', speed = 1.0) {
    try {
      const response = await fetch(`${this.baseURL}/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice, speed })
      });

      if (!response.ok) {
        throw new Error(`TTS synthesis failed: ${response.statusText}`);
      }

      return await response.blob();
    } catch (error) {
      console.error('TTS synthesis error:', error);
      throw error;
    }
  }

  /**
   * Get available TTS voices
   * @returns {Promise<Object>} - Voice ID to name mapping
   */
  async getVoices() {
    try {
      const response = await fetch(`${this.baseURL}/tts/voices`);

      if (!response.ok) {
        throw new Error(`Failed to fetch voices: ${response.statusText}`);
      }

      const data = await response.json();
      return data.voices;
    } catch (error) {
      console.error('Get voices error:', error);
      throw error;
    }
  }

  /**
   * Get service status and feature states
   * @returns {Promise<Object>} - Status data
   */
  async getStatus() {
    try {
      const response = await fetch(`${this.baseURL}/api/status`);

      if (!response.ok) {
        throw new Error(`Failed to fetch status: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Get status error:', error);
      throw error;
    }
  }

  /**
   * Toggle a feature on/off
   * @param {string} feature - Feature name
   * @param {boolean} enabled - Enable/disable
   * @returns {Promise<Object>} - Updated feature state
   */
  async toggleFeature(feature, enabled) {
    try {
      const response = await fetch(`${this.baseURL}/api/features/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature, enabled })
      });

      if (!response.ok) {
        throw new Error(`Failed to toggle feature: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Toggle feature error:', error);
      throw error;
    }
  }

  /**
   * Connect to logs WebSocket
   * @param {Function} onMessage - Callback for log messages
   * @param {Function} onError - Callback for errors
   * @returns {WebSocket} - WebSocket connection
   */
  connectLogs(onMessage, onError) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws/logs`);

    ws.onmessage = (event) => {
      onMessage(event.data);
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      if (onError) onError(error);
    };

    ws.onclose = () => {
      console.log('WebSocket closed');
    };

    return ws;
  }

  /**
   * Get logs (REST endpoint fallback)
   * @param {number} lines - Number of lines to fetch
   * @returns {Promise<string>} - Log content
   */
  async getLogs(lines = 100) {
    try {
      const response = await fetch(`${this.baseURL}/logs?lines=${lines}`);

      if (!response.ok) {
        throw new Error(`Failed to fetch logs: ${response.statusText}`);
      }

      return await response.text();
    } catch (error) {
      console.error('Get logs error:', error);
      throw error;
    }
  }

  /**
   * Health check
   * @returns {Promise<Object>} - Health status
   */
  async health() {
    try {
      const response = await fetch(`${this.baseURL}/health`);

      if (!response.ok) {
        throw new Error(`Health check failed: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Health check error:', error);
      throw error;
    }
  }
}
