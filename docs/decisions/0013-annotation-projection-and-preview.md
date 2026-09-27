# 0013 标注投影、预览与按章处理

日期：2026-09-28 · 状态：已采纳（T11）

## 问题

T09/T10 已经把模型输出落成「当前标注」，但前端还看不到任何结果。T11 要决定：
阅读页与预览页怎么拿到颜色/编号、初读时怎么避免用后文证据提前同色、
预览结果怎样与正式处理复用同一份数据、以及界面切换怎样保证不产生任何模型调用。

## 选择

1. **单一投影端点**：新增只读 `GET /api/books/{id}/annotations`，返回范围内的
   条目、图例与统计。**不写数据库、不调用模型**：翻页、切范围、切阅读模式都只重读投影
   （已有测试对比请求前后的 `annotations`/`inference_runs` 行数不变）。
2. **投影即前端唯一事实来源**：颜色与编号只能来自后端；没有标注就不显示任何颜色或编号。
   `status=UNKNOWN` 不下发分组、不分配颜色；`stale`/`user_locked` 原样透出，留给 T12/T13。
3. **场景内稳定色号**：`color_index` 按分组在场景内的首次发言顺序取 0..N-1（与
   `speaker_groups.display_label` 的 S1/S2… 同源），超出色板时靠编号辨认。
4. **初读用 horizon 遮断后文证据**：候选查询参数 `visible_horizon_cp` 默认取请求范围末端；
   `visible_from_cp > horizon` 的条目下发 `withheld=true` 且 **label/color_index 均为 null**。
   重读（`reread`）不做遮断。这样“后文才揭示的身份”不会提前把两个声音合成一个颜色。
5. **预览与正式处理共用一套存储**：预览只是 `POST /api/jobs` 的 `mode=preview`，
   结果照常写 `annotations`，因此正式处理同范围会**命中 T10 的缓存**（不重复调用模型）。
   不引入任何临时识别表。
6. **编号是文本而不是伪元素**：前端把 `〔S1〕` 渲染成真实文本节点，跨节点的同一引语拆成多个
   `span` 但共享 `data-quote-id`；灰度打印或覆盖颜色时仍可辨认。
7. **切换视图零调用**：原文/标注切换、读模式切换、图例定位都只改前端渲染；
   E2E 与组件测试都对“切换后模型调用数不变”做了断言。
8. **幂等键用短摘要**：`idempotency_key` 有 128 字符上限，前端用范围/预算/配置的
   FNV-1a 短摘要构造键：同输入复用同一任务，不同输入才新建任务（preview 与 process 前缀不同，
   但缓存键相同）。

## 被放弃的方案

- **前端自己算颜色**：会让投影与后端状态漂移，也会在“未知/暂不显示”时凭空造出颜色。
- **预览另存一份临时结果**：既会重复计费，又会让预览与正式阅读不一致；违反 T11 的“直接复用”。
- **用 `visible_from_cp` 前端过滤而不下发 `withheld`**：模型可见性判断属于后端，
  前端只应渲染后端已经裁好的投影。
- **把未知也当一个匿名分组**：违反 PLAN「未知不制造新人」，会把几句无关对白并成同一个人。
- **用 CSS 伪元素画编号**：复制粘贴/灰度打印时编号会丢失，无法满足“编号可辨认”。

## 验证

- 后端：`pytest backend/tests` → **287 passed**。新增 `test_annotations_projection.py` 4 项
  （FakeProvider 默认 UNKNOWN 时无颜色/编号；有明确归属时给稳定色号且 horizon 之下不下发；
  投影只读、多次请求不新增标注与推理尝试；确定性 FakeProvider 端到端产生 S1 颜色）。
- 前端：`vitest` **41 passed**（新增 `DocumentRenderer` 标注 6 项：范围着色、真实文本编号、
  withheld 不着色、UNKNOWN 无颜色无编号、跨节点拆分、嵌套取最内层；`PreviewPage` 4 项；
  `ReaderPage` 新增 2 项）。`typecheck`/`build` 通过。
- E2E：`npm --prefix frontend run test:e2e` → **12 passed**，其中新增 `preview.spec.ts` 3 项：
  TXT 估算 → 试运行着色 → 视图切换零调用 → **正式处理 `calls=0` 且缓存命中窗口数等于窗口总数**；
  EPUB 预览同样着色（ruby 仍在 `<rt>` 里）；按章切换后范围与着色同步变化。
- 同时修复：预览页最初的幂等键直接把预算 JSON 拼进去，超过后端 128 字符上限导致 422
  （改为短摘要）；`settings.spec.ts` 原先假设配置列表为空，多个配置时会触发 strict mode 冲突
  （改为按名称限定到具体卡片，并用唯一名称）。
- **未验证（BLOCKED）**：真实提供方的预览效果与真实小说的着色质量（无凭据、无真实作品，属 T16）。

## 迁移与回滚

不新增迁移（沿用 T01 的 quotes/scenes/speaker_groups/annotations）。
回滚 = 删除 `scenes/projection.py`、`api/annotations.py`、`domain/annotations.py` 与前端预览页；
数据库里的标注是真实推理产物，保留。