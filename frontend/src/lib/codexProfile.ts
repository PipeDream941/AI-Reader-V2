import type { CodexModelInfo } from "@/api/types"

/** Reasoning levels offered when following the Codex default model (model = ""). */
export const DEFAULT_REASONING_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const

/** Chinese labels for Codex reasoning levels. */
export const REASONING_LEVEL_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "超高",
  max: "最大",
}

/** Levels that consume noticeably more membership quota and latency. */
export const QUOTA_HEAVY_LEVELS = ["high", "xhigh", "max"] as const

export function reasoningLevelLabel(level: string): string {
  return REASONING_LEVEL_LABELS[level] ?? level
}

export function isQuotaHeavyLevel(level: string): boolean {
  return (QUOTA_HEAVY_LEVELS as readonly string[]).includes(level)
}

export function findCodexModel(
  models: CodexModelInfo[],
  slug: string,
): CodexModelInfo | undefined {
  return models.find((m) => m.slug === slug)
}

/**
 * Reasoning levels selectable for a model slug.
 * "" (or an unknown slug) means "follow Codex default" and offers the full
 * default level set.
 */
export function supportedReasoningLevels(
  models: CodexModelInfo[],
  slug: string,
): string[] {
  const model = findCodexModel(models, slug)
  if (!model || model.supported_reasoning_levels.length === 0) {
    return [...DEFAULT_REASONING_LEVELS]
  }
  return model.supported_reasoning_levels
}

/**
 * Keep the current effort when the selected model supports it; otherwise fall
 * back to the model's default level, then to the first supported level.
 */
export function adjustReasoningEffort(
  models: CodexModelInfo[],
  slug: string,
  currentEffort: string,
): string {
  const levels = supportedReasoningLevels(models, slug)
  if (levels.includes(currentEffort)) return currentEffort
  const model = findCodexModel(models, slug)
  if (model && levels.includes(model.default_reasoning_level)) {
    return model.default_reasoning_level
  }
  return levels[0] ?? "low"
}
