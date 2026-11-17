"""Unit tests for server tools (execute_sql, list_schemas, list_objects, get_object_details)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import pytest_asyncio

import postgres_mcp.server as server


class MockRow:
    def __init__(self, cells: dict[str, Any]):
        self.cells = cells


@pytest_asyncio.fixture
def mock_sql_driver():
    driver = MagicMock()
    driver.execute_query = AsyncMock()
    return driver


@pytest.mark.asyncio
async def test_execute_sql_returns_compact_tsv(mock_sql_driver):
    mock_sql_driver.execute_query.return_value = [
        MockRow({"id": 1, "username": "alice", "email": "alice@example.com"}),
        MockRow({"id": 2, "username": "bob", "email": "bob@example.com"}),
    ]

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        result = await server.execute_sql("SELECT id, username, email FROM users")

        assert len(result) == 1
        text = result[0].text  # type: ignore
        lines = text.split("\n")
        assert lines[0] == "id\tusername\temail"
        assert lines[1] == "1\talice\talice@example.com"
        assert lines[2] == "2\tbob\tbob@example.com"
        assert lines[3] == "(2 rows)"


@pytest.mark.asyncio
async def test_execute_sql_cell_truncation_and_full_text(mock_sql_driver):
    long_description = "A" * 50
    mock_sql_driver.execute_query.return_value = [
        MockRow({"id": 1, "desc": long_description}),
    ]

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        # Truncated with max_cell_chars=20
        res_truncated = await server.execute_sql("SELECT id, desc FROM items", max_cell_chars=20)
        text_truncated = res_truncated[0].text  # type: ignore
        assert "A" * 20 + "...[+30 chars]" in text_truncated

        # Full text disables truncation
        res_full = await server.execute_sql("SELECT id, desc FROM items", max_cell_chars=20, full_text=True)
        text_full = res_full[0].text  # type: ignore
        assert long_description in text_full
        assert "...[+" not in text_full


@pytest.mark.asyncio
async def test_execute_sql_truncation_notice_when_exceeding_max_rows(mock_sql_driver):
    # Simulate DB returning 11 rows when max_rows=10
    mock_sql_driver.execute_query.return_value = [MockRow({"id": i}) for i in range(11)]

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        result = await server.execute_sql("SELECT id FROM numbers", max_rows=10)

        text = result[0].text  # type: ignore
        assert "(10 rows)" in text
        assert "[Truncated at 10 rows; add LIMIT/OFFSET or narrow WHERE]" in text


@pytest.mark.asyncio
async def test_execute_sql_preserves_smaller_explicit_limit_without_truncation_warning(mock_sql_driver):
    # User query has explicit LIMIT 5, max_rows is 100
    mock_sql_driver.execute_query.return_value = [MockRow({"id": i}) for i in range(5)]

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        result = await server.execute_sql("SELECT id FROM numbers LIMIT 5", max_rows=100)

        text = result[0].text  # type: ignore
        assert "(5 rows)" in text
        assert "[Truncated" not in text
        # Verify query executed without rewriting
        mock_sql_driver.execute_query.assert_awaited_once_with("SELECT id FROM numbers LIMIT 5")


@pytest.mark.asyncio
async def test_execute_sql_no_results(mock_sql_driver):
    mock_sql_driver.execute_query.return_value = None

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        result = await server.execute_sql("CREATE TABLE test (id int)")
        assert result[0].text == "No results"  # type: ignore


@pytest.mark.asyncio
async def test_list_schemas_formatting(mock_sql_driver):
    mock_sql_driver.execute_query.return_value = [
        MockRow({"schema_name": "information_schema", "schema_owner": "postgres", "schema_type": "System Information Schema"}),
        MockRow({"schema_name": "public", "schema_owner": "postgres", "schema_type": "User Schema"}),
    ]

    with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
        result = await server.list_schemas()
        text = result[0].text  # type: ignore
        lines = text.split("\n")
        assert lines[0] == "schema_name\tschema_owner\tschema_type"
        assert "public\tpostgres\tUser Schema" in lines[2]
        assert lines[3] == "(2 rows)"


@pytest.mark.asyncio
async def test_list_objects_formatting(mock_sql_driver):
    with patch("postgres_mcp.server.SafeSqlDriver.execute_param_query") as mock_param_query:
        mock_param_query.return_value = [
            MockRow({"table_schema": "public", "table_name": "users", "table_type": "BASE TABLE"}),
            MockRow({"table_schema": "public", "table_name": "orders", "table_type": "BASE TABLE"}),
        ]
        with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
            result = await server.list_objects(schema_name="public", object_type="table")
            text = result[0].text  # type: ignore
            lines = text.split("\n")
            assert lines[0] == "schema\tname\ttype"
            assert lines[1] == "public\tusers\tBASE TABLE"
            assert lines[2] == "public\torders\tBASE TABLE"
            assert lines[3] == "(2 rows)"


@pytest.mark.asyncio
async def test_get_object_details_formatting(mock_sql_driver):
    col_rows = [
        MockRow({"column_name": "id", "data_type": "integer", "is_nullable": "NO", "column_default": "nextval('users_id_seq')"}),
        MockRow({"column_name": "name", "data_type": "text", "is_nullable": "YES", "column_default": None}),
    ]
    con_rows = [
        MockRow({"constraint_name": "users_pkey", "constraint_type": "PRIMARY KEY", "column_name": "id"}),
    ]
    idx_rows = [
        MockRow({"indexname": "users_pkey", "indexdef": "CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)"}),
    ]

    with patch("postgres_mcp.server.SafeSqlDriver.execute_param_query", side_effect=[col_rows, con_rows, idx_rows]):
        with patch("postgres_mcp.server.get_sql_driver", return_value=mock_sql_driver):
            result = await server.get_object_details(schema_name="public", object_name="users", object_type="table")
            text = result[0].text  # type: ignore

            assert "Table: public.users" in text
            assert "Columns:" in text
            assert "id\tinteger\tNO\tnextval('users_id_seq')" in text
            assert "name\ttext\tYES\tNULL" in text
            assert "Constraints:" in text
            assert "users_pkey\tPRIMARY KEY\tid" in text
            assert "Indexes:" in text
            assert "users_pkey\tCREATE UNIQUE INDEX users_pkey" in text
