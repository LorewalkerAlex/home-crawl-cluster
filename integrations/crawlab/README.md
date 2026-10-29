# Crawlab 薄接入

当前生产客户端为client.py：向既有Community0.6.3的/api/tasks/run发布程序ID＋本次param，selected-nodes每次只选一个Worker，多节点轮换，不重试不确定的发布结果。任务日志／执行状态仍由Crawlab维护，业务Worker直接回传M05，不使用原生SDK结果转导业务库。

两类通用程序由tools/register_crawlab.py显式登记并同步小bootstrap；自定义来源由tools/register_source.py登记并同步网站脚本。使用独立Worker venv保留原SDK。HTTP协议见[当前约定](../../docs/protocol.md)，用户部署步骤见[部署文档](../../deploy/proxypool/README.md)。

既有平台部署、文件同步、三条SDK结果及分页属于2026-10-25—28已完成阶段，不再重跑。本次正式业务客户端仅通过本地接口契约替身验证，实际镜像／原生程序联调待用户服务器执行。详细历史研究见[官方复核](../../docs/crawlab-official-review.md)。
