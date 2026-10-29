# 服务器接收回传 POC

本轮已完成并清理。2026-10-27用户终端输出确认：上传包回读SHA与审阅包一致；callback PASS、sent=3、received=3、persisted=false，sender为existing_worker_container且native_task_created=false；cleanup PASS，container_absent／sender_absent／upload_absent／task_directory_absent均true。按用户输出验收本轮Docker网络合成回传和全部清单内临时产物清理，不冒充本地独立执行或受限目录检查。无需重复上传、运行或删除；本轮包和目录已退出，不再作为可用服务器脚本路径。本地源码仅供审阅；未来需要复现时重新审查并使用新的任务标识，不直接重跑已清理路径。

2026-10-27用户授权服务器POC，并要求测试后清理本次服务器产物。范围是现有Worker容器经Docker网络向临时接收器发送3条合成结果；接收器复用本项目contracts／receiver代码，只确认格式及接收，`persisted=false`。这里不创建Crawlab原生Spider／Task，不调用真实代理，不写SQLite，因此不能把它称为真实任务调度或HTTP入库验证。

服务器变更全部由用户执行。准备阶段本地仅只读确认宿主Python3.14.4；随后Docker回传与清理已由用户执行输出确认，来源与范围见顶部，不另要求重复验证。脚本自行读取现有Worker的本地image ID及所需非秘密元数据，不读取环境文件、凭据或完整inspect。

## 历史构建和执行说明（本轮已结束，不重跑）

在本地用Python3构建zipapp，例如：

```bash
python -B examples/m05-server-poc/build_bundle.py --output /absolute/private/output/m05_poc.pyz
```

输出路径由本地工作环境决定，已存在会拒绝覆盖；包包含实际接收器／字段校验源码及可审查的控制脚本，不包含SQLite、配置或凭据。用既有Upload-ServerFile.ps1分两步上传，Task为m05-poc，RemoteDirectory固定为 `/tmp/home-server-ops/m05-poc-20261027-01`；看到Upload verified才执行。此次已准备的本地包路径由聊天交付，不要求重新构建。

服务器已有SSH会话执行：

```bash
if sudo python3 -B /tmp/home-server-ops/m05-poc-20261027-01/m05_poc.pyz --run; then
    printf '%s\n' 'POC finished; check callback PASS and cleanup PASS above.'
else
    printf '%s\n' 'POC failed or cleanup incomplete; stop and send the output.'
fi
```

使用if保护失败返回，不在交互SSH中设置errexit。脚本限定在上述准确路径以sudo运行；自行停止临时服务和删除上传包，不要求另存脚本／日志或从输出拼删除命令。

## 资源与清理清单

- 唯一新容器：`m05-poc-20261027-01`，复用Worker当前本地镜像，`--pull never`，不创建或拉取新镜像；覆盖ENTRYPOINT，仅运行Python接收器。
- 加入既有`crawlab_default`，无宿主端口发布。接收器仅容器内18080；不改Caddy、DNS、UFW、现有Compose、服务或持久目录。
- 容器非root、根文件系统只读、能力全移除、no-new-privileges，CPU0.25／内存及含swap总额64MiB／最多32进程。这是临时限额，不是实测消耗。镜像声明的数据卷均用有限tmpfs覆盖，运行时复核没有volume挂载；tmpfs每路径上限4MiB且计入容器内存限额。
- 发送方在现有Worker里短时运行一个Python进程，不新增依赖或文件；清理按本POC专用参数、随机标记及发送代码摘要匹配进程，绝不kill所有Python进程。
- 唯一上传文件：`/tmp/home-server-ops/m05-poc-20261027-01/m05_poc.pyz`；退出时删除，再用rmdir移除空任务目录。出现额外文件会停止并报告，不递归删除、不清理其他任务目录。
- 临时容器使用`--rm`并禁用容器文件日志；最终仍检查并按本任务标签及包SHA核验后删除准确容器ID，清理其临时挂载。既有镜像不删除。本地源码和交付包保留用于审阅，不是服务器临时残留。

退出条件是本次回传尝试结束，无论成功失败均执行finally清理。正常输出应同时有 `phase=callback, check=PASS` 和 `phase=cleanup, check=PASS`；后者确认sender_absent／container_absent／upload_absent／task_directory_absent均true。只收到前者不能关闭POC。失败清理不能被记为已完成；Docker不可用、强杀或断电时不承诺脚本一定完成，必须核实残留。清理失败保留可恢复包时支持仅`--cleanup`，具体操作先依据实际输出，不重跑试验。

自动清理只覆盖本POC清单。正常系统审计记录／终端输出不做抹除，也不批删原有Crawlab数据、日志或历史任务。本轮不需要创建回退副本，因为不修改任何正式文件。

依据：[Docker run的rm／pull／资源参数](https://docs.docker.com/reference/cli/docker/container/run/)、[临时tmpfs挂载](https://docs.docker.com/engine/storage/tmpfs/)。本地离线验证仅检查打包和安全／失败清理逻辑，不冒充真实Docker或服务器回传验证。
