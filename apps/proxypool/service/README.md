# M05 正式服务

当前入口 `python -B -m apps.proxypool.service.main --config config/proxypool.example.json`。启动前设置私有PROXYPOOL_API_KEY（程序使用）；网页登录另设单管理员密码哈希与会话密钥，见根README和部署指引。自动发布默认关闭，不需要运行Crawlab就能本地查看空库页面和使用业务接口。

实际代码由app、config、models、database、writer、policy、cache构成，M03/M04发布器与单页UI共用一个常驻进程。仅Writer线程拥有写连接；API等待COMMIT但不阻塞接收其他请求。生产务必单进程，禁用多Uvicorn Worker，活动库不要放网络共享盘。

`storage.py`、`quality.py`、`contracts.py`、`http_api.py`、`receiver.py`、`http_transport.py` 是此前已完成PoC的保留实现，旧测试仍运行；正式路径不导入它们。新库schema v1不迁移PoC状态／历史表。

协议见 [docs/protocol.md](../../../docs/protocol.md)，字段与计分见 [storage.md](../storage.md)，本地验证从仓库根执行 `python -B tools/check_local.py`。部署按 [正式指引](../../../deploy/proxypool/README.md)，不要重跑已关闭的服务器PoC。
