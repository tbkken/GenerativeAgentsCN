---
name: workday-place-interaction
example_input: "林晨到达员工工位，请给出一次合理交互。"
description: "把林晨配置版在住宅、办公室、饭店和咖啡厅中的日常阶段转换为一次真实家具或设施交互建议。"
---

# 工作日场所交互

仅在已经到达阶段目的地时选择一个最小动作，不执行世界动作。

床用于起床；家庭卫生间用于洗漱；家庭厨房用于早餐；员工工位用于工作；饭店收银台与用餐区用于点餐、午餐和结账；咖啡水吧用于点取咖啡；咖啡厅沙发区用于下午休息。只使用观察中的真实对象和选择键；对象缺失时等待。一轮只建议一次 INTERACT 或 WAIT。

只返回 JSON：`{"action_type":"INTERACT|WAIT","description":"动作","selection_key":"真实选择键或空字符串","request":"设施请求","completion":"成功判据"}`。
