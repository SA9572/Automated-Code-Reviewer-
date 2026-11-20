from app.agents.diff_line_mapper import (
    build_changed_line_map,
    validate_finding_location,
)


def test_maps_added_lines_and_skips_context_and_removed_lines():
    diff = """\
diff --git a/app/example.py b/app/example.py
index 1111111..2222222 100644
--- a/app/example.py
+++ b/app/example.py
@@ -1,4 +1,5 @@
 context_before = True
-old_value = 1
+new_value = 2
+another_value = 3
 context_after = True
"""

    assert build_changed_line_map(diff) == {"app/example.py": {2, 3}}


def test_maps_multiple_files():
    diff = """\
diff --git a/app/one.py b/app/one.py
--- a/app/one.py
+++ b/app/one.py
@@ -1 +1 @@
-old = 1
+new = 1
diff --git a/app/two.py b/app/two.py
--- a/app/two.py
+++ b/app/two.py
@@ -4 +4 @@
-old = 2
+new = 2
"""

    assert build_changed_line_map(diff) == {
        "app/one.py": {1},
        "app/two.py": {4},
    }


def test_maps_multiple_hunks_in_one_file():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old_one
+new_one
@@ -10 +10 @@
-old_two
+new_two
"""

    assert build_changed_line_map(diff) == {"app/example.py": {1, 10}}


def test_does_not_map_deleted_only_file():
    diff = """\
diff --git a/app/removed.py b/app/removed.py
--- a/app/removed.py
+++ /dev/null
@@ -1,2 +0,0 @@
-old_one
-old_two
"""

    assert build_changed_line_map(diff) == {}


def test_rename_uses_new_file_path():
    diff = """\
diff --git a/app/old_name.py b/app/new_name.py
similarity index 90%
rename from app/old_name.py
rename to app/new_name.py
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -1 +1 @@
-old_name = True
+new_name = True
"""

    assert build_changed_line_map(diff) == {"app/new_name.py": {1}}


def test_normalizes_windows_path_in_finding():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old = 1
+new = 1
"""

    finding = {
        "file_path": r"app\example.py",
        "start_line": 1,
        "end_line": 1,
    }

    result = validate_finding_location(finding, diff)

    assert result["start_line"] == 1
    assert result["end_line"] == 1


def test_clears_line_numbers_when_reported_range_is_not_changed():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1,3 +1,3 @@
 context_one
-old_value = 1
+new_value = 1
 context_two
"""

    finding = {
        "file_path": "app/example.py",
        "start_line": 1,
        "end_line": 1,
    }

    result = validate_finding_location(finding, diff)

    assert result["start_line"] is None
    assert result["end_line"] is None


def test_clamps_reported_range_to_actual_changed_lines():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1,4 +1,4 @@
 context_one
-old_value = 1
+new_value = 1
 context_two
+another_value = 2
"""

    finding = {
        "file_path": "app/example.py",
        "start_line": 1,
        "end_line": 4,
    }

    result = validate_finding_location(finding, diff)

    assert result["start_line"] == 2
    assert result["end_line"] == 4


def test_clears_location_when_file_path_does_not_match():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old = 1
+new = 1
"""

    finding = {
        "file_path": "app/other.py",
        "start_line": 1,
        "end_line": 1,
    }

    result = validate_finding_location(finding, diff)

    assert result["start_line"] is None
    assert result["end_line"] is None


def test_preserves_finding_without_reported_line_numbers():
    finding = {
        "file_path": "app/example.py",
        "start_line": None,
        "end_line": None,
    }

    result = validate_finding_location(finding, "not a unified diff")

    assert result["start_line"] is None
    assert result["end_line"] is None


def test_does_not_mutate_original_finding():
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old = 1
+new = 1
"""

    finding = {
        "file_path": "app/example.py",
        "start_line": 50,
        "end_line": 50,
    }

    result = validate_finding_location(finding, diff)

    assert finding["start_line"] == 50
    assert result["start_line"] is None
