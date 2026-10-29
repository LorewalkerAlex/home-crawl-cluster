# home-crawl-cluster

个人爬虫集群：Crawlab Community 0.6.3 负责程序同步、节点执行与任务日志；Proxypool 负责公开代理抓取、按目标评价、SQLite 存储及代理获取。其他爬虫可以复用 Crawlab，各自管理业务数据，不必进入 Proxypool。

2026-10-29：已修正首版部署边界，撤回业务Worker镜像／覆盖文件，保留既有通用Crawlab。服务器上本轮业务产物已按用户回退输出确认清理。包含常驻 API、串行发布模块、Scrapy 来源脚本、异步测试任务程序、单页控制台、离线／回环集成测试及服务器部署资产。本地没有运行 Docker，没有修改服务器、抓取公开代理或发布真实任务。已有服务器平台和历史 PoC 的验收继续有效；**本次正式业务镜像和服务器联调仍待用户执行**。

| 入口 | 内容 |
| --- | --- |
| [progress.md](progress.md) | 当前完成范围、配置决定与待部署验收 |
| [架构](docs/architecture.md) | M01—M08 模块、进程与资源边界 |
| [协议](docs/protocol.md) | HTTP、Worker 参数、反馈与完成语义 |
| [存储](apps/proxypool/storage.md) | 四表、63 位窗口、Wilson、选测与淘汰 |
| [代码导航](apps/proxypool/design.md) | 文件及实际方法名 |
| [来源接入](plugins/README.md) | Scrapy 脚本、离线测试与显式热注册 |
| [服务器部署](deploy/proxypool/README.md) | Git 拉取后的配置、构建、启动及回退 |
| [验证记录](docs/validation.md) | 自动检查、实际测量与未验证边界 |
| [官方研究](docs/crawlab-official-review.md) | 历史官方文档／版本研究，不代替当前代码协议 |

本地使用 Python 3.13、隔离 venv，无需 Docker、Redis、MongoDB 或前端构建服务。在仓库根目录执行，`python` 指自己的 Python 3.13：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/dev.txt
.\.venv\Scripts\python.exe -B tools/check_local.py
```

完整浏览器检查使用已安装 Edge／Chromium，不自动下载浏览器：

```powershell
.\.venv\Scripts\python.exe -B tools/check_local.py --node "C:\path\to\node.exe" --browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

本地查看页面：仅在当前 PowerShell 会话设置私有 `PROXYPOOL_API_KEY`（至少 16 字符），然后运行：

```powershell
.\.venv\Scripts\python.exe -B -m apps.proxypool.service.main --config config/proxypool.example.json
```

打开 `http://127.0.0.1:13004/` 输入同一密钥。默认自动发布关闭；空库没有来源、目标和代理，需要自行添加。退出按 Ctrl+C。运行数据在 `runtime/`，测试证据在 `.local/check-results/`，均不进 Git。

锁定依赖分为 API、Linux/Python 3.10任务环境及开发环境；勿用本机 `pip freeze` 覆盖任务依赖。Crawlab与通用Worker独立管理，本仓库只部署自己的API，完整任务程序通过原生文件同步交付；[节点依赖准备](deploy/crawlab/task-runtime.md)由集群侧负责。旧 `examples/m05-*`、`service/storage.py` 等保留历史 PoC 测试，正式入口是 `service/main.py`，旧 PoC 包不再上传重跑。

仓库只保存源码、脱敏样例和部署声明。真实 Token、请求头、API 密钥、运行库与网页不进 Git。服务器共用事实归 [home-server-ops](https://github.com/LorewalkerAlex/home-server-ops) 管理。
