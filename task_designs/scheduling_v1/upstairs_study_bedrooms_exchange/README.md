# 二层双卧室与中央书房跨房载具巡回分拣及吸尘清洁（`upstairs_study_bedrooms_exchange`）

状态：**已在远端 RTX PRO 6000 完成初始场景冻结、双策略特权参考流程验收、物理反例校验，并完成 `gpt-6.1-sol`、`gpt-6.1-terra`、`gemini-4-hydrogen-low`、`gemini-3.8-flash-low` 全套强弱模型消融回合**。

- 生成脚本：[make_recipe.py](make_recipe.py)
- 静态配方：[recipe.json](recipe.json)
- 冻结契约：[task.json](task.json)（初始场景快照 `initial_scene.json` SHA-256：`aee964f69b1d724a61e73ad296a59740f8a2ce5fc93edeb7fd3b0bb37fcc6727`，存放于仓库外 `experiments/hot_lunch_benchmark_20261113/inputs/upstairs_study_bedrooms_exchange/`）
- 底层场景：`vacuuming_floors` instance 301, seed 0（`house_double_floor_upper` 二层四连通房间：西卧 `bedroom_0`、中央书房 `living_room_0`、东卧 `bedroom_1`、北工具卫浴 `bathroom_0`）
- 规划结构特征：**零家电计时器（`0 s` 等待）纯空间双向回程配货任务（M1 + M3）**，结合半未知跨房找物（8 件异类物品散落三房）、双向 4 换 4 托盘置换（`54 × 54 cm` 托盘）、共用清洁工具巡回（抹布擦 6 处 + 吸尘器吸两间卧室地板）与顺路关灯。
- 远端 PRO 6000 双策略实测参考差距：
  - `optimal_carrier_tour`（最优载具巡回）：`894.1 s` 机器人时间、`134.8 m` 路程、`0.0 s` 等待
  - `habit_sequential`（无绕路习惯顺序）：`1,040.9 s` 机器人时间（`+16.4%`）、`262.9 m` 路程（`+95.0%`）、`0.0 s` 等待
