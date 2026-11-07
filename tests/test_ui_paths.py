"""TASK 007 §28: the main user paths of the interface on SYNTHETIC data.

The same code writes the QA artifact of the CI (``python -m sports_edge.qa``).
"""

import pytest
from conftest import ROOT


def test_main_user_paths_of_the_interface():
    pytest.importorskip("streamlit")
    pytest.importorskip("pymupdf")
    from sports_edge.qa import ui_paths

    results = ui_paths(ROOT / "config")
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    names = {r["path"] for r in results}
    for expected in (
        "start aplikacji (nawigacja)",
        "dashboard",
        "kupon dnia",
        "mój kupon",
        "dodanie selekcji",
        "ocena AI",
        "zmiana stawki",
        "kurs docelowy",
        "tasiemka",
        "symulator budżetu",
        "historia",
        "Uczenie AI",
        "brak internetu (tryb offline)",
        "nieaktualne kursy",
        "brak prognozy",
        "awaria źródła wyników",
        "modele: strefa kuponów i rynek STS",  # TASK 011
        "automatyzacja: prognozy automatyczne i raport tygodniowy",  # TASK 012
        "automatyzacja: kursy STS w dniu meczu",
        "kupon dnia: metoda Ryzyka dnia",
        "modele: bramka v3 (stan)",  # TASK 013
        "modele: akceptacja bramki v3 (zapis decyzji)",
        "kupon dnia: najbliżej (WYSOKA TRAFNOŚĆ)",
        "historia: oczekiwane kontra faktyczne",  # TASK 014
        "ustawienia: profil PEWNY SOLO (domyślnie wyłączony)",
    ):
        assert expected in names
    assert any(name.startswith("brak danych") for name in names)


def test_qa_artifact_contains_no_private_files(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    pytest.importorskip("pymupdf")
    from sports_edge import qa

    fake = [{"path": "x", "ok": True, "detail": ""}]
    monkeypatch.setattr(qa, "ui_paths", lambda config=None: fake)
    summary = qa.write_artifact(tmp_path / "qa")
    assert summary == {"paths": 1, "failed": 0}
    names = {p.name for p in (tmp_path / "qa").rglob("*")}
    assert {"ui_report.json", "versions.txt", "README.md", "demo_sts.pdf"} <= names
    assert not [n for n in names if n.endswith((".db", ".sqlite")) or n == ".env"]
    (tmp_path / "qa" / "leak.db").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="niedozwolone"):
        qa.write_artifact(tmp_path / "qa")
