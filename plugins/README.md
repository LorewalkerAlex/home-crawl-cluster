# 来源脚本：显式登记、动态开关

这里的插件是一个公开代理网站的Scrapy抓取／解析类，分页仍由同一个类处理；不是Crawlab插件或通用插件包。系统不扫描目录执行脚本，注册也不等于可信沙箱：必须先人工审阅代码和离线测试，再显式登记。

内置代码在`apps/proxypool/crawler/spiders/`：proxyscrape、proxy_list_download为文本端点来源，example_table为离线HTML样例。公开网站地址只是初始入口，未在本次抓取验证，页面／API变更时可前端停用后修正脚本。

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
