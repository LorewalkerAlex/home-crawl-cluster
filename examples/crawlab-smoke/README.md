# Crawlab 最小执行与结果检查

2026-10-26新内网路径验收已通过。2026-10-26用户返回切换内网路径后的新任务日志及Data截图：日志包含新增LAN_PATH_CHECK_20261026，任务Finished，三条platform_smoke结果的sequence为1/2/3，run_marker显示部分一致。结合此前覆盖配置和网络核验，确认本轮更新代码同步、Worker调度执行、原生结果回传及页面查看链路通过；不再重跑相同测试。完整标记未做独立API比对，未验证其他设备接入、M05／SQLite业务事务或长期资源性能。网络探测和新代码标记同时具有证据，不以旧任务或仅TCP可连接替代本次结果；以下步骤保留供复现，无需再跑。

在平台的platform_smoke → Files → smoke.py，将main开头新增 `print("LAN_PATH_CHECK_20261026", flush=True)` 并保存（本地示例已同步这行）。Nodes确认home-worker-01 Online／启用、Master执行关闭；若Worker Offline先反馈状态，不开始任务。然后只在home-worker-01运行一次，查看新Task：Finished、Logs包含LAN_PATH_CHECK_20261026及三条SMOKE_ITEM_SENT、该Task的Data恰为三条platform_smoke结果、sequence集合为1/2/3且run_marker一致。代码变更标记用于确认Worker收到更新，不只执行之前缓存的相同文件；采用标准Files编辑／同步能力，不加新框架或节点。

用户已提供本轮日志及结果截图。此轮是合成数据平台验证，未调用代理、实现M05或验证长期性能；日志和截图不等于独立API全字段比对。

供 Community 0.6.3 平台体验使用，不属于 Proxypool 业务实现。程序不访问外部网站、不调用代理、不读取业务数据库或凭据；SDK 仍会与集群通信以保存三条合成结果。只使用标准库与镜像已有的 crawlab SDK，不安装依赖、不创建 Schedule。

2026-10-25 用户已手动运行并确认结果无问题：截图显示 Finished、平台结果计数3、SMOKE_START、三条 SMOKE_ITEM_SENT 和 SMOKE_SDK_CALLS_RETURNED，日志 hostname 与此前 Worker 容器ID一致。据用户输出确认单次代码下发、Worker执行、日志及少量 SDK 结果计数链路。本地没有执行该程序或独立读取 Data 原始字段，API读取、幂等、SQLite业务事务和长期运行不在此次通过范围；无需重跑同一检查。

## 用户操作

1. 确认 Nodes：home-master Online／Enabled 关闭，home-worker-01 Online／Enabled 开启，Runners 上限 1。
2. 在 Spiders 点 New Spider，名称填写 platform_smoke，执行命令填写 `python -u smoke.py`，其他选项先保持默认。
3. 进入该 Spider 的 Files，上传本目录的 smoke.py 单个文件，确认它位于程序文件根目录，不是额外嵌套目录。
4. 点击 Run，选择指定节点模式，且只选 home-worker-01。由用户手动运行一次，不创建定时任务。
5. 到这一次的 Task 详情查看状态、Node、Logs 和 Data。

## 验证内容

- Node 是 home-worker-01；任务进入 finished。
- 日志有 SMOKE_START、三条 SMOKE_ITEM_SENT 以及 SMOKE_SDK_CALLS_RETURNED。
- 同一次 Task 的 Data 有且仅有三条结果，kind 为 platform_smoke，sequence 分别为 1、2、3，run_marker 相同。

请查看 Task 的 Data，不用 Spider 的跨运行总结果来判断条数；重新运行会创建新任务和新的 marker。SDK 方法返回及 finished 都不替代实际 Data 核对；缺结果、异常或 Task 卡住时保留现场，先检查该任务日志，不重装 SDK、不反复重跑或删库。

只需反馈状态、执行节点、上述日志和结果条数。不要分享环境文件、完整环境变量、节点 config.json、密码或 Token。成功也仅证明这一次代码下发、执行、日志和少量原生结果链路，不证明重试幂等、SQLite 业务事务、长期保留或资源上限已经验收。

依据：[官方 0.6 数据集成指南](https://github.com/crawlab-team/crawlab-docs/blob/3fc3ee9f527c1d7d9e69537fd58a3029b1f057a1/packages/v0.6/docs/zh/guide/spider/integration.md)。

## 读取已有任务的 API 结果

2026-10-25 用户同意继续验证结果读取；[read_results.py](read_results.py) 是一次性诊断程序，不是 M05 实现。由用户上传后在服务器交互终端执行 `python3 /实际上传目录/read_results.py`，无需 sudo。上传与执行分两步交付，不自动连接或执行。程序固定访问宿主机回环 `http://127.0.0.1:13003/api`，不放到 Spider Files，不在 Worker 中运行，不重跑 smoke.py。

输入已有 platform_smoke 的 Task ID（Task Overview 中复制，非 Spider ID）、Crawlab 用户名和管理界面的密码；密码隐藏输入，认证 Token 只在进程内使用。使用标准库，禁用环境代理和重定向，不保存凭据或原始响应，也不写入业务数据库。登录请求为 POST，之后只读取任务及其结果。

程序按 0.6.3 源码使用 POST `/login`，将返回的原始 Token 放入 Authorization 头（无 Bearer 前缀），GET `/tasks/{id}` 和 `/tasks/{id}/data?page=N&size=2`。读取上限为三页，每次响应最多 1 MiB，网络超时15秒。检查 finished、总数3、分页条数2/1/0、sequence恰为1/2/3、kind及message为预期值、run_marker相同；成功仅打印脱敏摘要：

```json
{"check":"PASS","task_status":"finished","total":3,"page_sizes":[2,1,0],"sequences":[1,2,3],"same_run_marker":true}
```

失败保留已有任务，返回 CHECK_FAILED 摘要后分析，不重跑或修改数据库。此检查通过也只证明当前版本已有任务的小规模 API 读取及分页，不代表 M05 已实现、断点导入或长期一致性已验证。2026-10-25 用户已返回上述完整PASS摘要，确认当前版本既有任务的小规模API结果读取和分页通过；依据用户实际执行输出记录，本地未直接登录读取，不要求重复检查。

依据：固定 core 版本 25ed70356f13 的 [login.go](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/controllers/login.go)、[鉴权中间件](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/middlewares/auth.go)、[task.go](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13/controllers/task.go)。
