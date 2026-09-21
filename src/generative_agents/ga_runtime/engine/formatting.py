"""generative_agents.utils.arguments"""

import json








def dump_dict(dict_obj: dict, flavor: str = "table:2") -> str:
    """执行 的`dump``dict`操作。

    参数:
        dict_obj: 待复制、比较、映射或序列化的字典对象。 类型：`dict`。
        flavor: 底层组件或模型实现的变体标识。 类型：`str`。 默认值：`'table:2'`。

    返回:
        返回处理后的文本或稳定标识。
    """

    if not dict_obj:
        return ""
    if flavor.startswith("table:"):

        def _get_lines(value, indent=0):
            """获取`lines`。

            参数:
                value: 当前操作使用的`value`。
                indent: 序列化或渲染文本时使用的缩进空格数。 默认值：`0`。

            返回:
                返回函数计算得到的结果。
            """
            max_size = int(flavor.split(":")[1]) - indent - 2
            lines = []
            for k, v in value.items():
                if v is None:
                    continue
                if isinstance(v, (dict, tuple, list, set)) and not v:
                    continue
                if isinstance(v, dict) and len(str(k) + str(v)) > max_size:
                    lines.append("{}{}:".format(indent * " ", k))
                    lines.extend(_get_lines(v, indent + 2))
                elif (
                    isinstance(v, (tuple, list, set))
                    and len(str(k) + str(v)) > max_size
                ):
                    lines.append("{}{}:".format(indent * " ", k))
                    for idx, ele in enumerate(v):
                        if isinstance(ele, dict) and len(str(ele)) > max_size:
                            lines.append(
                                "{}[{}.{}]:".format((indent + 2) * " ", k, idx)
                            )
                            lines.extend(_get_lines(ele, indent + 4))
                        else:
                            lines.append(
                                "{}<{}>{}".format((indent + 2) * " ", idx, ele)
                            )
                elif isinstance(v, bool):
                    lines.append(
                        "{}{}: {}".format(indent * " ", k, "true" if v else "false")
                    )
                elif hasattr(v, "__name__"):
                    lines.append(
                        "{}{}: {}({})".format(indent * " ", k, v.__name__, type(v))
                    )
                else:
                    lines.append("{}{}: {}".format(indent * " ", k, v))
            return lines

        lines = _get_lines(dict_obj) or [
            "  {}: {}".format(k, v) for k, v in dict_obj.items()
        ]
        return "\n".join(lines)
    return json.dumps(dict_obj, ensure_ascii=False)
