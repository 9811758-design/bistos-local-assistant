"""Bounded document parsing; input content never controls executable commands."""

import base64
import hashlib
import io
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from bistos.errors import AppError
from bistos.models import Page


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def checked_filename(filename: str) -> str:
    if not filename or len(filename) > 180 or any(char in filename for char in "/\\:\x00"):
        raise AppError("경로가 포함되지 않은 PDF 또는 DOCX 파일 이름이 필요합니다.")
    extension = Path(filename).suffix.casefold()
    if extension not in {".pdf", ".docx"}:
        raise AppError("PDF와 DOCX 파일만 등록할 수 있습니다.")
    return extension


def pdf_pages(path: Path, document_id: str) -> list[Page]:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise AppError("암호화된 PDF는 등록할 수 없습니다.")
        if not 1 <= len(reader.pages) <= 1500:
            raise AppError("PDF 페이지 수는 1~1,500쪽이어야 합니다.")
        return [
            Page(
                document_id=document_id,
                number=index + 1,
                text=page.extract_text() or "",
                layout_reviewed=False,
            )
            for index, page in enumerate(reader.pages)
        ]
    except (PdfReadError, OSError, ValueError, KeyError) as exc:
        raise AppError("PDF를 읽을 수 없습니다. 원본 파일을 확인하세요.") from exc


def pdf_page_count(path: Path) -> int:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 1500:
            raise AppError("암호화 또는 페이지 제한으로 PDF를 등록할 수 없습니다.")
        return len(reader.pages)
    except (PdfReadError, OSError, ValueError, KeyError) as exc:
        raise AppError("PDF 페이지 수를 읽을 수 없습니다.") from exc


def check_docx(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > 200 * 1024 * 1024:
                raise AppError("DOCX 압축 해제 크기 또는 파일 수 제한을 초과했습니다.")
            names = {item.filename for item in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise AppError("올바른 DOCX 파일이 아닙니다.")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                    raise AppError("DOCX에 안전하지 않은 경로가 있습니다.")
                if any(
                    marker in entry.filename.casefold() for marker in ("vbaproject", "embeddings/")
                ):
                    raise AppError("매크로 또는 내장 실행 객체가 있는 DOCX는 허용하지 않습니다.")
                if entry.filename.endswith(".rels"):
                    xml = archive.read(entry)
                    if (
                        len(xml) > 1024 * 1024
                        or b"\x00" in xml
                        or b"<!DOCTYPE" in xml.upper()
                        or b"<!ENTITY" in xml.upper()
                    ):
                        raise AppError("DOCX 관계 XML에 허용하지 않는 선언이 있습니다.")
                    root = ET.fromstring(xml)  # noqa: S314 -- DTD/entities rejected above; 1MiB cap.
                    if any(
                        item.attrib.get("TargetMode", "").casefold() == "external" for item in root
                    ):
                        raise AppError("외부 연결이 있는 DOCX입니다. 링크를 제거하고 등록하세요.")
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        raise AppError("DOCX 파일 구조를 읽을 수 없습니다.") from exc


def export_docx(source: Path, target: Path) -> None:
    executable = shutil.which("powershell.exe")
    if not executable:
        raise AppError("Word 변환 실행 환경이 없습니다. PDF로 저장해 등록하세요.")
    source_literal = str(source).replace("'", "''")
    target_literal = str(target).replace("'", "''")
    script = f"""
    $ErrorActionPreference = 'Stop'
    $word = $null
    $document = $null
    $sourcePath = '{source_literal}'
    $confirmConversions = $false
    $readOnly = $true
    $addToRecentFiles = $false
    $doNotSave = 0
    try {{
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        $word.AutomationSecurity = 3
        $word.Options.UpdateLinksAtOpen = $false
        $document = $word.Documents.Open(
            [ref]$sourcePath, [ref]$confirmConversions, [ref]$readOnly, [ref]$addToRecentFiles)
        $document.ExportAsFixedFormat('{target_literal}', 17)
    }} finally {{
        if ($null -ne $document) {{ $document.Close([ref]$doNotSave) }}
        if ($null -ne $word) {{ $word.Quit([ref]$doNotSave) }}
    }}
    """
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(  # noqa: S603 -- Fixed script; generated paths are single-quoted.
            [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=120,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AppError("Word 변환에 실패했습니다. PDF로 저장해 다시 등록하세요.") from exc
    if result.returncode != 0 or not target.is_file():
        raise AppError("Word 변환에 실패했습니다. Microsoft Word 설치 또는 원본을 확인하세요.")
