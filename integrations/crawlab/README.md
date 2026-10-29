# Crawlab接入

client.py只调用独立平台原生API：tasks/run以random模式创建一个不固定节点的任务，不覆盖登记命令；tasks列表读取本业务pending/running数量，达到配置上限跳过本轮。不重试不确定发布，不维护任务状态库。执行结果直接回传M05，平台继续保存日志及退出状态。

program.py列出完整任务源码与requirements.txt，register_crawlab逐项同步并读回。节点无需预装本业务包，依赖／私有JSON由集群管理，见[节点环境](../../deploy/crawlab/task-runtime.md)。自定义网站脚本登记到同一抓取程序的sources/<key>.py，不额外创建每网站一个原生程序。

本地契约和独立工作区执行不等于真实平台验收；现有历史PoC继续有效，本次修正版需在服务器实际验证源码同步、任务启动及可信回调。版本核对以Community0.6.3对应核心25ed70356f13为依据，不使用新版文档假定旧版支持。详见[协议](../../docs/protocol.md)。
