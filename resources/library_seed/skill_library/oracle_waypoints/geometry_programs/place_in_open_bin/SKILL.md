# 单瓶入桶流程 v1

适用：Oracle 教师、model_program_v1、原生 assisted 抓持、native_joint_v1 持物导航。学生训练继续保留末端 waypoint、phase、gripper；Oracle 网格/物体真值仅作教师与审计元数据。

1. 读取当前观测，选择一个瓶子和敞口桶。空手导航到既能抓取又便于退出的站位。
2. 刷新瓶体网格、手指偏移和开度。读取/选择适用抓取程序；优先复用已有成功管线，形状不符才由 Astra 修改。预抓与闭爪候选完整预规划后执行，核查实际 native retention。
3. Astra 从当前实测瓶体生成抬升 waypoint；必要时合并收拢意图或再生成一个紧凑运输 waypoint。高度和腕部方向由任务/障碍决定，不恢复写死躯干角度。
4. navigate_to 执行携物运输。优先经过完整扫掠碰撞检查的 holonomic 直达连接，速度渐变；保留分层整机/瓶体碰撞外形。invalid_start 表示外形与障碍重叠，先原生 Manip 清障或换站位。
5. 到桶附近重读瓶/桶网格和手瓶位姿。读取 place_in_open_bin，传入当前 object_id/container_id/arm，以及 Astra 自己估计的 control_steps。
6. 程序计算真实桶口截面、整瓶旋转后的通过半径、瓶底越沿高度及实测手瓶变换。每候选一个对准目标，少量腕部方向备选；由 cuRobo 选首个完整可行路径。release=true 为明确到位后开爪；默认 false 只对准。底盘站位不合适时模型调整站位，不能靠大量小 waypoint 绕过时长检查。
7. 自然等待至少90步，查询 native Inside 与 released；再等待60步复核。两次关系真值只是两点稳定证据，不能声称连续每步稳定。
8. 验收只代表一个瓶子入桶；完整 sorting 的官方成绩单列。

## 步数和轨迹

model_estimated_v1 接受每段独立的正整数 control_steps（30 Hz，不含夹爪30步）。不是固定100，也不是硬截止：后端保留完整路径/端点，以平滑时间映射重采样，在速度/加速度/jerk 超限时延长，记录估计与实际。省略时以规划器时长为起点。重采样后重新检查 float32 命令和密集碰撞点。不能截取轨迹前缀冒充到达。

waypoint 数量与底层控制步数量不同。一段 EEF 目标可以执行数十至数百控制步；每步发关节位置目标并推进一次原生物理控制。完整原始路径、执行命令和失败候选均保留，后续数据筛选不覆盖原始证据。历史 explicit_fast_v1/historical_full_trajectory 比较策略独立保留。

## 版本与证据

源码：src/manipulation_agent/geometry_programs/place_in_open_bin.py。catalog/read 返回 version、SHA256、作者和 draft 状态；每次运行冻结所选源码，修改后 hash 改变。完整流程的机器可读索引为 task_designs/bottle_to_bin_flow_v1.json，模型可读流程通过任务经验文档冻结。

程序由开发者整理自 Astra 在 instance_309_18 的 mouth_targets 思路，来源 SHA256 afa170207a960a13f66ab89676b599d23373acc6be0908d366136ab82cb132da；没有将旧16段恢复路线固定成通用路线。旧版单瓶验收成功不等于此新版已通过原生验证或可跨实例可靠使用。原生测试结果另外记录。

## 持久发布与调用

几何程序和流程包通过 scripts/publish_geometry_assets.py 发布到本项目自己的本地 Git 库 oracle_waypoints/geometry_programs/place_in_open_bin。包内包含 source.py、flow.json、SKILL.md、版本、源哈希、依赖指纹和证据索引；发布始终为 draft，旧版保留在提交和 release tag 中。发布要求库干净并匹配 expected-version，不包含远程 push。

后续只指定任务即可让 Astra 查库。也可指定 place_in_open_bin 的版本和 source_sha256 并要求禁止改写。每个新运行冻结源文件与发布身份，不执行共享库中可变的文件。当前模型调用为 list_manip_programs → read_manip_program → manip(mode=program, program=place_in_open_bin, source_sha256=读取值, parameters=当前物体和桶与步数及释放意图, revision=当前值)。模型修改另存当前 episode 的新 hash，保留旧发布与作者归属。
