import asyncio

from app.agents.diff_agent import DiffAgent


SAMPLE_DIFF = """\
diff --git a/app/checkout.py b/app/checkout.py
--- a/app/checkout.py
+++ b/app/checkout.py
@@ -8,3 +8,3 @@
 def calculate_discount(price, discount_percent):
-    discount = price * (discount_percent / 100)
+    discount = price * discount_percent
     return price - discount
"""


async def main():
    agent = DiffAgent()

    result = await agent.execute({
        "repository": "demo/checkout",
        "changed_files": ["app/checkout.py"],
        "diff_content": SAMPLE_DIFF,
    })

    print("\\n========== DIFF AGENT LIVE TEST ==========")
    print("Agent:", result.agent_name)
    print("Status:", result.status.value)
    print("Summary:", result.summary)
    print("Execution time:", result.execution_time_ms, "ms")
    print("Findings:", result.finding_count)

    if result.error:
        print("Error:", result.error)

    for index, finding in enumerate(result.findings, start=1):
        print(f"\\nFinding {index}")
        print("Title:", finding.title)
        print("Severity:", finding.severity.value)
        print("Confidence:", finding.confidence)
        print("File:", finding.file_path)
        print("Lines:", finding.start_line, "-", finding.end_line)
        print("Evidence:", finding.evidence)
        print("Recommendation:", finding.recommendation)


if __name__ == "__main__":
    asyncio.run(main())