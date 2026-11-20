import json
import logging
import re
from typing import Any

from app.agents.base_agent import AgentOutput, BaseAgent
from app.schemas.finding import Finding
from app.schemas.fix_suggestion import FixSuggestion
from app.security.unicode_utils import repair_unicode

logger = logging.getLogger(__name__)


FIX_SUGGESTION_SYSTEM_PROMPT = """
You are a senior software engineer specializing in safe code remediation.

Your task is to propose minimal, targeted code fixes for confirmed code review
findings.

STRICT RULES:

1. Only propose fixes supported by the supplied finding and original diff.
2. Never invent source code or assume unavailable project context.
3. Preserve existing behavior unless the finding requires a change.
4. Generate a unified diff, not a full replacement file.
5. Use the exact file path associated with the finding.
6. Do not modify unrelated files or introduce unrelated refactoring.
7. Do not include secrets, credentials, or private data in patches.
8. Never generate patches that execute destructive commands or access
   external systems.
9. If a finding cannot be fixed reliably with the available context,
   do not generate a suggestion for it.
10. Do not claim that a patch has been tested, validated, or applied.

UNIFIED DIFF REQUIREMENTS:

11. Every unified diff MUST contain a valid hunk header with line ranges.
12. Use this format:
    @@ -OLD_START,OLD_COUNT +NEW_START,NEW_COUNT @@
13. OLD_COUNT must equal the number of removed and context lines.
14. NEW_COUNT must equal the number of added and context lines.
15. NEVER use an incomplete hunk header such as @@.
16. Use the finding's start_line and end_line when available.
17. Include the actual replacement code in the diff.
18. Return a complete unified diff, not a conceptual patch.
19. Do not omit required context lines.
20. Ensure the diff is internally consistent and syntactically valid.
21. Every removed line must begin with a single minus marker.
22. Every added line must begin with a single plus marker.
23. Do not include Markdown code fences around the unified diff.
24. Do not include explanations or comments outside the JSON response.
25. If the available evidence is insufficient to produce a reliable patch,
    return no suggestion for that finding.

EXAMPLE OF A VALID UNIFIED DIFF:

--- a/app/example.py
+++ b/app/example.py
@@ -10,1 +10,2 @@
-old_value = get_user_input()
+new_value = validate_user_input()
+result = process_input(new_value)

Return ONLY valid JSON in this exact structure:

{
  "fix_suggestions": [
    {
      "finding_id": "original finding ID",
      "file_path": "path/to/file.py",
      "title": "Short fix title",
      "explanation": "Explain what the proposed fix changes and why.",
      "unified_diff": "valid unified diff with file headers and hunk ranges"
    }
  ]
}

If no reliable fixes can be proposed, return:

{
  "fix_suggestions": []
}
"""


class FixSuggestionAgent(BaseAgent):
    """Generate proposed patches for consolidated code review findings."""

    def __init__(self, llm_service=None, settings=None):
        super().__init__(
            agent_name="FixSuggestionAgent",
            llm_service=llm_service,
            settings=settings,
        )

    async def _analyze(self, context: dict[str, Any]) -> AgentOutput:
        """
        Implement the BaseAgent abstract contract.

        The dedicated generate_suggestions() method produces FixSuggestion
        objects. AgentOutput cannot carry those objects, so this method
        returns an execution summary.
        """

        review_id = context.get("review_id")
        findings = context.get("findings", [])
        diff_content = context.get("diff_content", "")

        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError("context must contain a valid review_id.")

        if not isinstance(findings, list):
            raise ValueError("context findings must be a list.")

        if not all(isinstance(item, Finding) for item in findings):
            raise ValueError("context findings must contain Finding objects.")

        suggestions = await self.generate_suggestions(
            review_id=review_id,
            findings=findings,
            diff_content=diff_content,
        )

        return AgentOutput(
            summary=(
                f"Generated {len(suggestions)} fix suggestion(s)."
                if suggestions
                else "No reliable fix suggestions could be generated."
            ),
            findings=[],
        )

    async def generate_suggestions(
        self,
        review_id: str,
        findings: list[Finding],
        diff_content: str,
    ) -> list[FixSuggestion]:
        """Generate structured fix suggestions without applying patches."""

        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError("review_id must be a non-empty string.")

        if not isinstance(findings, list):
            raise ValueError("findings must be a list.")

        if not all(isinstance(item, Finding) for item in findings):
            raise ValueError("findings must contain Finding objects.")

        if not isinstance(diff_content, str):
            raise ValueError("diff_content must be a string.")

        if not findings or not diff_content.strip():
            return []

        user_prompt = self._build_user_prompt(findings, diff_content)

        response = await self.generate_response(
            system_prompt=FIX_SUGGESTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        try:
            parsed = self._parse_response(response)
        except ValueError as exc:
            if not self._is_likely_truncated_json(response, exc):
                raise

            logger.warning(
                "Fix Suggestion response appears truncated; retrying once."
            )
            retry_response = await self.generate_response(
                system_prompt=FIX_SUGGESTION_SYSTEM_PROMPT,
                user_prompt=(
                    f"{user_prompt}\n\n"
                    "Your previous response was truncated or incomplete. "
                    "Return the complete response again as valid JSON. "
                    "Do not truncate the JSON and do not use Markdown fences."
                ),
            )
            parsed = self._parse_response(retry_response)

        return self._build_suggestions(
            review_id=review_id,
            findings=findings,
            candidates=parsed,
        )

    @staticmethod
    def _is_likely_truncated_json(response: str, error: ValueError) -> bool:
        """Return True only for JSON parse failures occurring at end-of-input."""
        cause = error.__cause__
        if not isinstance(cause, json.JSONDecodeError):
            return False

        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(
                r"^```(?:json)?\\s*",
                "",
                cleaned,
                count=1,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r"\\s*```$", "", cleaned, count=1)

        # A syntax error well before EOF is malformed JSON, not truncation.
        return cause.pos >= max(0, len(cleaned) - 2)

    @staticmethod
    def _build_user_prompt(
        findings: list[Finding],
        diff_content: str,
    ) -> str:
        """Build the model input from findings and the original diff."""

        finding_data = [finding.model_dump(mode="json") for finding in findings]

        return (
            "Review findings:\n"
            f"{json.dumps(finding_data, ensure_ascii=False, indent=2)}\n\n"
            "Original Git diff:\n"
            f"{diff_content}\n\n"
            "Generate only safe, minimal fixes supported by this evidence.\n"
            "IMPORTANT: Every patch must have complete unified diff hunk "
            "headers with accurate old and new line counts. Never return "
            "a bare @@ header."
        )

    @staticmethod
    def _parse_response(response: str) -> list[dict[str, Any]]:
        """Parse the model response and validate its basic JSON structure."""

        if not isinstance(response, str) or not response.strip():
            raise ValueError("FixSuggestionAgent received an empty response.")

        cleaned = response.strip()

        if cleaned.startswith("```"):
            cleaned = re.sub(
                r"^```(?:json)?\s*",
                "",
                cleaned,
                count=1,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r"\s*```$", "", cleaned, count=1)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("FixSuggestionAgent returned invalid JSON.") from exc

        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")

        candidates = data.get("fix_suggestions")

        if not isinstance(candidates, list):
            raise ValueError("Response must contain a fix_suggestions list.")

        if not all(isinstance(item, dict) for item in candidates):
            raise ValueError("Every fix suggestion must be a JSON object.")

        return candidates

    def _build_suggestions(
        self,
        review_id: str,
        findings: list[Finding],
        candidates: list[dict[str, Any]],
    ) -> list[FixSuggestion]:
        """Convert safe, correctly linked candidates into Pydantic models."""

        findings_by_id = {str(finding.finding_id): finding for finding in findings}

        suggestions: list[FixSuggestion] = []

        for candidate in candidates:
            finding_id = candidate.get("finding_id")

            if not isinstance(finding_id, str):
                logger.warning("Skipping suggestion without a finding ID.")
                continue

            finding = findings_by_id.get(finding_id)

            if finding is None:
                logger.warning(
                    "Skipping suggestion for unknown finding ID: %s",
                    finding_id,
                )
                continue

            file_path = candidate.get("file_path")

            if not isinstance(file_path, str) or file_path != finding.file_path:
                logger.warning("Skipping suggestion with mismatched file path.")
                continue

            title = candidate.get("title")
            explanation = candidate.get("explanation")
            unified_diff = candidate.get("unified_diff")

            if not all(
                isinstance(value, str) and value.strip()
                for value in (title, explanation, unified_diff)
            ):
                logger.warning("Skipping incomplete fix suggestion.")
                continue

            # Repair only a single incomplete hunk header. Multiple malformed
            # hunks are rejected rather than guessed.
            unified_diff = self._repair_single_hunk_header(
                unified_diff=unified_diff,
                start_line=finding.start_line or 1,
            )

            if not self._has_basic_unified_diff_structure(
                unified_diff,
                file_path,
            ):
                logger.warning(
                    "Skipping malformed unified diff for %s.",
                    file_path,
                )
                continue

            try:
                suggestion = FixSuggestion(
                    review_id=review_id,
                    finding_id=finding_id,
                    file_path=file_path,
                    title=repair_unicode(title),
                    explanation=repair_unicode(explanation),
                    unified_diff=repair_unicode(unified_diff),
                )
            except (ValueError, TypeError):
                logger.exception("Could not validate fix suggestion.")
                continue

            suggestions.append(suggestion)

        return suggestions

    @staticmethod
    def _repair_single_hunk_header(
        unified_diff: str,
        start_line: int,
    ) -> str:
        """
        Repair one incomplete @@ header using the finding's source line.

        This repairs only missing hunk ranges. It does not alter patch content
        or establish that the patch applies to the actual source file.
        """

        lines = unified_diff.splitlines()

        hunk_indices = [
            index for index, line in enumerate(lines) if line.startswith("@@")
        ]

        # Do not guess when there are zero or multiple hunks.
        if len(hunk_indices) != 1:
            return unified_diff

        hunk_index = hunk_indices[0]

        if lines[hunk_index].strip() != "@@":
            return unified_diff

        if start_line < 1:
            return unified_diff

        old_count = 0
        new_count = 0

        for line in lines[hunk_index + 1 :]:
            if line.startswith("@@"):
                return unified_diff

            if line.startswith("\\ No newline at end of file"):
                continue

            if line.startswith("+"):
                new_count += 1
            elif line.startswith("-"):
                old_count += 1
            elif line.startswith(" "):
                old_count += 1
                new_count += 1
            else:
                return unified_diff

        # A useful replacement must remove and add at least one line.
        if old_count == 0 or new_count == 0:
            return unified_diff

        lines[hunk_index] = f"@@ -{start_line},{old_count} +{start_line},{new_count} @@"

        return "\n".join(lines) + "\n"

    @staticmethod
    def _has_basic_unified_diff_structure(
        unified_diff: str,
        expected_path: str,
    ) -> bool:
        """
        Validate unified diff paths, hunk headers and line counts.

        This is structural validation only. It does not prove that a patch
        applies cleanly or is safe to execute.
        """

        lines = unified_diff.splitlines()

        if len(lines) < 4:
            return False

        old_header_indices = [
            index for index, line in enumerate(lines) if line.startswith("--- ")
        ]
        new_header_indices = [
            index for index, line in enumerate(lines) if line.startswith("+++ ")
        ]
        if len(old_header_indices) != 1 or len(new_header_indices) != 1:
            return False

        old_header_index = old_header_indices[0]
        new_header_index = new_header_indices[0]
        if new_header_index != old_header_index + 1:
            return False

        metadata_patterns = (
            re.compile(r"^diff --git a/(.+) b/(.+)$"),
            re.compile(r"^index [0-9a-fA-F]+\.\.[0-9a-fA-F]+(?: [0-7]{6})?$"),
            re.compile(
                r"^(?:new file mode|deleted file mode|old mode|new mode) [0-7]{6}$"
            ),
            re.compile(r"^(?:similarity|dissimilarity) index \d+%$"),
            re.compile(r"^(?:rename|copy) (?:from|to) .+$"),
        )
        for line in lines[:old_header_index]:
            if not any(pattern.fullmatch(line) for pattern in metadata_patterns):
                return False

        hunk_header_pattern = re.compile(
            r"^@@ -(\d+)(?:,(\d+))? "
            r"\+(\d+)(?:,(\d+))? @@(?: .*)?$"
        )

        def normalize_path(path: str) -> str:
            path = path.replace("\\", "/").strip()

            # Ignore optional timestamps after a tab.
            path = path.split("\t", 1)[0]

            if path.startswith("a/") or path.startswith("b/"):
                path = path[2:]

            return path.removeprefix("./")

        old_path = lines[old_header_index][4:].strip()
        new_path = lines[new_header_index][4:].strip()
        expected = normalize_path(expected_path)

        if normalize_path(old_path) != expected or normalize_path(new_path) != expected:
            return False

        for line in lines[:old_header_index]:
            if line.startswith("diff --git "):
                metadata_match = metadata_patterns[0].fullmatch(line)
                if metadata_match is None:
                    return False
                if (
                    normalize_path(metadata_match.group(1)) != expected
                    or normalize_path(metadata_match.group(2)) != expected
                ):
                    return False

        index = new_header_index + 1
        hunk_count = 0

        while index < len(lines):
            header = lines[index]

            match = hunk_header_pattern.match(header)

            if match is None:
                return False

            hunk_count += 1

            old_count = int(match.group(2) or "1")
            new_count = int(match.group(4) or "1")

            old_lines = 0
            new_lines = 0

            index += 1

            while index < len(lines) and not lines[index].startswith("@@"):
                line = lines[index]

                if line.startswith("--- ") or line.startswith("+++ "):
                    return False

                if line.startswith("\\ No newline at end of file"):
                    index += 1
                    continue

                if line.startswith("+"):
                    new_lines += 1
                elif line.startswith("-"):
                    old_lines += 1
                elif line.startswith(" "):
                    old_lines += 1
                    new_lines += 1
                else:
                    return False

                index += 1

            if old_lines != old_count or new_lines != new_count:
                return False

        return hunk_count > 0
