# 从截图核价工具到 AI 应用工程项目

## 最新版：DeepSeek + RAG 已接通

本轮已完成一次真实 DeepSeek V4 Flash 合成问题联调：201 输入 tokens、34 输出 tokens，总计 235；没有发送截图或真实物资。这与下面历史描述“尚未验收真实模型”不同，以此节为准。

使用步骤：打开本机网页 → 右上角“AI 模型设置”（已有密钥加密保存，无需重填）→ 展开“AI 分析”→ 勾选使用已配置模型和允许发送本次文字 → 输入问题并点击“分析当前物资”。保存模型设置不会发请求。默认不勾选时只用本地检索与规则，不消耗模型额度。支持关闭、DeepSeek 和已自行安装的本机 Ollama。

可以问“这张图有哪些可以替换的物品？说明价差依据”“为什么这把枪不建议丢弃？”“扑克牌没有报价怎么办？”。模型根据本地知识、已录入物资和计算器结果生成解释并返回引用；它不看图片，不自动识别隐藏配件，不修正 OCR，不编造市场价。上下文有长度限制，未必包含全部物资，完整排名以价格表为准。

当前业务流程默认接受识别，取消常驻待核对界面；错误时点物品纠错。建议不再等待每件物品人工确认，采用 CP-SAT 不重复一换一匹配，计算当前价格/占格数假设下的最大合计价差。没有二维放置与多件换一件；实际能否放下仍由用户判断。未知、缺价、不完整整枪不参与替换，锁定/安全箱/身上装备不会作为换出项。

| 新增文件 | 原理与学习重点 |
|---|---|
| `src/delta_loot_assistant/llm_settings.py` | 配置与密钥分离、Windows DPAPI、服务商/模型白名单、接口不回显密钥 |
| `src/delta_loot_assistant/deepseek_provider.py` | 固定端点适配器、一次请求、JSON 输出合同、引用校验、限长与脱敏错误 |
| `tools/configure_deepseek.py` | 无回显输入密钥、一次显式合成测试；不能把真实 key 写进命令行 |
| `tools/update_prices_once.py` | 付费调用预算、同步前备份、24 小时保护与显式例外、端点调用审计 |
| `tests/test_llm_and_swaps.py` | 假 API 合同测试、密钥泄露防护、随机小规模匹配与暴力枚举对照 |

AI 接轨的核心不是多装几个库：检索器提供证据，确定性工具计算钱，LangChain LCEL 编排数据流，模型负责解释，输出校验和降级保护业务结果。可以学习 RAG、状态版本、权限边界、结构化输出、引用追溯和评估，但不能把它写成自主操作游戏的 Agent。

下一步建议：先人工标注一批多布局截图做准确率评估；再增加配件详情图与指定枪械实例关联；最后考虑多模态模型提出识别候选（需要另行选支持图片的模型、明确图片出云授权并测成本）。现有 DeepSeek 文本接入不能自动解决视觉漏识别。当前 BM25 是稀疏检索，不是向量数据库；是否增加 embedding 应用真实检索测试决定。

接口参考 [DeepSeek 对话接口](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [JSON 模式](https://api-docs.deepseek.com/guides/json_mode/)。聊天里出现过的密钥建议联调后轮换，再在设置窗口更新。以下内容保留原学习结构；涉及仅本机模型、逐项确认、独立备选的旧描述以本节为准。

## 先运行什么

打开本机网页，上传原图或点“增强补扫”。范围选“全部物品（身上＋地上）”，先看参考价格与来源标签。建议区给出地上值得关注、先保留、背包已核对低价项；这些不是自动丢弃指令。

本机当前已安装可选 LangChain 依赖。在另一台开发电脑复现时，项目根目录安装命令为 `.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai]"`；不装 `ai` 也能运行基础网页和 Python 降级工作流，相关 LangChain 专属测试会跳过。安装 Python 包不等于安装大语言模型。

展开“检索助手”，输入物品/估价问题并点击按钮。默认就能看到知识检索结果、来源和工具执行记录，不用买 Key。需要 LLM 时才勾选本机 Ollama，填写自己已安装的模型名；没有本机服务或模型会明确降级，不自动下载。当前工作已验证本地检索与 LangChain，真实模型生成尚未验收。

## 代码与数据如何流动

```text
上传图片 → 分块放大 OCR → 坐标合并 / 朝向与边框候选
                              ↓
                       目录 ID → 本地价格快照 → 参考排序
                              ↓ 用户纠错、确认、锁定
                       确定性比较工具 → 条件建议

问题 → BM25 检索规则/目录 → 读取当前会话的比较结果
                          ↓
                  LangChain LCEL 工作流
                   ├─ 默认：展示证据与规则结果
                   └─ 可选：本机 LLM → JSON 校验 / 引用校验 → 解释
```

视觉识别不使用 LLM/RAG；知识检索也不替代价格关联。价格必须来自本地物品 ID 对应的快照或用户手工覆盖。

## 每个新模块的作用

| 源文件（相对项目根目录） | 作用与可学习概念 |
|---|---|
| `src/delta_loot_assistant/spatial_recognition.py` | 重叠分块、放大、坐标反变换、空间去重、角标朝向和边框检测。是计算机视觉工程，不是训练新的模型 |
| `src/delta_loot_assistant/adaptive_recognition.py` | OCR 名称与目录候选关联、区域推断、数量估计、未知框；不能凭候选数量证明准确率 |
| `src/delta_loot_assistant/advisor.py` | 确定性业务工具：过滤不可靠项、保护锁定、计算替换差价。没有二维布局求解 |
| `src/delta_loot_assistant/knowledge.py` | 中文双字词与英文数字分词、BM25 稀疏索引、Top-K 检索、文档 ID/来源。没有向量模型依赖，也不应称为向量数据库 |
| `src/delta_loot_assistant/agent_workflow.py` | LCEL 三阶段编排、只读会话工具、本机 LLM 适配器、结构化输出、引用校验、超时降级、禁用外部追踪 |
| `src/delta_loot_assistant/web_review.py` | 会话状态、版本校验、后台补扫、纠错保护、并发建议锁 |
| `src/delta_loot_assistant/web_server.py` | 本机 HTTP API、同源/CSRF 检查、请求限长、端口独占；没有付费同步路由 |
| `tests/test_spatial_recognition.py` | 位移、旋转、去重、框越界范围、模型输出合同测试 |
| `tests/test_web_review.py` | 全部范围、保护条件、价差、检索调用、LLM 失败降级、接口安全与会话一致性 |
| `tools/evaluate_recognition.py` | 同图、同数据的候选和耗时对照，不保存整图，不产生 API 费用 |
| `tools/restart_web_preserving_session.py` | 维护工具：先在内存接收旧会话/图片，再等待旧服务退出并启动新版；不强制关闭进程 |

## RAG、LLM、Agent、LangChain 到底用了哪些

- **检索**已经能运行：知识库由 6 条项目规则文档和本地目录/价格条目构成，当前截图关键物品作为额外证据。BM25 检索分数只表示相关程度，不是正确概率。
- **RAG**的生成路径已实现：检索证据送给本机 LLM，模型返回解释和引用。未启用 LLM 时，准确叫“检索＋规则工作流”，不要声称已经发生生成式 RAG。
- **LangChain**不是只写在简历里：实际用 `RunnableLambda` 与 LCEL `|` 构造并调用流程，页面能看到编排模式和执行记录。仅增加可选 `ai` 依赖，不把它强塞进桌面核心。
- **Agent 工程基础**包括工具边界、状态版本、人工确认、可观察记录、失败降级、输出校验和回归评估。本版没有让 LLM 自主挑工具、多轮计划或操作游戏，应叫“受控工作流”，不是自主 Agent。

API 与编排写法参考 [LangChain Runnable 文档](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable)；生成请求的 JSON 格式参考 [Ollama 结构化输出](https://docs.ollama.com/capabilities/structured-outputs) 和 [Chat API](https://docs.ollama.com/api/chat)。

## 为什么 LLM 不负责算价格

模型可能编造数字、误读配件或服从检索文本中的恶意指令。这里的金额、保护条件和替换比较由 Python 计算，模型只能生成解释；输入文本按不可信数据处理，结果用纯文本展示，不执行任何输出。引用 ID 校验只检查引用存在，不证明模型每句话都得到支持。

Ollama 请求固定为本机地址，禁止使用环境代理和重定向；单次读取上限 64 KB，超时 40 秒，无自动重试。LangSmith 云追踪显式禁用。截图与付费 Token 不进入模型上下文。服务端只允许单个建议任务，避免重复占用本机资源。

## 怎么验证，而不只看演示

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
node --test tests/web_preview.test.cjs
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests tools
.\.venv\Scripts\python.exe tools/evaluate_recognition.py '自己的原图路径.png' --enhanced
```

后续应建立人工真值集，分别统计检测 Precision/Recall、名称 Top-1、弹药数量准确率和推理耗时分位数。新旧候选数量不能代替这些指标。真实截图不进 Git；标注训练与跨图纠错检索仍未实现。

本轮验证记录（2026-09-08）：88 项 Python 测试、7 项前端逻辑测试通过，Ruff、JS 语法检查与依赖兼容检查通过。真实本机会话保留原 32 项（含 1 项手动录入）后补扫至 45 项，其中 4 项未知，原有 1 项已确认记录保留。此数量是补扫后的会话覆盖，不是 45 项全对；真实原图增强识别测得约 30 秒，尚未达到最初 2 秒性能目标。HTTP 路由和本地 LangChain 检索链已实际运行；未进行浏览器点击验收、真实 LLM 推理或大规模准确率测试。真实价格库修改时间未变，未调用付费接口。

第二张位置不同的用户样图 `02.png` 离线增强评估：35 个候选，其中 5 个未知，耗时 27.26 秒；没有替换网页当前会话或保存图片副本。它只是跨布局冒烟测试，不是跨分辨率/任意视角精度验收。

## 建议的学习顺序

1. 看 `Snapshot.item()`，搞清“图 → 物品 ID → 单价 × 数量”。
2. 看 `build_advice()` 和测试，用两件物品算差价，理解为什么缺价/锁定不能推荐丢弃。
3. 看 BM25 分词和检索输出，修改一个问题观察来源变化。
4. 看 LCEL 三个节点和 trace，理解工作流不等于 LLM 自主 Agent。
5. 本机有模型后开启 LLM，检查模型解释、引用与失败降级；先不要接云端或传截图。
6. 下一阶段再做标注集、图标重排、向量检索/纠错记忆、枪械配件结构化解析与二维优化。

简历可描述“实现本地 OCR＋价格快照的物资估价应用，接入 BM25 知识检索和 LangChain 受控工作流，设计人工纠错、引用校验及降级机制”。不要写尚未验证的识别准确率、生产级规模或自主决策能力。
