
import asyncio

from app.agents.style_agent import StyleAgent


async def main():

    agent = StyleAgent()

    sample_diff = """
diff --git a/app/user.py b/app/user.py
--- a/app/user.py
+++ b/app/user.py
@@ -1,5 +1,8 @@
+import os
+
+UserName = "Saurabh"
+
+def GetUserData():
+    unused_value = 100
+    return UserName
"""

    result = await agent.execute({
        "repository": "StyleAgent-Live-Test",
        "changed_files": ["app/user.py"],
        "diff_content": sample_diff,
    })

    print("\n========== STYLE AGENT LIVE TEST ==========")

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
        print("Evidence:", finding.evidence)
        print("Recommendation:", finding.recommendation)

    if result.error:
        print("Error:", result.error)


if __name__ == "__main__":
    asyncio.run(main())
