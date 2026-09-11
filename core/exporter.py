import os
from .excel_reader import load_excel
from .lua_writer import generate_lua


class ExportResult:
    def __init__(self):
        self.success = []
        self.failed = []

    def add_success(self, msg):
        self.success.append(msg)

    def add_failed(self, msg):
        self.failed.append(msg)

    @property
    def total(self):
        return len(self.success) + len(self.failed)


def export_table(filepath, table_info, client_dir, server_dir, client_encoding="utf-8", server_encoding="utf-8"):
    result = ExportResult()
    table_name = table_info.get("sheet_name", "unknown")
    filename = table_info.get("output_filename", "output.lua")

    try:
        if client_dir:
            client_code = generate_lua(table_info, "c")
            if client_code:
                _write_lua_file(client_dir, filename, client_code, client_encoding)
                result.add_success(f"[client] {table_name} -> {filename}")

        if server_dir:
            server_code = generate_lua(table_info, "s")
            if server_code:
                _write_lua_file(server_dir, filename, server_code, server_encoding)
                result.add_success(f"[server] {table_name} -> {filename}")

        if not result.success and not result.failed:
            result.add_failed(f"{table_name}: no scopable fields")

    except Exception as e:
        result.add_failed(f"{table_name}: {str(e)}")

    return result


def export_all(filepaths, client_dir, server_dir, client_encoding="utf-8", server_encoding="utf-8", progress_callback=None):
    results = []
    total = len(filepaths)

    for i, filepath in enumerate(filepaths):
        try:
            tables = load_excel(filepath)
            for table_info in tables:
                r = export_table(filepath, table_info, client_dir, server_dir, client_encoding, server_encoding)
                results.append(r)
        except Exception as e:
            r = ExportResult()
            r.add_failed(f"{os.path.basename(filepath)}: {str(e)}")
            results.append(r)

        if progress_callback:
            progress_callback(i + 1, total)

    return results


def _write_lua_file(output_dir, filename, content, encoding="utf-8"):
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    # Unify to CRLF, matching the legacy tool's output (Lua accepts either, but byte-identical files keep diffs and SVN noise down)
    with open(filepath, "w", encoding=encoding, newline="\r\n") as f:
        f.write(content)
