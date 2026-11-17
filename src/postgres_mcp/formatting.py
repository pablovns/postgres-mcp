"""Output formatting utilities for tabular and multi-line responses."""

from __future__ import annotations

import json
from typing import Any

import pglast
from pglast.ast import A_Const
from pglast.ast import Integer
from pglast.ast import SelectStmt
from pglast.enums import LimitOption
from pglast.stream import RawStream


def format_cell_value(
    val: Any,
    max_cell_chars: int = 300,
    full_text: bool = False,
) -> str:
    """Format a single cell value for TSV output.

    - None -> 'NULL'
    - bool -> 'true' / 'false'
    - dict / list -> JSON string
    - Line breaks (\\r\\n, \\n, \\r) -> literal '\\n'
    - Tabs (\\t) -> literal '\\t'
    - Truncated if length > max_cell_chars (unless full_text=True)
    """
    if not isinstance(max_cell_chars, int):
        try:
            max_cell_chars = int(getattr(max_cell_chars, "default", 300))
        except Exception:
            max_cell_chars = 300
    if not isinstance(full_text, bool):
        full_text = bool(getattr(full_text, "default", False))

    if val is None:
        return "NULL"
    elif isinstance(val, bool):
        cell_str = "true" if val else "false"
    elif isinstance(val, (dict, list)):
        cell_str = json.dumps(val, ensure_ascii=False, default=str)
    else:
        cell_str = str(val)

    # Normalize line breaks and tabs to guarantee strictly 1 line per row
    cell_str = cell_str.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n").replace("\t", "\\t")

    # Cell truncation
    if not full_text and max_cell_chars > 0 and len(cell_str) > max_cell_chars:
        diff = len(cell_str) - max_cell_chars
        cell_str = f"{cell_str[:max_cell_chars]}...[+{diff} chars]"

    return cell_str


def format_tabular_output(
    rows: list[dict[str, Any]],
    max_cell_chars: int = 300,
    full_text: bool = False,
    truncated: bool = False,
    max_rows: int | None = None,
) -> str:
    """Format a list of row dicts as a compact TSV table (1 record per line).

    Header is on line 1, followed by 1 line per record, and a footer with row count
    and optional truncation warning.
    """
    if not isinstance(max_cell_chars, int):
        try:
            max_cell_chars = int(getattr(max_cell_chars, "default", 300))
        except Exception:
            max_cell_chars = 300
    if not isinstance(full_text, bool):
        full_text = bool(getattr(full_text, "default", False))

    if not rows:
        return "(0 rows)"

    columns = list(rows[0].keys())
    if not columns:
        return "(0 rows)"

    header = "\t".join(columns)
    data_lines = ["\t".join(format_cell_value(row.get(col), max_cell_chars=max_cell_chars, full_text=full_text) for col in columns) for row in rows]

    count_label = f"({len(rows)} row{'s' if len(rows) != 1 else ''})"
    if truncated and max_rows is not None:
        footer = f"{count_label}\n[Truncated at {max_rows} rows; add LIMIT/OFFSET or narrow WHERE]"
    else:
        footer = count_label

    return "\n".join([header, *data_lines, footer])


def extract_integer_limit(limit_node: Any) -> int | None:
    """Extract integer value from a pglast limit node if present."""
    if limit_node is None:
        return None
    if isinstance(limit_node, A_Const):
        if getattr(limit_node, "isnull", False):
            return None
        val = getattr(limit_node, "val", None)
        if isinstance(val, Integer):
            ival = getattr(val, "ival", None)
            if isinstance(ival, int):
                return ival
    return None


def apply_safe_limit(sql: str, max_rows: int = 100) -> tuple[str, int | None]:
    """Inspect query and apply a safe server LIMIT (max_rows + 1) if SELECT/WITH query
    does not have an explicit limit smaller than or equal to max_rows.

    Returns:
        tuple[str, int | None]: (effective_sql, enforced_max_rows)
        If a safe limit was applied, enforced_max_rows is set to max_rows.
        If the query already had a smaller explicit limit or is non-SELECT,
        enforced_max_rows is None.
    """
    if not isinstance(max_rows, int):
        try:
            max_rows = int(getattr(max_rows, "default", 100))
        except Exception:
            max_rows = 100

    if max_rows <= 0 or not sql or not sql.strip():
        return sql, None

    try:
        tree = pglast.parse_sql(sql)
        if len(tree) != 1:
            return sql, max_rows
        stmt = tree[0].stmt
        if not isinstance(stmt, SelectStmt):
            return sql, None

        explicit_limit = extract_integer_limit(stmt.limitCount)
        if explicit_limit is not None and explicit_limit <= max_rows:
            return sql, None

        stmt.limitCount = A_Const(val=Integer(ival=max_rows + 1), isnull=False)
        stmt.limitOption = LimitOption.LIMIT_OPTION_COUNT
        return RawStream()(stmt), max_rows
    except Exception:
        # If parsing or AST streaming fails, return original query and enforce client-side
        return sql, max_rows


def format_object_details(
    details: dict[str, Any],
    max_cell_chars: int = 300,
    full_text: bool = False,
) -> str:
    """Format get_object_details output into clean, multi-line compact sections."""
    if not details:
        return "Object not found"

    # Check if table or view details
    if "basic" in details and "columns" in details:
        basic = details.get("basic", {})
        schema = basic.get("schema", "")
        name = basic.get("name", "")
        obj_type = basic.get("type", "table")
        title = f"{obj_type.capitalize()}: {schema}.{name}" if schema else f"{obj_type.capitalize()}: {name}"

        sections = [title, ""]

        # Columns
        sections.append("Columns:")
        columns_data = details.get("columns", [])
        if columns_data:
            sections.append(format_tabular_output(columns_data, max_cell_chars=max_cell_chars, full_text=full_text))
        else:
            sections.append("(0 rows)")
        sections.append("")

        # Constraints
        sections.append("Constraints:")
        constraints_data = details.get("constraints", [])
        if constraints_data:
            # Flatten columns list in constraints if needed
            flattened_constraints = []
            for c in constraints_data:
                c_copy = dict(c)
                if isinstance(c_copy.get("columns"), list):
                    c_copy["columns"] = ", ".join(str(x) for x in c_copy["columns"])
                flattened_constraints.append(c_copy)
            sections.append(format_tabular_output(flattened_constraints, max_cell_chars=max_cell_chars, full_text=full_text))
        else:
            sections.append("(0 rows)")
        sections.append("")

        # Indexes
        sections.append("Indexes:")
        indexes_data = details.get("indexes", [])
        if indexes_data:
            sections.append(format_tabular_output(indexes_data, max_cell_chars=max_cell_chars, full_text=full_text))
        else:
            sections.append("(0 rows)")

        return "\n".join(sections)

    # Check if sequence
    if "sequence_schema" in details or "start_value" in details or "data_type" in details:
        schema = details.get("schema", "")
        name = details.get("name", "")
        title = f"Sequence: {schema}.{name}" if schema else f"Sequence: {name}"
        lines = [title]
        for k, v in details.items():
            if k not in ("schema", "name"):
                val_str = format_cell_value(v, max_cell_chars=max_cell_chars, full_text=full_text)
                lines.append(f"{k}: {val_str}")
        return "\n".join(lines)

    # Check if extension
    if "version" in details or "relocatable" in details:
        name = details.get("name", "")
        title = f"Extension: {name}" if name else "Extension"
        lines = [title]
        for k, v in details.items():
            if k != "name":
                val_str = format_cell_value(v, max_cell_chars=max_cell_chars, full_text=full_text)
                lines.append(f"{k}: {val_str}")
        return "\n".join(lines)

    # Generic dictionary fallback
    lines = []
    for k, v in details.items():
        val_str = format_cell_value(v, max_cell_chars=max_cell_chars, full_text=full_text)
        lines.append(f"{k}: {val_str}")
    return "\n".join(lines)
