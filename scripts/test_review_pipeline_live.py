import asyncio
from app.agents.diff_agent import DiffAgent
from app.agents.security_agent import SecurityAgent
from app.agents.style_agent import StyleAgent
from app.agents.test_coverage_agent import TestCoverageAgent
from app.agents.fix_suggestion_agent import FixSuggestionAgent
from app.orchestration.review_pipeline import ReviewPipeline
from app.state.state_manager import StateManager
SOURCE_DIFF = """
diff --git a/app/checkout.py b/app/checkout.py
--- a/app/checkout.py
+++ b/app/checkout.py
@@ -1,4 +1,4 @@
 def calculate_discount(price, discount_percent):
-    return price - (price * discount_percent / 100)
+    return price - (price * discount_percent)
 def checkout(price, discount_percent):
     return calculate_discount(price, discount_percent)
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
@@ -1,8 +1,8 @@
-import os
+import os
+import json
-def GetUserData(UserID):
-    result = UserID
-    unused_value = 100
-    return result
+def GetUserData(UserID):
+    result = UserID
+    unused_value = 100
+    return result
"""
TEST_DIFF = """
diff --git a/tests/test_checkout.py b/tests/test_checkout.py
--- a/tests/test_checkout.py
+++ b/tests/test_checkout.py
@@ -1,4 +1,5 @@
+from app.checkout import calculate_discount
-def test_checkout():
-    assert True
+def test_calculate_discount():
+    assert calculate_discount(100, 10) == 90
"""
async def main():
    state_manager = StateManager()
    agents = [
        DiffAgent(),
        SecurityAgent(),
        StyleAgent(),
        TestCoverageAgent(),
    ]
    fix_agent = FixSuggestionAgent()
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=agents,
        max_concurrency=4,
        fix_suggestion_agent=fix_agent,
    )
    # Collect unique services so shared clients are never closed twice.
    services = {}
    for agent in [*agents, fix_agent]:
        service = agent.llm_service
        services[id(service)] = service
    try:
        print("\n========== LIVE REVIEW PIPELINE ==========")
        print("Repository: demo/live-pipeline")
        print("Execution: Four specialist agents + Reviewer Manager")
        print("Fix suggestions: Enabled")
        print("State storage: In-memory")
        print("Patch application: Disabled")
        review = await pipeline.run(
            repository="demo/live-pipeline",
            base_commit="live-test-base",
            head_commit="live-test-head",
            diff_content=SOURCE_DIFF,
            changed_files=[
                "app/checkout.py",
                "app/database.py",
                "app/user.py",
            ],
            test_diff=TEST_DIFF,
            test_files=["tests/test_checkout.py"],
        )
        print("\n========== FINAL REVIEW ==========")
        print(f"Review ID: {review.review_id}")
        print(f"Status: {review.status.value}")
        print(f"Recommendation: {review.recommendation.value}")
        print(f"Consolidated findings: {len(review.findings)}")
        print("\n========== SPECIALIST AGENT RESULTS ==========")
        for name, result in review.agent_results.items():
            print(f"\nAgent: {name}")
            print(f"Status: {result.status.value}")
            print(f"Execution time: {result.execution_time_ms} ms")
            print(f"Findings: {result.finding_count}")
            print(f"Summary: {result.summary}")
            if result.error:
                print(f"Error: {result.error}")
        print("\n========== CONSOLIDATED FINDINGS ==========")
        for index, finding in enumerate(review.findings, start=1):
            print(f"\nFinding {index}")
            print(f"Agent: {finding.agent_name}")
            print(f"Severity: {finding.severity.value}")
            print(f"File: {finding.file_path}")
            print(f"Title: {getattr(finding, 'title', 'N/A')}")
        # Independently retrieve the state saved by the pipeline.
        saved_state = await state_manager.get_state(review.review_id)
        print("\n========== STATE PERSISTENCE ==========")
        print(f"Retrieved review: {saved_state.review_id}")
        print(f"Saved status: {saved_state.status.value}")
        print(f"Saved agent results: {len(saved_state.agent_results)}")
        print(f"Saved final findings: {len(saved_state.final_findings)}")
        # Independently retrieve generated fix suggestions.
        suggestions = await state_manager.get_fix_suggestions(
            review.review_id
        )
        print("\n========== FIX SUGGESTIONS ==========")
        print(f"Suggestions retrieved: {len(suggestions)}")
        for index, suggestion in enumerate(suggestions, start=1):
            print(f"\nSuggestion {index}")
            print(f"ID: {suggestion.suggestion_id}")
            print(f"File: {suggestion.file_path}")
            print(f"Title: {suggestion.title}")
            print(
                "Validation status: "
                f"{suggestion.validation_status.value}"
            )
            print(
                "Approval status: "
                f"{suggestion.approval_status.value}"
            )
        expected_agents = {
            "DiffAgent",
            "SecurityAgent",
            "StyleAgent",
            "TestCoverageAgent",
        }
        actual_agents = set(saved_state.agent_results.keys())
        missing_agents = expected_agents - actual_agents
        failed_agents = [
            name
            for name in expected_agents
            if name in saved_state.agent_results
            and saved_state.agent_results[name].status.value != "SUCCESS"
        ]
        print("\n========== INTEGRATION CHECKS ==========")
        checks = {
            "Review completed": review.status.value == "COMPLETED",
            "All four agents recorded": not missing_agents,
            "All four agents successful": not failed_agents,
            "State persisted and retrieved": (
                saved_state.review_id == review.review_id
            ),
            "Consolidated review persisted": (
                len(saved_state.final_findings) == len(review.findings)
            ),
        }
        for check, passed in checks.items():
            print(f"[{'PASS' if passed else 'FAIL'}] {check}")
        if missing_agents:
            print(f"Missing agents: {sorted(missing_agents)}")
        if failed_agents:
            print(f"Unsuccessful agents: {sorted(failed_agents)}")
        if not suggestions:
            print(
                "[INFO] No fix suggestions were returned. "
                "Suggestion generation is non-critical."
            )
        if not all(checks.values()):
            print("\nLIVE PIPELINE TEST: FAILED")
            return 1
        print("\nLIVE PIPELINE TEST: SUCCESS")
        print(
            "Note: Suggested patches were retrieved only. "
            "No patch was applied or approved."
        )
        return 0
    finally:
        print("\n========== LLM RESOURCE CLEANUP ==========")
        for service in services.values():
            try:
                await service.close()
                print("[CLOSED] LLM service")
            except Exception as exc:
                print(
                    "[CLEANUP WARNING] "
                    f"{type(exc).__name__}"
                )
if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
