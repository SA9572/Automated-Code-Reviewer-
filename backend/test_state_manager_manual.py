import asyncio
from uuid import uuid4

from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


async def main():
    state_manager = StateManager()

    review_id = str(uuid4())

    state = ChangeState(
        review_id=review_id,
        repository="test-repository",
        pull_request_number=1,
        base_commit="abc123",
        head_commit="def456",
        changed_files=["main.py"],
        diff_content="print('Hello World')",
    )

    created = await state_manager.create_state(state)
    print("Created:", created.review_id)

    retrieved = await state_manager.get_state(review_id)
    print("Retrieved:", retrieved.repository)


if __name__ == "__main__":
    asyncio.run(main())
