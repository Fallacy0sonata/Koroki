"""
Mood compositions — emergent emotions from hormone combinations.

These are NOT stored as state. They are *computed every tick* from the
current body state. The key insight from the research (Eric Kim review on
neuropsychology of fear/thrill/excitement; multiple sources cited in
master_queue.md endocrine entry):

  Same physiological arousal can feel like terror OR euphoria OR neither.
  The differentiator is the hormone *combination*, not any single component.

Specifically:
  - High NE alone → vague alertness
  - NE + high cortisol → anxiety / fear
  - NE + dopamine phasic + low cortisol → thrill / excitement
  - NE + dopamine phasic + high cortisol → fear (overrides excitement)
  - High cortisol + low oxytocin → tension, isolation
  - High oxytocin + low cortisol → safe, warm

This file implements those compositions. Interoception calls
`compose_mood_fragments` after collecting per-hormone fragments.

Why this matters for captain-in-cabin: the captain LLM reads "you feel
afraid" or "you feel excited" as a sensation. It can't *decide* which one
based on text — the body's combination tells it which one is happening.
"""

from __future__ import annotations


def compose_mood_fragments(
    raw_levels: dict[str, float],
    out: dict[str, list[str]],
) -> None:
    """Append composed mood fragments to `out` based on hormone combinations.

    `raw_levels` should contain the current effective levels of:
      cortisol, dopamine_tonic, dopamine_phasic, oxytocin,
      serotonin, norepinephrine, melatonin

    Missing keys default to 0 (handled safely).

    Composed fragments go into `out['mood']` and `out['body']` as appropriate.
    These complement (not replace) the per-component contributions.
    """
    cort = raw_levels.get("cortisol", 0)
    ne = raw_levels.get("norepinephrine", 0)
    dopa_p = raw_levels.get("dopamine_phasic", 0)
    dopa_t = raw_levels.get("dopamine_tonic", 0)
    oxy = raw_levels.get("oxytocin", 0)
    sero = raw_levels.get("serotonin", 0)
    mel = raw_levels.get("melatonin", 0)

    # ── Fear / anxiety: NE + high cortisol, no positive dopamine ──
    if ne > 0.55 and cort > 0.6 and dopa_p < 0.1:
        if cort > 0.75:
            out["mood"].append("a held-breath, edge-of-something fear")
            out["body"].append("breath shallow, ready to flinch")
        else:
            out["mood"].append("anxious — the kind that sits in the chest")

    # ── Thrill / excitement: NE + positive dopamine phasic + low-ish cortisol ──
    elif ne > 0.55 and dopa_p > 0.2 and cort < 0.55:
        if dopa_p > 0.35:
            out["mood"].append("a bright, alive thrill")
            out["body"].append("electric, leaning forward")
        else:
            out["mood"].append("an upward pull, something coming")

    # ── Safe warmth: high oxytocin + low cortisol + decent serotonin ──
    if oxy > 0.65 and cort < 0.45 and sero > 0.55:
        out["mood"].append("a low, durable safety")
        out["body"].append("shoulders truly loose")

    # ── Tense isolation: high cortisol + low oxytocin (no buffering) ──
    if cort > 0.65 and oxy < 0.25:
        out["mood"].append("a sense of being alone with it")

    # ── Sleepy contentment: melatonin + low cortisol + okay oxytocin ──
    if mel > 0.55 and cort < 0.4 and oxy > 0.4:
        out["mood"].append("the calm of about-to-rest")
        out["body"].append("a softening through the whole body")

    # ── Sleepy-but-restless: melatonin + cortisol both high (sleep deprivation pattern) ──
    if mel > 0.5 and cort > 0.6:
        out["mood"].append("tired-wired — body wants sleep, mind won't let go")

    # ── Bored/flat: low NE + low dopamine_tonic + low cortisol ──
    if ne < 0.25 and dopa_t < 0.3 and cort < 0.4:
        out["mood"].append("a blunt boredom, nothing pulling")

    # ── Engaged satisfaction: dopamine tonic moderate + serotonin moderate ──
    if 0.5 < dopa_t < 0.8 and sero > 0.55 and cort < 0.5:
        out["mood"].append("quiet engagement, things landing right")
