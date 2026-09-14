---
name: book-case02-visible-object-navigation-check
description: "B02-001：已感知对象完整地址的只读导航检查。"
---

# 已感知对象导航检查

你在原地检查一条路线，本轮不走动。依次执行以下自然语言 SOP：

1. 调用 world-perceive，参数为空对象，观察当前位置和 game_objects。
2. 在本次返回的 game_objects 中，选择一个完整四层 address 与当前地址不同、且不在 known_spatial_memory.tree 中的对象。优先选择床边或同房间附近的对象；只使用本次实际返回的对象，禁止自己编造地址。
3. 将这个对象实际返回的完整 address 数组原样作为 target_address，调用一次 world-navigate。不要使用 target_coord，不要写入记忆，不要对地址进行缩短。如果未找到满足条件的对象，明确说明前置条件不成立，不得声称复现。
4. 无论导航成功还是被拒绝，都如实记录目标地址、结果或错误。不要换目标重试，不要为了绕过拒绝改用坐标或三层地址。
5. 最后调用 world-act，action_type 为 ACT，event 的 predicate 为“检查路线”，object 为上述对象的名称，description 简要写明实际导航结果。不要填写位置或地址；不要提交 MOVE。

不得把导航成功等同于抵达，不得在没有工具证据时声称成功。
