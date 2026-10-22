# 宏观导航与操作小脑

`target_locked_approach_v2` 是 RGB 执行器的新默认协议。大脑选择任务顺序、宏观路线、目标像素与操作；一次 manipulation `act` 内，小脑准备局部站位并执行该操作。它沿用私有仿真几何和理想抓放执行器，不是 Startlux 或真实机械臂 IK 控制。

## 调用方式

1. 大脑先检查当前四路 RGB。目标能可靠选中时，直接调用 `grasp`、`open`、`place_inside` 等操作；这也适用于初始画面，不以先调用 `navigate_to` 为前提。只是不确定精确距离或最佳站位时，让操作小脑检查和调整。
2. `navigate_to` 用于跨区域移动、探索/重新找到无法辨认的目标，或根据执行反馈选择新的宏观接近方案。导航后先检查全部四路图像；只要目标可选，就转入操作，不继续为了居中、转向或靠近一点而导航。目标在一路图像中被裁切时，先检查其他视角；四路都无法可靠选中才调整观察位置。
3. 小脑依次锁定目标、检查操作前提、检查可达性与可见性、选择目标周围的站位、沿路径移动、重新检查、执行原子操作和验证结果。
4. 大脑收到一次结果和一组新的四路 RGB。内部移动不增加大脑对话轮次，不逐帧发送图像，也不占用额外的 agent act/look 次数；实际控制步、内部尝试和移动成本单独记录。

`initialize.control` 声明实际协议。`MAS_LOCAL_MANIPULATION=0` 保留旧的 `explicit_navigation_v1`，适合对照试验；运行记录同时保留该设置。旧实验使用冻结源码，不受开发目录修改影响。

目标可在任一路当前 RGB 中可靠选中时，可以直接请求操作，不再要求先估计距离或调用宏观导航。执行器在目标周围的 1.4 m 操作范围内选站位，并从当前位置规划完整接近路线。目标不清楚、需要探索或切换任务区域时，模型仍使用显式导航；站位失败仍需根据 RGB 与反馈重新判断。目标可见不等于动作可行。

## 目标与权限边界

当前图像中的一次点选通过原有私有深度投影绑定对象和具体 link。选中表面点保存为 link 局部坐标，移动之后重新计算其世界位置，不重新解释旧像素，不选择其他同类物体、容器或层板。对象/link 消失或点位移动超过 25 cm 时交还控制。锁定仅在当前 act 内有效，下一次 act 仍需要最新 RGB 的选点；本版不提供历史目标句柄或 ROI 检索。

手占用、空手、关闭容器、不支持的状态或关系、明显不兼容的材料工具，优先作为操作前提错误返回，不为这些错误盲目导航。放置采样失败和后验失败不触发自动换站位重试。容积候选生成问题应在放置采样器中处理。

组合放置优先在按整体包围盒收缩后的容积中选中心，同时交错尝试完整标注容积中的中心。标注容积用于官方 Inside 的中心点判定，不等同于物理空腔边界；包围盒大于标注不能直接证明放不下。所有候选仍须通过组合碰撞、重力接触与稳定、根物体及载物 Inside、容器状态和最终事务检查。日志区分收缩域为空、各候选域的尝试次数、实际物理拒绝原因及官方采样器返回失败或耗尽预算。

表面放置在候选自由落定及最终环境步进期间记录真实物体位姿。最近至少 0.25 秒内，任意采样位置相差不超过 2 毫米、姿态相差不超过 2 度，才形成稳定证据；接触求解器的单帧速度仍写入日志，但不单独否决已测得稳定的物体。支撑物、接触几何、目标层板与点击邻域、载物关系及失败回滚检查保持独立，不通过冻结或改动物体来制造稳定。

局部可操作性包括：原有 1.4 m XY 作用范围、四相机视锥及场景射线、自遮挡、底盘与携带物碰撞范围。新站位以 1.25 m 为优选范围，留下距离余量；它不是机械臂关节空间可达性的保证。只调整实际存在的底盘 XY 和 yaw，不虚构俯仰相机或上下移动能力。

站位朝向地图只计算目标操作圆及短程离开证明需要的窗口，窗口外仍读取完整机器人/载荷轮廓覆盖到的障碍。这个窗口仅加速终点筛选；接近路线使用完整碰撞地图，不限制在目标窗口或调用起点附近。碰撞余量保持不变。

站位由目标周围五圈、每圈 16 个方向的径向候选与整个目标操作圆盘内的空闲格子共同提供。先检查排序靠前的 48 个径向姿态，然后优先加入地图格点，避免径向候选占满列表而漏掉狭窄空间中的站位；剩余径向姿态在格点之后补充。地图格点包含当前实际朝向和每 15° 的候选朝向，并交错不同位置和朝向的候选。候选距调用起点的距离只影响排序，不再限制候选是否可用。所有候选仍需通过完整碰撞、可见性、离开、开关门/携物预检和前向路线检查；接近路线可以绕行到目标区域，保留碰撞余量和工作预算。有限格点和朝向采样不构成连续空间可达性的完整证明。

理想抓取将完整载荷直接转移到检查通过的携带位置，不要求竖直抬升、抽出或收拢轨迹。预检查和实际执行使用同一个目的姿态，物体原朝向、支撑/包含的刚体、粒子与关节状态一起保留。目的位置须通过完整载荷与环境、机器人碰撞检查；携物离开和之后的底盘移动仍检查连续扫掠。位置候选优先在机器人前方，按载荷实际大小确定偏移，不再用固定 18 cm 抬升或 95 cm 收拢半径上限拒绝抓取。

开关门前事务性采样关节运动，检查门扫过的空间及目标状态下的离开路线；不推进物理时间、env 控制步、指标或录像。预演无论成功、失败或取消都恢复场景、粒子和随机状态。实际操作使用同一组已检查的目标关节值，避免重新随机选择开门方向。

预演使用独立的占用缓存，结束后恢复实际场景的缓存，避免正式开门复用带有相同关节目标的旧障碍。由于未推进物理时 PhysX 查询可能仍返回门的旧位置，未来地图只对发生移动的 link 使用按高度裁剪、保留完整格子余量的几何投影；其他环境障碍仍由碰撞查询提供。正式操作后的地图使用实际物理场景重新检查。

实际操作后重新检查离开能力；受困时在剩余预算内尝试接触递减的短退让或前向局部移动。初始姿态已重叠时也可尝试同一受限退让。恢复不重复已完成的原子效果。仍未获离开证明时公共结果明确标记 `operation_committed=true`、`recovery_required=true`，由大脑检查当前 RGB 并选择新的接近方案；不会自动关门、丢下物体或改变任务计划。

## 有界执行

默认每次 act 不再设置以起点为中心的活动半径，也不设置累计路径米数上限。站位候选来自目标周围的径向采样及整个 1.4 m 操作圆内的空闲格点；1.25 m 只影响排序。新选站位在 1.4 m 边界内保留 5 mm 余量，以覆盖底盘跟随器 2 mm 的到位误差；实际操作范围仍为 1.4 m，已经在范围内的当前位置不因此被拒绝。每个候选仍检查碰撞、可见性、操作前提、离开条件及从实际起点出发的完整路线。保留最多 3 次换站位、2 次原子尝试、600 个 env 控制步、1200 个采样物理步和 300 秒墙钟工作预算；路线或操作超过工作预算仍返回耗尽，不宣称几何不可达。规划共享 12 秒；候选准备最多使用其中 4 秒，优先尝试直接检查通过的路线，否则将最多 32 个已通过操作预检的站位交给共享路线规划：先使用编译的八朝向可达图连接到已预检的精确连续站位，失败后使用连续姿态规划。每个站位保留自己对应的携物或开门预检结果，最终选中后使用同一结果执行。调用方更小的预算、取消和 episode deadline 始终优先。

抓取位置转移不插值经过中间位置，之后保留 6 个控制步稳定携带关系。目的位置在附着前重新检查；如果物体或底盘已改变，拒绝使用旧计划。这是理想物体姿态控制，不代表真实机械臂运动。底盘和载荷分别保留碰撞轮廓，载荷按真实高度细分占用层；脱困检查按实际高度连接障碍区域。

显式 `look` 的底盘旋转使用与导航相同的当前碰撞层和完整旋转扫掠检查，只允许原地转动；受阻返回 `look_rotation_blocked`，不自动走绕路。观察四相机本身仍然不移动机器人。导航反馈额外区分地面点旅行、选中表面已可见且在作用距离内、仍需要更近接近；只有随后实际操作的小脑检查才能确认该操作可执行。

局部协议下，宏观物体接近优先搜索 1.25–2 m 的距离带，低目标依据相机高度、俯角和安装半径选择更远的观察站位，避免把有限可见性检查用在目标落出画面下沿的过近位置。候选仍需通过实际视锥、遮挡、进出与完整路径检查；距离带不是免碰撞或可操作保证。`target_approach_available` 表示目标可交由操作工具继续准备站位，不保证能通过完整路线与操作检查。地面点旅行与旧显式协议保持原有目标语义。底层占用缓存只按不同高度保存一次；多个机器人/载荷轮廓共享同高度占用图，抓前预测和抓后切换只更新实际变化的物体区域。

没有进展、已访问过的站位和重复无效尝试受到限制。只有未改变世界的 `out_of_reach` 可以进入有限恢复；其他原子错误保留原类别。公共反馈只包含协议、有限尝试计数和动作级状态，私有坐标、对象 ID、碰撞图和 evaluator 信息不进入大脑上下文。

| 返回 | 大脑应做什么 |
| --- | --- |
| `local_adjustment_blocked` | 在目标操作范围内没有找到通过完整检查的站位，重新评估路线或任务前提 |
| `local_adjustment_exhausted` | 操作工作预算耗尽，根据当前 RGB 决定明显不同的宏观方案 |
| `target_changed` | 目标身份/表面跟踪失效，重新观察并选点 |
| `container_closed`、`sampling_error` 等 | 处理对应操作前提或几何问题，不重复微调导航 |

## 证据与比较

`local_manipulation.jsonl` 的子阶段带 `parent_request_id`，记录目标绑定、候选拒绝原因、内部路线、尝试、错误和完成状态；连续视频仍记录实际运动。外层 request ID 缓存保证网络重试不会再次执行整个操作。局部执行器不调用全任务 evaluator，也不把工具成功当成整任务成功。

`stance_search` 额外记录是否进入空闲空间补充候选、候选来源、各阶段拒绝计数以及结束原因。局部准备失败时，公共错误保留原错误码，并可增加固定白名单内的 `stage`：站位碰撞、目标可见性、离开空间、门运动空间、带物离开、局部路线或规划预算。阶段表示候选已到达的检查阶段，不表示所有候选只有同一种失败原因。只发送固定说明文字，不发送异常原文、私有几何、对象 ID 或坐标。

本次同时修正旋转碰撞检查：长转动由与短转动相同的整数角度区间掩模取并集，避免“整段规划通过、其中一步碰撞拒绝”的离散化不一致。原捡垃圾记录的 CPU 地图重放已显示整段和失败小步一致拒绝；这不等于真实任务已通过。

验证记录位于工作根目录 `operations/local_manipulation_20261020/`。CPU 状态机、目标锁定、真实 adapter 控制循环夹具和历史地图重放是分开的验证层级。真实模拟器与自主任务的结果必须单独记录，比较时同时报告大脑调用次数、内部移动/尝试、时间、视频和独立任务成功率。

2026-10-20 站位、携物及开门撤离修复的分层验证记录位于 `operations/local_navigation_repair_20261020/`；CPU 回归、真实动作与自主整任务结果分开记录。

2026-10-21 空闲空间站位补充修复位于 `operations/local_stance_repair_20261021/`。存档 CPU 夹具保留桌椅、机器人和载荷的真实碰撞层，验证原径向候选全碰撞时仍可找到经过扫掠检查的路线；它不包含相机射线、开门或抬物的真实仿真验证，这些结果单独记录。

2026-10-21 目标中心站位修改记录位于 `operations/target_centered_stance_20261021/`。移除调用起点 2 m 活动圆和累计 4 m 底盘路线限制；几何重放、真实模拟器及完整自主任务分别记录状态，未完成的验证不得视为通过。
# Placement geometry diagnostics (2026-10-21)

Inside proposals now also project volume XY centers onto upward collision
surfaces of the selected container, enumerating all downward ray hits so an
upper shelf does not hide a lower one. The held object's measured collision
bottom determines the candidate height. The existing 25 mm contact clearance,
physical settling, collision rejection, root/payload containment and rollback
remain in force. Volume-height proposals remain available as fallback. This
change requires simulator validation; candidate generation is not a proof of fit.

Local stance diagnostics distinguish out-of-frame targets, own-body/payload
occlusion and scene ray occluders. Budget exhaustion with zero admitted route
endpoints reports `local_candidate_preparation_budget`, distinct from route
search exhaustion. Detailed geometry remains executor-private.

For `place_inside`, the click binds the container identity. A stance may see a
different surface of that same container along the target ray; requiring the
original interior point to remain unoccluded incorrectly rejects approaches
behind a shelf or the container's own collision shell. Other scene occluders,
camera frusta, own-body/payload occlusion, reach, collision and the open-container
precondition remain checked. Surface-specific placement keeps exact-point
visibility. This ideal executor still does not certify an arm insertion path.

Support proposals first use the model-selected XY neighbourhood and prioritize
physical shelf heights near the click. The previous three volume-wide XY
fractions can all hit a central divider or side walls, missing an otherwise
usable compartment. The original volume candidates remain a fallback.

2026-10-22，盒内支撑候选也包括已通过 Inside 检查的现有内容物表面。对已有物体占据的容器，按与其包围盒的重叠量排序，并分散同优先级候选的位置；包围盒只作排序，不用来证明无解。收缩域与完整容积域产生的相同位姿去重。碰撞、自由落定、正式 Inside、原内容物保留与失败回滚检查不变。采样仍有预算，失败不证明连续空间中无解。诊断与自主任务证据分别保存于 `operations/instant_carry_inside_20261021/`。
