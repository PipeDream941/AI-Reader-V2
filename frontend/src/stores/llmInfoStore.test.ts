import { describe, expect, it } from "vitest"

import { formatLlmLabel } from "./llmInfoStore"

describe("formatLlmLabel", () => {
  it("distinguishes Codex from local and cloud providers", () => {
    expect(formatLlmLabel("qwen3:8b", "ollama")).toBe("qwen3:8b（本地）")
    expect(formatLlmLabel("deepseek-chat", "openai")).toBe("deepseek-chat（云端）")
    expect(formatLlmLabel("codex-default", "codex")).toBe("codex-default（Codex）")
  })
})
