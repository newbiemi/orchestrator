---
claude_sync: true
project: orchestrator
type: architecture
date: 2026-05-17
---

# Architecture: Orchestrator — Prompt Caching Two-Block System Prompt

## Overview
ClaudeNode assembles the system prompt as a two-block list at call time rather than a single string. This separates the stable base prompt (eligible for caching) from the volatile per-run memory block (not cached), so the Anthropic cache prefix is never invalidated by memory injection.

## Components
| Block | Content | Cache? |
|-------|---------|--------|
| Block 1 | `self.system` — static base prompt set at node construction | `cache_control: ephemeral` |
| Block 2 | `self.memory_context` — injected by `_inject_memory_context` each run | Not cached |

## Data Flow
1. `Pipeline._inject_memory_context()` checks the first node's type.
2. For `ClaudeNode`: writes to `node.memory_context` (separate attribute). Base `node.system` stays immutable.
3. For `OllamaNode`: appends memory to `node.system` string (Ollama has no cache concept).
4. `ClaudeNode.run()` assembles `system_blocks` list — Block 1 always present, Block 2 appended only if `memory_context` is set.
5. Cache metrics (`cache_creation_input_tokens`, `cache_read_input_tokens`) are extracted from `response.usage` and persisted to `runs.db`.

## Known Constraints
- Cache TTL is 5 minutes. Second run must happen within that window to get a cache hit.
- Minimum cacheable block size is 1024 tokens (Anthropic requirement). Short system prompts will never produce cache hits.
- `temperature` parameter is intentionally excluded from `messages.create()` — Anthropic SDK handles temperature via the `messages` call directly for standard models.
