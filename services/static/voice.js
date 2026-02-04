/**
 * Voice Recorder - Handles voice input via MediaRecorder API
 */

export class VoiceRecorder {
  constructor(buttonElement, api, chatUI) {
    this.button = buttonElement;  // Optional, can be null for hands-free only mode
    this.api = api;
    this.chatUI = chatUI;
    this.centerPanel = document.getElementById('centerPanel');

    this.isRecording = false;
    this.isProcessing = false;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.stream = null;
    this.micInitialized = false;  // Track if microphone has been initialized

    // VAD (Voice Activity Detection) settings
    this.vadEnabled = false;  // Whether VAD listening is active
    this.audioContext = null;
    this.analyser = null;
    this.silenceThreshold = 0.01;  // Adjust based on testing
    this.silenceDuration = 2000;  // 2 seconds of silence stops recording
    this.minRecordingDuration = 1000;  // Minimum 1 second
    this.silenceStart = null;
    this.recordingStart = null;
    this.vadCheckInterval = null;

    // Setup event listeners only if button exists
    if (this.button) {
      this.setupEventListeners();
    }
  }

  /**
   * Initialize microphone access (only needs to be called once)
   */
  async initializeMicrophone() {
    if (this.micInitialized) {
      return true;  // Already initialized
    }

    try {
      console.log('Initializing microphone access...');

      // Request microphone access
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      console.log('Microphone access granted');

      // Setup audio analysis
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = this.audioContext.createMediaStreamSource(this.stream);
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 2048;
      this.analyser.smoothingTimeConstant = 0.8;
      source.connect(this.analyser);

      this.micInitialized = true;
      console.log('Microphone initialized successfully');
      return true;

    } catch (error) {
      console.error('Failed to initialize microphone:', error);
      this.chatUI.showError('Failed to access microphone. Please check permissions.');
      return false;
    }
  }

  /**
   * Toggle VAD on/off (doesn't affect microphone connection)
   * @param {boolean} enabled - Whether VAD should be enabled
   */
  async setSpeechEnabled(enabled, fromUserToggle = false) {
    if (enabled) {
      // Enable VAD
      if (!this.vadEnabled) {
        // Initialize microphone if needed
        if (!this.micInitialized) {
          const success = await this.initializeMicrophone();
          if (!success) return;  // Failed to init microphone
        }

        // Start VAD monitoring
        this.vadEnabled = true;
        this.centerPanel.classList.add('vad-listening');
        this.vadCheckInterval = setInterval(() => this.checkAudioLevel(), 100);
        console.log('VAD enabled - listening for speech...');
      }
    } else {
      // Disable VAD
      if (this.vadEnabled) {
        this.vadEnabled = false;
        this.centerPanel.classList.remove('vad-listening');

        // Stop any active recording
        if (this.isRecording) {
          this.stopRecording();
        }

        // Clear VAD check interval
        if (this.vadCheckInterval) {
          clearInterval(this.vadCheckInterval);
          this.vadCheckInterval = null;
        }

        console.log('VAD disabled - stopped listening');
      }
    }

    // Note: We keep the microphone and audio context open for instant restart
  }

  setupEventListeners() {
    // No event listeners needed - controlled by toggle only
  }

  /**
   * Check audio level for speech detection
   */
  checkAudioLevel() {
    if (!this.analyser || this.isProcessing) return;

    const bufferLength = this.analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    this.analyser.getByteTimeDomainData(dataArray);

    // Calculate RMS (Root Mean Square) for volume level
    let sum = 0;
    for (let i = 0; i < bufferLength; i++) {
      const normalized = (dataArray[i] - 128) / 128;
      sum += normalized * normalized;
    }
    const rms = Math.sqrt(sum / bufferLength);

    // Speech detected
    if (rms > this.silenceThreshold) {
      if (!this.isRecording) {
        console.log('Speech detected, starting recording...');
        this.startRecording();
      }
      this.silenceStart = null;
    }
    // Silence detected while recording
    else if (this.isRecording) {
      if (!this.silenceStart) {
        this.silenceStart = Date.now();
      } else {
        const silenceDuration = Date.now() - this.silenceStart;
        const recordingDuration = Date.now() - this.recordingStart;

        // Stop if silence duration exceeded and minimum recording met
        if (silenceDuration >= this.silenceDuration && recordingDuration >= this.minRecordingDuration) {
          console.log('Silence detected, stopping recording...');
          this.stopRecording();
        }
      }
    }
  }

  /**
   * Start recording audio (called by VAD or manual trigger)
   */
  async startRecording() {
    try {
      if (this.isRecording) return;

      console.log('Starting recording...');
      this.recordingStart = Date.now();
      this.silenceStart = null;

      // Create MediaRecorder from existing stream
      this.mediaRecorder = new MediaRecorder(this.stream);
      this.audioChunks = [];
      console.log('MediaRecorder created, format:', this.mediaRecorder.mimeType);

      // Collect audio data
      this.mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          this.audioChunks.push(event.data);
        }
      };

      // Handle recording stop
      this.mediaRecorder.onstop = async () => {
        console.log('Recording stopped, processing...');
        await this.processRecording();
      };

      // Start recording (request data every 100ms)
      this.mediaRecorder.start(100);
      this.isRecording = true;

      // Update UI
      this.updateButtonState('recording');
      this.centerPanel.classList.remove('vad-listening');
      this.centerPanel.classList.add('voice-active');

      console.log('Recording active');
    } catch (error) {
      console.error('Failed to start recording:', error);
      this.chatUI.showError('Failed to start recording.');
    }
  }

  /**
   * Stop recording audio
   */
  stopRecording() {
    if (this.mediaRecorder && this.isRecording) {
      this.mediaRecorder.stop();
      this.isRecording = false;
      this.recordingStart = null;
      this.silenceStart = null;

      // In VAD mode, keep stream open for continued listening
      if (!this.vadEnabled && this.stream) {
        this.stream.getTracks().forEach(track => track.stop());
        this.stream = null;
      }

      console.log('Recording stopped');
    }
  }

  /**
   * Process recorded audio
   */
  async processRecording() {
    console.log('processRecording called, chunks:', this.audioChunks.length);

    if (this.audioChunks.length === 0) {
      console.warn('No audio chunks captured');

      // Return to appropriate state
      if (this.vadEnabled) {
        this.updateButtonState('listening');
        this.centerPanel.classList.remove('voice-active');
        this.centerPanel.classList.add('vad-listening');
      } else {
        this.updateButtonState('idle');
        this.centerPanel.classList.remove('voice-active');
      }
      return;
    }

    // Update UI to processing state
    this.updateButtonState('processing');
    this.isProcessing = true;

    try {
      // Create audio blob (WebM format)
      const webmBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
      console.log(`WebM blob created: ${webmBlob.size} bytes, ${this.audioChunks.length} chunks`);

      // Convert to WAV format for better compatibility
      console.log('Converting WebM to WAV...');
      const wavBlob = await this.convertToWav(webmBlob);
      console.log(`WAV blob created: ${wavBlob.size} bytes`);

      // Send to ASR
      console.log('Sending audio to ASR endpoint...');
      const text = await this.api.transcribe(wavBlob);
      console.log(`Transcription received: "${text}"`);

      if (text && text.trim()) {
        // In VAD mode, auto-send and speak response
        if (this.vadEnabled) {
          console.log('VAD mode: Auto-sending message and speaking response...');

          // Add to chat UI
          this.chatUI.addMessage('user', text);
          this.chatUI.conversationHistory.push({ role: 'user', content: text });

          // Get response from Friday
          const typingId = this.chatUI.showTypingIndicator();
          const messages = await this.api.chat(this.chatUI.conversationHistory);
          this.chatUI.removeTypingIndicator(typingId);

          // Only process the LAST assistant message (final response, not intermediate routing)
          const assistantMessages = messages.filter(msg => msg.role === 'assistant');
          if (assistantMessages.length > 0) {
            const finalMessage = assistantMessages[assistantMessages.length - 1];

            // Add to chat
            this.chatUI.addMessage('assistant', finalMessage.content);
            this.chatUI.conversationHistory.push(finalMessage);

            // Speak the response via TTS
            await this.speakText(finalMessage.content);
          }
        } else {
          // Manual mode: just add to input field
          this.chatUI.addTranscriptionToInput(text);
        }
      } else {
        console.warn('Empty transcription received');
      }
    } catch (error) {
      console.error('Transcription failed:', error);
      this.chatUI.showError('Failed to transcribe audio. Please try again.');
    } finally {
      // Reset state
      this.isProcessing = false;
      this.audioChunks = [];
      this.centerPanel.classList.remove('voice-active');

      // Return to appropriate state
      if (this.vadEnabled) {
        this.updateButtonState('listening');
        this.centerPanel.classList.add('vad-listening');
      } else {
        this.updateButtonState('idle');
      }
    }
  }

  /**
   * Speak text via TTS
   * @param {string} text - Text to speak
   */
  async speakText(text) {
    try {
      console.log(`Speaking text: "${text.substring(0, 50)}..."`);

      // Update UI to show TTS is active
      this.updateButtonState('speaking');

      // Request TTS audio
      const audioBlob = await this.api.synthesize(text);
      console.log(`TTS audio received: ${audioBlob.size} bytes`);

      // Play the audio
      await this.playAudio(audioBlob);

      console.log('TTS playback complete');
    } catch (error) {
      console.error('TTS failed:', error);
      // Continue anyway, don't block the flow
    }
  }

  /**
   * Play audio blob
   * @param {Blob} audioBlob - Audio to play
   * @returns {Promise} - Resolves when playback completes
   */
  async playAudio(audioBlob) {
    return new Promise((resolve, reject) => {
      const audioUrl = URL.createObjectURL(audioBlob);
      const audio = new Audio(audioUrl);

      audio.onended = () => {
        URL.revokeObjectURL(audioUrl);
        resolve();
      };

      audio.onerror = (error) => {
        URL.revokeObjectURL(audioUrl);
        reject(error);
      };

      audio.play().catch(reject);
    });
  }

  /**
   * Convert audio blob to WAV format
   * @param {Blob} audioBlob - Audio blob in any format
   * @returns {Promise<Blob>} - WAV format blob
   */
  async convertToWav(audioBlob) {
    return new Promise((resolve, reject) => {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const reader = new FileReader();

      reader.onload = async (e) => {
        try {
          // Decode audio data
          const audioBuffer = await audioContext.decodeAudioData(e.target.result);

          // Get audio data
          const channelData = audioBuffer.getChannelData(0);
          const sampleRate = audioBuffer.sampleRate;

          // Convert to 16-bit PCM
          const pcmData = new Int16Array(channelData.length);
          for (let i = 0; i < channelData.length; i++) {
            const s = Math.max(-1, Math.min(1, channelData[i]));
            pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }

          // Create WAV file
          const wavBlob = this.createWavBlob(pcmData, sampleRate);
          audioContext.close();
          resolve(wavBlob);
        } catch (error) {
          audioContext.close();
          reject(error);
        }
      };

      reader.onerror = () => {
        reject(new Error('Failed to read audio blob'));
      };

      reader.readAsArrayBuffer(audioBlob);
    });
  }

  /**
   * Create WAV blob from PCM data
   * @param {Int16Array} pcmData - PCM audio data
   * @param {number} sampleRate - Sample rate
   * @returns {Blob} - WAV blob
   */
  createWavBlob(pcmData, sampleRate) {
    const numChannels = 1; // Mono
    const bitsPerSample = 16;
    const bytesPerSample = bitsPerSample / 8;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = pcmData.length * bytesPerSample;
    const fileSize = 44 + dataSize;

    const buffer = new ArrayBuffer(fileSize);
    const view = new DataView(buffer);

    // RIFF header
    this.writeString(view, 0, 'RIFF');
    view.setUint32(4, fileSize - 8, true);
    this.writeString(view, 8, 'WAVE');

    // fmt chunk
    this.writeString(view, 12, 'fmt ');
    view.setUint32(16, 16, true); // fmt chunk size
    view.setUint16(20, 1, true); // PCM format
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);

    // data chunk
    this.writeString(view, 36, 'data');
    view.setUint32(40, dataSize, true);

    // Write PCM samples
    let offset = 44;
    for (let i = 0; i < pcmData.length; i++) {
      view.setInt16(offset, pcmData[i], true);
      offset += 2;
    }

    return new Blob([buffer], { type: 'audio/wav' });
  }

  /**
   * Write string to DataView
   * @param {DataView} view - DataView
   * @param {number} offset - Offset
   * @param {string} string - String to write
   */
  writeString(view, offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  /**
   * Update button appearance based on state
   * @param {string} state - 'idle', 'listening', 'recording', 'processing', or 'speaking'
   */
  updateButtonState(state) {
    // No button to update in hands-free mode
    if (!this.button) return;

    const icon = this.button.querySelector('.icon');
    const label = this.button.querySelector('.label');

    // Remove all state classes
    this.button.classList.remove('recording', 'processing', 'listening', 'speaking');

    switch (state) {
      case 'listening':
        this.button.classList.add('listening');
        icon.textContent = '👂';
        label.textContent = 'Listening...';
        this.button.disabled = false;
        break;

      case 'recording':
        this.button.classList.add('recording');
        icon.textContent = '🔴';
        label.textContent = 'Recording...';
        this.button.disabled = false;
        break;

      case 'processing':
        this.button.classList.add('processing');
        icon.textContent = '⚙️';
        label.textContent = 'Processing...';
        this.button.disabled = true;
        break;

      case 'speaking':
        this.button.classList.add('speaking');
        icon.textContent = '🔊';
        label.textContent = 'Speaking...';
        this.button.disabled = true;
        break;

      case 'idle':
      default:
        icon.textContent = '🎤';
        label.textContent = 'Voice';
        this.button.disabled = false;
        break;
    }
  }

  /**
   * Check if browser supports recording
   * @returns {boolean}
   */
  static isSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }
}
