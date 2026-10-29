# Crawlab 平台体验部署

## 用途与状态

截至 2026-10-26，Community 0.6.3 的 Master、专用 MongoDB 和一个 Worker 已完成基础部署；Master 禁用任务执行，home-worker-01 为一任务槽。用户执行全部服务器变更，smoke 三条结果、已有结果 API 分页及现有 Worker 改走内网域名后的新代码同步／执行／结果回传已阶段验收。本地 Codex 独立复核正式配置哈希、绑定及路由，任务日志和 Data 截图仍按用户证据记录，不冒充独立 API 全字段核验。

2026-10-29补充：Proxypool／M05已本地实现，修正版尚未常驻部署；Crawlab保持独立通用平台，任务依赖准备见[节点环境](task-runtime.md)。异设备接入、长期性能、实际资源、重启保持和备份恢复未验收。完整进度见 [progress.md](../../progress.md)，服务器事实与来源以 home-server-ops 的 facts.yaml 为准。本文是本地部署文档，不表示服务器内旧 README 已同步更新。

## 部署与配置

- 服务目录 `/srv/apps/crawlab`。正式声明由基础 `compose.yaml` 与长期 `compose.override.yaml` 合并，`.env` 提供初始资源和回环端口参数；覆盖模板见 [compose.override.yaml.example](compose.override.yaml.example)，真实域名不在 Git 中。
- Master／Worker 使用 `crawlabteam/crawlab:0.6.3@sha256:9dbe97240ba2670ce49fd1be5d557b59653e602d8fa29a71c9e05b26c557193b`，MongoDB 使用 `mongo:7.0.45-jammy@sha256:6dad6a5e2addb71d84eb417fcbcba0e11d096669917f8e8c26607cd7b52f9495`，均为固定 amd64 构件。启动兼容性已按用户实际输出确认，不据此证明长期生产适用性。
- Master 初始 1.5 CPU／2GiB、Worker 1 CPU／1GiB、MongoDB 1 CPU／1GiB，WiredTiger 缓存 0.25GiB。它们是部署限制，不是实测占用或业务吞吐；镜像压缩大小也不等于运行时磁盘预算。
- 0.6.3 的任务槽为 `CRAWLAB_TASK_HANDLER_MAXRUNNERS`；节点 config.json 和平台设置也保存它。已验收 Worker 一任务槽，不把 MaxRunners=0 当禁用 Master，也不承诺改环境变量即可热加载。
- 沿用上游 root 运行以兼容 Nginx／SeaweedFS；无 privileged、无 Docker socket 挂载，启用 no-new-privileges，不宣称逐任务强隔离。Worker 执行任务子进程，不是每任务新建容器。
- 重启策略 unless-stopped；Docker 输出使用 local 驱动、10m×3。应用内部 `/var/log/weed.log`、任务历史和结果的有限清理尚未验证，不能用 Docker 轮转代替它们的保留策略；体验阶段不建立周期任务。

2026-10-26 22:29 本地只读复核：基础 Compose SHA256 为 `ab5ea272e614e834ad8f388d6979ce42f85d0aa084e800022ba1865264a5a7f6`，正式覆盖为 `387e3b6e9a0fd668e1dc85264783d1672623cb567baca29302c31a1d6f0d5631`，Caddyfile 为 `40cd6be91391679516e626a8663b3a0c2011112805b4462fc34aff0239c24238`。这是文件核对，不是独立 Docker 镜像身份／挂载核验。

## 访问入口

- 当前管理页：`https://crawlab.<DOMAIN>:4129`。家庭通配 DNS 解析为 `192.168.1.110`，Caddy 的 LAN4129 站点反代 `127.0.0.1:13003`，来源允许家庭 LAN 和本 Crawlab Docker 网段。
- Master HTTP 宿主发布：`127.0.0.1:13003 → 8080`。回环访问及原 SSH 隧道仍可用于排查。
- Master gRPC 宿主发布：`192.168.1.110:19666 → 9666`，由正式覆盖文件新增。现有 Worker 的 `CRAWLAB_GRPC_ADDRESS` 使用 `crawlab.<DOMAIN>:19666`，`CRAWLAB_FS_FILER_URL` 使用 `https://crawlab.<DOMAIN>:4129/api/filer`，Compose DNS 为 `192.168.1.110`。
- MongoDB 和 Worker 不发布宿主端口。管理域名公网 A／AAAA 和 Caddy 公网443管理站点未配置；19:24 的 AliDNS 查询为 NXDOMAIN，是当时 DNS 快照，不代替公网访问控制。

用户已添加 `172.20.0.0/16 → 192.168.1.110` 的 UDP53、TCP53、TCP4129，以及 LAN／该 Docker 网段到服务器 TCP19666 的规则；用户容器探测 DNS／HTTPS／gRPC_TCP 通过，本地只读确认 LAN 绑定。Docker DNAT 可能走转发路径，UFW INPUT 规则不构成完整来源隔离或 IPv6 公网审计。该版本 gRPC 使用节点认证但没有此链路的 TLS，未来外网 Worker 须另行设计和授权，不直接发布19666到公网。

台式机已验收的 AirTCP 子域名通配绕过覆盖该管理域名，无须重复添加精确项。仅现有同宿主机 Worker 的新路径通过，不能据此宣称其他设备的 DNS 和实际 LAN 入站已通过。

## 数据与密钥位置

数据根目录 `/srv/persistent/data/crawlab`，业务数据库不复用平台集合：

| 宿主机子目录 | 容器位置及用途 |
| --- | --- |
| master/node、worker/node | 各自 `/root/.crawlab`，节点身份及认证配置 |
| master/data、worker/data | 各自 `/data`，显式覆盖镜像声明卷；Master 含内置 SeaweedFS 数据 |
| master/spiders、master/workspace、master/repo | Master 对应根目录，代码及工作区 |
| worker/spiders、worker/workspace、worker/repo | Worker 对应根目录，代码副本及工作区 |
| mongo/db、mongo/configdb | MongoDB `/data/db`、`/data/configdb` |

以上为部署声明；不把声明等同独立运行时挂载核验。私密环境文件在 `/srv/persistent/secrets/crawlab`，初始化约定目录0700、文件0600；节点 config.json 同样含秘密，不贴出内容。Crawlab 使用专用库 readWrite 用户，Mongo root 凭据只注入数据库容器，节点密钥与 API 登录令牌分开。

初始化仅作用于新数据库；改环境文件不会自动轮换已存在的数据库用户。未建立或验证备份恢复，不删库重试。管理密码修改状态尚未确认，不把曾经登录成功当作已修改证明。

## 常用操作与阶段材料

以下只读维护示例在服务器已有 SSH 会话、服务目录中执行，不要求现在重复检查：

```bash
cd /srv/apps/crawlab
sudo docker compose --profile worker config -q
sudo docker compose --profile worker ps -a
sudo docker stats --no-stream
```

按需读取有限日志并脱敏；不要贴普通 `compose config`、完整 inspect、环境文件或节点 config.json。容器健康、平台节点、任务完成及业务提交是不同检查层次。

首次部署顺序已完成：用户运行 prepare.sh、校验并拉取固定镜像，先启动 MongoDB／Master，禁用 Master 执行，再启动 Worker。prepare.sh 遇目标存在会停止，不在本实例重跑、不删目录绕过。今后确需新环境上传时遵循任务专属 `/tmp/home-server-ops/<TASK>/` 及两步操作约定，不默认暂存 home。

[enable-lan-web.py](enable-lan-web.py) 是已应用的阶段一材料：它针对当时完整 Caddy 配置的固定哈希生成增量、校验、原位写入并热重载，保护单文件 bind 的 inode；当前不要重跑。阶段二已通过正式覆盖文件调整现有 Master／Worker，MongoDB 未因该次有限重建而重建；不得把示例 `example.invalid` 当真实地址。

合成任务复现方法见 [smoke 说明](../../examples/crawlab-smoke/README.md)。已验收的日志包含 `LAN_PATH_CHECK_20261026`，新任务 Finished、Data 三条序号1/2/3，截图标记显示部分一致；不要求重复任务，也不将该证据扩大为 M05 完整性或永久幂等验证。

**本轮清理现状：** 用户清理时 SSH 断开，随后确认已重连。2026-10-26 22:09 本地只读确认 `/srv/apps/caddy/Caddyfile.before-crawlab-lan-20261026T100555584066Z` 已不存在；上传脚本 `/tmp/home-server-ops/crawlab-lan-web-20261026-01/enable-lan-web.py` 及其任务目录因审查权限不足，清理状态未知。后续清理步骤已撤回，不重跑删除、不索取相同日志；断开原因未确定。此项不推翻已通过的功能阶段，也不把临时材料当长期回退依据。正式 `compose.override.yaml` 保留，不属于清理对象。

## 升级、暂停与回退

不自动升级。Community 0.6.3 与 core25ed70356f13 的关键源码已复核；2026-10-25 的发行渠道调查只代表当时结果，不能混用官网0.7、新SDK与此运行物。变更版本前按实际数据、依赖和身份配置确定兼容及恢复方式，不用可变标签代替审核。

按用户授权暂停时可在本项目目录使用 `sudo docker compose --profile worker stop`，保留配置和数据，不停止其他项目。正式覆盖文件是当前配置的一部分；若未来确需回退到 Docker 内部 Master 地址，先读取当时完整配置并取得对应变更授权，再按基础 Compose 重建现有 Master／Worker并处理仅属于本次的网络规则。不能把它作为临时文件随意删除；本轮不交付新的删除步骤，不依赖已经不存在的 Caddy 回退副本。

不使用 `down -v` 或删除持久目录进行排障。镜像、配置回退与数据恢复分别验证，未验证的重启保持、长期资源和业务恢复仍按 progress.md 留待后续授权。

依据：[0.6.3 发行说明](https://github.com/crawlab-team/crawlab/releases/tag/v0.6.3)、[固定 core 源码](https://github.com/crawlab-team/crawlab-core/tree/25ed70356f13)、[Compose 覆盖合并](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)。
