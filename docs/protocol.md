# 当前业务协议 v1

2026-10-29，以 `service/models.py`、`service/app.py` 和 `worker.py` 为实现依据。HTTP JSON、字段snake_case；未知字段拒绝。下述不是Crawlab官方插件协议，Crawlab原生任务仍负责执行状态。

M05所有`/v1/`请求用`Authorization: Bearer <PROXYPOOL_API_KEY>`；密钥至少16字符。`/healthz`和静态页面不需要密钥，业务数据需要。Crawlab原生API则是`Authorization: <CRAWLAB_API_TOKEN>`，两者不要混用。跨设备使用可信HTTPS，不忽略证书校验。当前为个人单一共享业务密钥，不建设用户体系。

| 请求 | 用途 |
| --- | --- |
| GET /healthz | 写线程存活，200或503；不代表代理／外部网站健康 |
| GET /v1/stats | 总量、质量／来源摘要、队列和发布状态；统计最多缓存5秒 |
| GET /v1/targets | items目标列表，含target_id、revision |
| POST /v1/targets | 新建`{name,test_url,enabled}` |
| PUT /v1/targets/{id} | 同字段全量修改；URL变更重置该目标评价 |
| DELETE /v1/targets/{id} | 删除目标及质量 |
| GET /v1/source-entries | 当前注册脚本的key、name、example_url |
| GET /v1/sources | 已配置来源及最近运行摘要 |
| POST /v1/sources | 新建`{name,spider_key,url,enabled}`，仅允许已注册key |
| PUT /v1/sources/{id} | 同字段全量修改；开关只影响后续发布 |
| DELETE /v1/sources/{id} | 删除配置，不删除已抓到的代理 |
| POST /v1/proxies/import | `{proxies:[{scheme,host,port}]}`，服务端端点去重 |
| POST /v1/source-results | 来源执行结果＋代理批，见下文 |
| POST /v1/proxies/acquire | `{target_id:1}`，返回一个候选，见下文 |
| POST /v1/proxies/feedback | 自测与使用统一回传 |
| GET /v1/proxies?target_id=1&min_score=50&page=1&size=20 | 按目标分页看评分；size最大100 |
| POST /v1/actions/capture 或 test | 手动触发短发布；202只表示接受，不表示执行／业务提交成功 |

写入API在实际COMMIT后200，响应含`committed:true`及inserted／accepted／ignored等计数。队列满或事务失败503；参数错误422；单包超过记录数／字节413；目标／来源不存在404；配置上限、停用目标等409；密钥错误401。事务失败重传原包；别因超时生成新ID。默认单包1000条／256KiB，请求方应同时遵守两项限制。

## 获取和反馈

获取返回proxy_id、scheme、host、port、proxy_url、target_id、target_revision、score、sample_count、success_count、successful_latency_ewma_ms。例如：

```json
{"proxy_id":1,"scheme":"http","host":"192.0.2.10","port":8080,"proxy_url":"http://192.0.2.10:8080","target_id":1,"target_revision":1,"score":94.25,"sample_count":63,"success_count":63,"successful_latency_ewma_ms":120.0}
```

从该目标分数≥candidate_min_score的前candidate_count个中打乱pop。无候选404 `{"error":"NO_PROXY_AVAILABLE"}`，不触发同步测试。客户端保留返回的target_revision；一次真实访问生成一个result_id，例如UUID。获取但没有使用不需要反馈。

```json
{"results":[{"result_id":"example-access-01","proxy_id":1,"target_id":1,"target_revision":1,"outcome":"response","http_status":200,"latency_ms":120.0}]}
```

outcome：response需http_status和latency_ms（非负有限毫秒）；request_error需latency_ms，可带如timeout的error_code，不传http_status；local_error／cancelled不评分。HTTP200成功，其他HTTP响应失败。无客户端score、测试状态、领取票据、completed_at或接受期限。M05处理顺序就是窗口顺序；同ID有限重传，短期去重不更新时间。已删除端点／目标或旧revision反馈计ignored，不恢复已删除内容。

## 来源结果

普通小批一次回传：

```json
{"source_id":1,"task_id":"000000000000000000000001","outcome":"success","proxies":[{"scheme":"http","host":"192.0.2.10","port":8080}],"duration_ms":300.0,"pages_ok":1,"pages_failed":0,"final":true,"part":0,"output_count":1}
```

成功包括合法零产出（界面显示0，便于发现页面变化）；失败outcome=failed并带可选error_code，仍回传来源摘要。若结果超过单包记录数，分part回传final=false，最后以空proxies、final=true、output_count和total_inserted提交汇总。中途失败时已提交部分不回滚，Crawlab日志说明任务未完整交付；不是两阶段事务。

M05以最后收到的完整结果更新该来源最近健康及Task ID；不保留每次运行历史，也不按Task发布时间重排。来源已删除则忽略其回传。不存逐代理来源，task_id只是来源摘要／短期重传键。Crawlab任务Finished和M05入库是不同事实；正常Worker仅在回调committed后成功退出，异常退出可去平台看日志。M03不轮询任务完成。

## Crawlab任务与Worker

预注册Crawlab程序，命令固定`python -B -u proxypool_task.py`，调用`POST /api/tasks/run`传spider_id、selected-nodes模式及本次`--job <urlsafe-base64-json>`参数。每任务仅选一个启用Worker，避免原生selected-nodes多选广播；多节点列表由客户端轮换选择。参数最大约45KiB原始JSON，不拼接网址进Shell。

test输入含kind、target快照（target_id/revision/test_url等）、proxies端点列表、concurrency、request_timeout_seconds、retry_attempts、retry_window_seconds、max_request_records。capture输入含source（source_id/spider_key/url）、来源页／条／超时上限及workspace_spider。Base64只是安全传参，不是加密；**URL不得带凭据，Token／真实请求头不进入任务参数**。

Worker环境：PROXYPOOL_M05_URL、PROXYPOOL_API_KEY或_FILE、PROXYPOOL_WORKER_SETTINGS私有JSON路径；Crawlab注入CRAWLAB_TASK_ID。私有JSON可配request_headers及ca_file。通用bootstrap进入独立venv执行包，不升级原Crawlab Python/SDK。

请求测试每成员一次，响应后释放连接，只读取响应头判断状态和耗时；不下载完整目标内容。HTTP跳转按aiohttp默认规则处理，计最终响应。callback仅在网络错误／429／5xx时原包有限重试，默认3次、总窗口60秒；超过后任务失败，不在Worker重新测试该代理，下次M04按双时间再选择。

脚本协议及热注册见 [plugins/README.md](../plugins/README.md)，SQLite批写和选测公式见 [存储](../apps/proxypool/storage.md)。
