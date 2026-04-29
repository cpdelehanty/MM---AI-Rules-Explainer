"""
Diagnostic: For each PDF in rulebooks/, compute the title that
process_rulebooks.py will derive from its filename, and check whether
that title matches a cafe_games.name.

Reports:
- Exact match (rulebook -> cafe game by name)
- Fuzzy match (likely same game, name differs by punctuation/casing)
- No match (rulebook for a game not in our cafe library)
- Cafe games missing a rulebook
"""
import os
import re
import sqlite3
from process_rulebooks import extract_game_title_from_filename
from database_recs import DB_PATH

RULEBOOKS = "rulebooks"


def normalize(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def main():
    pdfs = sorted(f for f in os.listdir(RULEBOOKS) if f.endswith(".pdf"))
    derived = {f: extract_game_title_from_filename(f) for f in pdfs}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM cafe_games")
    cafe_names = sorted(r[0] for r in cur.fetchall())
    conn.close()

    cafe_norm = {normalize(n): n for n in cafe_names}

    exact = []
    fuzzy = []
    missing_cafe = []
    matched_cafe = set()

    for pdf, title in derived.items():
        if title in cafe_names:
            exact.append((pdf, title))
            matched_cafe.add(title)
        else:
            n = normalize(title)
            if n in cafe_norm:
                fuzzy.append((pdf, title, cafe_norm[n]))
                matched_cafe.add(cafe_norm[n])
            else:
                missing_cafe.append((pdf, title))

    cafe_no_rulebook = [n for n in cafe_names if n not in matched_cafe]

    print(f"PDFs:         {len(pdfs)}")
    print(f"Cafe games:   {len(cafe_names)}")
    print()
    print(f"EXACT match (rulebook title == cafe_games.name): {len(exact)}")
    print()
    print(f"FUZZY match (same after normalizing): {len(fuzzy)}")
    if fuzzy:
        print("  These rulebooks land on a slightly-different cafe_games.name —")
        print("  the link will break unless we fix it:")
        for pdf, derived_title, cafe in fuzzy:
            print(f"    {pdf}")
            print(f"      derived -> {derived_title!r}")
            print(f"      cafe    -> {cafe!r}")
    print()
    print(f"NO match (rulebook title not in cafe_games at all): {len(missing_cafe)}")
    for pdf, derived_title in missing_cafe:
        print(f"    {pdf}  ->  {derived_title!r}")
    print()
    print(f"Cafe games without a rulebook ({len(cafe_no_rulebook)}):")
    for n in cafe_no_rulebook[:30]:
        print(f"    {n!r}")
    if len(cafe_no_rulebook) > 30:
        print(f"    ... and {len(cafe_no_rulebook) - 30} more")


if __name__ == "__main__":
    main()
