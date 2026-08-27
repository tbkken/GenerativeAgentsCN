"""generative_agents.memory.event"""


class Event:
    """发生在某个语义地址上的主语—谓语—宾语事件。"""

    def __init__(
        self,
        subject,
        predicate=None,
        object=None,
        address=None,
        describe=None,
        emoji=None,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。
            predicate: 事件三元组中描述主体与宾语关系的谓词。 默认值：`None`。
            object: 事件三元组中的宾语或当前交互对象。 默认值：`None`。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 默认值：`None`。
            describe: 事件、行为或记忆的人类可读描述文本。 默认值：`None`。
            emoji: 用于回放或界面展示行为含义的单个表情符号。 默认值：`None`。

        返回:
            无返回值。
        """
        self.subject = subject
        # self.predicate = predicate or "is"
        # self.object = object or "idle"
        self.predicate = predicate or "此时"
        self.object = object or "空闲"
        self._describe = describe or ""
        self.address = address or []
        self.emoji = emoji or ""

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        if self._describe:
            des = "{}".format(self._describe)
        else:
            des = "{} {} {}".format(self.subject, self.predicate, self.object)
        # if self.emoji:
        #     des += "[{}]".format(self.emoji)
        if self.address:
            des += " @ " + ":".join(self.address)
        return des

    def __hash__(self):
        """计算哈希当前对象。

        返回:
            返回函数计算得到的结果。
        """
        return hash(
            (
                self.subject,
                self.predicate,
                self.object,
                self._describe,
                ":".join(self.address),
            )
        )

    def __eq__(self, other):
        """执行`eq`的内部处理，供当前模块或类复用。

        参数:
            other: 当前操作使用的`other`。

        返回:
            返回函数计算得到的结果。
        """
        if isinstance(other, Event):
            return hash(self) == hash(other)
        return False

    def update(self, predicate=None, object=None, describe=None):
        # self.predicate = predicate or "is"
        # self.object = object or "idle"
        """执行 `Event` 的`update`操作。

        参数:
            predicate: 事件三元组中描述主体与宾语关系的谓词。 默认值：`None`。
            object: 事件三元组中的宾语或当前交互对象。 默认值：`None`。
            describe: 事件、行为或记忆的人类可读描述文本。 默认值：`None`。

        返回:
            无返回值。
        """
        self.predicate = predicate or "此时"
        self.object = object or "空闲"
        self._describe = describe or self._describe

    def to_id(self):
        """执行 `Event` 的`to``id`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self.subject, self.predicate, self.object, self._describe

    def fit(self, subject=None, predicate=None, object=None):
        """执行 `Event` 的`fit`操作。

        参数:
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。 默认值：`None`。
            predicate: 事件三元组中描述主体与宾语关系的谓词。 默认值：`None`。
            object: 事件三元组中的宾语或当前交互对象。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        if subject and self.subject != subject:
            return False
        if predicate and self.predicate != predicate:
            return False
        if object and self.object != object:
            return False
        return True

    def to_dict(self):
        """执行 `Event` 的`to``dict`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "describe": self._describe,
            "address": self.address,
            "emoji": self.emoji,
        }

    def get_describe(self, with_subject=True):
        """获取`describe`。

        参数:
            with_subject: 控制或表示`with``subject`条件的布尔值。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        describe = self._describe or "{} {}".format(self.predicate, self.object)
        subject = ""
        if with_subject:
            if self.subject not in describe:
                subject = self.subject + " "
        else:
            if describe.startswith(self.subject + " "):
                describe = describe[len(self.subject) + 1 :]
        return "{}{}".format(subject, describe)

    @classmethod
    def from_dict(cls, config):
        """执行 `Event` 的`from``dict`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            返回函数计算得到的结果。
        """
        return cls(**config)

    @classmethod
    def from_list(cls, event):
        """执行 `Event` 的`from``list`操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            返回函数计算得到的结果。
        """
        if len(event) == 3:
            return cls(event[0], event[1], event[2])
        return cls(event[0], event[1], event[2], event[3])
