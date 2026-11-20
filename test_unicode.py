
from backend.app.security.unicode_utils import repair_unicode


expected_values = [
    "café",
    "नमस्ते",
    "中文",
    "résumé",
]

print("\n========== UNICODE REPAIR TEST ==========\n")

all_passed = True

for expected in expected_values:
    # Generate genuine UTF-8 interpreted as Latin-1 mojibake.
    incorrect = expected.encode("utf-8").decode("latin-1")

    actual = repair_unicode(incorrect)
    passed = actual == expected

    all_passed = all_passed and passed

    print(f"Original:  {expected}")
    print(f"Input:     {incorrect!r}")
    print(f"Repaired:  {actual}")
    print(f"Status:    {'PASS' if passed else 'FAIL'}")
    print("-" * 50)

print(
    "\nFinal result:",
    "ALL PASSED" if all_passed else "SOME TESTS FAILED",
)
