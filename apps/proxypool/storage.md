# SQLite 存储与评分

正式实现为 `service/database.py`（schema v1，application_id=1347964215），与历史PoC库不兼容；启动遇到其他已有库会拒绝，使用新路径，不自动迁移。

| 表 | 字段与语义 |
| --- | --- |
| proxies | proxy_id自增主键；scheme、host、port组合唯一。IP规范化，可IPv4／IPv6；不存来源、发现时间和全局反馈计数 |
| targets | target_id、name、test_url、enabled、revision；每目标一行 |
| proxy_quality | (target_id,proxy_id)主键、target_revision、result_bits、sample_count、feedback_count、score、successful_latency_ewma_ms、last_feedback_at、last_test_scheduled_at |
| sources | source_id、name、spider_key、url、enabled；最近发布时间／Task ID；最近结果时间、outcome、产出／新增数、耗时、页面成功失败数及错误码 |

质量表采用目标在前的复合主键 `WITHOUT ROWID`，另有 `(target_id,score DESC,successful_latency_ewma_ms ASC)` 排名索引及 `proxy_id` 删除反查索引。不是每网址建表；新增网址不用DDL。选测读目标配置后对所有端点LEFT JOIN该目标质量，新端点缺行也能选中。安排测试或有效反馈才创建质量行。

修改URL：revision+1，清空该目标旧质量，旧revision回传忽略；改名或启停保留质量。删除目标级联删除其质量；删代理级联删除该代理所有质量。不保留已删除目标的历史表。抓取来源健康保留最近一次摘要，不建立“来源→代理”关系。

## 63位窗口与分数

SQLite有符号INTEGER保留63个非负位；每条有效反馈按M05写入口处理顺序：

```python
bits = ((bits << 1) | int(success)) & ((1 << window_size) - 1)
n = min(n + 1, window_size)  # window_size默认63，最大63
s = bits.bit_count()
p = s / n
score = 100 * (p + z*z/(2*n) - z*((p*(1-p)+z*z/(4*n))/n)**0.5) / (1+z*z/n)
```

z默认1.96；n=0无分数；63/63约94.25，62/63约91.54，57/63约80.74，0/63为0。不是用十六进制字符串存储；展示可转hex，数据库中仍为一个整数。feedback_count累计有效反馈次数，不随窗口满63而停止增长，供最低测试次数和统计使用；没有全局主表计数。

最终HTTP200为成功；其他HTTP状态、代理连接／访问超时为失败。local_error、cancelled以及没有结果不增加样本、不更新时间。成功耗时单独EWMA：`L = alpha*new + (1-alpha)*old`，首次取本次，默认alpha0.2；失败不更新速度。获取先按目标分数降序、同分按成功耗时升序选有限候选，再随机打乱发放。

## 双时间选测

时间为M05的Unix毫秒，不采用调用方事件时间重排。F=last_feedback_at，S=last_test_scheduled_at，N=当前时间；满足以下两项即应测试：

1. F缺失，或 N-F≥min_test_interval。
2. S缺失，或 F存在且F≥S，或 N-S≥test_timeout。

任何新的有效反馈（自测／实际使用）都更新F；安排测试只更新S。无测试状态字段、租约、check_id、批次完成等待。M04单实例串行发布，每块收到Crawlab成功响应后批量更新S；S取发布调用前时间，允许很快回传先于安排写入。平台接收但发布响应／安排写入丢失可能造成后续重复任务，当前接受，不自动重发不确定的原生发布请求。

## 写入和重复

只有Writer线程持写连接，所有修改共用入口。有界queue接收请求，API异步等待Future；SQLite提交期间仍能接收入队。首条起等待窗口／条数触发事务，配置操作促使本批及时提交。COMMIT后才返回committed:true；事务回滚不会把未提交ID记入去重缓存。

有效反馈ID短期OrderedDict去重，发送端原ID原数据重传；重复不计分、不改F，不查持久凭证表。全重复包不启动SQLite事务。来源包按(source_id,task_id,part)同样短期去重，代理端点本身由唯一约束去重。重启、超窗口或ID容量驱逐后极晚反馈可能重复计分，这是明确取舍。

WAL允许读连接在写事务期间读取已提交快照；写连接synchronous=FULL，事务短且合批，读连接query_only。不要在网络共享文件系统放活动库，不要另启进程写同一库，不把活动库文件直接复制当作完整在线备份。

## 轮前淘汰

每次抓取发布前，在写入口获取当前数量和超量快照。以启用目标的当前质量行汇总feedback_count及score；累计次数达到配置阈值（默认50）才有资格删除，按总分末尾、proxy_id确定顺序，只删本轮开始时超出的数量。无分数和未达最低次数的新代理受保护。已删除／停用目标不参与当前淘汰汇总。

删除按最多1000代理一批，FK删除质量，清空候选缓存。计算后新反馈或新代理不重排本次快照；下一次发布再处理。极少数不够删时本次发布报告失败，不扩展保护规则或强删新代理。发布不等待抓取任务结束，不用轮末阻塞下一次定时触发。

默认容量／实测见 [架构](../../docs/architecture.md) 与 [验证记录](../../docs/validation.md)。删除后SQLite文件通常复用空闲页而不会立刻变小；不在热路径自动VACUUM。
