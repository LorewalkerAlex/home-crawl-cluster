# 通用节点的任务环境

Crawlab负责文件同步和启动命令；一个通用Worker能运行多个业务程序，但不会因为收到Python源码就自动拥有第三方依赖。节点运行环境由集群侧管理，Proxypool只声明所需Python／依赖及程序入口，不管理平台容器生命周期。

Proxypool交付两个原生程序，每份都有完整apps包、proxypool_task.py与requirements.txt。无需克隆仓库到Worker，也不要求/opt/proxypool。执行工作区包含自己的代码，不包含API、SQLite、前端或凭据。通用节点所需为Python3.10及本项目requirements/tasks.txt中锁定的依赖；本地开发使用3.13，不能拿开发freeze覆盖3.10锁文件。

集群管理员在节点空闲时为任务准备隔离venv，使用`<任务解释器> -m pip install -r <requirements.txt>`安装一次；不改Crawlab自身Python/SDK，不每个任务在线安装。已有节点是否具备venv／pip、目录持久化与写权限需在部署时最小核对，再提供准确命令；本仓库不假定原上游镜像内这些能力齐备，也不虚构Community按任务自动安装依赖。虚拟环境无法跨OS／Python版本直接复制。

管理员在各可被平台选中的节点配置相同可见的解释器路径及私有JSON路径，用register_crawlab的--python和--settings-file指定。私有JSON字段：m05_url、api_key、可选request_headers、ca_file、max_concurrency（默认20）。只有地址和共享密钥可以改用PROXYPOOL_M05_URL、PROXYPOOL_API_KEY或_FILE；完整JSON也可用PROXYPOOL_TASK_SETTINGS指定。此文件必须由节点管理员安全放置，权限仅允许执行用户读取，不能上传到原生源码或任务参数中。样例见[config/task-settings.example.json](../../config/task-settings.example.json)。

准备环境后从同步工作区运行`<任务解释器> -B proxypool_task.py --check --settings <节点私有JSON>`，只检查本地代码、依赖及配置格式，不访问来源或M05。随后单独验证M05可信HTTPS可达并手动跑小任务。不要关闭TLS校验。本地隔离目录测试只能证明程序不依赖仓库安装，不能代替实际Linux节点及原生同步验收。

增加网站脚本且依赖不变时只更新原生源码；新增依赖时按集群维护流程更新相应venv，再--check。其他爬虫复用同一Crawlab，使用各自入口、依赖和业务回调，资源槽位由平台统一管理。默认random调度要求所有启用节点具备对应环境；若未来节点运行环境异构，再使用经版本核验的平台节点分组／任务策略，不重新加业务轮换节点算法。

源码依据：[运行任务管理](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/spider/admin/service.go)、[文件控制器](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/controllers/spider.go)。本轮未改变已部署Crawlab。
