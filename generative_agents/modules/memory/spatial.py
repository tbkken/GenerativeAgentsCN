"""generative_agents.memory.spatial"""

from generative_agents.modules import utils


class Spatial:
    """智能体已经认识的世界—区域—场所—对象空间树。"""

    def __init__(self, tree, address=None, random_source=None):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            tree: 需要遍历、校验或转换的层级树结构。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 默认值：`None`。
            random_source: 运行私有的伪随机数生成器，用于保证快照恢复后的确定性。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if random_source is None:
            raise ValueError("Spatial requires an injected random source")
        self._rng = random_source
        self.tree = tree
        self.address = dict(address or {})
        if (
            "sleeping" not in self.address
            and "睡觉" not in self.address
            and "living_area" in self.address
        ):
            # self.address["sleeping"] = self.address["living_area"] + ["bed"]
            self.address["睡觉"] = self.address["living_area"] + ["床"]

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.tree)

    def add_leaf(self, address):
        """执行 `Spatial` 的`add``leaf`操作。

        参数:
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            无返回值。
        """

        def _add_leaf(left_address, tree):
            """执行`add``leaf`的内部处理，供当前模块或类复用。

            参数:
                left_address: 传入当前算法的`left``address`；其结构与有效范围由类型注解和调用协议共同限定。
                tree: 需要遍历、校验或转换的层级树结构。

            返回:
                无返回值。
            """
            if len(left_address) == 2:
                leaves = tree.setdefault(left_address[0], [])
                if left_address[1] not in leaves:
                    leaves.append(left_address[1])
            elif len(left_address) > 2:
                _add_leaf(left_address[1:], tree.setdefault(left_address[0], {}))

        _add_leaf(address, self.tree)

    def find_address(self, hint, as_list=True):
        """执行 `Spatial` 的`find``address`操作。

        参数:
            hint: 帮助模型、解析器或选择器缩小候选范围的提示信息。
            as_list: 是否以列表形式返回结果；否则返回调用方要求的结构。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        address = []
        for key, path in self.address.items():
            if key in hint:
                address = path
                break
        if as_list:
            return address
        return ":".join(address)

    def get_leaves(self, address):
        """获取`leaves`。

        参数:
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。
        """

        def _get_tree(address, tree):
            """获取`tree`。

            参数:
                address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。
                tree: 需要遍历、校验或转换的层级树结构。

            返回:
                返回函数计算得到的结果。
            """
            if not address:
                if isinstance(tree, dict):
                    return list(tree.keys())
                return tree
            if address[0] not in tree:
                return []
            return _get_tree(address[1:], tree[address[0]])

        return _get_tree(address, self.tree)

    def random_address(self):
        """执行 `Spatial` 的`random``address`操作。

        返回:
            返回函数计算得到的结果。
        """
        address, tree = [], self.tree
        while isinstance(tree, dict):
            roots = [r for r in tree if len(tree[r]) > 0]
            address.append(self._rng.choice(roots))
            tree = tree[address[-1]]
        address.append(self._rng.choice(tree))
        return address
