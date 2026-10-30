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
