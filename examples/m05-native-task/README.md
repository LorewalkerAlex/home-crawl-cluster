# 原生 Task → M05 → SQLite 隔离联调

状态：2026-10-28。用户执行本轮真实Task业务验证及仅清理恢复，均返回PASS；**本次隔离PoC已完成并清理，不重跑任何历史上传、--run或恢复入口**。业务为三条合成结果的M05提交／SQLite独立回读及评分查询；清理覆盖登记的本轮记录、文件、任务日志、容器／数据库、上传包／状态及空目录。验收依据用户终端输出，非本地独立服务器检查。Crawlab平台及既有Worker保留，未部署常驻M05，也不替代正式M04／M05实现。

## 本轮验证什么

一个原生Task运行三个合成结果（200、503、200），不访问真实代理或网站：

1. 用户在已有SSH终端运行包。先检查现有容器运行状态、Worker的Python／SQLite／包内源码语法、回环18081未占用及平台API连通，再询问一次Crawlab密码。
2. 同一次登录读取节点安全字段：只选择启用、status=on且有可用槽位的home-worker-01；ready是快照，不是锁。正式发布前再次读取。
3. 确认本轮随机ID的Mongo记录、Filer及节点文件初始不存在，才启动临时M05容器。先在现有镜像内建库／UPSERT／领取三条，提交待测试标记，再创建临时Spider。
4. 向Spider上传worker.py及native_task.py，逐文件回读相等后，用selected-nodes向一个Worker发布一次Task；字段为param。已知Task ID先记入私密状态，再绑定M05。
5. Worker从CRAWLAB_TASK_ID读取身份，最多15秒等候绑定，之后只回传一次。不是重试代理测试。只有M05确认persisted／committed及三个成员才退出0。
6. 控制器等待原生Task在指定节点finished，回读三个成员、共同质量和按分数获取结果，再用独立只读SQLite连接确认三条观测／凭证及完整性。
7. 精确清理本轮对象、文件、日志、容器／SQLite、上传包／状态和空目录；逐项核对后才输出cleanup PASS。退出清除客户端Token引用。

503表示一次合成的代理访问失败，仍是完成的测试；执行／传输错误不会伪造成代理失败。当前显式注入wilson_preview，仅用于验证分数读写，不代表正式评分策略已确定。

## 已执行入口（只作追溯，不重跑）

本机历史包：运维仓库.local/m05-unified-20261028/m05_native.pyz。允许文件14个，SHA256为fc8736d488e34f8cf6aebc67af95ffacbf1b5b5b9702460641c2c6e6de485d4d。用户已用上传助手传至/tmp/home-server-ops/m05-native-20261028-01/m05_native.pyz并回读校验通过，随后在SSH运行--run。父目录已存在引起-mkdir的Failure为非致命提示，没有影响该次上传；与清理阶段失败无因果证据。

本轮上传、业务执行及恢复命令均已退出，仅作历史追溯；原包／状态及任务目录已按用户输出删除，无需再次上传、诊断或清理。
本轮包内只登录一次Crawlab，节点读取、发布、核对及清理共用内存Token；SFTP／SSH和sudo仍按现有方式认证。原PC只读脚本仍只有check／quit，可以退出；它不热加载本入口，不导出Token。密码隐藏输入，密码和Token不写命令行、文件或日志，不修改SSH／sudo策略，不创建长期Token。所有服务器写入均由用户运行入口完成，本地准备不代执行。

运行中输出phase及START标记，失败输出failed_step；不回显API正文、查询值或秘密。验收需要两个最终结果同时通过：

- native_task PASS：真实Task finished、sent=3、persisted=true、committed=true、sample_counts=[1,1,1]、success_counts=[1,0,1]，分数查询和独立SQLite回读通过。
- cleanup PASS：原生记录、Filer、节点文件／进程、容器／SQLite、上传包和空目录的对应absent字段均为true。

成功后无需再手工删除。任何FAIL均停止并返回输出，不覆盖目录、不重跑--run。普通失败仍尝试清理；归属不符、任务未停或发布响应不确定时保留本轮包及state.json，明确cleanup FAIL。--cleanup仅用于审阅后的同包恢复，不自动绕过发布不确定标记。

## 产物、退出与资源边界

| 产物 | 位置／限制 | 退出条件 |
| --- | --- | --- |
| 上传包、state.json／写入中的.partial | /tmp/home-server-ops/m05-native-20261028-01；父目录0700，状态0600，无秘密 | 清理全部核对通过后删包／状态并rmdir空目录；异常保留，不递归删除未知文件 |
| 临时M05容器 | m05-native-20261028-01，按随机run及包SHA标签核对所有权 | 普通失败或通过后按已核对容器ID移除；不删现有Master／Worker／Mongo |
| 临时SQLite、WAL／SHM及ready.json | 容器内/tmp的32MiB tmpfs；不落宿主持久卷 | 删除容器退出；服务最多180秒，无后台长期服务 |
| 临时Spider／Task、统计／队列／Artifact、结果集合登记 | 独立随机ID，名称和说明含run；最多一个Task，不复用platform_smoke | 先确认任务／Worker进程结束，再按归属核对后删除并回读 |
| Filer源码与节点同步副本／任务专属日志 | /fs/<sid>、已登记workspace／repo／spiders下的<sid>及/var/log/crawlab/<tid> | 初始不存在、任务停止且归属匹配后精确删除；重定向／未知路径拒绝 |

复用现有Worker本地镜像ID，--pull never，不安装依赖、不修改网络、Compose或防火墙。临时M05共享现有Worker网络命名空间，仅监听回环18081，无宿主端口、非root、只读根、drop ALL、no-new-privileges，0.25CPU／128MiB／32进程。镜像自带VOLUME改为各4MiB tmpfs，不产生匿名持久卷。数据库主文件上限16MiB不等于活跃WAL硬上限，整体仍受内存和tmpfs限制；上述是预算，未测峰值／吞吐。

容器内临时目录及Crawlab强制创建的源码／日志属于本任务登记的运行时例外，随本轮清理；不在宿主home或服务目录手工散放。共享Mongo／SeaweedFS正常分配页、共享运行／审计日志不通过清空共享卷、压缩全库或擦除日志处理。普通API删除留Artifact／统计等，故只使用经归属核对的PoC专用清理；这不是生产保留策略。Mongo账号和Filer独立密钥只在既有容器内使用，不导出到宿主或聊天。

## 已有证据与本地检查

- 2026-10-27用户确认现有Worker Python3.10.12／SQLite3.31.1及五类挂载。未设置选定的路径／TTL环境变量不代表默认工作目录位于挂载内；固定源码默认/root/crawlab_workspace和/root/crawlab_repo，清理同时覆盖它们及已登记挂载路径。未知自定义路径或符号链接拒绝。
- 01包返回API500／cleanup PASS；漏page导致负skip是源码和本地重现定位，原输出未带路由，未声称读取服务器日志确诊。02包被脚本的节点检查误拦／cleanup PASS；online误用了界面翻译，真实在线枚举为on。两轮业务均失败、清理按用户输出接受，无需补删或再试旧包。
- 2026-10-28本地匿名检查内网DNS、可信HTTPS、版本200／v0.6.2及匿名节点401；随后用户PC登录报告Master禁用、Worker启用／on／available=1、max=1、readiness=ready。按用户证据接受实际只读阶段，不冒充独立登录或未来容量保证。
- 本轮本地106项检查通过。新增统一生命周期检查中，Crawlab和Docker是替身，M05的HTTP、SQLite和上传代码后启动的独立Python子进程是真实的；核对单次登录／发布、先启动后绑定、事务回读、清理及Token退出清除。另验证预检失败不询问密码、端口占用和源码语法拒绝。此前11个直接执行真实Mongo清理JS的离线场景仍覆盖归属拒绝、运行中拒绝和其他记录保留。**没有把替身称为部署兼容或真实平台回放，随后真实业务阶段已按下方用户输出通过；清理随后也已按用户输出验收，见下方。**

本地运行（仓库根目录）：

```text
python -B -W error::ResourceWarning -m unittest discover -s tests
```

## 本轮真实执行结果（2026-10-28，用户输出）

Worker运行时预检为Python3.10.12／SQLite3.31.1、端口可用，两次节点选择ready。native_task返回native_task_created=true、task_status=finished、check=PASS、sent=3、persisted=true、committed=true、sample_counts=[1,1,1]、success_counts=[1,0,1]、explicit_Wilson_preview。按用户输出接受本轮合成业务及独立SQLite提交回读，不要求重复Task；未冒充本地独立认证检查，不等于正式评分、真实代理或性能通过。

cleanup开始后返回FAIL、Command failed (docker)。后续用户只读报告显示：spiders、tasks及data_collections主记录／Artifact均存在，集合匹配、_del=false、_obj=null。核心ModelDelegate.upsertArtifact不填_obj，deleteArtifact才SetObj，旧清理脚本在初始快照读取空_obj.name时中断；该处缺陷已确认，不能据此声明其他资源已删。

## 本轮仅清理恢复（已执行，勿重跑）

本地三处Artifact校验已修正：先以主记录验证名称／说明、Task命令／批次／节点、数据登记及引用；Artifact集合必须匹配。_obj=null仅在_del=false且该主记录存在时接受；已删除的归档仍须验证_obj内容，空对象缺主记录／已删除情况均拒绝。21个Mongo离线场景通过；恢复入口另有2项离线检查。初始替身误用了完整_obj，现改用用户实际字段形状的匿名样本；不把此描述成原始数据库回放。

已执行的恢复命令由本轮会话提供，在已有SSH中运行，无新上传或Crawlab登录。命令核对原包SHA及state身份、唯一已finished任务和节点无相关进程，仅在内存替换三处已测试校验，复用保留包的精确清理和回读；所有其他归属／路径条件保留。原包不重建或覆盖，不调用平台发布或其他管理API，不改变SSH／sudo认证，不外传Mongo／Filer秘密。按步骤输出位置；清理全部核对通过后才删包／状态及空目录，失败保留并返回输出，不盲目重试。用户随后执行该恢复命令并返回cleanup PASS；此入口现已退出，不重跑，原包／状态及任务目录已清理。

## 整体PoC验收与清理退出（2026-10-28，用户输出）

用户终端显示Mongo snapshot、节点进程检查、Filer GET／DELETE／GET、Master／Worker文件prune、Mongo prune／verify、两节点文件verify、cleanup_container、remove_uploads依次完成。最终cleanup PASS，native_records_absent、filer_absent、node_files_absent、worker_process_absent、container_absent、sqlite_absent、upload_absent、task_directory_absent全部true，并显示POC cleanup completed。结合已接受的真实Task业务PASS，本轮隔离PoC完成。

接受用户执行证据，不声称独立查询受限目录，不要求补删或重复核验。本轮临时服务和上传目录已退出，旧包及诊断／恢复命令均不再运行；本地历史材料保留追溯，共享服务正常日志按原规则保留。未新增常驻M05、镜像／网络／认证配置，没有启动真实代理或后续业务工作。

## 固定源码与未解决边界

基于已部署0.6.3对应核心提交25ed70356f13f9685d74c01859a25830d94b0065：

- [Task运行参数](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/controllers/task.go)：spider_id、cmd、param、mode、node_ids；返回ID列表。Authorization用原始Token，不加Bearer。
- [节点枚举](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/constants/node.go)为u／r／on／off；[分页](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/controllers/delegate_list.go)必须显式page=1、size=100；版本常量v0.6.2只观察，不作镜像版本门槛。
- [Spider与文件接口](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/controllers/spider.go)：customized、col_name为JSON字段；使用返回col_id，清理不假设Mongo保留col_name。文件保存path／data，回读标准files/get。
- [Worker运行及退出](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/task/handler/runner.go)：CRAWLAB_TASK_ID、退出0产生finished；业务提交仍需自行确认。
- [模型与Artifact删除](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/models/delegate/model.go)、[日志驱动](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/task/log/file_driver.go)、[文件服务](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/fs/service.go)及[Filer鉴权](https://github.com/crawlab-team/crawlab-core/blob/25ed70356f13f9685d74c01859a25830d94b0065/middlewares/filer_auth.go)界定本轮清理。

假设单批次单发布者。GET后发布不是跨发布者原子互斥，进程崩溃／发布响应丢失没有正式恢复系统，不承诺恰好一次。发布不确定不重发；已知Task ID但绑定失败不创建第二个Task，保留身份供核对。当前只验证现有同宿主Worker，不等于异设备M05认证／网络、真实代理测试、合批写队列、容量或长期性能通过。
