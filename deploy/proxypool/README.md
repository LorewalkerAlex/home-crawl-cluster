# Proxypool 部署指引

2026-10-29修正：此前要求替换Crawlab Worker业务镜像的方案撤回。按用户输出，本轮API／Worker业务镜像标签、服务器克隆和四处业务路径已清理，既有Crawlab未改动，共享镜像和构建缓存未清空。2026-10-30补充：用户已完成修正版API启动和内网连通阶段，临时文件清理通过；下一步为集群统一Worker镜像及完整业务联调。下文保留从初始化开始的部署流程，已验收步骤不重复执行。本地没有使用Docker或代操作服务器。

Proxypool只部署自己的一个API容器，包含M03/M04/M05及页面。Crawlab是独立平台，已有通用Worker由集群侧统一依赖镜像维护；本业务不管理其镜像、Compose、节点目录或Docker socket。源码以完整程序交给Crawlab同步，依赖与节点私有配置由集群管理员准备，见[通用节点任务环境](../crawlab/task-runtime.md)。

## 部署顺序

1. 用户从main克隆本次修正版；确认版本包含完整任务交付修正，勿按1b64932及此前版本的Worker步骤部署。
2. 用户运行prepare工具，仅生成API私有env、业务config、来源注册表和数据库目录，自动发布保持关闭。
3. 配置crawlab_url为业务容器可达的原生API地址（含/api）；配置API Token。容器独立网络，不依赖master这个Docker服务名，也不自动加入crawlab_default。
4. 服务器构建并启动自己的API镜像，验证回环13005及鉴权；容器内端口仍13004。13004宿主端口已由Cashew使用，不能复用。
5. 按服务器当前完整Caddy配置增量接通M05仅内网可信HTTPS，验证通用节点能回调、API容器能访问Crawlab。新业务网络的实际来源网段需核对后加入对应站点ACL，不照搬Crawlab网段。
6. 集群侧构建统一Worker依赖镜像，准备只读私有回调JSON；任务解释器为/opt/crawl-runtime/bin/python，不覆盖原Crawlab Python/SDK。登记完整抓取／测试程序并记录两个ID。
7. 手动运行一来源、一目标的小批任务，验证原生日志→直接回调→SQLite提交→评分→获取／使用反馈，然后观察资源并开启有限定时发布。

上述步骤按真实输出逐段推进，失败先分析；用户执行所有服务器变更。本文件不提供未经当前配置核验的Caddy整文件、UFW规则或Worker替换命令。

## 配置与自己的API

从修正版仓库根目录，由用户在服务器SSH执行。准备工具不调用Docker、不下载、不覆盖既有文件，也不输出密钥：

```bash
if sudo python3 -B tools/prepare_proxypool.py --apply; then
    printf '%s\n' 'API configuration prepared; keep automatic publishing disabled.'
else
    printf '%s\n' 'Preparation failed; stop and report this output.'
fi
```

| 路径 | 用途 |
| --- | --- |
| /srv/apps/proxypool/config.json | 单进程参数，crawlab_url和两个程序ID；publishing_enabled=false |
| /srv/apps/proxypool/source-registry.json | 已审阅网站脚本的小型元数据表，可热更新 |
| /srv/persistent/secrets/proxypool/app.env | API密钥及Crawlab原始Token，0600；不加Bearer前缀到平台Token |
| /srv/persistent/data/proxypool/ | SQLite／WAL，UID/GID10001、0700 |

不生成Worker环境文件、业务Worker镜像或平台覆盖文件。真实Token、请求头、回调密钥不进Git、任务参数或同步源码。

仅在服务器检查Compose并构建API。本机PC与Airtcp在下载期间保持在线，下面分别传客户端代理和构建下载代理；仅build-arg曾导致元数据超时：

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml config -q &&
    sudo env HTTP_PROXY=http://192.168.1.120:5780 HTTPS_PROXY=http://192.168.1.120:5780 docker compose -f deploy/proxypool/compose.yaml build --build-arg HTTP_PROXY=http://192.168.1.120:5780 --build-arg HTTPS_PROXY=http://192.168.1.120:5780 api; then
    printf '%s\n' 'API image built. Stop here.'
else
    printf '%s\n' 'Validation or build failed; stop and report this output.'
fi
```

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml up -d --no-build --wait --wait-timeout 180 api &&
    sudo docker compose -f deploy/proxypool/compose.yaml exec -T api python -B tools/probe_api.py; then
    printf '%s\n' 'API and read-only probe passed; automatic publishing remains disabled.'
else
    printf '%s\n' 'API verification failed; stop and report this output.'
fi
```

初始预算API0.5CPU／512MiB，写队列10000条、1秒或1000条提交；不是服务器实测保证。原通用Worker保持原槽位和配额，由平台管理。首次真实测试建议batch10、concurrency2；节点私有max_concurrency对请求并发另设上限。

## 原生程序登记

`tools/register_crawlab.py`创建／复用本业务两个专属程序，逐个同步完整源码和requirements.txt并读回校验，不运行任务。可在API容器中运行；解释器和私有配置路径是集群管理员提供的节点路径，不能填写API容器路径。统一镜像及节点配置挂载验收后，登记参数为`--python /opt/crawl-runtime/bin/python --settings-file /run/crawl-tasks/proxypool.json`。镜像尚未切换时不要先运行这些路径。

默认解释器python；若使用节点环境变量提供PROXYPOOL_M05_URL与PROXYPOOL_API_KEY，可以省略settings-file。不要把Token写到命令中。Crawlab各可执行节点都要具有所指定的环境；Master继续禁用执行。两程序允许平台随机分配节点，不由业务轮换固定节点ID。

登记后把capture_spider_id／tester_spider_id写入config，保留自动发布关闭。修改单文件bind配置时原位保留inode；若编辑器替换文件，用下面force-recreate重新挂载。只操作自己的API：

```bash
if sudo docker compose -f deploy/proxypool/compose.yaml up -d --no-build --force-recreate --wait --wait-timeout 180 api; then
    printf '%s\n' 'API configuration loaded; proceed with manual acceptance.'
else
    printf '%s\n' 'API reload failed; stop and report this output.'
fi
```

## 验收、更新与停止

先确认目标依赖和可信回调路径，再用页面启用一来源并手动发布，核对Task退出与来源最近摘要、代理入库。对一目标测小批，检查200／失败计分、时间和重复传输只计一次；积累足够成功样本后测试获取与统一使用反馈。没有高分代理时NO_PROXY_AVAILABLE是正常冷启动。

发布前读取本业务两个程序pending／running数量，达到native_task_limit（默认8）则本轮跳过，避免节点停机时无限积压；不等待任务完成、不创建业务任务状态库。每分钟一个100代理任务是最多6000次安排/小时，10万代理单目标一轮至少16.7小时，不能承诺5分钟全池重测。按完成速度、目标数、CPU／RSS、排队量、WAL和平台卷增长调整；先观察30—60分钟再开启定时。

更新时先关闭自动发布，确认本业务原生任务空闲，再更新API与Crawlab程序源码；依赖变化由集群侧更新任务环境。注册／代码更新不追改正在运行的任务。停用只停止自己的API；保留数据库和私有配置。确认无在途回调后由用户执行`docker compose -f deploy/proxypool/compose.yaml stop api`，无需恢复或重建Crawlab Worker。不要down -v、清空Mongo或删共享镜像。

正式配置及数据库长期保留。额外手工临时文件仅放/tmp/home-server-ops/<任务>-<唯一后缀>/，列明文件与阶段验收后的精确清理命令；不再启动旧PoC。正式运行后调整窗口／z不自动重算历史分数，需另有明确迁移再执行。
