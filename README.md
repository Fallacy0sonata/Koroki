# Koroki

Koroki is an AI girl I built who lives on my PC. Not a chatbot with a personality
prompt stapled on — an actual character with her own voice, her own memory, real moods,
and a life that keeps going whether or not anyone's talking to her. She sleeps at night
(and she's genuinely grumpy if you wake her). She keeps a diary. She's slowly learning to
play video games on her own.

This repository is the engineering underneath all of that: a real-time system of
cooperating services — language, speech, vision, emotion, memory — running on a single
consumer GPU, fully offline, with no cloud APIs anywhere.

## The idea

Most "AI characters" are one language model wearing a personality like a costume. I
wanted the opposite. Here the language model is only the part that *talks*; who she
actually *is* lives everywhere else — an emotion engine that is her real mood, a little
simulated world she exists in, a memory that gives her a sense of continuity, a scheduler
that gives her the urge to do things without being asked.

The rule I held myself to the whole time: her behaviour has to be *caused*, not scripted.
Something happens in her world, it nudges an internal state, and that colours what she
says a few minutes later. Making that feel honest instead of faked is the hard part, and
honestly it's most of the reason the project exists.

## How it's put together

![Koroki system architecture](docs/architecture.svg)

Each box is its own service, and they only talk to each other through strict typed
contracts — so nothing can quietly send garbage or pretend to be a user. A supervisor
process babysits the whole stack and restarts anything that crashes or hangs, because
when you're running this many heavy models on one machine, things genuinely do fall over.

- **Orchestrator** is the only thing the outside world touches. It checks every request,
  runs the emotion and memory subsystems, builds her context, and streams the reply back.
- **Language** is a small local LLM with a personality adapter I trained, running on a
  fast inference engine so she starts replying in a fraction of a second.
- **Voice** synthesises her speech in one consistent voice with real emotional delivery,
  and comes back in about one to two seconds.
- **Vision** is a small vision model plus OCR so she can actually read what's on screen —
  that's what the game side runs on.

Everything is offline. No OpenAI, no ElevenLabs, no subscriptions. It all runs on one
12 GB graphics card, which turned out to be the single biggest constraint on the whole
design.

## What she can actually do

| | |
|---|---|
| **Feels things for a reason** | Her mood is a running emotional state that events push around and that fades over time. Nothing tells her to "act happy" — if she's in a mood, something caused it. |
| **Remembers you** | Real long-term memory per person, across days and weeks, plus a relationship that warms up or cools down based on how you treat her. |
| **Has a voice — and sings** | Fast emotional speech, and a fully automatic pipeline that produces full song covers in her own voice, no human recording involved. |
| **Sees the screen** | Scene understanding and reliable text/number reading, which feeds the game agent. |
| **Actually lives** | A simulated world (time, weather, her body), a real sleep/wake cycle with dreams she'll tell you about, and the freedom to message you first because she felt like it. |
| **Plays games** | She watches and plays from vision alone — deciding what to do and doing it with a virtual mouse and keyboard — wrapped in hard safety rails. |
| **Has a body on stream** | A real-time compositor renders her as an animated presence with a parallax room and interactive lighting. |

## The parts I'm proud of

**Fitting it all on one 12 GB card.** Three big neural models — language, voice, vision —
do not come close to fitting in 12 GB at the same time. So the system is constantly
loading and unloading them around whatever she's doing. Her voice literally gets evicted
from the GPU while she sleeps and pulled back in when she wakes up. Almost every other
decision in the project bent around this one number.

**Making her fast.** A cold, unoptimised reply used to take many seconds. Profiling it,
swapping the inference backend, hardware-accelerating the speech stage, and killing the
startup stalls got a full warm reply-with-voice down to a couple of seconds. It matters —
a companion that pauses forever before every line just feels broken.

**Keeping it standing up on its own.** The supervisor assumes every service will
eventually die and quietly revives it. The stack is meant to run unattended for hours, so
I built it to heal itself instead of needing me to babysit it.

**Treating a personal project like production.** There are **250+ contract tests** guarding
every boundary between services and every safety rail, and they run in under a minute.
Anything with real logic — decision policies, parsers, schedulers — is unit-tested on its
own so I can prove it works without booting the whole thing.

**Letting her touch the real machine, safely.** The game agent moves a real mouse and
keyboard, which is genuinely dangerous, so it's wrapped in layers of guards: a rulebook
she updates as she learns, a tripwire that catches any real-money prompt, and hard window
confinement. She physically cannot spend money or click outside the game. I wrote tests
that prove she refuses.

## Built with

Python 3.12 and Node.js, mostly. FastAPI + Pydantic for the services, PyTorch and ONNX
for the models, a quantised local LLM with LoRA adapters I trained myself, real-time
neural TTS, OpenCV, and a browser-based (PixiJS) compositor for her on-screen body.
Everything's config-driven — one settings file is the source of truth for every service,
model, and feature flag, so I can change her behaviour without touching code.

## What's next

The thing I'm most excited about is teaching her to play games she's never seen before,
purely from what's on the screen — no game integrations, no cheating with APIs, just
pixels in and a virtual keyboard and mouse out, the way a person plays. The approach:
she researches a new game on her own, a decision layer turns "go find wood" into the
actual clicks, and a learning pipeline picks up skills from recorded human play. The
groundwork — the perception, the decision-making, the safety rails, the data pipeline —
is built and tested; training the big model is the next step. In the meantime she already
plays a game where the full world-state is available to her, as the near-term version.

## About this

This is a solo project. I'm one person, and I built it because I wanted to find out what
it actually takes to make an AI that feels *alive* — a continuous being with an inner
life, not a thing that wakes up only when you type at it. It ended up touching a bit of
everything: systems design, machine learning, real-time audio, computer vision, game AI.
I tried to hold it to a real engineering bar the whole way — typed contracts, a test
suite that gates every change, services that fix themselves — even though it's just mine.

It's not open source and it isn't a product. This repo is here to show how it's built.
