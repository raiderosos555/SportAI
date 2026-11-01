# Archiwum konfiguracji (nieaktywne)

TASK 003 (01.11.2026): aplikacja obsługuje wyłącznie STS. Pliki Superbet przeniesiono tutaj,
aby zachować historię decyzji; `load_profiles()` czyta tylko `config/bookmakers/` i tych
plików nie wczytuje. Ewentualne rekordy Superbet w bazie są oznaczone w tabeli
`archived_entities` i pomijane we wszystkich aktywnych analizach.
