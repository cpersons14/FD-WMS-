"""Bounded, formula-free customer data imports (CSV or XLSX)."""

import csv
import io
import zipfile
from openpyxl import load_workbook
from .services import WorkflowError


def read_table(content, filename, headers, max_rows=20000):
    if len(content) > 10 * 1024 * 1024:
        raise WorkflowError("Use a file under 10 MB.")
    try:
        if filename.lower().endswith(".xlsx"):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 80 * 1024 * 1024:
                    raise WorkflowError("Workbook expands beyond the import limit.")
            book = load_workbook(
                io.BytesIO(content), read_only=True, data_only=False, keep_links=False
            )
            try:
                sheet = book.worksheets[0]
                if sheet.max_row and sheet.max_row > max_rows + 1:
                    raise WorkflowError(f"Use at most {max_rows} data rows.")
                rows = []
                for row in sheet.iter_rows(
                    max_row=max_rows + 2, max_col=len(headers) + 1
                ):
                    if any(c.data_type in ("f", "e") for c in row):
                        raise WorkflowError(
                            "Replace formulas and Excel errors with plain values before upload."
                        )
                    rows.append(
                        [
                            str(c.value).strip() if c.value is not None else ""
                            for c in row
                        ]
                    )
            finally:
                book.close()
        elif filename.lower().endswith(".csv"):
            rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        else:
            raise WorkflowError("Use an XLSX or UTF-8 CSV file.")
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError(
            "The file could not be read. Use the template columns and plain values."
        ) from exc
    if not rows or rows[0][: len(headers)] != headers:
        raise WorkflowError("Expected header row: " + ", ".join(headers))
    rows = [r for r in rows[1:] if any(str(v).strip() for v in r)]
    if not rows or len(rows) > max_rows:
        raise WorkflowError(f"Use 1–{max_rows} data rows.")
    result = []
    for i, row in enumerate(rows, 2):
        if len(row) < len(headers) or any(row[len(headers) :]):
            raise WorkflowError(f"Row {i}: incorrect column count.")
        values = [str(v).strip() for v in row[: len(headers)]]
        if any(len(v) > 500 for v in values):
            raise WorkflowError(f"Row {i}: a value is too long.")
        result.append(dict(zip(headers, values)))
    return result
