import asyncio
import logging

from app.agents.fix_suggestion_agent import (
    FIX_SUGGESTION_SYSTEM_PROMPT,
    FixSuggestionAgent,
)
from app.schemas.finding import Finding, Severity

logging.basicConfig(level=logging.INFO)


async def main():
    agent = FixSuggestionAgent()

    finding = Finding(
        finding_id="test-finding-001",
        agent_name="SecurityAgent",
        title="Potential SQL injection",
        description="User input is directly interpolated into a SQL query.",
        severity=Severity.HIGH,
        confidence=0.98,
        file_path="app/users.py",
        start_line=10,
        end_line=10,
        evidence='query = f"SELECT * FROM users WHERE name = {username}"',
        recommendation="Use a parameterized SQL query.",
    )

    diff = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -10,1 +10,1 @@
-query = f"SELECT * FROM users WHERE name = {username}"
+query = "SELECT * FROM users WHERE name = ?"
"""

    try:
        prompt = agent._build_user_prompt([finding], diff)

        response = await agent.generate_response(
            system_prompt=FIX_SUGGESTION_SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        print("\n========== RAW LLM RESPONSE ==========")
        print(response)

        print("\n========== PARSED CANDIDATES ==========")
        candidates = agent._parse_response(response)

        for index, candidate in enumerate(candidates, start=1):
            print(f"\nCandidate {index}:")
            print("Finding ID:", candidate.get("finding_id"))
            print("File:", candidate.get("file_path"))
            print("Generated diff:")
            print(candidate.get("unified_diff"))

            print(
                "Validator result:",
                agent._has_basic_unified_diff_structure(
                    candidate.get("unified_diff", ""),
                    "app/users.py",
                ),
            )

    except Exception as exc:
        print("\nDiagnostic failed:", type(exc).__name__, str(exc))

    finally:
        await agent.llm_service.close()


asyncio.run(main())