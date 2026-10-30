# 通用节点的任务环境

2026-10-30用户确认改为统一构建、部署集群Worker镜像，替代逐节点手动pip安装。当前镜像、平台覆盖、分发及回退见[统一运行环境](worker-runtime/README.md)。Proxypool独立API只发布任务，不管理平台容器。

共享依赖清单为requirements/tasks.txt（Linux/amd64 Python3.10），首版包含Scrapy、aiohttp、Pydantic。任务解释器固定/opt/crawl-runtime/bin/python；上游Crawlab的Python／SDK不变。源码及依赖声明由Crawlab同步，节点镜像不包含业务源码。

节点私密目录只读挂载到/run/crawl-tasks，各业务使用独立JSON；Proxypool使用proxypool.json，字段为m05_url、api_key、max_concurrency及可选request_headers／ca_file。准备工具从已有私有app.env复制业务密钥，原生源码／参数不包含秘密。节点镜像和业务代码都不固化某台设备的URL或密钥。

Proxypool登记命令指定`--python /opt/crawl-runtime/bin/python --settings-file /run/crawl-tasks/proxypool.json`。从原生工作区执行`/opt/crawl-runtime/bin/python -B proxypool_task.py --check --settings /run/crawl-tasks/proxypool.json`，只检查依赖和配置格式，不访问来源／M05。之后单独验证可信回调和业务小任务。示例见[配置字段](../../config/task-settings.example.json)。

使用已有依赖的新爬虫只注册源码；新增依赖集中更新镜像后统一分发。每个节点仍须加入平台并提供自己的私密配置；统一镜像不能代替节点连接和凭据配置。平台random调度要求所有启用节点具备所需任务环境。
