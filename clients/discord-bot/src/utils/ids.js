'use strict';

const { randomUUID } = require('crypto');

/**
 * Generate a new unique request ID for pipeline tracing.
 * @returns {string} UUID v4
 */
function newRequestId() {
  return randomUUID();
}

module.exports = { newRequestId };
