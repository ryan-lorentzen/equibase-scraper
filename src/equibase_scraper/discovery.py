"""Follow published chart navigation; never enumerate hypothetical card URLs."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from equibase_scraper.config import validate_source_url
from equibase_scraper.http import FetchError, PacedHttp
from equibase_scraper.models import Card, DiscoveredRace, Track

CARD_PATTERN = re.compile(
    r"^(?P<track>[A-Z0-9]{1,6}?)(?P<date>\d{6})(?P<country>[A-Z]{2,3})\.html$"
)
PDF_PATTERN = re.compile(
    r"^(?P<track>[A-Z0-9]{1,6}?)(?P<date>\d{6})(?P<country>[A-Z]{2,3})"
    r"(?P<race>\d{1,2})?\.pdf$"
)
LITERAL_NAV = re.compile(r"(?:window\.)?location\.href\s*=\s*(['\"])([^'\"]+)\1\s*;?$")


def source_link(base: str, href: str) -> str | None:
    try:
        url = validate_source_url(urljoin(base, href.strip()))
    except ValueError:
        return None
    parts = urlsplit(url)
    return urlunsplit(parts._replace(fragment=""))


def card_from_url(url: str, name: str | None = None) -> Card:
    validate_source_url(url)
    match = CARD_PATTERN.fullmatch(urlsplit(url).path.rsplit("/", 1)[-1])
    if not match:
        raise ValueError("Listing URL must identify an actual track/date chart HTML page")
    return Card(
        track=Track(code=match["track"], country=match["country"], name=name),
        race_date=datetime.strptime(match["date"], "%m%d%y").date(),
        listing_url=url,
    )


def parse_index(html: str, base: str) -> tuple[list[Track], list[Card]]:
    soup = BeautifulSoup(html, "html.parser")
    tracks: dict[str, Track] = {}
    cards: dict[str, Card] = {}
    for row in soup.select("tr"):
        cell = row.select_one("td.track")
        if cell is None:
            continue
        anchor = cell.find("a", href=True)
        if anchor is None:
            continue
        query = parse_qs(urlsplit(str(anchor["href"])).query)
        code = query.get("tid", query.get("trk", [None]))[0]
        country = query.get("ctry", query.get("cy", ["USA"]))[0]
        if not code or not re.fullmatch(r"[A-Z0-9]{1,6}", code):
            continue
        track = Track(code=code, country=country, name=anchor.get_text(" ", strip=True) or None)
        tracks[code] = track
        for link in row.select("a[href]"):
            url = source_link(base, str(link["href"]))
            if url is None:
                continue
            try:
                card = card_from_url(url, track.name)
            except ValueError:
                continue
            if card.track.code == code:
                cards[url] = card
    if not tracks and "full charts" not in soup.get_text(" ", strip=True).lower():
        raise FetchError("Unrecognized Full Charts index page")
    return list(tracks.values()), list(cards.values())


def _navigation(soup: BeautifulSoup) -> list[tuple[Tag, str]]:
    targets = [(tag, str(tag["href"])) for tag in soup.select("a[href]")]
    # Observed mobile listing uses literal navigation attributes. No JavaScript execution.
    for tag in soup.select("[onclick]"):
        match = LITERAL_NAV.fullmatch(str(tag["onclick"]).strip())
        if match:
            targets.append((tag, match[2]))
    return targets


def _race_number(url: str, card: Card) -> tuple[int | None, bool]:
    parts = urlsplit(url)
    pdf = PDF_PATTERN.fullmatch(parts.path.rsplit("/", 1)[-1])
    if pdf:
        if (
            pdf["track"] != card.track.code
            or pdf["date"] != card.race_date.strftime("%m%d%y")
            or pdf["country"] != card.track.country
        ):
            return None, False
        return (int(pdf["race"]) if pdf["race"] else None), True
    if parts.path.lower().endswith("/chartemb.cfm"):
        q = parse_qs(parts.query)
        if (
            q.get("track") == [card.track.code]
            and q.get("raceDate") == [card.race_date.strftime("%m/%d/%Y")]
            and q.get("cy") == [card.track.country]
            and q.get("rn", [""])[0].isdigit()
        ):
            return int(q["rn"][0]), False
    return None, False


def parse_listing(html: str, card: Card) -> list[DiscoveredRace]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#pageHeader") or soup.find("h1")
    name = card.track.name
    if title:
        name = re.sub(r"\s+Charts$", "", title.get_text(" ", strip=True))
    track = card.track.model_copy(update={"name": name})
    races: dict[int, DiscoveredRace] = {}
    for tag, href in _navigation(soup):
        url = source_link(card.listing_url, href)
        if url is None:
            continue
        number, is_pdf = _race_number(url, card)
        if number is None or not 1 <= number <= 99:
            continue
        row = tag.find_parent("tr") or tag
        cells = row.select("td")
        race_type = cells[1].get_text(" ", strip=True) if len(cells) > 1 else None
        purse_match = re.search(r"\$([\d,]+(?:\.\d{2})?)", row.get_text(" ", strip=True))
        purse = Decimal(purse_match[1].replace(",", "")) if purse_match else None
        if number not in races:
            races[number] = DiscoveredRace(
                track=track, race_date=card.race_date, race_number=number,
                race_type=race_type or None, purse=purse, chart_url=url,
                listing_url=card.listing_url, pdf_url=url if is_pdf else None,
            )
        elif is_pdf:
            races[number].pdf_url = url
    for race in races.values():
        if not race.race_type:
            race.warnings.append("Race type absent from recognized listing row")
        if race.purse is None:
            race.warnings.append("Purse absent from listing")
    if not races:
        text = soup.get_text(" ", strip=True).lower()
        if not any(phrase in text for phrase in ("no charts", "no races", "no results")):
            raise FetchError(f"No recognized race links in listing: {card.listing_url}")
    return [races[n] for n in sorted(races)]


def published_pdf_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = _navigation(soup)
    for tag in soup.select("iframe[src], embed[src], object[data]"):
        candidates.append((tag, str(tag.get("src") or tag.get("data"))))
    urls = {
        url for _, href in candidates
        if (url := source_link(base, href)) and urlsplit(url).path.lower().endswith(".pdf")
    }
    return sorted(urls)


class Discovery:
    def __init__(self, http: PacedHttp) -> None:
        self.http = http
        self.tracks_checked: set[str] = set()
        self.dates_checked: set[tuple[str, date]] = set()

    def cards(
        self, *, today: date, track_code: str | None = None,
        race_date: date | None = None, listing_url: str | None = None,
    ) -> list[Card]:
        if listing_url:
            card = card_from_url(listing_url)
            if track_code and card.track.code != track_code:
                raise ValueError("Listing URL does not match --track")
            if race_date and card.race_date != race_date:
                raise ValueError("Listing URL does not match --date")
            if not today - timedelta(days=30) <= card.race_date <= today:
                raise ValueError("Version 1 explicit cards must be within the last 31 days")
            self.tracks_checked.add(card.track.code)
            return [card]
        response = self.http.get(self.http.settings.chart_index_url)
        self.http.check_html(response.text, str(response.url))
        tracks, cards = parse_index(response.text, str(response.url))
        self.tracks_checked.update(t.code for t in tracks if not track_code or t.code == track_code)
        first = today - timedelta(days=self.http.settings.lookback_days - 1)
        return sorted(
            [c for c in cards if (not track_code or c.track.code == track_code)
             and (c.race_date == race_date if race_date else first <= c.race_date <= today)],
            key=lambda c: (c.race_date, c.track.code), reverse=True,
        )

    def races(self, card: Card) -> list[DiscoveredRace]:
        self.dates_checked.add((card.track.code, card.race_date))
        response = self.http.get(card.listing_url)
        self.http.check_html(response.text, str(response.url))
        return parse_listing(response.text, card)
