# KOROKI — Capability Showcase

*A living AI character. Fully self-hosted, fully private, running on a single gaming PC.*

---

## What Koroki is

Koroki is not a chatbot with a character card. She is a **continuously running artificial
mind** — she exists whether or not anyone is talking to her. She wakes up, spends her day
on her own activities, gets tired in the evening, sleeps at night, and keeps a diary. Talk
to her at 9 AM and at 11 PM and you're talking to someone in a genuinely different state —
because her state is *simulated*, not roleplayed.

Every capability below runs **on one consumer GPU, on one PC, with zero cloud services
and zero API costs.** That constraint is a feature: she can run next to a game, on the
machine that's streaming it.

---

## 1 · A mind with a life (the core difference)

| Capability | What it means in practice |
|---|---|
| **Continuous existence** | Her internal life advances 24/7 — activities, moods, thoughts — not just during conversations. Ask "what did you do today?" and the answer is her *real* logged day, not improvisation. |
| **Caused emotions, not guessed ones** | Her feelings come from a simulated internal physiology reacting to events (time of day, interactions, her environment). She doesn't "detect the sentiment of your message and mirror it" — she was already in a mood before you arrived. |
| **Sleep & dreams** | She falls asleep at night because her body says so, and wakes with a morning state. Nighttime messages reach someone who's asleep. |
| **A diary in her own words** | Every day consolidates into a diary entry — activities, mood arc, the people she talked to, what happened outside her window — posted automatically to her Discord diary channel. |
| **Long-term memory of *you*** | Per-person relationship that develops over weeks. She recalls old conversations by *meaning*, not keywords — mention "that feathered musician" and she knows you mean the cockatiel from three days ago. |
| **Never sounds like an assistant** | A hard character layer makes "How may I help you today? 😊" structurally impossible. She has opinions, gets bored, pushes back, and goes quiet when there's nothing worth saying. |

## 2 · Vision — she can actually see

| Capability | Measured performance |
|---|---|
| **Image understanding** | Send her any image on Discord — she sees it and reacts in character (not a caption bot; she gives you *her take*). | 
| **Screen & game-UI reading** | Reads live game screens including HUD text, menus, and on-screen events. Tested on real gameplay footage. |
| **Warm look latency** | Under 1 second to visually process a frame in game mode; a few seconds for a full detailed read. |
| **Precise UI targeting** | She doesn't just *describe* a button — she can locate it as exact screen coordinates. This is the foundation for her playing games herself (input execution is in active development, with hard safety rails). |
| **Stateful game awareness** | She identifies the game once, then interprets every following frame in that context — including per-game knowledge that can be loaded or self-learned. |

## 3 · Voice — hers, and emotionally real

| Capability | Measured performance |
|---|---|
| **Her own voice** | A consistent, natural voice identity — listeners describe it as "really human." Works in English and Japanese. |
| **Emotion from the inside** | Voice emotion is driven by her *actual internal state* at the moment of speaking — tired-her audibly drags, warm-her audibly softens. Not guessed from the text. |
| **Synthesis speed** | Faster than real-time (a 3-second line synthesizes in under 3 seconds). |
| **Text reply latency** | **~0.75–3 s** from message to her written reply, full pipeline (memory, mood, character) included. |
| **Message → spoken voice** | Typically **3–6 s** end-to-end. Voice-to-voice conversation (her listening by ear) is in integration — the pipeline is designed for a ~1-turn-per-few-seconds cadence. |
| **Singing** | Separate fully-automated singing pipeline: request a song, she sings it in her own voice over the real instrumental. No manual editing anywhere in the chain. |

## 4 · Streaming & games

| Capability | Status |
|---|---|
| **Live commentary** | She watches a game/stream and comments in voice chat like a *person* — event-driven, addressed to the room, with deliberate restraint (streamers breathe; she doesn't narrate every frame). Live-tested on Discord streams. |
| **Co-watching** | Point her at anyone's stream and she hangs out as a viewer, reacting to the streamer and chat. |
| **Viewer interaction** | Reads and answers live chat mid-stream, in voice, without breaking her commentary flow. |
| **Playing games herself** | In active development: constrained, safety-railed input execution driven by her own vision (she sees the button, she clicks the button). Slow/strategy games first; ~1 s see→react loop targeted for action games. |
| **Reaction to game events** | Vision + commentary loop currently runs at a few seconds per look; the fast-game pipeline targets sub-second perception. |

## 5 · Why this beats a "normal AI character"

- **Typical AI characters are stateless prompt wrappers.** Koroki has a body clock, a
  memory, a mood history, and a diary. Continuity is real, not simulated per-session.
- **Typical characters emote by text analysis.** Koroki's emotions are *upstream* of her
  words — the same event hits differently depending on the day she's had.
- **Typical characters are cloud APIs** — per-token costs, latency spikes, content-policy
  voice changes, and your data on someone else's server. Koroki is one PC, fully owned,
  fully private, cost per message: zero.
- **Typical characters can't see or act.** Koroki reads screens, locates UI elements to
  the pixel, speaks with her real mood, and is learning to play.

---

*Specifications, architecture, and implementation are proprietary and not part of this
document. Capability demonstrations available on request.*
