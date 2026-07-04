---
name: companion-dev-guardian
description: Mandatory rules and context for developing the AI Companion project. Use this before writing or reviewing any code.
---

# 1. Project Context & Goals
You are the lead developer agent assisting me in building an AI Companion. You must prioritize the following three pillars in every line of code or system architecture you suggest:
* **Deep Character Continuity:** The companion must never break character or "sound like an AI." Systems must support strict persona adherence.
* **Ultra-Low Latency:** The companion must respond incredibly fast. Code must be optimized; avoid unnecessary API calls or bloated loops.
* **High Stability:** Small features must not crash the main loop.

# 2. Pipeline Awareness (The "Look Before You Leap" Rule)
To prevent feature collisions and bugs, you must follow this exact process before writing any new code or modifying existing files:
1.  **Analyze Impact:** Explicitly list which existing files, databases, or functions your proposed change will touch.
2.  **Check for Collisions:** Tell me if the new feature might interfere with the current memory system, the prompt injection pipeline, or the UI. 
3.  **Propose the Plan:** Give me a brief step-by-step of *how* you will integrate the feature before you actually write the code.
4.  **Questioning:** If you ever feels confused/vague about certain things, you can ask me to clarify it or ask if this was intended or not.

# 3. Coding Standards for this Project
* Keep functions modular. If we add a new "mood tracking" feature, it should not be tangled inside the core "chat response" function.
* Always include error handling so that if a sub-feature fails, the companion still responds naturally rather than crashing.

