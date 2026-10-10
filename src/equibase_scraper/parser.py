"""Position-aware parsing of the observed text-based official chart format.

Only the primary results table is used. Preview running lines and payouts never
provide official positions. Unsupported or incomplete tables cannot replace data.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

from equibase_scraper.downloader import validate_pdf
from equibase_scraper.models import DiscoveredRace, Entry, Fraction, ParsedRace

HEADER = re.compile(
    r"^(?P<track>.+?)\s+-\s+(?P<date>[A-Za-z]+\s+\d{1,2},\s+\d{4})"
    r"\s+-\s+Race\s+(?P<race>\d{1,2})\s*$", re.MULTILINE,
)
TIME = r"(?:\d{1,2}:)?\d{1,2}\.\d{2}"
PARSER_VERSION = "0.1.0"


class ParseError(ValueError):
    pass


def parse_file(path: Path, source: DiscoveredRace, *, max_bytes: int = 20_000_000) -> ParsedRace:
    """Offline parser entry point for authorized local validation."""
    if path.stat().st_size > max_bytes:
        raise ParseError("Local PDF exceeds configured maximum size")
    return parse_chart(path.read_bytes(), source)


@dataclass(frozen=True)
class Span:
    x: float
    y: float
    size: float
    text: str


def _spans(page: pymupdf.Page) -> list[Span]:
    return [
        Span(s["bbox"][0], s["origin"][1], s["size"], s["text"])
        for block in page.get_text("dict")["blocks"] if "lines" in block
        for line in block["lines"] for s in line["spans"]
    ]


def _number(value: str, warnings: list[str], field: str) -> Decimal | None:
    if not value.strip() or value.strip() in {"---", "-", "N/A"}:
        warnings.append(f"Missing {field}")
        return None
    try:
        number = Decimal(value.strip().rstrip("*").replace(",", ""))
        if not number.is_finite() or number < 0:
            raise InvalidOperation
        return number
    except InvalidOperation:
        warnings.append(f"Ambiguous {field}: {value}")
        return None


def _cell(spans: list[Span], left: float, right: float, y: float) -> str:
    # Superscripts are deliberately excluded by baseline, especially in the Fin column.
    return " ".join(
        s.text.strip() for s in sorted(spans, key=lambda s: s.x)
        if left - 1 <= s.x < right - 1 and abs(s.y - y) <= 1.1
    ).strip()


def _finish(text: str, warnings: list[str]) -> tuple[int | None, str]:
    if re.fullmatch(r"\d{1,2}(?:\s*(?:DH|D\.H\.))?", text, re.I):
        return int(re.match(r"\d+", text)[0]), "finished"
    status = {"SCR": "scratched", "SCRATCHED": "scratched", "DQ": "disqualified",
              "DSQ": "disqualified", "DNF": "did_not_finish"}.get(text.upper())
    if status:
        return None, status
    warnings.append(f"Uncertain official finish: {text or '[missing]'}")
    return None, "unknown"


def _trainers(text: str, warnings: list[str]) -> dict[str, str]:
    match = re.search(r"\bTrainers:\s*(.*?)(?=\bOwners:|\bFootnotes|\Z)", text, re.S)
    if not match:
        warnings.append("Program-keyed Trainers section not found")
        return {}
    section = " ".join(match[1].split())
    trainers = {}
    for item in re.finditer(r"(?:^|;)\s*(\d+[A-Z]?)\s*-\s*([^;]+)", section):
        if item[1] in trainers:
            warnings.append(f"Duplicate trainer mapping for program {item[1]}")
            trainers[item[1]] = None
        else:
            trainers[item[1]] = item[2].strip()
    return {k: v for k, v in trainers.items() if v}


def _table(
    page: pymupdf.Page, trainers: dict[str, str], warnings: list[str],
) -> tuple[list[Entry], bool, bool]:
    words = page.get_text("words")
    spans = _spans(page)
    headers = []
    for pgm in (w for w in words if w[4] == "Pgm"):
        line = [w for w in words if abs(w[1] - pgm[1]) < 1.1]
        labels = {w[4]: w[0] for w in line}
        if {"Pgm", "Horse", "Wgt", "PP", "Fin", "Odds", "Comments"} <= labels.keys():
            baseline = next(s.y for s in spans if s.text == "Pgm" and abs(s.x - pgm[0]) < 1)
            headers.append((baseline, labels))
    if not headers:
        return [], False, False
    if len(headers) != 1:
        warnings.append("Multiple primary results tables on one page; manual review required")
        return [], False, True
    top, labels = headers[0]
    # A timing/run-up boundary must be present after the table on this page.
    ends = [s.y for s in spans if s.y > top + 3
            and re.match(r"(?:Fractional Times:|Final Time:|Run-Up:|Winner:)", s.text)]
    if not ends:
        warnings.append("Results table has no verified ending boundary on the page")
        return [], False, True
    bottom = min(ends)
    column_labels = {
        "Pgm", "Horse", "Wgt", "M/E", "PP", "Start", "Str", "Fin", "Odds", "Comments",
    }
    xs = sorted({x for label, x in labels.items()
                 if label in column_labels or re.fullmatch(r"\d+/\d+", label)})

    def cell(label: str, y: float) -> str:
        x = labels[label]
        right = next((v for v in xs if v > x + 1), page.rect.width)
        return _cell(spans, x, right, y)

    anchors = sorted(
        [s for s in spans if top + 3 < s.y < bottom
         and labels["Pgm"] - 1 <= s.x < labels["Horse"] - 1
         and re.fullmatch(r"\d+[A-Z]?", s.text.strip())],
        key=lambda s: s.y,
    )
    entries = []
    complete = bool(anchors)
    for anchor in anchors:
        program = anchor.text.strip()
        horse_jockey = cell("Horse", anchor.y)
        match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", horse_jockey)
        if match:
            horse, jockey = match[1].strip(), match[2].strip()
        else:
            horse, jockey = horse_jockey, None
            warnings.append(f"Horse/jockey boundary ambiguous for program {program}")
            complete = False
        if not horse:
            warnings.append(f"Horse name missing for program {program}")
            complete = False
            continue
        finish_text = cell("Fin", anchor.y)
        finish, status = _finish(finish_text, warnings)
        pp = cell("PP", anchor.y)
        if not pp.isdigit():
            warnings.append(f"Post position missing/ambiguous for program {program}")
        weight = _number(cell("Wgt", anchor.y), warnings, f"weight for program {program}")
        odds = _number(cell("Odds", anchor.y), warnings, f"odds for program {program}")
        trainer = trainers.get(program)
        if not trainer:
            warnings.append(f"Trainer missing for program {program}")
        entries.append(Entry(
            horse_name=horse, program_number=program,
            post_position=int(pp) if pp.isdigit() else None,
            finishing_position=finish, finish_text=finish_text or None, status=status,
            jockey=jockey, trainer=trainer, weight=weight, odds=odds,
        ))
        if status == "unknown":
            complete = False
    # Detect rows that lost the program-number anchor, rather than silently dropping horses.
    horses = [s for s in spans if top + 3 < s.y < bottom
              and labels["Horse"] - 1 <= s.x < labels["Wgt"] - 1 and "(" in s.text]
    if any(not any(abs(h.y - a.y) < 1.1 for a in anchors) for h in horses):
        warnings.append("Misaligned or unanchored result row; table is incomplete")
        complete = False
    if len({e.program_number for e in entries}) != len(entries):
        warnings.append("Duplicate program numbers in results table")
        complete = False
    return entries, complete, True


def _scratches(text: str, warnings: list[str]) -> list[Entry]:
    match = re.search(
        r"Scratched Horse\(s\):\s*(.*?)"
        r"(?=\n(?:Total |Trainers:|Owners:|Footnotes|\s*Copyright)|\Z)",
        text, re.S,
    )
    if not match:
        return []
    items = re.split(r",\s*(?![^()]*\))", " ".join(match[1].split()))
    entries = []
    for item in items:
        name = re.sub(r"\s*\([^()]*\)\s*$", "", item).strip()
        if name:
            entries.append(Entry(horse_name=name, status="scratched", finish_text="SCR"))
            warnings.append(f"Scratch {name}: program, post, connections, odds, weight unavailable")
    return entries


def parse_chart(data: bytes, source: DiscoveredRace) -> ParsedRace:
    validate_pdf(data)
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        raw = "\n\f\n".join(page.get_text(sort=True) for page in doc)
        headers = list(HEADER.finditer(raw))
        identities = {(h["date"], h["race"]) for h in headers}
        if len(identities) != 1:
            raise ParseError("Missing race header or multiple race boundaries; card PDF rejected")
        first = headers[0]
        race_date = datetime.strptime(first["date"], "%B %d, %Y").date()
        if race_date != source.race_date or int(first["race"]) != source.race_number:
            raise ParseError("PDF race identity does not match discovered listing")
        extracted_name = first["track"].replace("/", "").strip()
        if source.track.name:
            normalize = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
            if normalize(extracted_name) != normalize(source.track.name):
                raise ParseError("PDF track name does not match discovered listing")
        warnings: list[str] = []
        track = source.track.model_copy(update={"name": source.track.name or extracted_name})
        trainers = _trainers(raw, warnings)
        entries = []
        verified = []
        for page in doc:
            page_entries, complete, has_table = _table(page, trainers, warnings)
            entries.extend(page_entries)
            if has_table:
                verified.append(complete)
        if not entries:
            warnings.append("Supported primary horse-results table not found")
        complete = bool(entries) and all(verified)
        programs = [e.program_number for e in entries if e.program_number]
        if len(programs) != len(set(programs)):
            warnings.append("Duplicate primary program numbers across pages")
            complete = False
        if re.search(r"\bdisqualif(?:ied|ication)\b", raw, re.I):
            warnings.append(
                "Disqualification narrative requires manual official-order verification"
            )
            for entry in entries:
                entry.finishing_position = None
                entry.status = "unknown"
            complete = False
        entries.extend(_scratches(raw, warnings))
        conditions = raw[first.end():].split("Last Raced", 1)[0]
        race_type_match = re.search(r"^(.+?)\s+-\s+(?:Thoroughbred|Quarter Horse|Arabian|Mixed)",
                                    conditions, re.M)
        distance = re.search(r"Distance:\s*(.+?)\s+On The\s+(.+?)(?:\s+Current Track|\n)",
                             conditions, re.I)
        purse = re.search(r"\bPurse:\s*\$([\d,]+(?:\.\d{2})?)", conditions)
        condition = re.search(r"\bTrack:\s*([^\n]+)", conditions)
        final = re.search(r"\bFinal Time:\s*(" + TIME + r")", raw)
        fraction_match = re.search(r"Fractional Times:\s*(.*?)(?=Final Time:|\n)", raw)
        fractions = []
        if fraction_match:
            for i, time in enumerate(re.findall(TIME, fraction_match[1]), 1):
                fractions.append(Fraction(label=f"fraction-{i}", time=time, order=i))
            if fractions:
                warnings.append("Fraction times are ordered; distance labels are not inferred")
        footnotes = re.search(r"(?:^|\n)Footnotes[^\n]*\n(.*?)(?=\n\s*Copyright|\Z)", raw, re.S)
        if not footnotes:
            warnings.append("Footnotes section not found")
        values = {
            "race_type": race_type_match[1].strip() if race_type_match else None,
            "distance": distance[1].strip() if distance else None,
            "surface": distance[2].strip() if distance else None,
            "purse": Decimal(purse[1].replace(",", "")) if purse else None,
            "track_condition": condition[1].strip() if condition else None,
            "final_time": final[1] if final else None,
        }
        for field, value in values.items():
            if value is None:
                warnings.append(f"Missing or unsupported race field: {field}")
        return ParsedRace(
            track=track, race_date=race_date, race_number=source.race_number,
            **values, entries=entries, fractions=fractions,
            footnotes=footnotes[1].strip() if footnotes else None,
            raw_text=raw, warnings=warnings, complete=complete,
        )
