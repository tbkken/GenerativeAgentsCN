"""A paid API request is made only when this script is explicitly run."""
from common import ROOT, checked_response_text, make_client, require_env, save_response


def main() -> None:
    client = make_client()
    response = client.responses.create(
        model=require_env("OPENAI_MODEL"),
        instructions="你是一名活动筹备助手。区分材料事实与待确认事项。回答使用中文。",
        input="虚构的青禾社区学习中心拟在2026-10-17举行开放日。请列出排期前需要收集的五类信息；不要编造预约。",
        max_output_tokens=4096,
        store=False,
    )
    save_response(response, ROOT / "output" / "first-response.json")
    print(checked_response_text(response))
    print("usage:", response.usage.model_dump() if response.usage else None)


if __name__ == "__main__":
    main()
