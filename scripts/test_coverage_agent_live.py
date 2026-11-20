import asyncio
from app.agents.test_coverage_agent import TestCoverageAgent
async def main():
    agent = TestCoverageAgent()
    source_diff = """
diff --git a/app/discount.py b/app/discount.py
--- a/app/discount.py
+++ b/app/discount.py
@@ -0,0 +1,3 @@
+def calculate_discount(price, discount_percent):
+    return price - (price * discount_percent / 100)
"""
    test_diff = """
diff --git a/tests/test_discount.py b/tests/test_discount.py
--- a/tests/test_discount.py
+++ b/tests/test_discount.py
@@ -0,0 +1,5 @@
+def test_calculate_discount():
+    from app.discount import calculate_discount
+    result = calculate_discount(100, 10)
+    assert result == 90
"""
    try:
        result = await agent.execute({
            "repository": "TestCoverageAgent-Live-Test",
            "changed_files": ["app/discount.py"],
            "test_files": ["tests/test_discount.py"],
            "diff_content": source_diff,
            "test_diff": test_diff,
        })
        print("\n========== TEST COVERAGE AGENT LIVE TEST ==========")
        print("Agent:", result.agent_name)
        print("Status:", result.status.value)
        print("Summary:", result.summary)
        print("Execution time:", result.execution_time_ms, "ms")
        print("Findings:", result.finding_count)
        for index, finding in enumerate(result.findings, start=1):
            print(f"\nFinding {index}")
            print("Title:", finding.title)
            print("Severity:", finding.severity.value)
            print("Confidence:", finding.confidence)
            print("File:", finding.file_path)
            print("Rule:", getattr(finding, "rule_id", "Not exposed"))
            print("Evidence:", finding.evidence)
            print("Recommendation:", finding.recommendation)
        if result.error:
            print("Error:", result.error)
    finally:
        if getattr(agent, "llm_service", None):
            await agent.llm_service.close()
if __name__ == "__main__":
    asyncio.run(main())
