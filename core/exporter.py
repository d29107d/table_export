import os

from .excel_reader import load_excel
from .i18n import t
from .json_writer import generate_json
from .lua_writer import generate_lua

#: Output encodings offered by the UI, in dropdown order.
#:
#: Deliberately small. ``gb2312`` is a strict subset of ``gbk`` and cannot encode
#: characters that really occur in the tables (``·`` U+00B7, ``萬`` U+842C), so it
#: used to fail halfway through an export; ``utf-8-sig`` only differs by a BOM.
#: Both were removed to keep a bad choice from silently corrupting the output.
ENCODINGS = ("utf-8", "gbk")

#: Output formats offered by the UI, in dropdown order. The first entry is the default.
#:
#: ``lua`` is what the game loads; ``json`` exists for readers that are not a Lua
#: runtime (build steps, editors, other engines). RFC 8259 says JSON is UTF-8, so a
#: gbk json file is legal only for a consumer that was told to expect one - the
#: encoding dropdown stays free rather than being locked, because the server side of
#: a toolkit like this one does sometimes need it.
FORMATS = ("lua", "json")

#: Codec names no longer offered -> the closest supported one. Lets an existing
#: ``projects.json`` with a legacy value keep working instead of silently falling
#: back to utf-8 (which would garble a GBK server file).
_ENCODING_ALIASES = {
    "utf8": "utf-8",
    "utf-8-sig": "utf-8",
    "utf8-sig": "utf-8",
    "cp936": "gbk",
    "ms936": "gbk",
    "gb2312": "gbk",
    "gb-2312": "gbk",
    "gb18030": "gbk",
}


def normalize_encoding(name):
    """Fold any codec name onto one of :data:`ENCODINGS`; unknown -> ``utf-8``."""
    if not isinstance(name, str):
        return ENCODINGS[0]
    key = name.strip().lower().replace("_", "-")
    if key in ENCODINGS:
        return key
    return _ENCODING_ALIASES.get(key, ENCODINGS[0])


def normalize_format(name):
    """Fold any format name onto one of :data:`FORMATS`; unknown -> ``lua``.

    Same job as :func:`normalize_encoding`: a ``projects.json`` written before the
    format existed has no ``*_format`` key at all, and a hand-edited one may hold
    anything. Both cases have to land on a valid dropdown entry rather than leaving the
    combobox blank or the export crashing on an unhandled name.
    """
    if not isinstance(name, str):
        return FORMATS[0]
    key = name.strip().lower()
    return key if key in FORMATS else FORMATS[0]


def output_name(filename, fmt):
    """``cfg_item.lua`` -> ``cfg_item.json`` when exporting JSON.

    Only the extension is swapped, so the base name in the workbook stays the single source
    of truth for both formats. The cell is read up to its first dot
    (``excel_reader._output_base_name``) and stored normalised to ``.lua``, so whatever B2
    said - ``cfg_item``, ``cfg_item.lua``, ``cfg_item.json`` - this function reduces it to
    ``cfg_item`` plus the extension the side's format selector asked for.
    """
    if normalize_format(fmt) == "json":
        return os.path.splitext(filename)[0] + ".json"
    return filename


def generate(table_info, scope_filter, fmt):
    """Generate one side of one table in the requested format."""
    if normalize_format(fmt) == "json":
        return generate_json(table_info, scope_filter)
    return generate_lua(table_info, scope_filter)


class EncodingError(Exception):
    """The generated text holds a character the chosen encoding cannot store.

    Carries the offending details so the caller can report them: ``filename``,
    ``encoding``, ``char`` and ``position`` (index into the generated text). The
    target file is left untouched - see :func:`_write_text_file`.
    """

    def __init__(self, filename, encoding, char, position):
        self.filename = filename
        self.encoding = encoding
        self.char = char
        self.position = position
        super().__init__(t("err.encoding", filename=filename, encoding=encoding,
                           char=char, code=ord(char)))


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


def export_table(filepath, table_info, client_dir, server_dir, client_encoding="utf-8", server_encoding="utf-8",
                 client_format="lua", server_format="lua"):
    """Write one table to the client and/or server directory.

    Client and server carry their own format, so a table can be a lua file on one side
    and a json file on the other - a Lua client next to a service that reads JSON is
    exactly the shape this is for.
    """
    result = ExportResult()
    table_name = table_info.get("sheet_name", "unknown")
    filename = table_info.get("output_filename", "output.lua")

    try:
        if client_dir:
            name = output_name(filename, client_format)
            client_code = generate(table_info, "c", client_format)
            if client_code:
                _write_text_file(client_dir, name, client_code,
                                 normalize_encoding(client_encoding))
                result.add_success(f"[client] {table_name} -> {name}")

        if server_dir:
            name = output_name(filename, server_format)
            server_code = generate(table_info, "s", server_format)
            if server_code:
                _write_text_file(server_dir, name, server_code,
                                 normalize_encoding(server_encoding))
                result.add_success(f"[server] {table_name} -> {name}")

        if not result.success and not result.failed:
            result.add_failed(f"{table_name}: no scopable fields")

    except Exception as e:
        result.add_failed(f"{table_name}: {str(e)}")

    return result


def export_all(filepaths, client_dir, server_dir, client_encoding="utf-8", server_encoding="utf-8",
               client_format="lua", server_format="lua", progress_callback=None):
    results = []
    total = len(filepaths)

    for i, filepath in enumerate(filepaths):
        try:
            tables = load_excel(filepath)
            for table_info in tables:
                r = export_table(filepath, table_info, client_dir, server_dir,
                                 client_encoding, server_encoding,
                                 client_format, server_format)
                results.append(r)
        except Exception as e:
            r = ExportResult()
            r.add_failed(f"{os.path.basename(filepath)}: {str(e)}")
            results.append(r)

        if progress_callback:
            progress_callback(i + 1, total)

    return results


def _write_text_file(output_dir, filename, content, encoding="utf-8"):
    """Write one generated file - all or nothing.

    The text is encoded **before** the target is opened. Encoding first is what
    keeps a failure harmless: ``open(path, "w")`` truncates the file, so the old
    code turned a ``UnicodeEncodeError`` (e.g. ``·`` with ``gb2312``) into a
    0-byte Lua file that the game then failed to load. Now the bytes are built in
    memory, written to a sibling ``.tmp`` file and swapped in with
    ``os.replace``, so the previous content survives any failure and the game
    never observes a half-written file.

    Shared by both formats: a json file that overflows a narrow encoding has to fail
    the same way a lua one does, for the same reason.
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    # Unify to CRLF, matching the legacy tool's output (Lua accepts either, but byte-identical files keep diffs and SVN noise down).
    # Byte-for-byte the same translation the old text-mode writer did
    # (``open(..., newline="\r\n")``): every "\n" becomes "\r\n" and any "\r"
    # already present is passed through untouched. Normalising "\r\n" first would
    # look tidier but would change the bytes of cells that hold CRLF line breaks
    # (Excel's Alt+Enter), and those bytes are what the client already loads.
    text = content.replace("\n", "\r\n")

    try:
        data = text.encode(encoding)
    except UnicodeEncodeError as e:
        # One character is what the codec reports; take the first just in case a
        # codec ever reports a wider slice (ord() needs exactly one character).
        raise EncodingError(filename, encoding,
                            text[e.start:e.start + 1] or "\ufffd", e.start) from None

    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, filepath)
    except BaseException:
        # Never leave a stray .tmp behind on a failed write
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
