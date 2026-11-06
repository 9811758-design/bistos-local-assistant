# ruff: noqa: E501, C901, PLR0912, PLR0915, S314
"""Safe, deterministic XLSX parser for domestic/overseas service snapshots."""

from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal
from xml.etree import ElementTree as ET

Source = Literal["domestic", "overseas"]

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_N = {"m": _MAIN, "r": _DOC_REL}
_REL = f"{{{_PKG_REL}}}Relationship"
_CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9]\d{0,5})$")
_PHONE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
_INTERNATIONAL_PHONE = re.compile(r"(?<![\d+])\+?\d[\d ()-]{7,}\d(?!\d)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_IDENTIFIER_VALUE_PATTERN = (
    r"(?i)(?:\bserial(?:\s+(?:no|number))?\b|\bS\s*/\s*N\b|"
    r"\bcustomer\s+name\b|\b(?:name|contact|phone)\b|(?<!IP )\baddress\b|"
    r"고객명|병원명|성명|시리얼|주소|연락처|전화번호)\s*(?:[:\uFF1A]|\s)\s*"
    r"(?P<value>[^,;|]+)"
)
_IDENTIFIER_VALUE = re.compile(_IDENTIFIER_VALUE_PATTERN)


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    sheet: str
    row_number: int
    case_number: str
    model: str
    symptom: str
    cause: str
    action: str
    result: str
    received_date: str
    completed_date: str


@dataclass(frozen=True, slots=True)
class SheetAudit:
    sheet: str
    total_rows: int
    imported_rows: int
    excluded_rows: int
    reason_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExcludedRow:
    sheet: str
    row_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedWorkbook:
    filename: str
    sha256: str
    records: list[ParsedRecord]
    sheets: list[SheetAudit]
    excluded: list[ExcludedRow] = field(default_factory=list)


class WorkbookError(ValueError):
    """Raised for malformed or unsafe workbook input."""


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()


def _mask(value: str) -> str:
    value = _PHONE.sub("[연락처 제외]", value)
    value = _INTERNATIONAL_PHONE.sub("[연락처 제외]", value)
    value = _EMAIL.sub("[이메일 제외]", value)
    def replace_identity(line: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            prefix = match.group(0)[: match.start("value") - match.start()]
            return f"{prefix}[식별정보 제외]"

        return _IDENTIFIER_VALUE.sub(replacement, line)

    lines = [replace_identity(line) for line in value.splitlines()]
    return "\n".join(_text(line) for line in lines if _text(line))


def _column_number(ref: str) -> int:
    match = _CELL_REF.match(ref)
    if not match:
        raise WorkbookError("셀 주소가 올바르지 않습니다.")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - 64
    return result - 1


def _safe_xml(payload: bytes) -> ET.Element:
    if len(payload) > 128 * 1024 * 1024:
        raise WorkbookError("XML 압축 해제 크기가 제한을 초과했습니다.")
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise WorkbookError("DTD/ENTITY가 포함된 XML은 읽지 않습니다.")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise WorkbookError("XLSX XML을 해석할 수 없습니다.") from exc


def _shared_strings(root: ET.Element) -> list[str]:
    return ["".join(node.text or "" for node in item.findall(".//m:t", _N)).replace("\x00", "") for item in root.findall("m:si", _N)]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", _N)).replace("\x00", "")
    value = cell.find("m:v", _N)
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        if index >= len(shared):
            raise WorkbookError("공유 문자열 참조가 올바르지 않습니다.")
        return shared[index]
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw.replace("\x00", "")


def _excel_date(
    value: str, style: str, date_styles: set[str], date1904: bool, *, allow_general: bool = False
) -> str:
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return value
    serial = float(value)
    if style not in date_styles and (not allow_general or not 20_000 <= serial <= 80_000):
        return value
    origin = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    try:
        result = origin + timedelta(days=math.floor(serial))
    except (OverflowError, ValueError) as exc:
        raise WorkbookError("엑셀 날짜 값이 올바르지 않습니다.") from exc
    return result.isoformat()


def _headers(
    rows: list[tuple[int, list[str]]], source: Source, sheet_name: str
) -> tuple[int, list[str]] | None:
    candidates: list[tuple[int, int, list[str]]] = []
    for index, (row_number, cells) in enumerate(rows[:20]):
        joined = " ".join(cells).casefold()
        model = "model" in joined or "모델" in joined
        symptom = "증상" in joined or "symptom" in joined
        action = "처리" in joined or "조치" in joined or "action" in joined
        if model and symptom and action:
            candidates.append((index, row_number, cells))
    if len(candidates) != 1:
        # These overseas sheets have an empty F3 model heading. Preserve every
        # other original heading; only repair F after checking the known layout.
        for row_number, cells in rows[:6]:
            data_row = rows[3][1] if len(rows) > 3 else []
            legacy_shape = (
                len(cells) > 6
                and cells[1].casefold() == "no."
                and cells[6].strip().casefold() == "s/n"
                and not cells[5].strip()
                and ("증상" in cells[13] or "symptom" in cells[13].casefold())
                and _text(cells[18]).casefold() in {"처리 방안", "action"}
                and len(data_row) > 18
                and data_row[1].upper().startswith("OS")
                and bool(data_row[5].strip())
                and bool(data_row[13].strip())
                and bool(data_row[18].strip())
            )
            if source == "overseas" and sheet_name in {"2023", "2024"} and legacy_shape:
                mapped = list(cells)
                mapped[5] = "Model"
                return row_number, mapped
        return None
    index, row_number, cells = candidates[0]
    previous = rows[index - 1][1] if index else []
    merged = [_text(f"{previous[i]} {cell}") if i < len(previous) and previous[i] and cell else _text(cell or (previous[i] if i < len(previous) else "")) for i, cell in enumerate(cells)]
    return row_number, merged


def _field_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized = [re.sub(r"[^a-z0-9가-힣]", "", value.casefold()) for value in headers]
    wanted = [re.sub(r"[^a-z0-9가-힣]", "", alias.casefold()) for alias in aliases]
    for alias in wanted:
        if alias:
            exact = next((index for index, value in enumerate(normalized) if value == alias), None)
            if exact is not None:
                return exact
    for index, value in enumerate(normalized):
        if any(alias and (value == alias or alias in value) for alias in wanted):
            return index
    return None


def _field_indices(headers: list[str], aliases: tuple[str, ...]) -> list[int]:
    normalized = [re.sub(r"[^a-z0-9가-힣]", "", value.casefold()) for value in headers]
    wanted = [re.sub(r"[^a-z0-9가-힣]", "", alias.casefold()) for alias in aliases]
    return [index for index, value in enumerate(normalized) if value and value in wanted]


def _combine_fields(headers: list[str], values: list[str], indices: list[int]) -> str:
    populated = [(headers[index], values[index]) for index in indices if index < len(values) and _text(values[index])]
    if len(populated) <= 1:
        return populated[0][1] if populated else ""
    return "\n".join(f"{_text(label)}: {_text(value)}" for label, value in populated)


def _record(sheet: str, row: int, headers: list[str], values: list[str], styles: list[str], date_styles: set[str], date1904: bool) -> tuple[ParsedRecord | None, str]:
    model_i = _field_index(headers, ("model", "모델"))
    symptom_i = _field_index(headers, ("증상 및 내용", "증상/요청사항", "증상", "symptom"))
    case_i = _field_index(headers, ("서비스번호", "no.", "no"))
    cause_indices = _field_indices(headers, ("원인 파악", "불량원인", "회수품 분석 / 수리 결과", "cause"))
    action_indices = _field_indices(headers, ("처리 방안", "대응조치", "처리 내역", "action"))
    result_indices = _field_indices(headers, ("처리결과", "수리 결과", "서비스(A/S) 후 검증", "result"))
    received_i = _field_index(headers, ("접수일", "의뢰일자", "서비스번호 / 의뢰일자"))
    completed_i = _field_index(headers, ("처리완료일", "처리완료일자", "처리일자", "completed"))
    if model_i is None or symptom_i is None or not action_indices:
        return None, "required_header_missing"
    def get(index: int | None) -> str:
        return values[index] if index is not None and index < len(values) else ""
    model = _text(get(model_i))
    symptom = _mask(get(symptom_i))
    cause = _mask(_combine_fields(headers, values, cause_indices))
    action = _mask(_combine_fields(headers, values, action_indices))
    result = _mask(_combine_fields(headers, values, result_indices))
    if not model or not any((symptom, cause, action, result)):
        return None, "empty_technical_fields"
    if model.casefold() in {"model", "sn", "n/a", "na"}:
        return None, "header_or_placeholder_row"
    received_value = get(received_i)
    if received_i == case_i and not re.fullmatch(r"(?:\d{4}[./-]\s?\d{1,2}[./-]\s?\d{1,2}|\d+(?:\.\d+)?)", received_value):
        received_value = ""
    received = _excel_date(received_value, styles[received_i] if received_i is not None and received_i < len(styles) else "", date_styles, date1904, allow_general=True)
    completed = _excel_date(get(completed_i), styles[completed_i] if completed_i is not None and completed_i < len(styles) else "", date_styles, date1904, allow_general=True)
    case_number = _text(get(case_i))
    return ParsedRecord(sheet, row, case_number, model, symptom, cause, action, result, received, completed), ""


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = _safe_xml(archive.read("xl/workbook.xml"))
    rels = _safe_xml(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {node.attrib.get("Id", ""): node.attrib.get("Target", "") for node in rels.findall(_REL)}
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall("m:sheets/m:sheet", _N):
        rel_id = sheet.attrib.get(f"{{{_DOC_REL}}}id", "")
        target = targets.get(rel_id, "").replace("\\", "/")
        if target.startswith("/") or ".." in target.split("/"):
            continue
        result.append((_text(sheet.attrib.get("name", "")), "xl/" + target.lstrip("/")))
    return result


def parse_workbook(content: bytes, filename: str, source: Source) -> ParsedWorkbook:
    """Parse a bounded XLSX byte snapshot without executing formulas or exposing PII."""
    if source not in {"domestic", "overseas"}:
        raise WorkbookError("source는 domestic 또는 overseas여야 합니다.")
    if len(content) > 25 * 1024 * 1024:
        raise WorkbookError("압축 파일은 25MB 이하만 등록할 수 있습니다.")
    digest = hashlib.sha256(content).hexdigest()
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        infos = archive.infolist()
        if len(infos) > 2000 or len({info.filename for info in infos}) != len(infos):
            raise WorkbookError("XLSX 항목 수 제한을 초과했습니다.")
        if sum(info.file_size for info in infos) > 128 * 1024 * 1024:
            raise WorkbookError("XLSX 압축 해제 크기 제한을 초과했습니다.")
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
            raise WorkbookError("유효한 XLSX 구조가 아닙니다.")
        workbook = _safe_xml(archive.read("xl/workbook.xml"))
        workbook_properties = workbook.find("m:workbookPr", _N)
        date1904 = workbook_properties is not None and workbook_properties.attrib.get("date1904") == "1"
        shared = _shared_strings(_safe_xml(archive.read("xl/sharedStrings.xml"))) if "xl/sharedStrings.xml" in names else []
        date_styles: set[str] = set()
        if "xl/styles.xml" in names:
            styles = _safe_xml(archive.read("xl/styles.xml"))
            formats = {int(x.attrib.get("numFmtId", "0")): x.attrib.get("formatCode", "") for x in styles.findall("m:numFmts/m:numFmt", _N)}
            for idx, xf in enumerate(styles.findall("m:cellXfs/m:xf", _N)):
                code = formats.get(int(xf.attrib.get("numFmtId", "0")), "")
                if any(token in code.casefold() for token in ("yy", "dd", "mm", "hh")):
                    date_styles.add(str(idx))
            date_styles.update(
                str(index)
                for index, xf in enumerate(styles.findall("m:cellXfs/m:xf", _N))
                if int(xf.attrib.get("numFmtId", "0")) in set(range(14, 23))
            )
        records: list[ParsedRecord] = []
        audits: list[SheetAudit] = []
        excluded: list[ExcludedRow] = []
        total_cells = 0
        for sheet_name, target in _sheet_targets(archive):
            if target not in names:
                audits.append(SheetAudit(sheet_name, 0, 0, 0, {"missing_target": 1}))
                continue
            root = _safe_xml(archive.read(target))
            rows: list[tuple[int, list[str], list[str]]] = []
            for row_node in root.findall(".//m:sheetData/m:row", _N):
                if len(rows) >= 100_000:
                    raise WorkbookError("행 수 제한을 초과했습니다.")
                values = [""] * 256
                styles = [""] * 256
                for cell in row_node.findall("m:c", _N):
                    total_cells += 1
                    if total_cells > 2_000_000:
                        raise WorkbookError("셀 수 제한을 초과했습니다.")
                    column = _column_number(cell.attrib.get("r", ""))
                    if column >= 256:
                        raise WorkbookError("열 수 제한을 초과했습니다.")
                    values[column] = _excel_date(_cell_value(cell, shared), cell.attrib.get("s", ""), date_styles, date1904)
                    styles[column] = cell.attrib.get("s", "")
                try:
                    row_number = int(row_node.attrib.get("r", "0"))
                except (TypeError, ValueError) as exc:
                    raise WorkbookError("행 주소가 올바르지 않습니다.") from exc
                if row_number < 1 or row_number > 100_000:
                    raise WorkbookError("행 주소가 제한을 초과했습니다.")
                rows.append((row_number, values, styles))
            header = _headers([(row, values) for row, values, _ in rows], source, sheet_name)
            if header is None:
                reason = "unsupported_or_ambiguous_header"
                for row, _values, _styles in rows:
                    excluded.append(ExcludedRow(sheet_name, row, reason))
                audits.append(SheetAudit(sheet_name, len(rows), 0, len(rows), {reason: len(rows)}))
                continue
            header_row, headers = header
            imported = 0
            reasons: dict[str, int] = {}
            for row, values, styles in rows:
                if row <= header_row:
                    excluded.append(ExcludedRow(sheet_name, row, "header_row"))
                    reasons["header_row"] = reasons.get("header_row", 0) + 1
                    continue
                parsed, reason = _record(sheet_name, row, headers, values, styles, date_styles, date1904)
                if parsed is None:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    excluded.append(ExcludedRow(sheet_name, row, reason))
                else:
                    records.append(parsed)
                    imported += 1
            audits.append(SheetAudit(sheet_name, len(rows), imported, sum(reasons.values()), reasons))
        return ParsedWorkbook(filename, digest, records, audits, excluded)
    except zipfile.BadZipFile as exc:
        raise WorkbookError("XLSX 압축을 읽을 수 없습니다.") from exc
    finally:
        if archive is not None:
            archive.close()
