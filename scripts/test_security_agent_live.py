
import asyncio

from app.agents.security_agent import SecurityAgent


async def main():

    agent = SecurityAgent()

    sample_diff = """
diff --git a/app/database.py b/app/database.py
--- a/app/database.py
+++ b/app/database.py
@@ -1,3 +1,6 @@
+def get_user(user_id):
+    query = "SELECT * FROM users WHERE id = " + user_id
+    cursor.execute(query)
"""

    result = await agent.execute({
        "repository": "SecurityAgent-Live-Test",
        "changed_files": ["app/database.py"],
        "diff_content": sample_diff,
    })

    print("\\n========== SECURITY AGENT LIVE TEST ==========")

    print("Agent:", result.agent_name)
    print("Status:", result.status.value)
    print("Summary:", result.summary)
    print("Execution time:", result.execution_time_ms, "ms")
    print("Findings:", result.finding_count)

    for index, finding in enumerate(result.findings, start=1):

        print(f"\\nFinding {index}")
        print("Title:", finding.title)
        print("Severity:", finding.severity.value)
        print("Confidence:", finding.confidence)
        print("File:", finding.file_path)
        print("Rule:", finding.rule_id)
        print("Evidence:", finding.evidence)
        print("Recommendation:", finding.recommendation)

    if result.error:
        print("Error:", result.error)


if __name__ == "__main__":
    asyncio.run(main())
