# 自适应本书结构（Book Ontology）

## 目标

AI Reader 的人物、地点、事件、组织、物品和概念是跨作品稳定的核心结构。
“功法”“丹药”“门派谱系”“某一届榜单”或“成套名册”则可能只属于一本书，
不应继续扩张成全局固定枚举。本功能把这类结构保存为每本书独立、有版本、可
审核的 Book Ontology。

它解决的是结构化抽取，不是文学综合解读，也不改变 Codex 在项目中的工程改造
与任务监督职责边界。

## 运行流程

```text
逐章事实抽取
  └─ 输出轻量 ontology_observations
       └─ 本地 ChapterStructureScout（零模型调用）
            ├─ 弱信号：累计观察，继续原流程
            └─ 强信号：Structure Discovery Agent
                    ├─ 提出集合名称、字段、证据、预期数量
                    └─ Collection Population Agent
                           └─ 确定性 Reviewer
                                ├─ 通过：激活新版本
                                └─ 不通过：保留待审核候选
```

Scout 只寻找通用的结构证据，例如明确总数、重复槽位、分组标题和名册式排布；
它不包含任何作品名、人物名或答案名单。Discovery 只接收相关证据窗口，而不是
整章全文，以减少 token。Population 只按刚发现的字段抽取成员。

正式激活前必须满足：

- 成员数量等于正文声明的预期数量；
- 成员名称唯一，并且在来源章节中有精确文本证据；
- 每个成员都有所有已声明字段；
- 候选来自明确列举，而不是模型根据常识补齐；
- 置信度达到阈值。

未通过校验的结果不会静默进入正式结构。简繁体差异只允许通过来源文本反向
对齐；对齐器不能使用基准答案，也不会凭常识补写名字。

## 演化与纠错

每个 Book Ontology 都保存当前版本和历史版本。后续章节可以继续提出：

- 新集合或新字段；
- 已有集合的新成员；
- 字段合并、别名或弃用建议。

候选状态包括 `pending`、`active`、`rejected`。确定性校验通过时可自动激活；
否则必须人工审核。API：

- `GET /api/novels/{novel_id}/ontology`：当前正式结构；
- `GET /api/novels/{novel_id}/ontology/versions`：版本历史；
- `GET /api/novels/{novel_id}/ontology/proposals`：候选与审核报告；
- `POST /api/novels/{novel_id}/ontology/scan/{chapter_num}`：扫描指定章节；
- `POST /api/novels/{novel_id}/ontology/proposals/{proposal_id}/approve`：批准；
- `POST /api/novels/{novel_id}/ontology/proposals/{proposal_id}/reject`：拒绝；
- `GET /api/novels/{novel_id}/ontology/collections/{collection_id}/members`：集合成员。

本书结构、版本、候选和集合成员均随项目备份导出，并可在导入时恢复。

## 《水浒传》验收测试

测试语料使用维基文库袁无涯一百二十回本。下载脚本通过 MediaWiki API 保存
每回的页面修订号、内容哈希和整书来源清单；语料放在仓库外，避免把整部公版
小说提交进代码仓库。

```bash
python scripts/download_wikisource_novel.py \
  --page-prefix '水滸傳 (120回本)' \
  --book-title '水滸傳-袁無涯一百二十回本' \
  --chapters 120 \
  --output-dir /path/to/corpora/shuihuzhuan-120

cd backend
python scripts/build_water_margin_108_gold.py \
  --corpus /path/to/corpora/shuihuzhuan-120/水滸傳-袁無涯一百二十回本.txt \
  --output tests/fixtures/water_margin_108_roster_gold.json

python scripts/benchmark_book_ontology.py \
  --corpus /path/to/corpora/shuihuzhuan-120/水滸傳-袁無涯一百二十回本.txt \
  --gold tests/fixtures/water_margin_108_roster_gold.json \
  --provider codex --model gpt-5.6-terra --reasoning-effort low \
  --output /path/to/report.json
```

防止“偷看答案”的边界：两个生产 Prompt 不得出现作品名、梁山、108 将、天罡
或地煞；基准名单只在模型完成推理后由评分器加载。单元测试会检查这个边界。

2026-08-04 的真实 Codex 低推理强度验收中，Discovery 自行提出了一个预期
108 人的名册集合及星宿组别、星宿名号、绰号字段。最终结果为：108 个唯一成员，
姓名 precision/recall/F1、顺序准确率、星宿准确率和绰号准确率均为 1.0；无缺失、
无多余成员。第二次 Population 请求使用 33,081 tokens；Discovery 的候选被复用，
没有为了修正文字符形而重复发现结构。

这项基准证明“显式成套名册”可以被自然发现，不代表所有隐含体系都能一次完整
识别。没有明确总数或集中列举的结构应保持渐进积累，并由人工批准后再进入正式
版本。
