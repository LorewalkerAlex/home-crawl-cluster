# 本地验证记录

主体：本地Codex，2026-10-29。代码仅本机工作区实现，无服务器变更、无Docker调用、无公开代理抓取／公网目标测试、无远程任务发布。已有平台与历史PoC成功属于此前用户证据，不复测、不写成这次独立验证。

## 自动验证

Python3.13.14、SQLite3.53.1，任务专用venv。`tools/check_local.py --node <已安装Node> --browser <已安装Edge>`运行旧PoC回归与新业务检查，包含：

- 63位窗口／Wilson／成功EWMA、跨目标质量、目标revision、双时间选测、轮前快照淘汰。
- 真正提交／回滚、短期纯内存去重、全重复不启动事务、队列上限及写事务期间继续入队／快照读取。
- 固定周期短发布、random模式不固定节点的原生参数及pending/running背压、原生接口替身、配置准备幂等／秘密不输出、构建所需文件齐全、3.10语法兼容检查。
- 真实回环HTTP代理和CONNECT可信HTTPS、超时／非200、完整源码从独立工作区执行（Python -I、移除PYTHONPATH，不借用仓库源码），及Scrapy正常重定向＋两页执行、直接HTTP回调并回读SQLite。
- 已提交但ACK丢失后同ID回调重传：目标请求没有重试，评分没有增加第二次。
- 来源执行失败摘要、显式注册工作区脚本、M05同进程读取新注册入口，无需重启。
- 已安装Edge实际单页交互：密钥、目标管理、字面脚本文本转义、63次反馈显示94.25分、启停、来源添加和页面截图；不下载浏览器。

2026-10-29修正后最终完整检查：137项测试＋57个子测试（JUnit共194项），0失败、0错误、0跳过，无资源泄漏警告。137项中当前业务31项、历史回归106项，子测试属于历史回归，不混称全部为新业务验收。新增独立程序--check、原生排队查询、大任务拆块、回调JSON字节上限检查；6项真实回环集成直接运行交付文件。首轮浏览器统计等待失败，补充页面／响应诊断后隔离复测与最终全套回归均通过，没有据此更改业务逻辑。原始JUnit及页面截图仅保留`.local/check-results/`，不提交Git。缺少Node或已安装浏览器时对应检查可跳过，因此应报告实际skip，不能只报命令退出0。

## 2026-10-30集群统一运行镜像

按用户新确认准备deploy/crawlab/worker-runtime：固定上游镜像、只COPY共享依赖清单、隔离任务解释器；节点只读配置挂载和仅Worker的覆盖由集群管理。没有业务源码／凭据进入镜像，没有修改Proxypool业务协议或节点选择方式；新增私密配置工具只复制业务密钥，不复制平台Token，已有不同内容不覆盖。

本轮完整回归139项＋57个历史子测试（JUnit196项）通过、0失败／0跳过；新增两项私密配置检查。初次全套有一条Windows子进程诊断日志解码线程警告，随后在测试读取端保留非UTF8字节的反斜杠表示，19项受影响／交付／配置／浏览器检查复测通过，并把线程警告作为错误处理，复测无警告。业务JSON及SQLite数据处理未改变。

构建输入检查确认共享镜像只复制requirements/tasks.txt，并保持上游启动环境；Dockerfile RUN的Shell语法、当前文档链接和Python3.10语法检查通过。未在本地执行Docker／Compose，未构建、推送容器镜像或切换服务器Worker；镜像实际构建、资源／依赖兼容性及节点更新待用户执行。用户已完成的独立API／内网阶段及临时清理另按progress.md记录，不由本地测试代替。

## 2026-10-30网页登录与连接提示

用户截图显示数据和更新时间已加载，残留的“正在连接”来自成功分支未更新提示。已改用单管理员账号密码、pwdlib/Argon2密码哈希和Starlette签名Cookie，程序Bearer鉴权保持兼容；不增加用户表或访问历史。旧sessionStorage中的API Key被移除，网页不再提交Authorization程序密钥。连接／登录成功明确更新提示，失败有错误提示和请求超时，退出清除已显示的业务数据。

完整检查143项＋57个历史子测试（JUnit200项）全部通过，0失败／0错误／0跳过。包含错误密码、正确登录、HttpOnly/Secure/SameSite Cookie、页面刷新恢复、退出、伪造Cookie、跨来源写入拒绝、机器Bearer兼容、私密初始化保留API Key／平台Token及Compose哈希转义。已安装Edge实际页面回归通过，不使用Docker。新增Linux/Python3.13服务依赖二进制闭包解析通过，Worker依赖清单未变。

正式服务器新网页登录和初始化容器尚未执行；当前机器API／Crawlab／Worker原生检查已通过，来源和目标仍未配置。下一步先更新网页登录，再添加一个来源验证真实抓取与入库，不要求先提供测试网址。

## 2026-10-30首次真实来源补验

此前来源执行覆盖的是离线页面、分页、解析和回环M05／SQLite，未覆盖ProxyScrape真实接口；不能用这些测试声称真实来源可达。用户首次抓取输出0、来源失败但结果已提交，随后Worker内对比确认直连ClientConnectorError、经PC代理HTTP200；未据单一解析地址认定DNS故障。

本轮增加节点私有capture_proxy_url，仅进入Scrapy标准代理中间件；质量测试与鉴权回调保持各自路径。同时恢复Scrapy错误日志，输出有界抓取诊断，禁用无需使用的Scrapy远程控制服务。回环验证覆盖分页经代理、回调不经代理，以及配置了不可用来源出口后质量测试仍正常使用候选代理。

本地补跑真实ProxyScrape官方v4 HTTP文本接口，使用已安装Python3.13.14、Scrapy2.19.0和PC代理，交付完整任务源码并启动独立子进程，回调一次性本地M05：HTTP200，解析去重后1311条，SQLite新增1311条，来源success、pages_ok=1、pages_failed=0、committed=true。抓取耗时约1.05秒；这是单次来源快照，不是吞吐保证或代理质量保证。Windows PyOpenSSL使用已安装certifi公有CA包，保持证书校验；临时库和源数据已清理。此验证没有访问服务器M05、发布原生Crawlab任务或修改服务器。

新增出口配置／任务源码尚待用户服务器同步，Linux/Python3.10实际抓取和正式入库仍需一次真实任务验收，不由本地结果代替。

本轮完整回归145项＋57个历史子测试（JUnit202项）全部通过，0失败／0错误／0跳过，包含已安装Edge交互；完整任务交付清单为19个文件。未改变API／Worker依赖清单，未执行本地Docker或服务器修改。

## 2026-10-30旧来源复核与适配

按用户要求复核旧项目四个脚本，新增ip89／ipscdn／ip3366／ipfreeonly四个标准Scrapy入口并纳入完整任务清单，支持同一文件外部显式注册。恢复原来源不是整体导入旧框架：没有恢复任意目录扫描、Redis、旧存储或关闭TLS验证。

只读服务器宿主机检查：89IP HTTPS200／60条；IP3366 HTTP521；SCDN直连失败；FreeOnly当前分页接口200、30条均有非空凭据字段。未运行docker、写入服务器文件或访问正式M05。Windows本地完整交付任务实抓：89IP直连输出／新增60、SCDN经PC代理输出／新增20；FreeOnly直连17页、按匿名模型过滤后0条；IP3366直连521、失败结果已提交。四次回调均committed=true；质量表仍0行，未进行候选代理访问测试。临时SQLite和任务工作目录已删除，原始HTTP／日志仅留本地私有核验目录。

旧FreeOnly批量接口确实越过现有2MiB下载预算，不能以HTTP200当作旧代码可直接使用；新接口逐页约11KiB，总共最多处理原500条范围，过滤CN／非HTTP／带凭据项。没有存储或转发网站返回的凭据。SCDN139次重复请求已移除；IP3366只保留已离线验证的七页解析，不声称破解了校验页。逐来源状态和服务器热注册方式见[来源说明](../plugins/README.md#旧来源复核与迁移2026-10-30)。

完整回归159项＋57个历史子测试（JUnit216项）通过，0失败／0错误／0跳过，含Edge交互。首次全套有一项历史接收PoC的错误Content-Type拒绝请求遇到Windows连接中止；该测试与既有超大请求测试一样，改为不发送会在头部校验前被拒绝的正文，消除提前关闭连接的未读正文竞态，正式HTTP服务没有修改。完整复测通过；新增四来源解析／错误模式／分页边界／热注册契约及真实回环Worker回调检查。Python3.10语法及23文件交付检查通过，依赖清单未变，未在本地运行Docker。

## 2026-10-30服务器89IP正式抓取验收

来源：用户在服务器执行只读诊断后返回的API／原生任务／日志输出，不是本地独立读取正式库。Task 6ae42d3ed3ce990c0cd599f5为finished；89IP已启用、outcome=success、输出60、实际新增60、pages_ok=1、pages_failed=0、duration_ms=488.649、error_code=null；代理总量60，任务日志committed=true、failed=false。正式发布→原生Worker→来源解析→回调M05→持久化摘要链路通过；单次耗时不换算为持续吞吐。代理质量与按目标测试仍未验收。

本次仅用现有register_source.py热同步网站脚本和元数据，没有重建镜像或执行完整任务源码更新。因此日志仍显示旧核心显式install_root_handler参数的Scrapy弃用警告；这是已知非失败提示，最新源码已有修正但尚未同步整个原生程序。不要求为消除提示重复抓取或立即更新核心。没有额外服务器临时脚本或PoC产物需要清理。

## 2026-10-30测试任务日志补充

用户首次Bilibili测试日志为kind=test、reported=60、committed=true，仅能确认传输并提交60条反馈，不能推断其中成功60条。用户报告页面没有高分代理；当前Wilson下界／z=1.96规则下，1/1成功约20.65分，4/4成功约51.01分，默认门槛50。若每个代理仅有一个样本，即使全部成功也没有高分项；实际成功／失败数与样本分布仍需只读读取质量记录，不能将页面空列表直接当作程序错误。

新增有界结构化任务日志：test_start记录目标ID／主机、任务ID、数量、节点限制后的并发和超时；test_progress在首次完成、每10条、距上次日志15秒后的下一次完成、以及整批完成时输出进度。test_summary汇总HTTP状态、错误类别、异常类、成功／失败／本地错误、成功平均耗时和最多3条失败样例；样例只有proxy_id及结果／类别，不含代理URL、异常原文或请求查询串。HTTPS代理CONNECT拒绝的状态单列proxy_http_status_counts，不误报为目标网站的HTTP响应。

完整回归161项＋57个历史子测试（JUnit218项）通过，0失败／0错误／0跳过，含Edge交互。真实回环代理分别返回200、503和超时，日志汇总与SQLite质量一致；另验证CONNECT407不会出现在目标HTTP计数中、回传重试不重复访问目标、重试耗尽不输出提交成功、节点并发限制生效，以及日志不含合成密钥／URL查询秘密／代理URL。23文件交付与Python3.10语法检查通过；临时测试数据已删除，没有本地Docker或服务器写入。

feedback_send／feedback_committed区分准备回传和已获M05提交确认；callback_retry／callback_failed描述有限传输重试，不重测网络请求。捕获任务也增加开始、结果摘要及每包入库确认，保留原最终committed日志兼容。诊断只在任务日志与内存汇总，不往SQLite添加字段或逐次历史；评分、请求、反馈JSON、重试边界和资源配置保持不变。完整原生任务仍23个文件，依赖未变；服务器需由用户显式同步任务源码，新日志不能补出旧任务未记录的HTTP状态或超时分布。

## 实际业务布局测量

本机临时库，50000端点×5目标=250000质量行；实际schema与两条索引，当前算法；热缓存、无HTTP，非服务器吞吐承诺。事务写完并清理临时库。各操作热身后5次中位数（候选获取3次、删除单次）：

| 操作 | 时间 |
| --- | ---: |
| 一个目标选测100个（端点LEFT JOIN） | 19.651 ms |
| 一个目标取排名100个＋端点JOIN | 0.614 ms |
| 统计当前全部目标 | 21.062 ms |
| 真实超量条件下汇总淘汰计划 | 67.289 ms |
| 反馈1000条＋COMMIT | 55.951 ms |
| 候选获取1000次（含补充） | 9.842 ms |
| 删除1000代理及5目标级联质量 | 122.663 ms |

主库26714112字节（约25.5MiB），WAL／峰值临时空间需另留；不把一次热测换算为生产QPS。此前统一表／分表布局实验只用于选择布局，现行统一表兼顾质量聚集读取和级联删除，无逐次历史增长。

## 依赖与部署资产

API锁定FastAPI／uvicorn／httpx／Pydantic；任务环境锁定Scrapy／aiohttp及完整依赖。任务目标Linux/amd64 Python3.10，与本机3.13分开解析，已验证目标平台二进制wheel闭包；例如cssselect、filelock、multidict、platformdirs、pycparser、zope-interface选取仍支持3.10的版本，未照搬本机freeze。此前service／任务目标依赖闭包及本机pip check通过，本轮复用相同锁定依赖，仅将worker清单改名tasks；没有因改名重新安装依赖。

2026-10-29边界修正时只有API Dockerfile。已删除业务Worker镜像、平台Compose覆盖、Worker构建／回退工具及过时venv引导清单；业务Compose不依赖平台Docker网络，prepare仅生成API资产。静态检查确认API构建输入和完整任务18文件清单存在，原生注册使用接口替身验证逐文件回读。通用节点只需兼容任务依赖，不预装本仓库；节点私有配置不进入同步清单。未执行Docker／Compose，也未实测原生平台的此次多文件同步。


本地没有Linux/Python3.10实际运行时，目标依赖解析和3.10 AST检查不能代替通用节点实际运行。服务器需实际验证：原生程序文件同步、环境／CA、权限／卷、Compose资源约束、M05可信内网入口、真实来源和代理、小规模任务闭环、长期队列／磁盘增长。部署步骤见[部署指引](../deploy/proxypool/README.md)。

## 复用依据

业务请求／解析复用 [Scrapy标准实践](https://docs.scrapy.org/en/latest/topics/practices.html)，[aiohttp ClientSession](https://docs.aiohttp.org/en/stable/client_reference.html)；生命周期复用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)，单写多读依据 [SQLite WAL](https://www.sqlite.org/wal.html)，布局依据 [WITHOUT ROWID](https://www.sqlite.org/withoutrowid.html)。Wilson依据 [NIST比例置信区间](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)。

Crawlab0.6保留机制的源码核对：[task scheduler](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/task/scheduler/service.go)、[task log file driver](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/task/log/file_driver.go)。任务／统计清理约30天、log.ttl默认30天（秒单位），没有在本次确认所有平台物理文件长期有界，也没有修改服务器TTL。既有项目对比和官方研究可见framework-references与crawlab-official-review；当前实施以本页及现行协议为准。
