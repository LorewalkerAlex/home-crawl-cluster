> 2026-10-29状态：下文为前期项目／官方文档研究，包含当时未实现的描述；当前实现、直接回调协议和验证边界以根README及docs/validation.md为准。

# Crawlab 官方文档复核与复用评估

复核日期：2026-10-25。用户要求先系统研究官方文档，再继续架构讨论。本轮阅读了 0.6 官方指南、教程、数据源、依赖、插件、版本差异及 API 参考，并对影响判断的差异核对发行版对应源码。没有访问或修改服务器，没有安装依赖／插件或执行任务；现有运行事实仍只采用用户已提供的输出及截图。

结论：Crawlab 是完整的爬虫运行管理平台，不能只按任务发布／领取器来设计。程序登记、文件管理、节点开关、定时触发、运行记录、取消重跑和结果查看应首先复用。代理质量、选测、调用反馈与生命周期仍是业务逻辑；原生数据集成是否能承担业务入库，需要按事务和错误语义验证，不能只看“支持 SQLite”。以下架构收缩为建议，不替用户确认新的数据链路。

## 文档版本与可信边界

| 材料 | 本轮用途 | 限制 |
| --- | --- | --- |
| 当前官网 [介绍](https://docs.crawlab.io/docs/)／[0.7 新功能](https://docs.crawlab.io/docs/whats-new) | 理解当前产品与升级差异 | 官网现默认讲 0.7；不能直接当作已部署 Community 0.6.3 的能力表 |
| 官方仓库 [0.6 中文文档](https://github.com/crawlab-team/crawlab-docs/tree/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh) | 完整阅读节点、项目、爬虫、任务、定时、文件、数据、依赖、Git、用户、CLI、插件与版本对比 | 部分旧页和 API 参考不完整；同目录不等于每个特性已在当前镜像联调 |
| 官方 [2023-07-23 文档快照](https://github.com/crawlab-team/crawlab-docs/tree/475caa48e0d874e5424299377ac0a7c1315c3cea/docs/zh) | 回看 0.6.3 发布前的文档，重点比对数据源、依赖与专业版边界 | 是接近发行时间的文档证据，不是运行测试 |
| 发行版 go.mod 对应 [core 25ed70356f13](https://github.com/crawlab-team/crawlab-core/tree/25ed70356f13) | 只在 API、SQLite 写入和运行状态等关键问题上补充核验 | 镜像构建使用过可变构件；源码对应关系不能代替实际 SDK、接口或数据源验证 |

此前引用的新分支 ee11cd78… 和新 Python SDK 不再作为 0.6.3 接入契约。它们保留为历史研究，不能把新旧实现拼成一套部署能力。0.7 迁移文档还明确要求全体节点一起升级；下面进一步评估升级价值，未更换已部署镜像。

## 0.7 社区版与升级判断（2026-10-25 补查）

用户询问 0.7 是否也是 Community、是否值得升级。技术版本与 Community／Pro 是不同概念，但不能仅凭官网有 0.7 文档就承诺已有明确可拉取的正式社区镜像。

| 当次核对 | 结果 | 应如何理解 |
| --- | --- | --- |
| 官方 [0.7 迁移文档](https://docs.crawlab.io/docs/getting-started/migration) | 示例写 crawlabteam/crawlab:0.7.0；[文档源码](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.7/docs/getting-started/migration.md) 同样如此 | 有以社区仓库为目标的升级说明，不是镜像实际存在的证据 |
| Docker Hub [社区仓库 0.7 标签查询](https://hub.docker.com/v2/repositories/crawlabteam/crawlab/tags/?page_size=100&name=0.7) | 本机读取公开 API，count=0 | 本次没查到该仓库明确的 0.7 标签；结论限于此次官方仓库查询，不宣布所有地方绝无社区 0.7 构件 |
| Docker Hub [Pro 仓库 0.7 标签查询](https://hub.docker.com/v2/repositories/crawlabteam/crawlab-pro/tags/?page_size=10&name=0.7) | count=6，0.7.0—0.7.5 | 能确认存在的是 Pro 发行镜像，不能据此当免费社区版替换 |
| GitHub [Latest Release](https://github.com/crawlab-team/crawlab/releases/latest)／[Tags](https://github.com/crawlab-team/crawlab/tags) | 最新正式 Release 为 v0.6.3，标签页也未提供明确 0.7 发行标签 | 文档与发行渠道存在不一致，暂不能推荐直接改成 0.7.0 或用 latest／develop 代替 |

上述查询在本机进行，只读取公开元数据，没有访问或修改家庭服务器，也没有拉取升级镜像。不能回答“0.7 必然只能付费”，也不能回答“已确认有成熟正式 Community 0.7 可直接升级”。若以后找到可验证的社区发行物，再核对来源、实际版本和功能边界。

官方介绍的 0.7 主要收益是节点／文件通信重构、进程清理与有界执行、现代编辑器等；但这些是文档描述，不能据此承诺当前社区镜像包含所有能力。SQL ORM 文档明确是 Pro 范围，列出的数据库也不是 SQLite。对我们当前的 SQLite 去重、评分、反馈及有限存储，升级没有已确认的直接替代收益。

当前建议保留固定的 0.6.3 继续受控体验，先验证一条程序执行／原生结果／业务入库链路，不为获得版本号而自行构建 develop 或误换 Pro。未来出现固定社区发行物、0.6.3 存在实际阻塞或新能力确有收益时再评估；所有节点需配套迁移，保留真实数据与配置的回退条件。此建议不是承诺旧发行版适用于长期公网生产，也没有要求用户现在执行升级。

## 当前社区版可优先复用的能力

| 能力 | 官方文档与核对结果 | 对我们方案的影响 |
| --- | --- | --- |
| Project 分组 | [项目](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/project/README.md)：项目用于组织相关 Spider | Proxypool 和其他业务可分组，不必另建爬虫项目管理页面 |
| Spider 登记与命令参数 | [爬虫](https://github.com/crawlab-team/crawlab-docs/blob/475caa48e0d874e5424299377ac0a7c1315c3cea/docs/zh/guide/spider/README.md)：登记程序、上传代码、配置命令、参数、节点模式和优先级 | 来源注册可映射为审核后的平台程序／命令；不应先做另一套完整代码发布平台 |
| 代码上传与编辑 | [文件编辑](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/spider/file-editor.md)：上传目录、编辑、保存及文件操作 | 日常代码部署有现成入口；代码测试／允许入口规则仍由业务约定 |
| 节点开关与并发 | [节点](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/node/README.md)：Enabled 控制是否执行，Max Runners 控制同时运行数量 | Master 不执行和 Worker 一任务槽直接用原生配置；Docker 继续限制整体资源 |
| 固定周期 | [定时任务](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/schedule/README.md)：Cron、启停、关联爬虫；最小单位分钟 | 周期来源不需要自建定时调度器；业务页面如保留开关，应明确映射平台 Schedule，避免两处配置互相覆盖 |
| 执行状态、日志、结果、取消、重跑 | [任务](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/task/README.md) 已说明这些操作 | 不需要完整复制任务前端和执行状态机；按 task_id 关联即可 |
| 原生结果接入 | [数据集成](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/spider/integration.md)：SDK save_item、Scrapy Pipeline；基础镜像预装 SDK | 结果传输有现成通道；先核对镜像里实际模块和版本，不盲装最新 SDK，也不默认重写上传器 |
| 数据源，包括 SQLite | [2023 数据源](https://github.com/crawlab-team/crawlab-docs/blob/475caa48e0d874e5424299377ac0a7c1315c3cea/docs/zh/guide/data-sources/README.md) 列出 SQLite 等；[0.6 对比表](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/pro/README.md) 明确社区版也有数据源集成 | 需要纠正“数据库集成一概属于专业版”的跨版本判断；不能先排除原生落库方案 |
| 手动依赖管理 | [2023 依赖指南](https://github.com/crawlab-team/crawlab-docs/blob/475caa48e0d874e5424299377ac0a7c1315c3cea/docs/zh/guide/deps/README.md) 有安装、卸载、指定节点和日志；对应源码具备路由及节点服务 | 先核验现成页面，不必另开发包管理 UI；持久化／重建可复现性仍须说明，实际安装未验证 |
| Git 与 CLI | 0.6 的 [Git](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/spider/git.md) 与 [CLI](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/cli/README.md) 有使用说明 | 可作为代码交付候选；当前镜像的具体可用项仍需页面／接口验证，不直接套 0.7 的授权说明 |

## 不能混为一谈的功能

### 手动依赖安装与自动安装

0.6 的手动依赖管理有文档和源码路径；归档爬虫文档对“每次运行时自动安装项目依赖”另标专业版。不能将自动安装限制扩大成整个 Dependencies 菜单都不可用，也不能因为菜单可见就认定自动安装已经启用。控制依赖版本和构建镜像仍可用于可复现部署，但不另做依赖管理平台。

### 消息通知、Webhooks 与业务提交确认

0.6 的 [消息通知](https://github.com/crawlab-team/crawlab-docs/blob/475caa48e0d874e5424299377ac0a7c1315c3cea/docs/zh/guide/notifications/README.md) 和版本对比标明其专业版范围。历史插件页还有邮箱／移动端 Webhook 的说明，但这不证明我们当前社区版已获得任意 HTTP 完成回调。平台通知即使可用，也不等于 M05 的数据已经持久提交，不能据此替代业务事务确认。

### 插件框架与我们的网站脚本

官方有 [平台插件框架](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/plugin/README.md)，用途是扩展 Crawlab 本身的能力。用户定义的来源插件仍是一个网站的普通抓取／解析代码，不需要因此开发 Crawlab 插件或 fork。可将审核后的脚本放到 Spider 程序里，用标准命令和参数调用。

### 广播执行与业务切块

节点选择／广播不是自动拆分代理列表。一个任务内部处理哪些代理、HTTP 并发和超时规则，仍由测试程序决定。同 URL 的代理切小批继续是合理候选，但不用再为它重造节点领取机制。

## 原生 SQLite 能替代多少

不能再说“Crawlab 不支持 SQLite”。0.6 文档、[ds/sqlite.go](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/ds/sqlite.go)、[数据源注册](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/ds/service.go) 都证明存在该结果适配；[连接实现](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/utils/sqlite.go) 使用数据源配置里的数据库文件路径。

但核对 [ds/sql.go](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/ds/sql.go) 后，能确定的只是有限功能：

- Insert 对记录逐条调用插入；这段实现没有我们需要的批次 UPSERT、观测幂等或质量汇总事务。
- 某条插入失败时记录错误并继续，函数最终可返回 nil；不能拿该函数返回值保证全部记录成功。
- 索引方法仍是 TODO；不能把数据源接通当作索引、字段约束和容量管理都已经完成。

以上判断只针对核对的 0.6.3 对应 SQL 适配，不外推所有版本或 MongoDB 通道。还未验证当前镜像的 SQLite 文件位置、建表前提、字段映射、失败表现或依赖版本。

如果直接让 Crawlab 写 M05 的业务 SQLite，就需要重新审阅“仅 M05 写库”的边界及错误一致性，不能偷偷把另一位写入者加进来。也不能将本机文件路径当作其他 Worker 都可以直接访问的共享盘。

因此，当前应保留两条候选再做最小验证：

1. SDK → Crawlab 原生结果 → M05 有界导入并处理业务事务。复用现成结果界面和传输，但中间结果也需要容量与清理。
2. 公共执行适配 → M05 业务结果 API。便于数据与质量／完成记录同事务确认，但接收、重传和展示需要自写。

原生 SQLite 可作为隔离实验对象，不能现在就认定优于上述任一方案。用户此前接受 SQLite 的业务存储选择保留；此处没有更换数据库或选定回传链路。

## 完成反馈应如何收缩

官方任务页面和 API 已提供排队、运行、结束、取消和重跑。此前要求网站脚本额外发送 start／finish 来重建相同平台状态，并不应作为默认前提。

建议只保留必要业务事实：本轮输入／配置、关联原生 task_id、结果是否已导入，以及逐测试项有无真实结果。前端的执行部分直接读／链接 Crawlab，业务部分读 M05。是否需要额外回调，只由“原生结果和状态无法覆盖哪项业务事实”决定。

同时保留必要边界：[0.6.3 Runner](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/task/handler/runner.go) 依据进程退出处理 finished／error 等状态，不能自行核对代理是否可用、每项结果是否进入 M05、调用方是否实际使用。结果提交后才正常退出可以对齐正常路径，但崩溃或迟到结果仍需核对。官网 0.7 的自动恢复和运行限制说明不能直接当作 0.6.3 的保证。

## API 文档存在的具体差异

0.6 API 页面引用的是 crawlab-core/main 的 OpenAPI 文件，属于浮动引用；任务／爬虫／周期等核心模块原理页仍为 Under construction，Token 指南文件也是空文件。本轮转而核对 [发行对应 OpenAPI](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/docs/api/openapi.yaml) 和对应控制器，不因文档标题叫 0.6 就逐字复制。

| 项目 | 文档／此前研究差异 | 0.6.3 对应源码结果 |
| --- | --- | --- |
| 结果读取 | 新分支示例使用 results 路径 | [任务控制器](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/controllers/task.go) 为 GET /tasks/{id}/data，支持 page／size；经镜像 Nginx 使用 /api 前缀 |
| 运行参数 | OpenAPI PostTaskRunRequest 写 params | 同控制器绑定 Task 模型并使用 param；不能直接按错误字段生成客户端 |
| 发布返回 | OpenAPI 示例称 EmptyResponse | 实际处理返回 task_id 列表；重跑也返回新调度的 ID，不是把旧任务直接改回运行 |
| Schedule 开关 | OpenAPI 写 enabled／disabled | [周期控制器](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/controllers/schedule.go) 实际为 enable／disable |

以上最初来自固定源码核对。2026-10-25用户随后在服务器交互登录读取已有smoke任务，结果API与小规模分页通过；不扩大为发布、重跑或Schedule接口已实测，不收集用户Token。

## 对 Proxypool 的收缩建议

| 模块 | 优先直接复用 | 剩下的必要业务 |
| --- | --- | --- |
| M03 抓取 | Spider 代码／命令登记、上传编辑、Schedule 周期及开关、运行状态、日志和数据查看 | 来源抓取／解析标准、测试准入、独立来源健康含义；确有数据驱动条件时才补发布判断 |
| M04 测试 | 通过原生 API 发布有限小任务，原生节点选择／并发／日志／任务状态 | 根据近期真实观测选测，构造同配置测试输入，逐项 HTTP 测试及结果语义 |
| M05 存储 | 评估 SDK／数据源作为结果接入，不重复开发通用数据库浏览工具 | 幂等业务事务、代理去重、按目标评价、有限记录、获取反馈、缓存失效和退出 |
| M06 前端 | 直接使用 Crawlab 管节点、代码、平台任务和日志 | 只补目标配置、代理质量／获取、业务完整性、特定来源健康及容量视图；具体保留哪些页面继续讨论 |
| M07 接入 | SDK、现成 API、已有上传方式 | 固定版本的小适配；不另造 runner、领取、租约、通用事件或任务平台 |

“不自行开发”也不是绝对目标。现成模块若不能提供我们的业务约束，应明确缺口后补小块代码；不能因为原生支持结果保存就放弃去重、评分或完成确认，也不能因为业务有差异就先复制整套平台。

## 后续验证建议与当前状态

用户已提供Worker Online／单任务槽及Master Online／Enabled关闭截图，并运行一次最小程序。随后于2026-10-25运行 [结果读取脚本](../examples/crawlab-smoke/README.md)，返回PASS、finished、total3、分页2/1/0、sequence1/2/3及相同run_marker。依据用户输出确认单次执行、SDK结果及已有任务的小规模API读取完整性，不重复要求相同检查；助手未执行服务器变更。

用户现要求分析抓取／测试输出及M05导入，候选 A／B 的统一状态及字段草案在 [数据协议](protocol.md#业务回传方案状态2026-10-26)。业务字段、终态后有界导入、短期凭证与摘要／成员核对均待审阅，尚未实现。后续按授权在本地隔离SQLite验证重复读取、缺结果、提交中断、配置变化和容量；SDK迟到、平台同ID重派及原生有限清理仍需独立验证，不能用已通过的三条smoke替代。密码修改、实际资源及重启保持也未因本次通过而确认。

资源方面仍逐项考虑：镜像内新增依赖的重建方式、平台结果与业务库双份空间、频繁轮询、SQLite 文件位置和实际写入方式、任务进程与小批大小。不能把首页统计当作专业版性能监控，也不把“日志可查看”解释为所有日志都有已验证的保留上限。
