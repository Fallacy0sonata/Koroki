'use strict';

/**
 * HTTP client for the Koroki Orchestrator service.
 *
 * Uses the global fetch API available in Node 18+.
 * Throws descriptive errors on non-2xx responses.
 */
class OrchestratorClient {
  /**
   * @param {string} baseUrl - e.g. "http://127.0.0.1:9882"
   */
  constructor(baseUrl) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  /**
   * Send a chat request and get a complete response.
   * @param {Object} payload - ChatRequest shape
   * @returns {Promise<Object>} - { request_id, text, has_audio, timings, ... }
   */
  async chat(payload) {
    const resp = await fetch(`${this.baseUrl}/v1/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const body = await resp.text().catch(() => '');
      throw new Error(`Orchestrator ${resp.status}: ${body}`);
    }

    return resp.json();
  }

  /**
   * Check orchestrator health.
   * @returns {Promise<Object>}
   */
  async health() {
    const resp = await fetch(`${this.baseUrl}/health`, { signal: AbortSignal.timeout(3000) });
    return resp.json();
  }

  /**
   * Check orchestrator readiness (includes upstream checks).
   * @returns {Promise<Object>}
   */
  async ready() {
    const resp = await fetch(`${this.baseUrl}/ready`, { signal: AbortSignal.timeout(5000) });
    return resp.json();
  }
}

module.exports = { OrchestratorClient };
