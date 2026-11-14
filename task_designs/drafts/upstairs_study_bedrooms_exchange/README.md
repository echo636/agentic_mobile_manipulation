# 二层双卧室与中央书房跨房载具巡回分拣及吸尘清洁（`upstairs_study_bedrooms_exchange`）

状态：**静态配方与二轮 CPU 可通行图/几何审校已完成，尚未在 GPU 上构建冻结场景（草稿）**。

- 生成脚本：[make_recipe.py](make_recipe.py)
- 静态配方：[recipe.json](recipe.json)
- 底层场景：`vacuuming_floors` instance 301, seed 0（`house_double_floor_upper` 二层四连通房间：西卧 `bedroom_0`、中央书房 `living_room_0`、东卧 `bedroom_1`、北工具卫浴 `bathroom_0`）
- 规划结构特征：**零家电计时器（`0 s` 等待）纯空间路由任务**，结合半未知跨房找物（8 件异类物品散落三房）、双向 4 换 4 托盘置换（`54 × 54 cm` 托盘）、共用清洁工具巡回（抹布擦 6 处 + 吸尘器吸两间卧室地板）与顺路关灯。
- CPU 精确可通行图（`0.05 m/px`，`k=15` 腐蚀）预审差距：
  - `optimal_carrier_tour`：`80` 步、`114.8 m` 路程、`891.8 s` 机器人时间（`0 s` 等待）
  - `habit_sequential`（无绕路习惯顺序）：`83` 步、`261.2 m` 路程（`+127.6%`）、`1,150.0 s` 机器人时间（`+29.0%`）
