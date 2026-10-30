# home-crawl-cluster

个人爬虫集群：Crawlab Community 0.6.3 负责程序同步、节点执行与任务日志；Proxypool 负责公开代理抓取、按目标评价、SQLite 存储及代理获取。其他爬虫可以复用 Crawlab，各自管理业务数据，不必进入 Proxypool。

2026-10-30：已完成本地业务实现。用户已部署独立API，健康和鉴权读检查PASS；M05内网HTTPS、API→Crawlab、Worker→M05连通及本轮临时清理已验收。集群统一Worker镜像已由用户构建／切换，原生正式程序--check通过。**网页登录修正尚待用户更新，真实来源抓取入库及后续业务联调仍待完成**。爬虫源码通过Crawlab同步，镜像只含通用依赖。当前实现包含Scrapy抓取、异步测试、SQLite合批写与单页管理；本地没有使用Docker或代操作服务器。

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

本地查看页面：在当前PowerShell会话设置程序密钥及网页登录凭据；密码通过隐藏提示输入，不写入命令历史：

```powershell
$env:PROXYPOOL_API_KEY = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:PROXYPOOL_ADMIN_USERNAME = "admin"
$env:PROXYPOOL_ADMIN_PASSWORD_HASH = & .\.venv\Scripts\python.exe -c "from getpass import getpass; from pwdlib import PasswordHash; print(PasswordHash.recommended().hash(getpass('Dashboard password: ')))"
$env:PROXYPOOL_SESSION_SECRET = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -B -m apps.proxypool.service.main --config config/proxypool.example.json
```

打开 `http://127.0.0.1:13004/` 使用上述管理员账号和密码登录；浏览器不接触程序API Key。本地示例关闭Secure Cookie以支持回环HTTP，生产默认要求HTTPS。默认自动发布关闭；空库没有来源、目标和代理，需要自行添加。退出按 Ctrl+C。运行数据在 `runtime/`，测试证据在 `.local/check-results/`，均不进 Git。

锁定依赖分为 API、Linux/Python 3.10任务环境及开发环境；勿用本机 `pip freeze` 覆盖任务依赖。Crawlab与通用Worker独立管理，本仓库只部署自己的API，完整任务程序通过原生文件同步交付；[节点运行镜像](deploy/crawlab/worker-runtime/README.md)由集群侧统一维护。旧 `examples/m05-*`、`service/storage.py` 等保留历史 PoC 测试，正式入口是 `service/main.py`，旧 PoC 包不再上传重跑。

仓库只保存源码、脱敏样例和部署声明。真实 Token、请求头、API 密钥、运行库与网页不进 Git。服务器共用事实归 [home-server-ops](https://github.com/LorewalkerAlex/home-server-ops) 管理。
