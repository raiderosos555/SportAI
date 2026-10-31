"""Labelled synthetic offer documents (FIXTURE TESTOWY). Odds are invented for tests only."""

from sports_edge.demo import build_generic_pdf, build_sts_pdf

SIX = ["1", "X", "2", "1X", "X2", "12"]
SUPERBET_TITLE = "SUPERBET - OFERTA DZIENNA - FIXTURE TESTOWY (DANE SYNTETYCZNE)"


def sts_blocks(filler: int = 160) -> list[dict]:
    blocks = [
        {"day": "SOBOTA 2026-10-31"},
        {"sport": "PIŁKA NOŻNA"},
        {"league": "HISZPANIA - LALIGA"},
        {"market": "Mecz", "labels": SIX},
        {
            "event": (
                "4101",
                "Real Madrid - Barcelona",
                ["2.10", "3.60", "3.25", "1.38", "1.70", None],
                "21:00",
            )
        },
        {"market": "Liczba goli", "labels": ["-", "+"]},
        {"event": ("4103", "Real Madrid - Barcelona (2.5)", ["2.05", "1.75"], "21:00")},
        {"market": "1. drużyna - liczba goli", "labels": ["-", "+"]},
        {"event": ("4104", "Real Madrid (1.5)", ["1.95", "1.80"], "21:00")},
        {"market": "Zakład bez remisu", "labels": ["1", "2"]},
        {"event": ("4105", "Real Madrid - Barcelona", ["1.55", "2.35"], "21:00")},
        {"league": "POLSKA - EKSTRAKLASA"},
        {"market": "Mecz", "labels": SIX},
        {
            "event": (
                "4201",
                "Legia Warszawa - Lech Poznań",
                ["2.40", "3.30", "2.90", "1.40", "1.55", "1.33"],
                "20:30",
            )
        },
        {
            "event": (
                "4202",
                "Górnik Zabrze - Śląsk Wrocław",
                ["2.00", "3.40", "3.70", "1.30", "1.75", "1.32"],
                "18:00",
            )
        },
        {"market": "Rynek Testowy Nieznany", "labels": ["1", "2"]},
        {"event": ("4203", "Legia Warszawa - Lech Poznań", ["1.50", "2.50"], "20:30")},
        {"league": "USA - NCAA"},
        {"market": "Mecz", "labels": SIX},
        {
            "event": (
                "4501",
                "Północne Orły Akademickie - Południowe Sokoły Uniw...",
                ["1.42", "20.00", "2.90", "1.32", "2.53", None],
                "23:30",
            )
        },
        {"market": "Zwycięzca meczu", "labels": ["1", "2"]},
        {
            "event": (
                "4502",
                "Północne Orły Akademickie - Południowe Sokoły Uniwersyteckie",
                ["1.40", "2.75"],
                "23:30",
            )
        },
        {"sport": "TENIS"},
        {"league": "ATP - PARYŻ"},
        {"market": "Zwycięzca meczu", "labels": ["1", "2"]},
        {"event": ("4301", "Kowalski Jan - Nowak Piotr", ["1.70", "2.10"], "15:00")},
        {"sport": "KOSZYKÓWKA"},
        {"league": "USA - NBA"},
        {"market": "Liczba punktów (z dogrywką)", "labels": ["-", "+"]},
        {"event": ("4401", "Zespół Alfa - Zespół Beta (215.5)", ["1.90", "1.90"], "23:00")},
        {"sport": "PIŁKA NOŻNA"},
        {"league": "TESTLANDIA - LIGA WYPEŁNIACZY"},
        {"market": "Mecz", "labels": SIX},
    ]
    for i in range(filler):
        blocks.append(
            {
                "event": (
                    f"{5000 + i}",
                    f"Klub {i:02d} - Klub {i + 100:02d}",
                    ["2.20", "3.20", "3.10", "1.33", "1.58", "1.30"],
                    "16:00",
                )
            }
        )
    return blocks


def sts_pdf(filler: int = 160) -> bytes:
    return build_sts_pdf(sts_blocks(filler), generated="2026-10-31 12:00")


def superbet_pdf() -> bytes:
    return build_generic_pdf(
        [
            ("text", SUPERBET_TITLE),
            ("text", "Sobota, 31.10.2026"),
            ("text", "PIŁKA NOŻNA"),
            ("text", "Hiszpania - LaLiga"),
            ("header", "Mecz", SIX),
            (
                "event",
                "7101",
                "21:00",
                "Real Madryt - FC Barcelona",
                ["2,15", "3,50", "3,20", "1,38", "1,66", "1,31"],
            ),
            ("header", "Gole", ["Poniżej", "Powyżej"]),
            ("event", "7102", "21:00", "Real Madryt - FC Barcelona (2.5)", ["2,00", "1,80"]),
            ("text", "Polska - Ekstraklasa"),
            ("header", "Mecz", SIX),
            (
                "event",
                "7201",
                "20:30",
                "Legia Warszawa - Lech Poznań",
                ["2,45", "3,25", "2,85", "1,41", "1,52", "1,34"],
            ),
            (
                "event",
                "7202",
                "18:05",
                "Górnik Zabrze - Śląsk Wrocław",
                ["1,95", "3,45", "3,80", "1,29", "1,78", "1,31"],
            ),
            (
                "event",
                "7203",
                "19:00",
                "Zagłębie Lubin - Pogoń Szczecin",
                ["2,60", "3,20", "2,70", "", "", ""],
            ),
        ],
        title=SUPERBET_TITLE,
    )
