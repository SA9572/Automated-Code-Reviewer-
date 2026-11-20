import asyncio
from agent_framework import ChatOptions, Message
from app.services.llm_service import LLMService
async def main():
    client = LLMService().get_client()
    try:
        response = await client.get_response(
            messages=[
                Message(role="user", contents=["Reply with exactly: LLM_TEST_OK"])
            ],
            options=ChatOptions(temperature=0.1),
        )
        print("Response type:", type(response).__name__)
        print("Response text:", repr(getattr(response, "text", None)))
        print("Response attributes:", [
            name for name in dir(response)
            if not name.startswith("_")
        ])
        messages = getattr(response, "messages", None)
        print("Messages:", repr(messages))
        if messages:
            for index, message in enumerate(messages):
                print(f"Message {index} type:", type(message).__name__)
                print(f"Message {index} contents:", repr(
                    getattr(message, "contents", None)
                ))
    finally:
        await client.close()
asyncio.run(main())
