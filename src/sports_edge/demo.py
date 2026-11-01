"""Synthetic PDFs for tests and the DEMO mode. Every document is labelled as synthetic.

The STS-like builder reproduces the measured geometry of the authentic printed offer (font
sizes, two columns, label centres) so that parser logic is exercised offline.
"""

from datetime import date, datetime

SIX = [150.45, 168.75, 187.05, 205.3, 223.6, 241.9]
THREE = [204.1, 222.75, 241.35]
TWO = [222.45, 241.15]
COLUMN_LEFT = (25.5, 300.5)
COLUMN_WIDTH = 270.0
MARKER = "FIXTURE TESTOWY - DANE SYNTETYCZNE - NIE JEST DOKUMENTEM OPERATORA"


def _font():
    import pymupdf

    return pymupdf.Font("cjk")  # built-in font with Polish characters


def build_sts_pdf(blocks: list[dict], *, generated: str, title: str = MARKER) -> bytes:
    """blocks: {day}, {sport}, {league}, {market, labels}, {event: (code, name, odds, time)}."""
    import pymupdf

    font = _font()
    doc = pymupdf.open()
    doc.set_metadata({"title": title, "subject": "synthetic fixture", "creationDate": ""})
    state = {"page": None, "writer": None, "column": 0, "y": 0.0, "centers": SIX}

    def new_page():
        if state["writer"] is not None:
            _footer(state, font, generated)
            state["writer"].write_text(state["page"])
        state["page"] = doc.new_page(width=595.28, height=841.89)
        state["writer"] = pymupdf.TextWriter(state["page"].rect)
        state["column"], state["y"] = 0, 30.0
        state["writer"].append((25.5, 26), "OFERTA KURSOWA", font=font, fontsize=12)

    def put(x, y, text, size, center=False):
        width = font.text_length(text, fontsize=size)
        state["writer"].append((x - width / 2 if center else x, y), text, font=font, fontsize=size)

    def advance(height):
        if state["y"] + height > 790:
            if state["column"] == 0:
                state["column"], state["y"] = 1, 30.0
            else:
                new_page()
        state["y"] += height
        return COLUMN_LEFT[state["column"]], state["y"]

    new_page()
    for block in blocks:
        if "day" in block:
            left, y = advance(24)
            put(left + COLUMN_WIDTH / 2, y, block["day"], 17, center=True)
        elif "sport" in block:
            left, y = advance(22)
            put(left + COLUMN_WIDTH / 2, y, block["sport"], 15, center=True)
        elif "league" in block:
            left, y = advance(18)
            put(left + COLUMN_WIDTH / 2, y, block["league"], 13, center=True)
        elif "market" in block:
            labels = block["labels"]
            centers = {6: SIX, 3: THREE, 2: TWO}.get(len(labels))
            if centers is None:
                step = (241.9 - 125.0) / (len(labels) - 1)
                centers = [125.0 + i * step for i in range(len(labels))]
            state["centers"] = centers
            left, y = advance(14)
            put(left + 27.8, y, block["market"], 9.5)
            for label, center in zip(labels, centers, strict=True):
                put(left + center, y, label, 8.5, center=True)
        elif "event" in block:
            code, name, odds, start = block["event"]
            left, y = advance(11)
            put(left, y, code, 8.2)
            # Like the authentic offer: shrink long names to end before the odds columns.
            room = state["centers"][0] - 8.0 - 27.8 - 2.0
            size = min(7.2, 7.2 * room / max(font.text_length(name, fontsize=7.2), 1e-6))
            put(left + 27.8, y, name, max(size, 4.0))
            for value, center in zip(odds, state["centers"], strict=True):
                if value:
                    put(left + center, y, value, 7.2, center=True)
            if start:
                put(left + 251.9, y, start, 7.2)
    left, y = advance(12)
    put(left, min(y, 805), MARKER, 6)
    _footer(state, font, generated)
    state["writer"].write_text(state["page"])
    data = doc.tobytes()
    doc.close()
    return data


def _footer(state, font, generated):
    state["writer"].append((31.2, 825), generated, font=font, fontsize=12)
    state["writer"].append((508.2, 825), "Strona 1/1", font=font, fontsize=12)


DAYS = ["PONIEDZIAŁEK", "WTOREK", "ŚRODA", "CZWARTEK", "PIĄTEK", "SOBOTA", "NIEDZIELA"]


def demo_document(day: date, generated: datetime) -> bytes:
    """DEMO STS-like offer with fictional teams; odds are invented, never analysed as real."""
    header = f"{DAYS[day.weekday()]} {day.isoformat()}"
    sts = build_sts_pdf(
        [
            {"day": header},
            {"sport": "PIŁKA NOŻNA"},
            {"league": "DEMOLANDIA - LIGA PRZYKŁADOWA"},
            {"market": "Mecz", "labels": ["1", "X", "2", "1X", "X2", "12"]},
            {
                "event": (
                    "9001",
                    "Orły Testowo - Sokoły Demo",
                    ["2.05", "3.40", "3.60", "1.30", "1.75", "1.33"],
                    "18:00",
                )
            },
            {
                "event": (
                    "9002",
                    "Kruki Przykładowo - Jastrzębie Fikcja",
                    ["1.80", "3.70", "4.40", "1.22", "2.00", "1.28"],
                    "20:30",
                )
            },
            {"market": "Liczba goli", "labels": ["-", "+"]},
            {"event": ("9003", "Orły Testowo - Sokoły Demo (2.5)", ["1.95", "1.85"], "18:00")},
            {"sport": "TENIS"},
            {"league": "DEMO TOUR - TURNIEJ PRZYKŁADOWY"},
            {"market": "Zwycięzca meczu", "labels": ["1", "2"]},
            {"event": ("9004", "Kowalska Anna - Nowak Ewa", ["1.62", "2.25"], "16:00")},
        ],
        generated=generated.strftime("%Y-%m-%d %H:%M"),
        title="DEMO - DANE SYNTETYCZNE (układ jak oferta STS)",
    )
    return sts
