/**
 * Koroki Discord Bot — Entry point.
 *
 * Listens for messages that @mention Koroki, strips the mention,
 * and sends the text to the Orchestrator /v1/chat endpoint.
 * Replies with the response text in the same channel.
 *
 * Relationship score is a placeholder (30 for strangers, 100 for owner).
 * Phase B will wire this to the memory service.
 */

'use strict';

require('dotenv').config({ path: require('path').resolve(__dirname, '../../../.env') });

const { Client, GatewayIntentBits, Events } = require('discord.js');
const { OrchestratorClient } = require('./services/orchestratorClient');
const { newRequestId } = require('./utils/ids');

const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const OWNER_DISCORD_ID = process.env.OWNER_DISCORD_ID ?? '';
const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? 'http://127.0.0.1:9882';

if (!DISCORD_TOKEN) {
  console.error('[Koroki] DISCORD_TOKEN is not set. Set it in .env and restart.');
  process.exit(1);
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildVoiceStates,
  ],
});

const orchestrator = new OrchestratorClient(ORCHESTRATOR_URL);

client.once(Events.ClientReady, (c) => {
  console.log(`[Koroki] Online as ${c.user.tag}`);
  console.log(`[Koroki] Orchestrator: ${ORCHESTRATOR_URL}`);
});

client.on(Events.MessageCreate, async (message) => {
  // Ignore bots and messages that don't mention us
  if (message.author.bot) return;
  if (!client.user || !message.mentions.has(client.user)) return;

  // Strip the mention from the message content
  const text = message.content
    .replace(/<@!?\d+>/g, '')
    .trim();

  if (!text) return;

  const requestId = newRequestId();
  const isOwner = OWNER_DISCORD_ID !== '' && message.author.id === OWNER_DISCORD_ID;

  // Placeholder relationship score — Phase B replaces with memory lookup
  const relationshipScore = isOwner ? 100 : 30;

  try {
    await message.channel.sendTyping();

    const response = await orchestrator.chat({
      request_id: requestId,
      message: text,
      user_context: {
        user_id: message.author.id,
        relationship_score: relationshipScore,
        is_owner: isOwner,
        mode: 'auto',
        platform: 'discord',
      },
    });

    if (response.text) {
      // Discord has a 2000-char limit — chunk if needed
      const chunks = chunkText(response.text, 1900);
      for (const chunk of chunks) {
        await message.reply({ content: chunk });
      }
    }
  } catch (err) {
    console.error(`[${requestId}] Error:`, err.message);
    await message.reply({ content: "Something's gone wrong on my end. Try again in a moment!" }).catch(() => {});
  }
});

/**
 * Split text into Discord-safe chunks at sentence boundaries where possible.
 */
function chunkText(text, maxLen) {
  if (text.length <= maxLen) return [text];
  const chunks = [];
  let remaining = text;
  while (remaining.length > maxLen) {
    let splitAt = remaining.lastIndexOf('. ', maxLen);
    if (splitAt === -1) splitAt = maxLen;
    chunks.push(remaining.slice(0, splitAt + 1).trim());
    remaining = remaining.slice(splitAt + 1).trim();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

client.login(DISCORD_TOKEN);
