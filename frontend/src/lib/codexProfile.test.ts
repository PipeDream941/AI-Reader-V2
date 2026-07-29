import { describe, it, expect } from "vitest"
import type { CodexModelInfo } from "@/api/types"
import {
  DEFAULT_REASONING_LEVELS,
  adjustReasoningEffort,
  isQuotaHeavyLevel,
  reasoningLevelLabel,
  supportedReasoningLevels,
} from "./codexProfile"

const luna: CodexModelInfo = {
  slug: "gpt-5.6-luna",
  display_name: "GPT-5.6 Luna",
  description: "fast structured extraction",
  default_reasoning_level: "medium",
  supported_reasoning_levels: ["low", "medium", "high", "xhigh", "max"],
}

const limited: CodexModelInfo = {
  slug: "gpt-mini",
  display_name: "GPT Mini",
  description: "",
  default_reasoning_level: "low",
  supported_reasoning_levels: ["low", "medium"],
}

describe("reasoningLevelLabel", () => {
  it("maps all levels to Chinese labels", () => {
    expect(reasoningLevelLabel("low")).toBe("低")
    expect(reasoningLevelLabel("medium")).toBe("中")
    expect(reasoningLevelLabel("high")).toBe("高")
    expect(reasoningLevelLabel("xhigh")).toBe("超高")
    expect(reasoningLevelLabel("max")).toBe("最大")
  })

  it("passes through unknown levels", () => {
    expect(reasoningLevelLabel("ultra")).toBe("ultra")
    expect(reasoningLevelLabel("")).toBe("")
  })
})

describe("supportedReasoningLevels", () => {
  it("returns the full default set for the unpinned default model", () => {
    expect(supportedReasoningLevels([luna], "")).toEqual([...DEFAULT_REASONING_LEVELS])
  })

  it("returns the default set for an unknown slug", () => {
    expect(supportedReasoningLevels([luna], "no-such-model")).toEqual([
      ...DEFAULT_REASONING_LEVELS,
    ])
  })

  it("returns the model's own supported levels", () => {
    expect(supportedReasoningLevels([luna, limited], "gpt-mini")).toEqual(["low", "medium"])
  })

  it("falls back to the default set when the model lists no levels", () => {
    const empty = { ...luna, supported_reasoning_levels: [] }
    expect(supportedReasoningLevels([empty], "gpt-5.6-luna")).toEqual([
      ...DEFAULT_REASONING_LEVELS,
    ])
  })
})

describe("adjustReasoningEffort", () => {
  it("keeps a supported effort unchanged", () => {
    expect(adjustReasoningEffort([luna], "gpt-5.6-luna", "high")).toBe("high")
    expect(adjustReasoningEffort([luna, limited], "gpt-mini", "medium")).toBe("medium")
  })

  it("falls back to the model's default level when the effort is unsupported", () => {
    expect(adjustReasoningEffort([luna, limited], "gpt-mini", "xhigh")).toBe("low")
  })

  it("falls back to the first supported level when the model default is unsupported", () => {
    const odd: CodexModelInfo = {
      ...limited,
      default_reasoning_level: "xhigh",
      supported_reasoning_levels: ["medium", "high"],
    }
    expect(adjustReasoningEffort([odd], "gpt-mini", "low")).toBe("medium")
  })

  it("keeps a valid effort for the unpinned default model", () => {
    expect(adjustReasoningEffort([luna], "", "xhigh")).toBe("xhigh")
  })

  it("falls back to low for an invalid effort on the unpinned default model", () => {
    expect(adjustReasoningEffort([luna], "", "bogus")).toBe("low")
  })
})

describe("isQuotaHeavyLevel", () => {
  it("flags high/xhigh/max only", () => {
    expect(isQuotaHeavyLevel("low")).toBe(false)
    expect(isQuotaHeavyLevel("medium")).toBe(false)
    expect(isQuotaHeavyLevel("high")).toBe(true)
    expect(isQuotaHeavyLevel("xhigh")).toBe(true)
    expect(isQuotaHeavyLevel("max")).toBe(true)
  })
})
