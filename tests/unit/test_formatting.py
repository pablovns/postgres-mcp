"""Unit tests for output formatting and safe query limiting."""

from __future__ import annotations

from postgres_mcp.formatting import apply_safe_limit
from postgres_mcp.formatting import format_cell_value
from postgres_mcp.formatting import format_object_details
from postgres_mcp.formatting import format_tabular_output


class TestFormatCellValue:
    def test_null_representation(self):
        assert format_cell_value(None) == "NULL"

    def test_boolean_representation(self):
        assert format_cell_value(True) == "true"
        assert format_cell_value(False) == "false"

    def test_empty_string(self):
        assert format_cell_value("") == ""

    def test_dict_and_list_to_json(self):
        assert format_cell_value({"a": 1, "b": "hello"}) == '{"a": 1, "b": "hello"}'
        assert format_cell_value([1, 2, "test"]) == '[1, 2, "test"]'

    def test_dict_with_datetime_and_decimal_to_json(self):
        from datetime import datetime
        from decimal import Decimal

        dt = datetime(2026, 1, 1, 12, 0, 0)
        formatted = format_cell_value({"time": dt, "amount": Decimal("10.5")})
        assert '"time": "2026-01-01 12:00:00"' in formatted
        assert '"amount": "10.5"' in formatted

    def test_escape_newlines_and_tabs(self):
        val = "line1\r\nline2\nline3\tcol2"
        formatted = format_cell_value(val)
        assert "\n" not in formatted
        assert "\r" not in formatted
        assert "\t" not in formatted
        assert formatted == "line1\\nline2\\nline3\\tcol2"

    def test_cell_truncation_default(self):
        long_val = "x" * 350
        formatted = format_cell_value(long_val, max_cell_chars=300)
        assert len(formatted) == 300 + len("...[+50 chars]")
        assert formatted.startswith("x" * 300)
        assert formatted.endswith("...[+50 chars]")

    def test_cell_truncation_custom_limit(self):
        long_val = "abcdefghij12345"
        formatted = format_cell_value(long_val, max_cell_chars=10)
        assert formatted == "abcdefghij...[+5 chars]"

    def test_cell_truncation_disabled_with_full_text(self):
        long_val = "x" * 500
        formatted = format_cell_value(long_val, max_cell_chars=300, full_text=True)
        assert formatted == long_val
        assert len(formatted) == 500

    def test_cell_truncation_zero_limit(self):
        long_val = "x" * 500
        formatted = format_cell_value(long_val, max_cell_chars=0)
        assert formatted == long_val


class TestFormatTabularOutput:
    def test_empty_rows(self):
        assert format_tabular_output([]) == "(0 rows)"

    def test_single_row_output(self):
        rows = [{"id": 1, "name": "Alice", "role": "admin"}]
        output = format_tabular_output(rows)
        lines = output.split("\n")
        assert len(lines) == 3
        assert lines[0] == "id\tname\trole"
        assert lines[1] == "1\tAlice\tadmin"
        assert lines[2] == "(1 row)"

    def test_multiple_rows_strictly_one_line_per_record(self):
        rows = [
            {"id": 1, "query": "SELECT *\nFROM tbl", "details": "tab\there"},
            {"id": 2, "query": "UPDATE tbl\nSET a=1", "details": None},
        ]
        output = format_tabular_output(rows)
        lines = output.split("\n")
        # header (line 0), 2 data lines (lines 1, 2), footer (line 3)
        assert len(lines) == 4
        assert lines[0] == "id\tquery\tdetails"
        assert lines[1] == "1\tSELECT *\\nFROM tbl\ttab\\there"
        assert lines[2] == "2\tUPDATE tbl\\nSET a=1\tNULL"
        assert lines[3] == "(2 rows)"

    def test_truncation_footer(self):
        rows = [{"id": i} for i in range(50)]
        output = format_tabular_output(rows, truncated=True, max_rows=50)
        assert "(50 rows)" in output
        assert "[Truncated at 50 rows; add LIMIT/OFFSET or narrow WHERE]" in output


class TestApplySafeLimit:
    def test_select_without_limit(self):
        sql = "SELECT * FROM users WHERE active = true"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=100)
        assert enforced_max == 100
        assert "LIMIT 101" in effective_sql
        assert "WHERE active = TRUE" in effective_sql

    def test_select_with_smaller_explicit_limit(self):
        sql = "SELECT * FROM users LIMIT 10"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=100)
        assert enforced_max is None
        assert effective_sql == sql

    def test_select_with_equal_explicit_limit(self):
        sql = "SELECT * FROM users LIMIT 100"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=100)
        assert enforced_max is None
        assert effective_sql == sql

    def test_select_with_larger_explicit_limit(self):
        sql = "SELECT * FROM users LIMIT 500"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=100)
        assert enforced_max == 100
        assert "LIMIT 101" in effective_sql

    def test_with_cte_without_limit(self):
        sql = "WITH active_users AS (SELECT * FROM users WHERE active = true) SELECT * FROM active_users"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=50)
        assert enforced_max == 50
        assert "LIMIT 51" in effective_sql

    def test_union_query_without_limit(self):
        sql = "SELECT id FROM users_a UNION SELECT id FROM users_b"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=100)
        assert enforced_max == 100
        assert "LIMIT 101" in effective_sql

    def test_non_select_statements_not_modified(self):
        queries = [
            "INSERT INTO users (name) VALUES ('bob')",
            "UPDATE users SET name = 'bob' WHERE id = 1",
            "DELETE FROM users WHERE id = 1",
            "SHOW search_path",
            "EXPLAIN SELECT * FROM users",
        ]
        for q in queries:
            effective_sql, enforced_max = apply_safe_limit(q, max_rows=100)
            assert enforced_max is None
            assert effective_sql == q

    def test_zero_or_negative_max_rows_disables_limit(self):
        sql = "SELECT * FROM users"
        effective_sql, enforced_max = apply_safe_limit(sql, max_rows=0)
        assert enforced_max is None
        assert effective_sql == sql

    def test_empty_or_whitespace_sql(self):
        assert apply_safe_limit("", max_rows=100) == ("", None)
        assert apply_safe_limit("   ", max_rows=100) == ("   ", None)


class TestFormatObjectDetails:
    def test_table_details(self):
        details = {
            "basic": {"schema": "public", "name": "users", "type": "table"},
            "columns": [
                {"column": "id", "data_type": "integer", "is_nullable": "NO", "default": "nextval('users_id_seq')"},
                {"column": "name", "data_type": "text", "is_nullable": "YES", "default": None},
            ],
            "constraints": [
                {"name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"]},
            ],
            "indexes": [
                {"name": "users_pkey", "definition": "CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)"},
            ],
        }
        formatted = format_object_details(details)
        assert "Table: public.users" in formatted
        assert "Columns:" in formatted
        assert "id\tinteger\tNO\tnextval('users_id_seq')" in formatted
        assert "name\ttext\tYES\tNULL" in formatted
        assert "Constraints:" in formatted
        assert "users_pkey\tPRIMARY KEY\tid" in formatted
        assert "Indexes:" in formatted
        assert "users_pkey\tCREATE UNIQUE INDEX" in formatted

    def test_empty_details(self):
        assert format_object_details({}) == "Object not found"

    def test_sequence_details(self):
        details = {
            "schema": "public",
            "name": "users_id_seq",
            "data_type": "bigint",
            "start_value": 1,
            "increment": 1,
        }
        formatted = format_object_details(details)
        assert "Sequence: public.users_id_seq" in formatted
        assert "data_type: bigint" in formatted
        assert "start_value: 1" in formatted

    def test_extension_details(self):
        details = {
            "name": "pg_stat_statements",
            "version": "1.10",
            "relocatable": True,
        }
        formatted = format_object_details(details)
        assert "Extension: pg_stat_statements" in formatted
        assert "version: 1.10" in formatted
        assert "relocatable: true" in formatted
