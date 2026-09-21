# 当前模型配置

模型配置是 Studio 公共作者资源；加入实验后成为包内副本。当前 Web 模型中心使用 OpenAI 兼容聊天与 embeddings 接口。运行机需要预先具备可访问的模型服务，项目不负责启动本机模型服务器。

**通过 Studio 配置**

进入基础配置的“模型”，填写用途、服务 API Base URL、明确的模型 ID 和必要的 API Key，然后保存并测试连接。聊天与向量用途分别配置；编辑一个用途不会删除同一模型资源的另一个用途。

服务地址应与兼容 API 的前缀一致。例如服务提供 /v1/chat/completions 与 /v1/embeddings 时，填写 http://127.0.0.1:8888/v1 一类地址。端口 8888 只是示例，应使用实际服务地址。当前模型中心要求明确模型 ID，不接受 auto。连接测试分别请求 /chat/completions 和 /embeddings。

地址里不能嵌入用户名、密码、查询参数或密钥。API Key 填入专用字段；本地不需要鉴权的服务可以留空。保存后看到的“已配置”表示存在凭据绑定，连接是否可用仍以测试结果为准。

现有初始化代码会在空作者库中建立本机模型预设；其中的模型名、端口不保证适用于其他机器。请按实际环境编辑，不将这些值写成案例或系统通用要求。

**实验副本与运行凭据**

模型配置被选入实验时物理复制。随后修改公共模型资源不会改变现有实验；需要调整时编辑对应草稿的模型副本。SEALED 实验需先复制为新的独立实验，不能修改原封存内容。

包内模型文件由 manifest.json 的 entrypoints.models 指定。它保存服务参数和 credential_env 环境变量名，不保存明文 API Key 或公共资源活引用。Studio 保存密钥时创建本机凭据绑定；启动 Run 时在作者侧解析并注入子进程。Runtime 只从执行环境读取凭据。

在 Studio 之外运行包时，先检查包声明的变量名，再在当前终端设置同名环境变量。例如某包声明 GA_CHAT_API_KEY 与 GA_EMBEDDING_API_KEY：

~~~powershell
$env:GA_CHAT_API_KEY = '替换为该主机的聊天服务密钥'
$env:GA_EMBEDDING_API_KEY = '替换为该主机的向量服务密钥'
ga run start ./my-experiment.gaexp ./my-run
~~~

实际包也可能使用 Studio 生成的 GA_MODEL_... 名称，不能假定所有包都使用示例变量。不要把凭据写入实验包、Skill、提交记录或日志。主机密钥管理见 [运行手册](operations-runbook.md)。

**连接故障定位**

| 现象 | 核对内容 |
| --- | --- |
| HTTP 401/403 | 当前用途的 API Key 与服务鉴权设置 |
| 接口不存在 | API Base URL 是否包含服务要求的 /v1 前缀，聊天与向量服务是否填反 |
| 模型不可用 | 模型 ID 是否与服务实际提供的 ID 一致 |
| CLI 缺少凭据 | 目标主机是否设置了包内 credential_env 对应的变量 |
| 公共模型已修正但 Run 仍失败 | Run 使用自己的内嵌副本，核对实际包参数与 Trace |

连接测试通过不能证明所有真实上下文、工具调用或案例行为都已验收。模型超时、重试和取消由调用基础设施处理，不要求 Brain Skill 自己实现网络重试。

代码入口：[model_services.py](../generative_agents/ga_studio/model_services.py)、[Runtime 模型装配](../generative_agents/ga_runtime/package.py)。
