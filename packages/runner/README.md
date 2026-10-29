# 不额外建设通用 runner

本目录保留边界说明，不引入独立执行框架。Crawlab启动固定bootstrap，进入业务venv调用apps.proxypool.worker，任务参数是数据、不是任意代码。Scrapy／aiohttp各用标准入口；M05协议由业务模块维护。

其他业务可注册自己的Crawlab程序，不要求继承Proxypool Worker。当前代码与参数见[方法导航](../../apps/proxypool/design.md)及[协议](../../docs/protocol.md)。
