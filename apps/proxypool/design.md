# 实现导航

首版采用Crawlab原生API／同步、Scrapy扩展点、aiohttp、FastAPI和sqlite3；不新造通用任务引擎。当前源码入口：

| 文件 | 主要方法／类 |
| --- | --- |
| service/main.py | main：加载Settings并启动单进程uvicorn |
| service/config.py | Settings.load、secret：JSON配置及env／_FILE秘密 |
| service/app.py | create_app、lifespan、write/read闭包：鉴权HTTP、共享生命周期及静态页 |
| service/database.py | Database.initialize、apply、select_tests、candidates、quality_page、stats |
| service/database.py | _write_import、_write_feedback、_write_scheduled、_write_source_result、_write_plan_eviction、_write_delete_proxies |
| service/writer.py | Writer.submit、_run、_commit、close：有界队列、合批、回滚及提交后确认 |
| service/policy.py | wilson、needs_test、proxy_url |
| service/cache.py | CandidateCache.acquire、clear、forget_target |
| publishers.py | Publishers.start、_timer、capture、prepare_capture、tests、close |
| integrations/crawlab/program.py | program_files、program_command、sync_program：完整源码和依赖清单交付 |
| task_settings.py／task_batches.py | TaskSettings.load、callback_chunks：节点私有配置与回调按条数／字节分包 |
| integrations/crawlab/client.py | CrawlabClient.run_job、outstanding、close |
| task_main.py | main、decode_job、report_tests、report_source、post_committed |
| tester/execute.py | test_proxies：有界aiohttp访问、每成员result_id |
| crawler/execute.py | capture：每Task新Scrapy进程、信号统计、显式脚本入口 |
| crawler/registry.py | load_entries：内置＋显式JSON注册表 |
| crawler/spiders/base.py | SourceSpider、TextSourceSpider、endpoint_line |
| web/app.js | 页面初始化、API操作、刷新和分页；无前端构建步骤 |
| tools/register_crawlab.py | register：两类原生程序、完整源码和依赖清单同步 |
| tools/register_source.py | validate_source、register_source：审阅后显式热注册 |

单入口统一写操作，而非每张表一个API服务；M03/M04是可分离代码模块，首版共进程以减少常驻资源。只使用一个发布实例，不增加并发领取／租约。业务配置变化由M05控制；程序全局运行参数改JSON后重启API，来源显式注册表和前端开关动态生效。

历史标准库PoC `storage.py/quality.py/http_api.py/receiver.py/contracts.py/http_transport.py` 及旧tests保留复现，不被正式main、任务程序、发布器导入。旧状态机、持久凭证和旧评分不构成现行协议；讨论原稿在docs/archive，仅供追溯。
