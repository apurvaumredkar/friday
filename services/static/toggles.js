/**
 * Feature Toggles - Manages service status display and feature toggles
 */

export class FeatureToggles {
  constructor(featureTogglesElement, api, callbacks = {}) {
    this.featureToggles = featureTogglesElement;
    this.api = api;
    this.currentStatus = null;
    this.callbacks = callbacks;  // Feature change callbacks
  }

  /**
   * Load and display current status
   */
  async loadStatus() {
    try {
      this.currentStatus = await this.api.getStatus();
      this.renderToggles();
    } catch (error) {
      console.error('Failed to load status:', error);
      this.showStatusError();
    }
  }

  /**
   * Render feature toggles
   */
  renderToggles() {
    if (!this.currentStatus || !this.currentStatus.features) {
      return;
    }

    const features = this.currentStatus.features;
    this.featureToggles.innerHTML = '';

    // Define feature display names and keys (only non-essential features that can be toggled)
    const featureList = [
      { key: 'web_search', name: 'Web Search' },
      { key: 'speech', name: 'Voice' },
      { key: 'discord_integration', name: 'Discord Bot' }
    ];

    featureList.forEach(({ key, name }) => {
      if (features[key] !== undefined) {
        this.addToggleItem(key, name, features[key]);
      }
    });
  }

  /**
   * Add a feature toggle item
   * @param {string} key - Feature key
   * @param {string} name - Display name
   * @param {boolean} enabled - Current state
   */
  addToggleItem(key, name, enabled) {
    const item = document.createElement('div');
    item.className = 'toggle-item';

    const label = document.createElement('label');
    label.title = `Toggle ${name}`;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = enabled;
    checkbox.id = `toggle-${key}`;

    // Handle toggle change
    checkbox.addEventListener('change', async (e) => {
      const newState = e.target.checked;

      // For speech feature, call callback BEFORE API request to preserve user gesture context
      if (key === 'speech' && this.callbacks[key]) {
        this.callbacks[key](newState);
      }

      await this.toggleFeature(key, newState, checkbox, key === 'speech');
    });

    const span = document.createElement('span');
    span.textContent = name;

    label.appendChild(checkbox);
    label.appendChild(span);
    item.appendChild(label);
    this.featureToggles.appendChild(item);
  }

  /**
   * Toggle a feature on/off
   * @param {string} feature - Feature key
   * @param {boolean} enabled - New state
   * @param {HTMLInputElement} checkbox - Checkbox element
   * @param {boolean} skipCallback - Whether to skip callback (already called)
   */
  async toggleFeature(feature, enabled, checkbox, skipCallback = false) {
    const originalState = !enabled;

    try {
      // Disable checkbox during request
      checkbox.disabled = true;

      // Send toggle request
      const result = await this.api.toggleFeature(feature, enabled);

      console.log(`Feature ${feature} toggled: ${enabled}`);

      // Update local status
      if (this.currentStatus && this.currentStatus.features) {
        this.currentStatus.features[feature] = enabled;
      }

      // Call callback if exists (unless already called before API request)
      if (!skipCallback && this.callbacks[feature]) {
        this.callbacks[feature](enabled);
      }
    } catch (error) {
      console.error(`Failed to toggle ${feature}:`, error);

      // Revert checkbox on error
      checkbox.checked = originalState;

      // Show error message
      this.showToggleError(feature);
    } finally {
      // Re-enable checkbox
      checkbox.disabled = false;
    }
  }

  /**
   * Show error when status loading fails
   */
  showStatusError() {
    this.featureToggles.innerHTML = '<div style="color: #ff4444; padding: 12px;">Failed to load feature toggles</div>';
  }

  /**
   * Show error when toggle fails
   * @param {string} feature - Feature name
   */
  showToggleError(feature) {
    // Create temporary error message
    const error = document.createElement('div');
    error.className = 'error-message';
    error.textContent = `Failed to toggle ${feature}`;
    error.style.fontSize = '12px';
    error.style.margin = '8px 0';

    this.featureToggles.appendChild(error);

    // Remove after 3 seconds
    setTimeout(() => error.remove(), 3000);
  }

  /**
   * Refresh status (call periodically)
   */
  async refresh() {
    await this.loadStatus();
  }
}
