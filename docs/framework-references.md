> 2026-10-29状态：下文为前期项目／官方文档研究，包含当时未实现的描述；当前实现、直接回调协议和验证边界以根README及docs/validation.md为准。

# 框架参考与复用边界

更新：2026-10-25。本文记录源码与官方文档支持的设计依据，区分直接复用、借鉴结构和需要自写的业务。下列项目是研究对象，不以项目名或知名度代替对具体代码的核对。

用户要求系统复读官方文档后再讨论。最新 [Crawlab 官方文档复核](crawlab-official-review.md) 以实际体验的 Community 0.6.3 为范围，区分归档 0.6、2023 文档和官网 0.7；纠正数据源集成／SQLite 的版本边界，确认原生程序、定时、节点、任务、日志和结果管理优先复用。此前新分支和新 SDK 研究仅供对照，不能套到已部署镜像；旧表若与复核冲突，以版本明确的复核为当前依据，自建流程仍待收缩审阅。

## 优先验证的组合

Crawlab 管跨设备执行与固定周期，Scrapy 管来源网页采集，旧 Proxypool 的 aiohttp 测试逻辑改造后继续使用，Proxypool 服务只补来源管理、按目标网站评价、批量分发与反馈。

这是替代上一版通用自建框架的建议，尚未选定发行版本或验证部署。先验证一条最小链路，再决定采用范围，不因引入 Scrapy 要求其他所有爬虫一起迁移。

## 逐项依据

| 需求 | 参考与核对范围 | 采用方式与边界 |
| --- | --- | --- |
| 一个网站一份抓取代码 | [Scrapy Spider](https://docs.scrapy.org/en/latest/topics/spiders.html)；[proxy_pool BaseFetcher](https://github.com/jhao104/proxy_pool/blob/9cc0cad4c47e84e34aaec2eead099421960dcd07/fetcher/baseFetcher.py) | 优先采用标准 Spider，来源代码写 start_urls／start 与 parse；不另定义 build_requests 协议 |
| 下载、重试、并发、解析与结果处理 | [Scrapy 架构](https://docs.scrapy.org/en/latest/topics/architecture.html)、[重试中间件](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#scrapy.downloadermiddlewares.retry.RetryMiddleware) | 使用原生 Downloader、设置及 Pipeline，不自写 http_fetcher 与抓取执行引擎 |
| 单文件来源与显式启动 | [Scrapy runspider 源码](https://github.com/scrapy/scrapy/blob/171ccbedb734c988fd86708fd5e0803029b6c730/scrapy/commands/runspider.py) | 研究提交使用 DummySpiderLoader，并只导入传入文件；我们先核对来源登记，再选择指定文件运行，不接受任务中任意路径 |
| 热插拔 | [proxy_pool 动态发现与 reload](https://github.com/jhao104/proxy_pool/blob/9cc0cad4c47e84e34aaec2eead099421960dcd07/helper/fetch.py) | 借鉴来源独立及动态启停；不照搬其目录扫描导入，不在主服务中复刻 mtime／reload 框架 |
| 固定抓取周期与启停 | [Crawlab Schedule 模型](https://github.com/crawlab-team/crawlab/blob/ee11cd78eceabb6b4b521b4040d8b5969f4abbef/core/models/models/schedule.go)、[调度服务](https://github.com/crawlab-team/crawlab/blob/ee11cd78eceabb6b4b521b4040d8b5969f4abbef/core/schedule/service.go) | 复用 Cron、Enabled、节点和参数配置；不另写来源周期调度器。研究代码用分钟级标准 Cron，不能假设支持任意秒级间隔 |
| 节点、任务、日志 | [Crawlab 发布接口](https://github.com/crawlab-team/crawlab/blob/ee11cd78eceabb6b4b521b4040d8b5969f4abbef/core/controllers/task.go#L203) | 业务保存平台任务关联，不再另造 job／attempt／lease 状态机 |
| 结果回传与查询 | 本轮补充 [新版 Python SDK Pipeline](https://github.com/crawlab-team/crawlab-python-sdk/blob/167b84859b57ac247e3409b5ef09c1e1ad5ddac2/crawlab/core/scrapy/pipelines.py)、[任务结果 API](https://github.com/crawlab-team/crawlab/blob/ee11cd78eceabb6b4b521b4040d8b5969f4abbef/core/controllers/task.go#L375)；旧 SDK 作历史对照 | 新 SDK 使用 stdout IPC，消息字段与研究平台接收结构吻合，仍需配套镜像／SDK／Scrapy 联调；不混入旧 SDK gRPC 入口 |
| 来源健康采集 | [Scrapy Stats](https://docs.scrapy.org/en/latest/topics/stats.html)、[Signals](https://docs.scrapy.org/en/latest/topics/signals.html) | 使用请求、响应、提取、错误及关闭事件；默认统计在内存，仍需薄适配持久保存，不能当作已自动进 Crawlab |
| 结果标准化与导出 | [Item Pipeline](https://docs.scrapy.org/en/latest/topics/item-pipeline.html)、[Feed exports](https://docs.scrapy.org/en/latest/topics/feed-exports.html) | 校验用 Pipeline，JSONL 用原生 FeedExporter；不手写一套通用序列化器 |
| 代理对象字段参考 | [proxy_pool Proxy](https://github.com/jhao104/proxy_pool/blob/9cc0cad4c47e84e34aaec2eead099421960dcd07/helper/proxy.py) | 参考地址及检查信息；用户已明确不保存逐代理来源，不采纳来源关系或跨轮来源合并；时间字段仍留 M05 审阅 |
| 按目标分别记录质量与时间 | [haipproxy Pipeline](https://github.com/SpiderClub/haipproxy/blob/ab30ccf4b1d78e9304c27830006cc5800fe41bb3/haipproxy/crawler/pipelines.py) | 借鉴 score、verified_time、response_time 分开；不照搬其加减分、删除和旧依赖 |
| 实际使用反馈和自测联动 | 现有三个代理项目均未在本轮核对范围内提供完全符合要求的实现 | 这是保留的自写业务：统一观测、去重、按网站评价、按近期使用规划自测 |

## 不把已有项目整套搬来

proxy_pool 的校验有全局失败次数和阈值删除逻辑，见 [DoValidator 与 Checker](https://github.com/jhao104/proxy_pool/blob/9cc0cad4c47e84e34aaec2eead099421960dcd07/helper/check.py)。它不能直接代替我们的逐目标成功率和反馈闭环。

haipproxy 已采用 Scrapy 与 Pipeline，其结构值得借鉴；历史提交及老式 Redis API 不应作为当前依赖模板，也不使用其低分删除来替代用户明确选择的质量降权。

Crawlab 自带执行管理，我们不再为它另做通用任务领取、执行尝试、节点租约和结果确认服务。必要的来源重叠检查、结果幂等入库是业务约束，验证确有缺口时才补最小适配。

## 代理测试继续复用 aiohttp 的理由

代理测试是在同一目标上逐个更换代理，结果要和调用方的一次真实访问对齐。Scrapy 的默认 URL 去重和重试不直接等于这种语义，见 [请求指纹及 dont_retry](https://docs.scrapy.org/en/latest/topics/request-response.html#request-fingerprints)。

因此不为了统一框架而重写测试器。先修复旧实现每个组合独立建 Session 导致总并发失控的问题，复用任务内共享客户端和明确总并发；网站采集的自动重试不悄悄变成代理测试的多次成功兜底。

## 原始 HTML 与跨设备解析

Scrapy 的 Response 在抓取进程内，普通 Feed exports 导出的是 Item，不能据此宣称 HTML 已持久保存。跨设备解析时仍将原文保存到服务器文件目录，以 ID 引用；只补具体文件存取和解析脚本，不建设通用 artifact 平台。

抓取和解析先在同一任务中跑通。分步模式保留为需求，在后续阶段验证 A 抓取、B 解析。复杂 Spider 可能继续产生 Request，不能假设任意 parse 都能直接变成离线解析任务。

## 版本与验证界限

研究代码固定为：Scrapy 171ccbedb734c988fd86708fd5e0803029b6c730，Crawlab ee11cd78eceabb6b4b521b4040d8b5969f4abbef，proxy_pool 9cc0cad4c47e84e34aaec2eead099421960dcd07，haipproxy ab30ccf4b1d78e9304c27830006cc5800fe41bb3，Crawlab SDK 2efb7e0c6830b341274d5b7d337dd1ad07b452ae。它们分别用于研究，不是一套已验证的软件组合。

Scrapy latest 文档在本轮显示 2.19.0，实际使用前仍选择发行版本并核对 Python、方法签名及 SDK 兼容。源码能力不等于端到端可靠性；平台分页、结果迟到、程序异常和节点断连须在隔离环境核验。未进行安装、运行采集或部署。

若原生结果通道验证不满足需要，再比较一次受控业务上传或 JSONL 文件导入；不预先实现两套回传，也不将普通 stdout 当作 Crawlab 结果。

## 存储与评价的补充研究

2026-10-24 用户要求将代理存储、更新、删除和交互协议具体化。本轮补充核对上述固定提交的 proxy_pool 校验／Redis Hash 与 haipproxy Pipeline，并读取原始 issue 和作者回复；据此提出 [存储方案](../apps/proxypool/storage.md) 和 [协议 v1](protocol.md)。它们是完整推荐稿，尚未取得用户逐项确认或运行验证。

| 参考 | 得到的依据 | 不能据此声称的内容 |
| --- | --- | --- |
| [proxy_pool #512](https://github.com/jhao104/proxy_pool/issues/512) | 实际使用者反对一次失败就清除，提出比例评价；作者确认当时默认失败容忍数为 0 | 其报告的吞吐和 10% 参数不是我们验证的结论，不照搬生命周期累计失败比 |
| [proxy_pool #729](https://github.com/jhao104/proxy_pool/issues/729) | 用户提出代理时效性，说明发现、最近验证和过期需要分别表达 | 没有验证代理通用寿命；不能把需求帖当现成功能 |
| [haipproxy #70](https://github.com/SpiderClub/haipproxy/issues/70) | 原始讨论关于验证时间更新，作者指向 validators/base.py 的 set_item_queue | 不将问题标题推断为所有版本均有同一故障 |
| [Wilson 排名分析](https://www.evanmiller.org/how-not-to-sort-by-average-rating.html) | 比较纯成功比例会偏好小样本，标准下界可用于保守排名 | 代理相关观测不满足严格独立假设，score 不等于下一次访问概率，也不是代理池专用实测算法 |
| [SQLite 官方选型说明](https://www.sqlite.org/whentouse.html)、[WAL](https://www.sqlite.org/wal.html)、[事务](https://www.sqlite.org/lang_transaction.html) | 本地应用服务器、单写短事务、WAL 读写并行的适用条件 | 不支持 Worker 跨设备共享数据库文件，不保证未压测业务的吞吐 |
| [UPSERT](https://www.sqlite.org/lang_upsert.html)、[外键](https://www.sqlite.org/foreignkeys.html)、[同步设置](https://www.sqlite.org/pragma.html#pragma_synchronous)、[在线备份](https://www.sqlite.org/backup.html) | 原生唯一性、引用、提交持久性及一致备份能力 | 去重冲突处理和多表事务仍是业务工作；不是已经完成配置或备份 |

SQLite WAL 和现成 Wilson 算法是本次根据个人单服务器部署方式做出的组合判断，不宣称某个开源代理项目已经给出完全相同方案。用户现已接受 SQLite，并要求历史有数量上限；方案改为每组合有限近期样本与独立短期凭证，撤回 30d 全量访问历史。条数、时间、合批阈值与容量都属建议，字段和增删网站语义见存储方案。外键／唯一键／事务直接复用 SQLite 能力，不创建自有存储框架。原生结果兼容性仍须实测。

容量补充：本轮进一步按流入速率×保留时间核算凭证和批次，提出 24h 反馈／固定 48h 保留、行数与空间两类上限；这些是建议参数，不是 SQLite 实测上限。[max_page_count](https://www.sqlite.org/pragma.html#pragma_max_page_count) 只限制主库；[WAL](https://www.sqlite.org/wal.html) 描述长读事务对 checkpoint 的影响；[journal_size_limit](https://www.sqlite.org/pragma.html#pragma_journal_size_limit) 不构成活跃 WAL 的绝对硬上限；[dbstat](https://www.sqlite.org/dbstat.html) 可测表和索引页面占用但取决于编译选项。性能验证应覆盖我们的去重、样本修剪、汇总与分发写入，不引用无关纯插入跑分当容量保证。

## 逐 URL 可靠完成补查

2026-10-24 用户明确提出输入库中已抓／在抓／永久卡住的识别问题，不能只用平台 finished 或“以后核对”代替方案。已核对 [Celery 任务](https://docs.celeryq.dev/en/stable/userguide/tasks.html) 的幂等、处理后确认及 Worker 丢失边界；[SQS 可见性超时](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) 的超时再处理、硬期限和至少一次投递；[SQLite 事务](https://www.sqlite.org/lang_transaction.html) 的本库原子性；[Scrapy JOBDIR](https://docs.scrapy.org/en/latest/topics/jobs.html) 的本地恢复、不可共享及非正常退出限制。由此提出输入当前状态、执行轮次、有限期限、原子完成和有界恢复的候选组合，不声称任一项目提供了整套相同架构，也不引入这些消息服务。JOBDIR 不提供跨 Worker 的业务 URL 入库确认，换用任务队列也不会自动把业务结果和处理状态跨库原子提交。细节见 [M03 闭环](../plugins/README.md)，待用户确认和断电／重复／迟到验证。

## M01 首轮依赖核对

### M03 成熟执行框架复查（2026-10-24）

用户要求重新结合 Crawlab 找成熟方案，不能直接把此前七状态、generation、start 握手等自建候选作为首版基线。本轮重查官方指南与固定源码，研究的是未采纳选项，没有安装、部署或故障测试。

| 候选 | 实际证据 | 适用边界和成本 |
| --- | --- | --- |
| Crawlab 原生 | [官方任务指南](https://docs.crawlab.io/docs/guides/spiders/) 与 [SDK 数据集成](https://docs.crawlab.io/docs/guides/data-integration/) 负责进程、周期、日志与平台结果；官方示例仍是 save_item／CrawlabPipeline | 未找到直接替本地业务 SQLite 提交及逐 URL 恢复的社区接口；[数据库指南](https://docs.crawlab.io/docs/guides/database/) 标为 Pro，不将其当已选开源组件。文档已描述 v0.7，但 latest GitHub release 接口仍为 v0.6.3，必须配套选版 |
| Feapder TaskSpider／请求队列 | 固定源码 b5ed609d04350bd9089c161946069c6be75125fc，并复核发布标签 v1.9.2 的 [TaskSpider](https://github.com/Boris-code/feapder/blob/v1.9.2/feapder/core/spiders/task_spider.py)、[Collector](https://github.com/Boris-code/feapder/blob/v1.9.2/feapder/core/collector.py)、[ItemBuffer](https://github.com/Boris-code/feapder/blob/v1.9.2/feapder/buffer/item_buffer.py)。任务源支持 MySQL／Redis；原生 MySQL 状态 0／2／1，支持查找／重置未完成任务；请求取出后延后可见，数据导出成功后才删除请求，导出失败继续处理或进失败记录 | 最贴近数据库待抓取输入与分布式处理后的确认。需要 Redis；完整复用原生 SQL 任务表需 MySQL，不能声称支持现有 SQLite。可研究 Redis 任务源＋业务提交 Pipeline，但输入库与队列交接、失败回写和容量仍需验证。2 表示已取出派发，不严格代表正在网络请求；各次导出和 SQL 状态不是单一跨库原子事务，回调异常及重试耗尽仍有边界 |
| Crawlee Python RequestQueue | 固定提交 1603c1c0d86c848eb4a3475b613a59c5d83ca8d7 的 [BasicCrawler](https://github.com/apify/crawlee-python/blob/1603c1c0d86c848eb4a3475b613a59c5d83ca8d7/src/crawlee/crawlers/_basic/_basic_crawler.py) 先运行 handler／提交输出后 mark handled，支持重试；Redis 队列含 in-progress 和 stale reclaim；[存储文档](https://github.com/apify/crawlee-python/blob/1603c1c0d86c848eb4a3475b613a59c5d83ca8d7/docs/03_concepts/12_storage_clients.mdx) 支持本地 SQLite、服务端 SQL、Redis | 主流程异步，值得比较；复核稳定 v1.11.0 的 [RedisStorageClient](https://github.com/apify/crawlee-python/blob/v1.11.0/src/crawlee/storage_clients/_redis/_storage_client.py) 明确 experimental 且要求 Redis 8+。本地 SQLite 不能直接成为异地共享文件。handled 也覆盖 ERROR／SKIPPED，不等同业务成功；失败次数耗尽会先标 handled 再调失败处理器，不能把它当 SQLite 成功原子性保证 |
| scrapy-redis | 固定提交 b4f333aec34e88bdd6cb7f563635c49f1b5fb122 的 [queue.py](https://github.com/rmax/scrapy-redis/blob/b4f333aec34e88bdd6cb7f563635c49f1b5fb122/src/scrapy_redis/queue.py) 在 pop 时 RPOP／BLPOP 或有序集合移除 | 可共享待抓队列及去重，但默认队列没有处理后 ACK／超时归还，不能只增加 persist 配置就声称解决 Worker 取到请求后被杀的问题 |

结论是有成熟的请求队列／数据导出组件可组合，不应直接自建全部 URL 状态管理；还没有验证开箱即用的 Crawlab＋现有 Scrapy＋远程 Worker＋本地 SQLite 方案。当前优先研究 Feapder 作为最贴近的对照，Crawlee 为备选，而非立即替换框架。Celery 等任务队列可提供一般任务重试，但与 Crawlab 存在进程管理职责重叠，未将增加第二套通用 Worker 作为推荐基线。

最小交互候选：框架抓取／解析 → 公共 Pipeline 批量调用业务写入 API → M05 幂等事务提交数据与必要的业务完成标记 → 确认后框架删除／确认请求，失败则保留或重试。Crawlab 保留代码、进程、周期和日志职责。若采用此路径，不能继续仅用 save_item 的 stdout flush 作为完成条件；平台先暂存后异步导入的路径要与持久提交路径二选一作为主要数据流，不同时维护两条权威写入路径。队列与 SQLite 仍不跨系统原子：提交后 ACK 前崩溃会重投，业务幂等、配置／输入版本校验、最终失败可见与有界数据保留仍是必要适配。

资源取舍需比较新增 Redis 常驻内存／持久化 I/O、框架缓存及线程／异步并发、请求和失败集合／去重集合保留；使用浏览器不作为必需依赖。Feapder 的 Redis 任务源不要求为了它新增 MySQL，但对接现有业务输入不是零代码；不能因为框架成熟就承诺全部故障、断电、回调或迟到输出已安全处理。下一步只讨论选型证据及最小闭环验证范围，不自动实现前述自建状态机或新接口。

2026-10-24 用户开始 Master 模块讨论。本轮重查 GitHub latest release 接口仍返回 v0.6.3，研究提交 ee11cd78eceabb6b4b521b4040d8b5969f4abbef 的最小 Compose 为 Master＋MongoDB，docker-init 启动 nginx／后端，fs.Service 操作本地目录；同提交 README 仍含 SeaweedFS 旧架构段，说明资料必须按最终配套版本验证，不能把两套拓扑混用。各源码链接及资源含义统一记录在 [M01 讨论稿](../integrations/crawlab/README.md)。本轮没有选择镜像或 MongoDB 版本，没有运行占用数据；MongoDB 当前官方资源／平台说明只用于确定待核验项，不当作历史服务器的实时诊断。

后续用户已选择专用平台 MongoDB，并要求聚焦参数和交互。已直接核对 config/config.go 的 Viper 映射、utils/config.go 的读取参数、router／task／schedule 控制器、Runner 参数／环境注入与 IPC 读取。参数清单和接口样例集中维护在 M01 接入说明，不能将其当其他镜像的配置保证；节点表 0 并发和配置 getter 的 0 行为不同，task.queue.size 只是本地缓冲，不是全局配额。

新增官方 crawlab-python-sdk 固定提交为 167b84859b57ac247e3409b5ef09c1e1ad5ddac2，源码声明版本 0.7.0，未安装发行物。save_item 使用 IPC JSON 行并 flush stdout，入口 crawlab.CrawlabPipeline；旧 2efb7e0 的 SDK 仍作为历史 gRPC 对照。源码结构已核对，未宣称持久确认或配套联调通过。Scanner 默认行上限、所查分支 API JWT 固定签名配置与 auth.key 不同、DeleteTask 未清结果集合等是具体待验证／处理边界，依据和资源影响见 M01 说明，不泛化为所有 Crawlab 版本结论。

M03 补查（2026-10-24）：用户要求聚焦普通网站代码的输入／请求头／返回、周期或业务数据驱动启动、完成判定与任务容量。已读取同研究提交的 spider/admin、scheduler.Enqueue、handler/service、worker_pool、Runner.wait 和原生 schedule，核对每节点并发、本地缓冲与 Mongo pending 的区别；发布入队路径未见全局条数拒绝，平台 finished 依据进程退出而非业务入库，关业务进程不等于关原生 Cron。路径及限制集中在 [平台接入说明](../integrations/crawlab/README.md)。同时核对 [Scrapy 信号](https://docs.scrapy.org/en/latest/topics/signals.html) 的 spider_idle／spider_closed 和 [内置扩展](https://docs.scrapy.org/en/latest/topics/extensions.html)：请求和 Pipeline 耗尽、关闭原因及错误统计用于收尾，不保证目标网页全成功；复用标准生命周期，不新增通用工作流或资源引擎。完成／暂停策略仍是待确认建议，未执行任务或验证发行镜像。
