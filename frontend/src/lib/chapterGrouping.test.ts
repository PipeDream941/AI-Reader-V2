import { describe, expect, it } from "vitest"
import type { Chapter } from "@/api/types"
import { chapterGroupKey, groupChapters } from "./chapterGrouping"

function chapter(
  chapterNum: number,
  title: string,
  volumeNum: number | null,
  volumeTitle: string | null = null,
): Chapter {
  return {
    id: chapterNum,
    novel_id: "novel",
    chapter_num: chapterNum,
    volume_num: volumeNum,
    volume_title: volumeTitle,
    title,
    word_count: 100,
    analysis_status: "pending",
    analyzed_at: null,
  }
}

describe("chapter grouping", () => {
  it("keeps volumes, bonus chapters, and accidental gaps visibly separate", () => {
    const groups = groupChapters([
      chapter(1, "第一章", 1, "第一卷 少年侠气"),
      chapter(2, "第二章", 1, "第一卷 少年侠气"),
      chapter(3, "标题缺少分卷", null),
      chapter(4, "番外（一）", null),
      chapter(5, "番外（二）", null),
    ])

    expect(groups.map((group) => [group.key, group.title, group.chapters.length]))
      .toEqual([
        ["volume-1", "第一卷 少年侠气", 2],
        ["unassigned", "未分卷", 1],
        ["bonus", "番外", 2],
      ])
  })

  it("uses the same stable key when auto-expanding a chapter group", () => {
    expect(chapterGroupKey(chapter(1, "正文", 8))).toBe("volume-8")
    expect(chapterGroupKey(chapter(2, "番外 后日谈", null))).toBe("bonus")
  })
})
