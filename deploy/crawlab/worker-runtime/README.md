# 集群统一 Worker 运行环境

2026-10-30用户确认：节点统一使用集群维护的运行环境镜像，避免逐节点手工安装依赖。此目录属于Crawlab平台层；Proxypool API不构建镜像、不管理节点或Docker。

镜像继承现有Community0.6.3的固定amd64镜像，在/opt/crawl-runtime建立隔离Python3.10环境，安装requirements/tasks.txt中的通用Scrapy、aiohttp、Pydantic及锁定依赖。它不包含apps、integrations、网站脚本、SQLite或凭据；不修改上游PATH、WORKDIR、ENTRYPOINT、CMD及Python/SDK。程序源码仍由Crawlab原生同步，每个使用共享依赖的程序明确使用/opt/crawl-runtime/bin/python。上游SDK保留在原Python中，共享venv不自动继承它；需要原生结果SDK的后续程序须核对现有版本再纳入共享依赖，不直接安装新版SDK替换平台。

上游Python缺少ensurepip，镜像使用venv --without-pip与已有pip --python。要求原pip>=22.3，构建时检查，不通过升级上游pip绕过。依据：[pip官方方案](https://pip.pypa.io/en/stable/topics/python-option/)。依赖仅安装二进制wheel，不在线编译；后续增加不需要新依赖的爬虫只发布源码，依赖变化才重新构建版本镜像。

## 单节点服务器阶段

用户在服务器现有SSH会话执行；本地没有运行Docker，语法和实际镜像需服务器验收。构建、切换、验证、清理分阶段；自动发布维持关闭。先读取原pip版本，确认>=22.3；若不支持则停止并调整构建方案，不手工升级Crawlab环境：

```bash
if sudo docker exec crawlab-worker-1 python -B -m pip --version; then
    printf '%s\n' 'Existing pip version inspected.'
else
    printf '%s\n' 'Inspection failed. Stop here.'
fi
```

在修正版仓库根目录构建集群镜像，先不重建Worker。PC代理在下载阶段保持在线：

```bash
if cd /srv/apps/home-crawl-cluster &&
    sudo env HTTP_PROXY=http://192.168.1.120:5780 HTTPS_PROXY=http://192.168.1.120:5780 docker build \
        --build-arg HTTP_PROXY=http://192.168.1.120:5780 \
        --build-arg HTTPS_PROXY=http://192.168.1.120:5780 \
        -f deploy/crawlab/worker-runtime/Dockerfile \
        -t home-crawl-cluster/worker-runtime:0.6.3-py310-20261030.1 .; then
    printf '%s\n' 'Shared Worker image built. Stop here.'
else
    printf '%s\n' 'Worker image build failed. Stop and report this output.'
fi
```

节点私密目录为/srv/persistent/secrets/crawlab-tasks/home-worker-01，首次由业务配置准备工具创建，模式0700，文件0600。工具不操作容器，不输出密钥，已有不同配置不覆盖。Proxypool示例（URL替换为已验收的实际HTTPS入口）：

```bash
if cd /srv/apps/home-crawl-cluster &&
    sudo python3 -B tools/prepare_proxypool_task.py --callback-url https://proxypool.example.invalid:4129; then
    printf '%s\n' 'Node-private task settings prepared.'
else
    printf '%s\n' 'Task settings preparation failed. Stop here.'
fi
```

确认本业务原生任务空闲，私密目录创建完成，再合并平台原基础配置、原LAN覆盖和本目录覆盖。只重建worker，保留节点身份、槽位、DNS/gRPC/filer和既有持久卷，Master/Mongo不在本次操作范围：

```bash
if sudo docker compose -f /srv/apps/crawlab/compose.yaml \
        -f /srv/apps/crawlab/compose.override.yaml \
        -f /srv/apps/home-crawl-cluster/deploy/crawlab/worker-runtime/compose.yaml \
        --profile worker config -q &&
    sudo docker compose -f /srv/apps/crawlab/compose.yaml \
        -f /srv/apps/crawlab/compose.override.yaml \
        -f /srv/apps/home-crawl-cluster/deploy/crawlab/worker-runtime/compose.yaml \
        --profile worker up -d --no-build --no-deps --wait --wait-timeout 180 worker; then
    printf '%s\n' 'Worker runtime switched; verify the node and task interpreter next.'
else
    printf '%s\n' 'Worker switch failed. Stop and report this output.'
fi
```

覆盖默认使用本次版本标签；更新可设置CRAWLAB_WORKER_RUNTIME_IMAGE，私密目录可设置CRAWLAB_TASK_CONFIG_DIR。平台后续管理Worker时继续合并这三个文件，不能遗漏最后一份使节点意外回到原镜像；原两个正式文件不被本工具修改，也无需临时回退副本。

切换后检查Nodes原节点在线且仍单槽、重启计数、/opt/crawl-runtime/bin/python能导入依赖、原python/SDK仍可导入、只读配置存在但不输出其内容，并重新验证Worker→M05的DNS／可信HTTPS。随后以完整原生程序--check验证交付，再进行小规模业务任务。构建完成不等于这些运行验收已完成。

Proxypool登记时使用`--python /opt/crawl-runtime/bin/python --settings-file /run/crawl-tasks/proxypool.json`。其他爬虫使用同一解释器和各自私有配置，注册源码不改变Worker启动命令。

## 更新、分发与回退

源码更新只更新Crawlab程序。共享依赖更新集中修改tasks.in并按Linux/amd64 Python3.10重新解析tasks.txt，构建新标签，空闲时逐节点更新；不在运行中pip install，不用latest覆盖已验收版本。API原生random调度要求所有启用节点具有相同所需运行环境；未来异构分组需核对对应平台版本后设计。

首次仅在服务器本地构建，其他设备无法直接拉取这个本地标签。多节点时由集群构建端把同一镜像发布到受控镜像仓库，节点统一pull并部署，或另行明确离线分发；当前没有配置registry凭据、推送镜像或承诺CI/CD已建成。镜像仓库选择和凭据不混入Proxypool API。私密配置工具运行在已有app.env的服务器，其他设备无需部署API；由管理员安全分发各节点JSON到只读配置目录，不能把Crawlab API Token一起传给任务。

回退新版本用上一已验收的CRAWLAB_WORKER_RUNTIME_IMAGE，仍合并三文件。首轮若需恢复原上游Worker，用户只合并原基础与LAN两文件并执行worker的up --no-build --no-deps；这样重建Worker，原有节点卷保留，Master/Mongo不动。任务依赖解释器暂时不可用，因此先停止业务发布并确认无在途任务。私密配置及正式库保留，不down -v、不清Mongo、不清共享镜像和缓存。

原Worker资源配额继续沿用（当前声明1CPU、1GiB、一个槽），不增加常驻容器。共享依赖占镜像层，节点启动无需重复安装，也不在持久化/data再建环境。实际镜像增量、Worker RSS和任务吞吐待服务器验证，不用本地检查代替实测。先前逐节点安装命令不再作为推荐路径；未收到执行结果，不自动删除可能存在的/data/task-runtimes/proxypool。
