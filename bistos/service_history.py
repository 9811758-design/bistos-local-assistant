"""Authorized local service snapshots, provenance and conservative case retrieval."""

import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import Field

from bistos.errors import AppError
from bistos.models import Model, User
from bistos.ollama import Ollama
from bistos.service_semantic import SemanticSearch
from bistos.service_workbooks import WorkbookError, parse_workbook
from bistos.store import Store

Source = Literal["domestic", "overseas"]
Language = Literal["ko", "en"]
log = logging.getLogger(__name__)
_MODEL = re.compile(r"\b(?:BT|BCM)[ -]?\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*\b", re.IGNORECASE)
_GROUPS = (
    ("터치", "touch", "touchscreen", "터치스크린"),
    ("보정", "교정", "calibration", "calibrate"),
    ("소리", "음량", "무음", "사운드", "sound", "audio", "speaker"),
    ("전원", "안켜", "켜지", "power", "startup", "boot"),
    ("배터리", "충전", "battery", "charging", "charge"),
    ("인쇄", "프린터", "출력", "printer", "printing", "print"),
    ("용지", "종이", "paper"),
    ("화면", "디스플레이", "display", "screen", "lcd"),
    ("프로브", "탐촉자", "probe"),
    ("센서", "sensor"),
    ("온도", "가열", "히터", "temperature", "heater", "heating"),
    ("압력", "혈압", "커프", "pressure", "nibp", "cuff"),
    ("심박", "태아", "도플러", "fhr", "fetal", "doppler"),
    ("산소", "산소포화도", "spo2", "oxygen", "saturation"),
    ("케이블", "단선", "cable", "wire"),
    ("문열림", "문이열", "도어", "door", "hood"),
    ("통신", "연결", "communication", "connection", "network", "cms"),
    ("누수", "누액", "leak", "leakage"),
    ("흡입", "흡인", "유축", "suction", "pump"),
    ("알람", "경보", "alarm"),
)
_STOP = {
    "알려줘",
    "알려주세요",
    "어떻게",
    "무엇",
    "방법",
    "처리",
    "사례",
    "내역",
    "원인",
    "조치",
    "고장",
    "문제",
    "수리",
    "기록",
    "확인",
    "please",
    "the",
    "how",
    "what",
    "case",
    "repair",
    "history",
    "issue",
    "problem",
    "with",
    "does",
    "not",
    "working",
    "안돼",
    "안됨",
    "안되",
    "안나",
    "나와",
}


def normal(value: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", value.casefold())


def _touch_unresponsive(value: str) -> bool:
    text = normal(value)
    subject = re.search(
        r"터치|touch|손가락|finger|(?:화면|screen).{0,12}(?:누르|눌|탭|tap|press)", text,
    )
    failure = re.search(
        (r"먹통|무반응|반응(?:이)?없|응답(?:하지|이)?않|안(?:됨|돼|되|눌)|"
         r"되지않|동작안|작동하지|말(?:을)?안들|unresponsive|"
         r"not(?:work|respond)|doesnotrespond|doesntrespond|noresponse"),
        text,
    )
    return bool(subject and failure)


def _group_is_negated(query: str, group: tuple[str, ...]) -> bool:
    """Return true only for explicit wording that the symptom is absent."""
    compact = normal(query)
    terms = [normal(term) for term in group if normal(term)]
    for term in terms:
        marker = (
            r"(?:정상(?!적이지않|이아니|아니|은아니|은안|적으로작동하지|"
            r"적으로작동안|적작동하지|적작동안)|문제없|문제아님|아님|아니라|아닌)"
        )
        if re.search(rf"{term}(?:는|은|이|가|도)?{marker}", compact):
            return True
        if re.search(rf"{marker}(?:인|인것은)?{term}", compact):
            return True
        if re.search(
            rf"(?:not|no)(?:a)?{term}(?:issue|problem)?|{term}(?:is)?(?:fine|normal|working)",
            compact,
        ):
            return True
    return False


class ServiceCase(Model):
    case_id: str
    import_id: str
    source: Source
    filename: str
    workbook_sha256: str
    sheet: str
    row_number: int = Field(ge=1)
    case_number: str = ""
    model: str
    symptom: str
    cause: str = ""
    action: str = ""
    result: str = ""
    case_date: str = ""
    completed_date: str = ""


class CaseMatch(ServiceCase):
    relevance: Literal["high", "medium"]
    match_reason: str


class ImportSummary(Model):
    source: Source
    filename: str
    sha256: str
    imported_at: str
    record_count: int
    duplicate_count: int
    excluded_count: int
    sheets: list[dict[str, object]]
    unchanged: bool = False


def _distinct_matches(items: list[CaseMatch], limit: int) -> list[CaseMatch]:
    results: list[CaseMatch] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        signature = (normal(item.model), item.symptom, item.cause, item.action, item.result)
        if signature in seen:
            continue
        seen.add(signature)
        results.append(item)
        if len(results) >= max(1, limit):
            break
    return results


def _date_value(value: str) -> datetime | None:
    cleaned = re.sub(r"\s+", "", value or "")
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d."):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def initialize_tables(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS service_imports (
            id TEXT PRIMARY KEY,source TEXT NOT NULL,sha256 TEXT NOT NULL,
            filename TEXT NOT NULL,created_at TEXT NOT NULL,actor_user_id TEXT NOT NULL,
            payload TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source,sha256));
        CREATE TABLE IF NOT EXISTS service_cases (
            version_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,
            import_id TEXT NOT NULL REFERENCES service_imports(id),
            model_key TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS service_cases_model ON service_cases(model_key,import_id);
        CREATE INDEX IF NOT EXISTS service_cases_identity ON service_cases(case_id,import_id);
    """)


def validate_snapshots(db: sqlite3.Connection, data_dir: Path) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='service_imports'").fetchone():
        return
    for row in db.execute("SELECT id,source,sha256,payload FROM service_imports"):
        sha = str(row[2])
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise AppError("서비스 원본 해시가 올바르지 않습니다.")
        target = data_dir / "service-sources" / f"{sha}.xlsx"
        if (
            target.is_symlink()
            or not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != sha
        ):
            raise AppError("서비스 이력 원본 파일의 무결성을 확인할 수 없습니다.")
        summary = ImportSummary.model_validate_json(row[3])
        if summary.source != row[1] or summary.sha256 != sha:
            raise AppError("서비스 이력 등록 정보가 일치하지 않습니다.")
        count = 0
        for case in db.execute(
            "SELECT case_id,model_key,payload FROM service_cases WHERE import_id=?", (row[0],)
        ):
            item = ServiceCase.model_validate_json(case[2])
            if (
                item.case_id,
                normal(item.model),
                item.import_id,
                item.workbook_sha256,
                item.source,
            ) != (case[0], case[1], row[0], sha, row[1]):
                raise AppError("서비스 사례의 원본 연결이 일치하지 않습니다.")
            count += 1
        if count != summary.record_count:
            raise AppError("서비스 이력 등록 건수가 일치하지 않습니다.")
    if db.execute(
        "SELECT source FROM service_imports WHERE active=1 GROUP BY source HAVING COUNT(*)>1"
    ).fetchone():
        raise AppError("서비스 이력의 활성 버전이 중복되었습니다.")


class ServiceHistory:
    def __init__(
        self,
        store: Store,
        root: Path | None = None,
        ollama: Ollama | None = None,
        semantic: SemanticSearch | None = None,
    ) -> None:
        self.store = store
        self.root = root
        self.ollama = ollama
        # Semantic retrieval is opt-in.  This keeps existing installations
        # deterministic until an explicitly configured local index is built.
        self.semantic = semantic
        self.sources_dir = store.data_dir / "service-sources"

    def initialize(self) -> None:
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        with self.store.connection() as db:
            initialize_tables(db)

    def _require(self, user: User, *, manage: bool = False) -> None:
        self.store.require(user, "read_service")
        if manage:
            self.store.require(user, "manage_documents")

    def import_file(self, user: User, path: Path, source: Source) -> ImportSummary:
        return self.import_content(user, path.name, path.read_bytes(), source)

    def import_content(  # noqa: C901, PLR0912, PLR0915
        self, user: User, filename: str, content: bytes, source: Source
    ) -> ImportSummary:
        self._require(user, manage=True)
        if source not in {"domestic", "overseas"}:
            raise AppError("국내 또는 해외 서비스 구분을 선택하세요.")
        if len(content) > 25 * 1024 * 1024:
            raise AppError("서비스 이력은 25MB 이하의 XLSX 파일로 등록하세요.", 413)
        filename = re.split(r"[/\\]", filename)[-1][:200]
        if not filename.casefold().endswith(".xlsx"):
            raise AppError("서비스 이력은 XLSX 파일만 등록할 수 있습니다.")
        try:
            parsed = parse_workbook(content, filename, source)
        except WorkbookError as error:
            raise AppError(str(error), 422) from error
        if not parsed.records:
            raise AppError("등록 가능한 서비스 행이 없습니다. 기존 자료는 유지됩니다.", 422)
        sha = hashlib.sha256(content).hexdigest()
        import_id = f"{source}-{sha}"
        records: list[ServiceCase] = []
        occurrences: Counter[str] = Counter()
        keys = Counter(
            (r.sheet, r.case_number, normal(r.model)) for r in parsed.records if r.case_number
        )
        for row in parsed.records:
            fields = (
                normal(row.model),
                row.symptom,
                row.cause,
                row.action,
                row.result,
                row.received_date,
                row.completed_date,
            )
            body_hash = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
            stable_key = (
                f"{source}|{row.sheet}|{row.case_number}|{normal(row.model)}"
                if row.case_number
                else f"{source}|{row.sheet}|{body_hash}"
            )
            if row.case_number and keys[(row.sheet, row.case_number, normal(row.model))] > 1:
                stable_key += "|" + body_hash
            # Repeated text can describe different serviced devices. Keep every
            # source row, even when identifying columns were intentionally omitted.
            occurrences[stable_key] += 1
            if occurrences[stable_key] > 1:
                stable_key += f"|occurrence:{occurrences[stable_key]}"
            case_id = hashlib.sha256(stable_key.encode()).hexdigest()[:32]
            records.append(
                ServiceCase(
                    case_id=case_id,
                    import_id=import_id,
                    source=source,
                    filename=filename,
                    workbook_sha256=sha,
                    sheet=row.sheet,
                    row_number=row.row_number,
                    case_number=row.case_number,
                    model=row.model,
                    symptom=row.symptom,
                    cause=row.cause,
                    action=row.action,
                    result=row.result,
                    case_date=row.received_date,
                    completed_date=row.completed_date,
                )
            )
        summary = ImportSummary(
            source=source,
            filename=filename,
            sha256=sha,
            imported_at=datetime.now(UTC).isoformat(),
            record_count=len(records),
            duplicate_count=0,
            excluded_count=sum(s.excluded_rows for s in parsed.sheets),
            sheets=[
                {
                    "sheet": s.sheet,
                    "total_rows": s.total_rows,
                    "imported_rows": s.imported_rows,
                    "excluded_rows": s.excluded_rows,
                    "reason_counts": s.reason_counts,
                }
                for s in parsed.sheets
            ],
        )
        with self.store.lock:
            self._require(user, manage=True)
            target = self.sources_dir / f"{sha}.xlsx"
            self.sources_dir.mkdir(exist_ok=True)
            if target.exists():
                if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    raise AppError("동일 이름의 서비스 원본 해시가 일치하지 않습니다.", 409)
            else:
                with target.open("xb") as stream:
                    stream.write(content)
            with self.store.connection() as db:
                existing = db.execute(
                    "SELECT active,payload FROM service_imports WHERE id=?", (import_id,)
                ).fetchone()
                if existing and existing[0]:
                    return ImportSummary.model_validate_json(existing[1]).model_copy(
                        update={"unchanged": True}
                    )
                db.execute("UPDATE service_imports SET active=0 WHERE source=?", (source,))
                if existing:
                    db.execute("UPDATE service_imports SET active=1 WHERE id=?", (import_id,))
                    result = ImportSummary.model_validate_json(existing[1])
                else:
                    db.execute(
                        "INSERT INTO service_imports VALUES (?,?,?,?,?,?,?,1)",
                        (
                            import_id,
                            source,
                            sha,
                            filename,
                            summary.imported_at,
                            user.id,
                            summary.model_dump_json(),
                        ),
                    )
                    for record in records:
                        db.execute(
                            "INSERT INTO service_cases VALUES (?,?,?,?,?)",
                            (
                                f"{import_id}:{record.case_id}",
                                record.case_id,
                                import_id,
                                normal(record.model),
                                record.model_dump_json(),
                            ),
                        )
                    result = summary
                self.store.bump_epoch(db)
                return result

    def sources(self, user: User) -> list[ImportSummary]:
        self._require(user)
        with self.store.connection() as db:
            return [
                ImportSummary.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM service_imports WHERE active=1 ORDER BY source"
                )
            ]

    def models(self, user: User) -> list[dict[str, str | int]]:
        self._require(user)
        counts: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for case in self._cases():
            key = normal(case.model)
            counts[key] += 1
            label = re.sub(r"^BT[- ]?(\d+)", r"BT-\1", case.model, flags=re.IGNORECASE)
            labels.setdefault(key, label)
        return [
            {"id": labels[key], "name": labels[key], "count": count}
            for key, count in sorted(counts.items())
        ]

    def _cases(self, model: str = "", source: str = "") -> list[ServiceCase]:
        with self.store.connection() as db:
            rows = db.execute(
                """SELECT c.payload FROM service_cases c JOIN service_imports i ON c.import_id=i.id
                WHERE i.active=1 AND (?='' OR c.model_key=?) AND (?='' OR i.source=?)""",
                (model, normal(model), source, source),
            ).fetchall()
            return [ServiceCase.model_validate_json(row[0]) for row in rows]

    def semantic_cases(self) -> list[ServiceCase]:
        """Return internal index input; retrieval still checks current user access."""
        return self._cases()

    def detail(self, user: User, case_id: str) -> ServiceCase:
        self._require(user)
        with self.store.connection() as db:
            row = db.execute(
                """SELECT c.payload FROM service_cases c JOIN service_imports i ON i.id=c.import_id
                WHERE c.case_id=? AND i.active=1""",
                (case_id,),
            ).fetchone()
        if row is None:
            raise AppError("현재 사용할 수 있는 서비스 사례가 없습니다.", 404)
        return ServiceCase.model_validate_json(row[0])

    def original(self, user: User, source: Source) -> tuple[Path, str]:
        self._require(user, manage=True)
        current = next((item for item in self.sources(user) if item.source == source), None)
        if current is None:
            raise AppError("서비스 이력 원본을 찾을 수 없습니다.", 404)
        target = self.sources_dir / f"{current.sha256}.xlsx"
        if (
            target.is_symlink()
            or not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != current.sha256
        ):
            raise AppError("서비스 이력 원본 해시가 일치하지 않습니다.", 409)
        return target, current.filename

    def search(  # noqa: C901, PLR0912, PLR0915
        self,
        user: User,
        query: str,
        model: str = "",
        source: str = "",
        limit: int = 5,
        language: Language = "ko",
        *,
        deduplicate: bool = True,
    ) -> list[CaseMatch]:
        self._require(user)
        if source not in {"", "domestic", "overseas"}:
            raise AppError("서비스 구분이 올바르지 않습니다.")
        mentions = {normal(m.group()) for m in _MODEL.finditer(query)}
        if len(mentions) > 1:
            return []
        if model and mentions and mentions != {normal(model)}:
            return []
        if not model and len(mentions) == 1:
            model = next(iter(mentions))
        stripped = _MODEL.sub(" ", query).casefold()
        compact = normal(stripped)
        mentioned_groups = [
            group for group in _GROUPS if any(normal(term) in compact for term in group)
        ]
        negated_groups = [group for group in mentioned_groups if _group_is_negated(stripped, group)]
        groups = [group for group in mentioned_groups if group not in negated_groups]
        touch_failure = _touch_unresponsive(stripped) and _GROUPS[0] not in negated_groups
        other_touch_fault = r"틀어|보정|파손|깨짐|끊어|misalign|calibrat|crack|broken|break"
        only_unresponsive = touch_failure and not re.search(other_touch_fault, stripped)
        # Resolve screen/finger interaction wording before keyword/semantic
        # fusion. Preserve unrelated positive symptoms and all exclusions.
        if touch_failure:
            groups = [group for group in groups if group not in {_GROUPS[0], _GROUPS[7]}]
            groups.append(_GROUPS[0])
        if mentioned_groups and not groups:
            return []
        terms = [t for t in re.findall(r"[a-z][a-z0-9_-]+|[가-힣]{2,}", stripped) if t not in _STOP]
        scored: list[tuple[float, str, CaseMatch]] = []
        with self.store.connection() as db:
            known_models = {
                row[0] for row in db.execute("SELECT DISTINCT model_key FROM service_cases")
            }
        candidates = self._cases(model, source)
        semantic_scores: dict[tuple[str, str], float] = {}
        if self.semantic is not None and not (mentioned_groups and not groups):
            try:
                semantic_query = stripped
                if touch_failure and groups == [_GROUPS[0]]:
                    semantic_query = "터치가 되지 않음"
                semantic_scores = self.semantic.rank(semantic_query, candidates)
            except Exception:  # semantic retrieval is supplementary; lexical remains available
                log.warning(
                    "service semantic retrieval failed; using lexical search", exc_info=True
                )
                semantic_scores = {}
        for item in candidates:
            raw_text = f"{item.symptom} {item.cause} {item.action} {item.result}"
            mentioned_models = {
                normal(value)
                for value in re.findall(
                    r"\b[A-Za-z]{2,8}[- ]?\d{2,4}[A-Za-z]*(?:-[A-Za-z0-9]+)*\b", raw_text
                )
            } & known_models
            if mentioned_models - {normal(item.model)}:
                # Conflicting source labels need human review. Do not use a
                # different model's repair merely because this row was mislabelled.
                continue
            symptom = normal(item.symptom)
            corpus = normal(raw_text)
            # Changing to date order must not promote a different, explicitly
            # recorded touch fault over the requested lack of response.
            if (
                only_unresponsive
                and not _touch_unresponsive(item.symptom)
                and re.search(other_touch_fault, item.symptom, re.IGNORECASE)
            ):
                continue
            if any(
                any(normal(term) in symptom for term in group)
                and not _group_is_negated(item.symptom, group)
                for group in negated_groups
            ):
                continue
            matched = [g for g in groups if any(normal(t) in corpus for t in g)]
            symptom_groups = [g for g in groups if any(normal(t) in symptom for t in g)]
            lexical = [term for term in terms if normal(term) in corpus and len(normal(term)) >= 3]
            semantic_score = semantic_scores.get((item.case_id, item.import_id), 0.0)
            if groups and len(matched) != len(groups) and semantic_score < 0.62:
                continue
            if not matched and not lexical and semantic_score <= 0:
                continue
            if not matched and not lexical and semantic_score < 0.62:
                continue
            if semantic_scores and not semantic_score and groups and not symptom_groups:
                continue
            lexical_score = len(symptom_groups) * 6 + len(matched) * 3 + len(lexical) * 2
            score = float(lexical_score)
            if semantic_scores:
                score = min(lexical_score, 12) / 12 * 0.20 + semantic_score
                if touch_failure and _touch_unresponsive(item.symptom):
                    score += 0.30
            reasons = [
                g[0] if language == "ko" else next((t for t in g if t.isascii()), g[0])
                for g in matched
            ]
            if semantic_score > 0:
                reasons.append("의미상 유사" if language == "ko" else "Semantic similarity")
            reason = ("동일 모델 · " if model else "") + " / ".join(reasons or lexical[:3])
            if language == "en":
                reason = ("Same model · " if model else "") + " / ".join(reasons or lexical[:3])
            match = CaseMatch(
                **item.model_dump(),
                relevance="high" if symptom_groups else "medium",
                match_reason=reason,
            )
            scored.append((score, item.case_date, match))
        scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
        with self.store.lock:
            self._require(user)
            active = {(case.case_id, case.import_id) for case in self._cases(model, source)}
            ranked = [item for _, _, item in scored if (item.case_id, item.import_id) in active]
            if not deduplicate:
                return ranked[: min(max(limit, 1), 100_000)]
            return _distinct_matches(ranked, min(max(limit, 1), 5))

    def candidates(
        self,
        user: User,
        query: str,
        model: str = "",
        source: str = "",
        language: Language = "ko",
    ) -> list[CaseMatch]:
        """Return the complete similarity-ranked active candidate set."""
        return self.search(
            user, query, model, source, limit=100_000, language=language, deduplicate=False
        )

    def search_page(
        self,
        user: User,
        query: str,
        model: str = "",
        source: str = "",
        language: Language = "ko",
        *,
        sort: Literal["similarity", "newest", "oldest"] = "similarity",
        date_field: Literal["received", "completed"] = "received",
        page: int = 1,
        page_size: int = 5,
    ) -> dict[str, object]:
        if page < 1:
            raise AppError("페이지 번호는 1 이상이어야 합니다.")
        if sort not in {"similarity", "newest", "oldest"}:
            raise AppError("정렬 방식이 올바르지 않습니다.")
        if date_field not in {"received", "completed"}:
            raise AppError("날짜 기준이 올바르지 않습니다.")
        if page_size != 5:
            raise AppError("페이지 크기는 5건으로 고정됩니다.")
        items = self.candidates(user, query, model, source, language)
        if sort != "similarity":
            field = "completed_date" if date_field == "completed" else "case_date"
            dated = [(index, _date_value(getattr(item, field))) for index, item in enumerate(items)]
            valid = [(index, date) for index, date in dated if date is not None]
            missing = [index for index, date in dated if date is None]
            # `items` is similarity-ranked; stable date sorting preserves that
            # order for equal dates in both directions.
            valid.sort(key=lambda pair: pair[1], reverse=sort == "newest")
            items = [items[index] for index, _ in valid] + [items[index] for index in missing]
        items = _distinct_matches(items, 100_000)
        total = len(items)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": start + page_size < total,
            "sort": sort,
            "date_field": date_field,
        }

    def service_question(
        self,
        user: User,
        question: str,
        model: str = "",
        source: str = "",
        language: Language = "ko",
        *,
        limit: int = 5,
        sort: Literal["similarity", "newest", "oldest"] = "similarity",
        date_field: Literal["received", "completed"] = "received",
        page: int = 1,
    ) -> dict[str, object]:
        page_result = self.search_page(
            user, question, model, source, language,
            sort=sort, date_field=date_field, page=page,
        )
        matches = cast("list[CaseMatch]", page_result["items"])
        if limit < 5:
            matches = matches[:limit]
        if not matches:
            return {
                "status": "no_match",
                "answer": "",
                "message": "질문과 일치하는 서비스 처리 사례를 확인하지 못했습니다."
                if language == "ko"
                else "No matching service record was found.",
                "cases": [],
                "sources": self.sources(user),
                "total": page_result["total"], "page": page_result["page"],
                "page_size": page_result["page_size"], "has_more": page_result["has_more"],
                "sort": sort, "date_field": date_field,
            }
        answer = ""
        if self.root is not None and self.ollama is not None:
            from bistos.service_answers import summarize_cases  # noqa: PLC0415

            answer = summarize_cases(question, matches, language, self.ollama, self.root)
        with self.store.lock:
            self._require(user)
            for item in matches:
                if self.detail(user, item.case_id).import_id != item.import_id:
                    raise AppError("서비스 이력이 갱신되었습니다. 다시 검색해 주세요.", 409)
        return {
            "status": "answered" if answer else "matched",
            "answer": answer,
            "message": "과거 처리 기록이며 현재 장비의 원인이나 수리 결과를 확정하지 않습니다."
            if language == "ko"
            else "Historical records do not confirm the current device's cause or repair outcome.",
            "cases": matches,
            "sources": self.sources(user),
            "total": page_result["total"], "page": page_result["page"],
            "page_size": page_result["page_size"], "has_more": page_result["has_more"],
            "sort": sort, "date_field": date_field,
        }
