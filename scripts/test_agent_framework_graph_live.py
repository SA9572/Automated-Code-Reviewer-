r"""Live Microsoft Agent Framework graph test (four Groq-backed specialists).

Run from the repository root after activating the project virtual environment:
    $env:PYTHONPATH = "$PWD\backend"
    python .\scripts\test_agent_framework_graph_live.py
"""

import asyncio
import sys

from app.agents.diff_agent import DiffAgent
from app.agents.security_agent import SecurityAgent
from app.agents.style_agent import StyleAgent
from app.agents.test_coverage_agent import TestCoverageAgent
from app.database.connection import AsyncSessionLocal, close_db, init_db
from app.orchestration.agent_framework_workflow import AgentFrameworkWorkflow
from app.orchestration.parallel_orchestrator import ParallelOrchestrator
from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


SOURCE_DIFF = """\
diff --git a/app/checkout.py b/app/checkout.py
--- a/app/checkout.py
+++ b/app/checkout.py
@@ -1,3 +1,3 @@
 def calculate_discount(price, discount_percent):
-    return price - (price * discount_percent / 100)
+    return price - (price * discount_percent)
diff --git a/app/database.py b/app/database.py
--- a/app/database.py
+++ b/app/database.py
@@ -1,3 +1,3 @@
 def get_user(user_id, connection):
-    query = "SELECT * FROM users WHERE id = ?"
+    query = "SELECT * FROM users WHERE id = " + user_id
     return connection.execute(query)
diff --git a/app/user.py b/app/user.py
--- a/app/user.py
+++ b/app/user.py
@@ -1,4 +1,5 @@
 import os
+import json
+
 def get_user(user_id):
-    return user_id
+    return str(user_id)
"""

TEST_DIFF = """\
diff --git a/tests/test_checkout.py b/tests/test_checkout.py
--- a/tests/test_checkout.py
+++ b/tests/test_checkout.py
@@ -1,2 +1,4 @@
+from app.checkout import calculate_discount
+
 def test_checkout():
-    assert True
+    assert calculate_discount(100, 10) == 90
"""


async def main() -> int:
    agents = [
        DiffAgent(),
        SecurityAgent(),
        StyleAgent(),
        TestCoverageAgent(),
    ]
    services = {
        id(agent.llm_service): agent.llm_service
        for agent in agents
        if getattr(agent, "llm_service", None) is not None
    }
    await init_db()
    state_manager = StateManager(session_factory=AsyncSessionLocal)
    orchestrator = ParallelOrchestrator(
        agents=agents,
        state_manager=state_manager,
        max_concurrency=4,
    )
    graph = AgentFrameworkWorkflow(orchestrator)

    state = ChangeState(
        repository="demo/agent-framework-live",
        base_commit="live-test-base",
        head_commit="live-test-head",
        changed_files=[
            "app/checkout.py",
            "app/database.py",
            "app/user.py",
        ],
        diff_content=SOURCE_DIFF,
    )
    await state_manager.create_state(state)

    try:
        print("========== MICROSOFT AGENT FRAMEWORK GRAPH LIVE TEST ==========")
        print("Review ID:", state.review_id)
        print("Graph: Dispatcher -> four parallel specialists -> fan-in")

        results = await graph.run(
            {
                "review_id": state.review_id,
                "repository": state.repository,
                "changed_files": state.changed_files,
                "diff_content": SOURCE_DIFF,
                "test_diff": TEST_DIFF,
                "test_files": ["tests/test_checkout.py"],
            },
            state.review_id,
        )

        saved_state = await state_manager.get_state(state.review_id)
        expected = {
            "DiffAgent",
            "SecurityAgent",
            "StyleAgent",
            "TestCoverageAgent",
        }
        actual = set(results)
        persisted = set(saved_state.agent_results)

        for name in sorted(results):
            result = results[name]
            print(
                f"[{result.status.value}] {name}: "
                f"{result.finding_count} finding(s), "
                f"{result.execution_time_ms} ms"
            )
            if result.error:
                print("  Error:", result.error)

        checks = {
            "All four graph outputs returned": actual == expected,
            "All four outputs persisted": persisted == expected,
            "Every specialist completed successfully": all(
                result.status.value == "SUCCESS"
                for result in results.values()
            ),
        }
        print("\n========== GRAPH VERIFICATION ==========")
        for label, passed in checks.items():
            print(f"[{'PASS' if passed else 'FAIL'}] {label}")

        if not all(checks.values()):
            print("LIVE MICROSOFT AGENT FRAMEWORK GRAPH: FAILED")
            return 1

        print("LIVE MICROSOFT AGENT FRAMEWORK GRAPH: SUCCESS")
        return 0
    finally:
        print("\n========== LLM RESOURCE CLEANUP ==========")
        errors = []
        for service in services.values():
            try:
                await service.close()
                print("[CLOSED] LLM service")
            except Exception as exc:
                errors.append(exc)
                print("[CLEANUP FAILED]", type(exc).__name__)
        await close_db()
        if errors and sys.exc_info()[0] is None:
            raise RuntimeError("LLM resource cleanup failed.") from errors[0]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
