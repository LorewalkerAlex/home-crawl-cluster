# 来源脚本：显式登记、动态开关

这里的插件是一个公开代理网站的Scrapy抓取／解析类，分页仍由同一个类处理；不是Crawlab插件或通用插件包。系统不扫描目录执行脚本，注册也不等于可信沙箱：必须先人工审阅代码和离线测试，再显式登记。

内置代码在`apps/proxypool/crawler/spiders/`。旧项目的ip89、ipscdn、ip3366、ipfreeonly均已适配相同入口；proxyscrape、proxy_list_download为文本端点来源，example_table为离线HTML样例。来源登记不代表当前网站可用，也不会自动创建或启用数据库中的抓取来源。

## 旧来源复核与迁移（2026-10-30）

核对了本地保全的四个旧脚本、当前网站／接口、真实响应及当前交付任务的抓取→M05→SQLite。下表的服务器结果是通过既有审阅账号做的宿主机只读HTTP检查，不是Worker容器任务；本地任务使用一次性M05／SQLite，不碰正式库，尚需用户同步源码后确认正式链路。

| 来源键 | 当前情况与适配 | 使用建议 |
| --- | --- | --- |
| ip89 | 旧HTTP接口仍返回60条地址，改用已验证可达的HTTPS地址；从HTML片段提取并校验IP／端口。本地真实任务直连输出／新增60条；服务器宿主机直连200／60条 | 首先仅启用这个来源，不需要电脑代理 |
| ipscdn | 原接口仍有效；解析code／data.proxies，错误JSON明确失败。本地经PC代理输出／新增20条；服务器宿主机直连失败 | 有可用出口时再启用；不要把该来源需要代理推断为所有来源都需要代理 |
| ip3366 | HTTP入口返回521及JS校验页，HTTPS握手失败；旧表格解析不能处理。保留七页表格解析／分页适配，异常页面明确失败；本地任务失败结果已回传，新增0 | 当前停用；未引入浏览器、Cookie绕过或关闭证书校验 |
| ipfreeonly | 旧JSON接口可达，但limit=500／50都未把响应限制到现有2MiB预算内；改用网站当前使用的/api/data/proxy-list分页接口，每页约11KiB，保留最多检查500条、非CN且HTTP的过滤。新country对象用countryCode；非空username／password跳过，不把带凭据条目伪装成匿名代理 | 本次17页处理后匿名候选0；不建议启用。网站可达不等于适合当前匿名模型 |

后续正式验收：同日用户返回Task finished、来源输出／新增60、代理总量60及committed=true，89IP热注册后的真实抓取入库已通过。见[验证记录](../docs/validation.md#2026-10-30服务器89ip正式抓取验收)；其他来源状态和代理质量测试不因此通过。

SCDN旧代码把完全相同的URL请求139次，实际没有分页参数；新版一次抓取只请求一次。其[官方文档](https://proxy.scdn.io/api_docs.php)限定count最大20，protocol=https是来源筛选条件，不能据此把裸IP:port的连接scheme改成TLS代理。FreeOnly分页地址来自其[当前页面](https://proxyfreeonly.com/free-proxy-list)使用的公开请求，仍可能变更；解析错误不会记成正常空列表。

四个脚本都遵守`ProxySourceSpider(SourceSpider)`，只yield标准端点或下一页Request，不写库、不带密钥、不负责回调。仍由现有任务程序统一限制请求超时、页数、条数、下载体积并直接回传M05；数据库去重、计分和调用协议没有变化。保留2MiB单响应预算，FreeOnly分17个小响应完成原500条检查上限，没有扩大成整站下载。来源输出只是候选，未做目标网站质量测试。

更新部署时，使用[原生源码同步步骤](../deploy/proxypool/README.md#来源抓取的可选网络出口)更新完整任务；本轮完整交付23文件，不需要重建Worker或API镜像。现有API镜像尚无这些内置元数据，可用下文已有register_source.py将选中的同一个脚本同步到sources/<key>.py并热更新source-registry.json；脚本中的绝对导入同时适用于内置和外部注册。先只注册ip89，确认入库再决定其他来源；不要将已知受阻的来源自动启用。

服务器拉取本轮Git后，在无本业务在途任务时，以下示例仅登记89IP，既不发布抓取任务，也不更改自动发布开关：

```bash
if cd /srv/apps/home-crawl-cluster &&
    sudo python3 -B tools/register_source.py \
        --crawlab-url http://127.0.0.1:13003/api \
        --env-file /srv/persistent/secrets/proxypool/app.env \
        --capture-spider-id 6ae3f6abd3ce990c0cd599f0 \
        --registry /srv/apps/proxypool/source-registry.json \
        --source apps/proxypool/crawler/spiders/ip89.py \
        --key ip89 --name '89IP' \
        --url 'https://api.89ip.cn/tqdl.html?api=1&num=60&port=&address=&isp='; then
    printf '%s\n' '89IP registered; select and enable it in the dashboard for one manual capture.'
else
    printf '%s\n' 'Registration failed. Stop and report this output.'
fi
```

这条命令在服务器宿主机执行，使用已部署的127.0.0.1:13003回环入口，不依赖宿主机解析内网域名；不要将这个回环地址用于Worker容器的连接配置。

上面的现有程序ID仅适用本项目已验收的home-server；其他安装需用自己的ID。注册工具不创建临时文件或常驻容器，保留注册后的正式脚本和元数据即可。若只登记这个独立脚本而不使用本轮其他抓取改动，可以直接复用旧任务中已存在的SourceSpider，无需先更新整套程序。

## 显式登记自定义来源

自定义来源从 [source_template.py](source_template.py) 开始：

- 入口固定 `class ProxySourceSpider(SourceSpider)`，使用source_url参数与标准Scrapy Request/Response。
- parse逐条yield `{scheme:"http",host:"192.0.2.10",port:8080}`，IP需有效、port1—65535、scheme为http／https；可yield标准Request继续同网站分页。
- HTTP头、解析选择器、网站专用逻辑写脚本，不在SQLite追加请求头字段。
- 不自行写SQLite、发送回调、持有API密钥或无限重试；框架负责页数／时间／条数上限和统一回传。

本地对审阅过的脚本显式解析一份脱敏HTML／文本，不向外网发请求：

```powershell
.\.venv\Scripts\python.exe -B tools/check_source.py --source plugins/source_template.py --fixture tests/fixtures/source.txt --url https://example.invalid/list
```

服务器使用stdlib注册工具（命令中路径换成实际文件），从私有env取Crawlab Token，不输入到参数或聊天：

```bash
if sudo python3 -B tools/register_source.py --crawlab-url http://127.0.0.1:13003/api --env-file /srv/persistent/secrets/proxypool/app.env --capture-spider-id YOUR_CAPTURE_PROGRAM_ID --registry /srv/apps/proxypool/source-registry.json --source plugins/source_template.py --key my_site --name 'My site' --url https://example.invalid/list; then
    printf '%s\n' 'Source registered; configure and enable it in the Proxypool page.'
else
    printf '%s\n' 'Registration failed; stop and inspect the reported step.'
fi
```

注册工具做AST入口检查（不执行脚本），将sources/<key>.py同步到已登记抓取程序并读回校验，原位更新source-registry.json的name／example_url。M05重新读取小表即可在页面新增来源，无需重启；任务从自身完整工作区载入对应网站代码，不为每个网站创建一个原生程序。登记前先确认该抓取程序属于本业务并已完整同步。依赖不变无需重建API或Worker；新增依赖由集群管理员更新任务环境。

前端停用：只停止后续发布，不强杀已运行任务。重新启用继续定时抓取。移除：先停用并删除业务来源；若还需移除注册入口，审阅修改JSON，移除外部同名覆盖后会恢复内置入口；覆盖内置key也必须显式审阅注册。网站脚本可在平台确认无在途任务后移除，不删除共享抓取程序。健康由来源最近发布／回调摘要展示，不记录每个代理来自何处。

注册表是一个小JSON，不含代码和凭据；只写已审阅入口，不建设版本依赖、发布市场或热导入任意目录。两步部署与回退见 [deploy/proxypool/README.md](../deploy/proxypool/README.md)。
