import type { Chapter } from "@/api/types"

export type ChapterGroupKind = "volume" | "bonus" | "unassigned"

export interface ChapterGroup {
  key: string
  kind: ChapterGroupKind
  volumeNum: number | null
  title: string
  chapters: Chapter[]
}

function describeChapterGroup(chapter: Chapter): Omit<ChapterGroup, "chapters"> {
  if (chapter.volume_num != null) {
    return {
      key: `volume-${chapter.volume_num}`,
      kind: "volume",
      volumeNum: chapter.volume_num,
      title: chapter.volume_title || `第${chapter.volume_num}卷`,
    }
  }

  if (/^(?:番外|后记|尾声|完本感言)/.test(chapter.title.trim())) {
    return {
      key: "bonus",
      kind: "bonus",
      volumeNum: null,
      title: "番外",
    }
  }

  return {
    key: "unassigned",
    kind: "unassigned",
    volumeNum: null,
    title: "未分卷",
  }
}

export function chapterGroupKey(chapter: Chapter): string {
  return describeChapterGroup(chapter).key
}

export function groupChapters(chapters: Chapter[]): ChapterGroup[] {
  const groups: ChapterGroup[] = []
  let current: ChapterGroup | null = null

  for (const chapter of chapters) {
    const descriptor = describeChapterGroup(chapter)
    if (!current || current.key !== descriptor.key) {
      current = { ...descriptor, chapters: [] }
      groups.push(current)
    }
    current.chapters.push(chapter)
  }
  return groups
}
