"""generative_agents.utils.arguments"""

import os
import json
import copy
from typing import Any


def load_dict(str_dict: str, flavor: str = "json") -> dict:
    """加载`dict`。

    参数:
        str_dict: 传入当前算法的`str``dict`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。
        flavor: 底层组件或模型实现的变体标识。 类型：`str`。 默认值：`'json'`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        Exception: 当底层操作报告该异常条件时抛出。
    """

    if not str_dict:
        return {}
    if isinstance(str_dict, str) and os.path.isfile(str_dict):
        with open(str_dict, "r", encoding="utf-8") as f:
            dict_obj = json.load(f)
    elif isinstance(str_dict, str):
        dict_obj = json.loads(str_dict)
    elif isinstance(str_dict, dict):
        dict_obj = copy_dict(str_dict)
    else:
        raise Exception("Unexpected str_dict {}({})".format(str_dict, type(str_dict)))
    assert flavor == "json", "Unexpected flavor for load_dict: " + str(flavor)
    return dict_obj


def save_dict(dict_obj: Any, path: str, indent: int = 2) -> str:
    """保存`dict`。

    参数:
        dict_obj: 待复制、比较、映射或序列化的字典对象。 类型：`Any`。
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。
        indent: 序列化或渲染文本时使用的缩进空格数。 类型：`int`。 默认值：`2`。

    返回:
        返回处理后的文本或稳定标识。
    """

    with open(path, "w") as f:
        f.write(json.dumps(load_dict(dict_obj), indent=indent, ensure_ascii=False))
    return path


def update_dict(src_dict: dict, new_dict: dict, soft_update: bool = False) -> dict:
    """更新`dict`。

    参数:
        src_dict: 传入当前算法的`src``dict`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict`。
        new_dict: 传入当前算法的`new``dict`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict`。
        soft_update: 传入当前算法的`soft``update`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`bool`。 默认值：`False`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    if not src_dict:
        return new_dict
    if not new_dict:
        return src_dict
    assert isinstance(src_dict, dict) and isinstance(new_dict, dict), (
        "update_dict only support dict, get src {} and new {}".format(
            type(src_dict), type(new_dict)
        )
    )
    for k, v in new_dict.items():
        if not src_dict.get(k):
            src_dict[k] = v
        elif isinstance(v, dict):
            v = update_dict(src_dict.get(k, {}), v, soft_update)
            src_dict[k] = v
        elif not soft_update:
            src_dict[k] = v
    return src_dict


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


def dict_equal(dict_a: dict, dict_b: dict) -> bool:
    """执行 的`dict``equal`操作。

    参数:
        dict_a: 传入当前算法的`dict``a`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict`。
        dict_b: 传入当前算法的`dict``b`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict`。

    返回:
        条件成立时返回 `True`，否则返回 `False`。
    """

    if not isinstance(dict_a, dict) or not isinstance(dict_b, dict):
        return False
    if dict_a.keys() != dict_b.keys():
        return False
    for k, v in dict_a.items():
        if not isinstance(v, type(dict_b[k])):
            return False
        if isinstance(v, dict) and not dict_equal(v, dict_b[k]):
            return False
        if v != dict_b[k]:
            return False
    return True


def copy_dict(dict_obj: dict) -> dict:
    """复制`dict`。

    参数:
        dict_obj: 待复制、比较、映射或序列化的字典对象。 类型：`dict`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    if not dict_obj:
        return {}
    try:
        return copy.deepcopy(dict_obj)
    except:  # pylint: disable=bare-except
        new_dict = {}
        for k, v in dict_obj.items():
            if isinstance(v, (list, tuple)):
                new_dict[k] = [copy_dict(e) for e in v]
            elif isinstance(v, dict):
                new_dict[k] = copy_dict(v)
            else:
                new_dict[k] = v
        return new_dict


def map_dict(dict_obj: dict, mapper: callable) -> dict:
    """执行 的地图`dict`操作。

    参数:
        dict_obj: 待复制、比较、映射或序列化的字典对象。 类型：`dict`。
        mapper: 传入当前算法的`mapper`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`callable`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    if not dict_obj:
        return {}
    new_dict = {}
    for k, v in dict_obj.items():
        if isinstance(v, (tuple, list)):
            new_dict[k] = [
                map_dict(mapper(e), mapper) if isinstance(e, dict) else mapper(e)
                for e in v
            ]
        elif isinstance(v, dict):
            new_dict[k] = map_dict(mapper(v), mapper)
        else:
            new_dict[k] = mapper(v)
    return new_dict
