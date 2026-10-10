"""Original synthetic charts generated in memory; no official PDFs are redistributed."""

import pymupdf


def chart_pdf(
    *, finishes=("1", "2"), missing=False, layout="normal", scratches=False,
    race_numbers=(1,), fragmented_horses=False, no_boundary_first=False,
    disqualification=False,
) -> bytes:
    with pymupdf.open() as doc:
        for page_index, race in enumerate(race_numbers):
            page = doc.new_page(width=700, height=800)

            def text(x, y, value, size=8, page=page):
                page.insert_text((x, y), value, fontsize=size)

            text(10, 30, f"SYNTHETIC DOWNS - September 27, 2026 - Race {race}")
            text(10, 45, "MAIDEN SPECIAL WEIGHT - Thoroughbred")
            text(10, 60, "Distance: Six Furlongs On The Dirt Current Track Record: (Test)")
            if not missing:
                text(10, 75, "Purse: $92,000")
            text(10, 90, "Weather: Clear Track: Fast")
            if layout != "unexpected":
                for x, value in [(10, "Last Raced"), (80, "Pgm"), (110, "Horse Name (Jockey)"),
                                 (300, "Wgt"), (330, "M/E"), (355, "PP"), (385, "Start"),
                                 (420, "1/4"), (450, "Str"), (480, "Fin"), (530, "Odds"),
                                 (575, "Comments")]:
                    text(x, 120, value)
                for i, finish in enumerate(finishes):
                    y = 140 + i * 15
                    text(80, y, str(7 - i))
                    horse_y = y + (5 if layout == "misaligned" and i == 1 else 0)
                    if fragmented_horses:
                        text(110, horse_y, f"Test Horse {i + 1}")
                        text(180, horse_y, "(Rider, Test)")
                    else:
                        text(110, horse_y, f"Test Horse {i + 1} (Rider, Test)")
                    if not missing:
                        text(300, y, "119")
                    text(355, y, str(9 - i))
                    text(385, y, "4")
                    text(420, y, "8")
                    text(450, y, "3")
                    text(480, y, finish)
                    # A raised margin must never become the finishing position.
                    text(490 + len(finish) * 3, y - 3, "6 3/4", size=6)
                    if not missing:
                        text(530, y, "1.35*")
                if not no_boundary_first or page_index != 0:
                    text(10, 210, "Fractional Times: 23.20 47.04 58.87 Final Time: 1:10.95")
            else:
                text(10, 140, "No recognizable results columns")
            text(10, 250, "Trainers: 7 - Trainer, One; 6 - Trainer, Two;")
            text(10, 265, "Owners: Synthetic Stable")
            if scratches:
                text(10, 280, "Scratched Horse(s): Test Scratch (Veterinarian)")
            text(10, 310, "Footnotes | View Glossary Of Terms")
            notes = "TEST HORSE 1 won. TEST HORSE 2 finished second."
            if disqualification:
                notes = "TEST HORSE 1 was disqualified after an inquiry."
            text(10, 330, notes)
            text(10, 350, "Copyright Synthetic Fixture")
        return doc.tobytes()
