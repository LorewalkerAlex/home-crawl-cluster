# Proxypool 服务器部署

本文件是交付步骤，**尚未在服务器执行**。本地不使用Docker，Windows业务／浏览器测试、目标Linux依赖解析、构建输入检查已经完成；首轮实际镜像构建和原生任务联调由用户在服务器完成。既有Crawlab Community0.6.3、Mongo、LAN覆盖配置保留，旧PoC不再运行。

先审阅配置后分阶段执行，失败停在当前阶段，提供脱敏输出再分析。以下命令均在已有服务器SSH会话执行，外层显式处理非零；不在登录Shell启用严格模式、不安装trap、不执行exit。`tools/worker_image.sh`只作为独立子脚本运行，禁止source。服务器操作均由用户执行，本地助手不代部署。

## 本轮部署安排与只读核对

2026-10-29，本地Codex通过既有codex-audit账号只读核对：服务器可通过PC的`http://192.168.1.120:5780`读取本仓库main；Docker加载的HTTP／HTTPS代理也指向该地址。12个CPU线程，内存总计约15.1GiB、当时可用约13.2GiB；持久盘约860GiB、Docker运行盘约422GiB可用。这些是本轮快照，不是持续监测或负载保证。

**端口修订：13004已由Cashew使用。** 本业务宿主绑定改为`127.0.0.1:13005`（本轮只读时未监听），容器内部API仍用13004；健康探针和容器内probe无需改端口。既有服务映射保持原样。

| 阶段 | 用户执行内容 | 验收后再继续 |
| --- | --- | --- |
| A | 代理拉取Git、准备私有配置，自动发布保持关闭 | 仓库版本正确、秘密仅在私密目录、无业务任务产生 |
| B | 顺序构建API／Worker镜像，先启动API回环端口 | 依赖安装完成、API healthy、鉴权读接口通过 |
| C | 现有Caddy增量添加M05仅内网入口 | 浏览器和Worker到M05可信HTTPS可达；既有入口正常 |
| D | 空闲时替换Worker业务镜像，登记两类原生程序 | Worker在线且保留单槽，程序ID／节点ID有效 |
| E | 一个来源、一个实际目标，手动抓取与小批测试 | 原生任务退出、回调COMMIT、SQLite统计／评分对应 |
| F | 实际获取／使用反馈，然后启用有限定时发布 | 获取不改分、反馈改分、队列与资源稳定、无持续积压 |

首轮建议：M05先沿用0.5 CPU／512MiB；现有Worker沿用1 CPU／1GiB／单任务槽。真实手动测试先用test_batch_size=10、worker_concurrency=2；稳定后再按完成时间调大。目标分数门槛保留50，冷启动未积累足够成功样本时NO_PROXY_AVAILABLE是正常结果；先看样本和计分，再验收实际取到高分代理。

A—D不发布真实采集任务，E开始才操作正式业务数据。先验证一条完整链路，再增加来源／目标和节点。测试阶段临时新增的来源／目标在验收后通过页面删除或停用；正式配置、数据库和业务程序保留，不留下额外PoC容器或脚本。

Git、基础镜像拉取和构建中pip下载是不同代理入口。镜像拉取复用已有Docker daemon代理，下面的构建命令另外显式传build-arg；不把通用出网代理写进业务镜像ENV。代理测试仍通过每个候选代理访问目标，避免把PC代理的成功误算成候选代理成功。依据：[Docker代理文档](https://docs.docker.com/engine/cli/proxy/)、[预定义代理构建参数](https://docs.docker.com/build/building/variables/#proxy-arguments)。PC与Airtcp在下载／构建阶段需保持在线。

## 1. 拉取与准备

仓库已发布至`https://github.com/LorewalkerAlex/home-crawl-cluster.git`的main。`/srv/apps`本轮只读核对归eureka所有，目标目录尚不存在；以下由eureka在服务器现有SSH会话执行，无需sudo：

```bash
if git -c http.proxy=http://192.168.1.120:5780 clone --branch main https://github.com/LorewalkerAlex/home-crawl-cluster.git /srv/apps/home-crawl-cluster && git -C /srv/apps/home-crawl-cluster log -1 --oneline; then
    printf '%s\n' 'Repository cloned; stop here before the next preparation stage.'
else
    printf '%s\n' 'Clone failed; stop and report this output. Do not delete an existing directory.'
fi
```

后续命令从该仓库根目录执行。准备之前先确认本轮克隆成功，不重复运行旧上传PoC。

先只读预览，再创建正式配置目录；准备工具不调用Docker、不修改既有文件、不输出密钥：

```bash
if cd /srv/apps/home-crawl-cluster && sudo python3 -B tools/prepare_proxypool.py; then
    printf '%s\n' 'Preparation preview completed; review the listed paths.'
else
    printf '%s\n' 'Preparation preview failed; stop here.'
fi
```

确认路径后单独执行：

```bash
if sudo python3 -B tools/prepare_proxypool.py --apply; then
    printf '%s\n' 'Private configuration created; automatic publication remains disabled.'
else
    printf '%s\n' 'Preparation failed; stop here.'
fi
```

| 正式文件／目录 | 用途 |
| --- | --- |
| /srv/apps/proxypool/config.json | M05运行参数；默认publishing_enabled=false |
| /srv/apps/proxypool/source-registry.json | 显式新增来源入口，小型JSON，API只读挂载 |
| /srv/apps/proxypool/compose.env | 记录此次仓库绝对路径PROXYPOOL_REPO，用于Worker构建 |
| /srv/persistent/data/proxypool/ | SQLite及WAL，UID/GID10001:10001、0700 |
| /srv/persistent/secrets/proxypool/app.env | 同一业务API密钥及Crawlab原始Token，0600 |
| /srv/persistent/secrets/proxypool/worker.env | 同一业务API密钥及回调URL，0600 |
| /srv/persistent/secrets/proxypool/worker.json | 私有请求头／可选ca_file，0600 |

上述是长期业务资产，不是临时PoC；部署后保留。不要把env、真实请求头和库提交Git或贴到聊天。独立临时产物若有需要，统一放`/tmp/home-server-ops/<任务>-<唯一后缀>/`并在该阶段验收后按精确路径清理，不用home或服务目录暂存备份。

## 2. 配置私密连接与内网入口

用服务器编辑器修改正式文件，不在命令参数放Token：

- app.env的CRAWLAB_API_TOKEN填当前Crawlab API Token；不是登录密码，不加Bearer前缀。
- worker.env的PROXYPOOL_M05_URL替换example.invalid占位，指向实际M05可信内网HTTPS入口；PROXYPOOL_API_KEY必须与app.env一致（首次准备已生成相同值）。
- config.json保留内部crawlab_url=`http://master:8080/api`；worker_node_ids填Crawlab Nodes现有启用Worker的24位ID，不选禁用Master。capture_spider_id和tester_spider_id在第4步登记后填写。
- source_registry_file为`/config/source-registry.json`，database为`/data/proxypool.sqlite3`，容器host为0.0.0.0、port为13004。先保留publishing_enabled=false。
- 可选crawlab_ui_url填既有内网管理入口；其他时间、条数、CPU／内存限制按设备调整。全局config变更需重启API，来源开关和显式注册表不需要重启。

M05只映射宿主127.0.0.1:13005（容器内仍为13004）。需要在现有Caddy/DNS方案新增M05内网HTTPS站点，反代该回环端口，限制家庭LAN及既有Crawlab Docker源网段；**不要公开解析到公网或开放数据库端口**。共享既有crawlab_default网络与192.168.1.110内网DNS，减少重复网络／防火墙规则。若实际网络名不同只改CRAWLAB_NETWORK配置，不盲猜网段。

路由配置需按服务器当前完整Caddyfile增量设计：原位保存inode，先当前版本格式检查、再校验、再reload，回读用途说明并验证可信TLS和来源限制；不覆盖完整文件。此步骤尚待服务器阶段确认真实域名／片段，本仓库没有伪装成已验收的Caddy配置。接通前可先在回环验证M05，但不要发Worker业务任务。异设备Worker还必须能用同一路径回传M05；本机Docker内部DNS可用并不替代跨设备验证。

## 3. 首轮构建与启动

从仓库根运行。config -q仅在服务器执行，不将本地构建输入检查称为Compose验证：

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml config -q && sudo bash tools/worker_image.sh check; then
    printf '%s\n' 'Server Compose syntax accepted.'
else
    printf '%s\n' 'Compose check failed; stop before building.'
fi
```

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml build --build-arg HTTP_PROXY=http://192.168.1.120:5780 --build-arg HTTPS_PROXY=http://192.168.1.120:5780 api && sudo bash tools/worker_image.sh build --build-arg HTTP_PROXY=http://192.168.1.120:5780 --build-arg HTTPS_PROXY=http://192.168.1.120:5780; then
    printf '%s\n' 'API and Worker images built.'
else
    printf '%s\n' 'Image build failed; stop before starting services.'
fi
```

这会在服务器下载锁定依赖并构建镜像。Worker基于与现有部署相同的0.6.3镜像摘要，新建业务venv，保留上游Python/SDK及原/app/backend工作目录，不升级平台。API基于锁定Python3.13镜像，非root运行。依赖闭包已分别按Linux/amd64 Python3.13与实际Worker3.10解析；实际镜像结果仍以本轮服务器输出为准。

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml up -d --no-build --wait --wait-timeout 180 api; then
    printf '%s\n' 'M05 started; automatic publication is still disabled.'
else
    printf '%s\n' 'M05 startup failed; stop here.'
fi
```

在Crawlab页面确认原Worker无正在执行任务后再替换其镜像：

```bash
if sudo bash tools/worker_image.sh start; then
    printf '%s\n' 'Only the Worker was recreated; verify it is online in Crawlab.'
else
    printf '%s\n' 'Worker startup failed; stop here.'
fi
```

脚本按顺序合并服务器原compose.yaml、既有LAN compose.override.yaml和本仓库worker.override.yaml，只操作worker --no-deps，保留原节点目录、DNS／gRPC／filer与资源限制。不要在原Crawlab目录执行泛化的全部服务重建来加载业务覆盖。

## 4. 登记两类原生程序

API镜像内工具使用私有环境Token创建／复用专属程序，只上传小bootstrap并读回校验，不在此步启动任务：

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml exec -T api python -B tools/register_crawlab.py --config /config/config.json; then
    printf '%s\n' 'Copy the two returned program IDs into config.json.'
else
    printf '%s\n' 'Native program registration failed; stop here.'
fi
```

填入返回capture_spider_id／tester_spider_id及正确worker_node_ids，仍保持自动发布关闭。配置文件是单文件bind挂载，使用原位写入以保留inode；如果编辑器采用替换文件，请在后续用`--force-recreate`重建API加载新挂载。

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml up -d --no-build --force-recreate --wait --wait-timeout 180 api && sudo docker compose -f deploy/proxypool/compose.yaml exec -T api python -B tools/probe_api.py; then
    printf '%s\n' 'Read-only API probe passed; continue with the manual small-sample acceptance.'
else
    printf '%s\n' 'Configuration or API probe failed; stop here.'
fi
```

## 5. 小规模验收后启用定时

通过可信M05内网页面输入私有业务密钥；先添加一个目标和一个已注册来源（初始关闭），将test_batch_size调小，并控制并发／页数／超时。此阶段才由用户授权执行真实来源和代理测试：

1. 确认Worker到M05可信HTTPS可达，Crawlab现有节点在线且Master不执行。
2. 启用一个来源，页面手动发布抓取。Crawlab看原生Task日志／退出，M05来源摘要和端点数变化应一致；区分合法0产出与失败。
3. 手动发布少量测试。查看目标样本／分数和反馈时间；同一代理对另一目标独立计分。失败访问应有失败样本，Worker执行异常不能伪造访问失败。
4. 达到候选门槛后获取代理，实际使用并回传统一反馈；验证获取不写评分、反馈才计分。尚无高分代理返回NO_PROXY_AVAILABLE属于正常冷启动。
5. 查看CPU、RSS、写队列／提交、实际完成速度和数据／平台卷增长（先观察30—60分钟）；确认发布预算不持续超过单槽Worker吞吐。根据设备改参数，不把本地毫秒结果当服务器保证。
6. 通过后将publishing_enabled=true，按第4步的force-recreate加载。抓取默认每小时、测试每分钟有限发布；不是等待全部结束后立刻无限下一轮。

此处使用正式业务数据，不自动创建可遗忘的临时PoC。若添加了临时目标／来源，验收后通过页面删除或停用；不删除原平台smoke或其他业务对象。详细执行阶段和输出按progress.md记录。

## 更新、来源热接入与回退

Git更新后先关闭自动发布并等待当前Worker空闲，再构建／替换需要变更的API或Worker；API停机前会停止新入队并排空已接收写入。发布器不等待平台所有任务，停API期间的回调仍可能失败，所以维护时机按平台任务情况决定。

同依赖的新网站只需审阅脚本、离线测试后执行 [来源注册指引](../../plugins/README.md)，Crawlab同步脚本，M05读取小JSON注册表并由前端启停，无需重建整套镜像。热修改只作用于后续发布／同步，别假定会更改正在运行的Task。

临时停用业务时先把publishing_enabled=false并重建API，确认没有在途任务后：

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml stop api && sudo bash tools/worker_image.sh rollback; then
    printf '%s\n' 'API stopped and original Crawlab Worker definition restored; data and private config retained.'
else
    printf '%s\n' 'Rollback incomplete; stop and report this output.'
fi
```

rollback只恢复原Crawlab＋LAN覆盖的Worker，不删除正式库／配置，不执行down -v或清理Mongo。新库无旧PoC迁移，修改评分窗口／z后不会自动重算已有分数；本版先在空库配置好算法参数，已有数据调整需明确迁移后再执行。
