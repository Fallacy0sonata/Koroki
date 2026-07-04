'use strict';

const axios = require('axios');

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://127.0.0.1:9882';
const BOT_USER_ID = process.env.MC_BOT_USER_ID || 'koroki_minecraft';

function uuidHex() {
  return Math.random().toString(16).slice(2, 14);
}

/**
 * Ask Koroki's brain for a reaction to a Minecraft event.
 * Returns the response text, or null if the response is [silent] or empty.
 */
async function requestCommentary({ gameEventContext, relationshipScore = 0, isOwner = false }) {
  try {
    const resp = await axios.post(`${ORCHESTRATOR_URL}/v1/chat`, {
      request_id: `mc_${uuidHex()}`,
      message: '.',
      defer_tts: true,
      proactive: true,
      game_event_context: gameEventContext,
      user_context: {
        user_id: BOT_USER_ID,
        relationship_score: relationshipScore,
        is_owner: isOwner,
        mode: 'auto',
        platform: 'minecraft',
      },
    }, { timeout: 20000 });

    const text = (resp.data.text || '').trim();
    if (!text || text.toLowerCase() === '[silent]' || text === '.') return null;
    return text;
  } catch (err) {
    console.warn('[BrainClient] Commentary request failed:', err.message);
    return null;
  }
}

module.exports = { requestCommentary };
