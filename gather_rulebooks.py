"""
Download + verify candidate rulebook PDFs for cafe games that don't yet
have one.

Workflow per game:
  1. Read candidate URLs from rulebook_candidates.json (key = game name).
  2. Try each candidate in order: download to a temp file, extract first
     page text, verify it mentions the game name (or close enough).
  3. On success, save to rulebooks/<safe-filename>.pdf and stop.
  4. Log per-game outcome to gather_rulebooks_log.json.

Usage:
    python gather_rulebooks.py            # process all candidates
    python gather_rulebooks.py --dry-run  # download + verify, don't save

The candidates file looks like:
    {
      "Mage Knight: Ultimate Edition": [
        "https://www.mageknight.net/.../rulebook.pdf",
        "https://wizkids.com/.../MKUE_Rulebook.pdf"
      ],
      ...
    }
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

CANDIDATES = "rulebook_candidates.json"
LOG = "gather_rulebooks_log.json"
RULEBOOKS_DIR = "rulebooks"
HTTP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def safe_filename(name):
    """Convert 'Mage Knight: Ultimate Edition' -> 'mage knight ultimate edition'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s + "-rulebook.pdf"


def download_pdf(url, dest_path, timeout=60):
    """Download via curl. Returns True on success."""
    try:
        proc = subprocess.run(
            ["curl", "-sL", "--fail", "--max-time", str(timeout),
             "-A", HTTP_UA, "-o", dest_path, url],
            capture_output=True, timeout=timeout + 10,
        )
        return proc.returncode == 0 and os.path.getsize(dest_path) > 5_000
    except Exception:
        return False


def verify_pdf(path, game_name, min_pages=1):
    """
    Extract first 2 pages of text, check it looks like a rulebook for this
    game. Returns (ok: bool, reason: str).
    """
    from pypdf import PdfReader
    try:
        reader = PdfReader(path)
    except Exception as e:
        return False, f"pypdf error: {e}"

    n_pages = len(reader.pages)
    if n_pages < min_pages:
        return False, f"too short ({n_pages} pages)"

    text = ""
    for i in range(min(3, n_pages)):
        try:
            text += reader.pages[i].extract_text() or ""
        except Exception:
            continue
    if not text.strip():
        return False, "no extractable text"

    # Loose name-match: each word of game name (>=4 letters) should appear
    norm_text = text.lower()
    important_words = [w.lower() for w in re.findall(r"[A-Za-z]+", game_name)
                        if len(w) >= 4]
    matched = [w for w in important_words if w in norm_text]
    if important_words and len(matched) >= max(1, len(important_words) // 2):
        return True, f"matched {len(matched)}/{len(important_words)} words"
    return False, f"name not in PDF (matched only {len(matched)}/{len(important_words)})"


def already_have(game_name):
    """
    Filename-based check for existing rulebook. Matches if a file's
    normalized stem (without the -rulebook/-faq suffix) equals the
    normalized game name. Avoids false positives where '7 Wonders'
    matches '7_wonders_duel-rulebook.pdf'.
    """
    def norm(s):
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    target = safe_filename(game_name)
    if os.path.exists(os.path.join(RULEBOOKS_DIR, target)):
        return target

    norm_game = norm(game_name)
    suffixes = ("rulebook", "faq", "errata", "supplement", "appendix")
    for f in os.listdir(RULEBOOKS_DIR):
        if not f.lower().endswith(".pdf"):
            continue
        stem = os.path.splitext(f)[0].lower()
        # Strip trailing "-suffix" / "_suffix"
        for sep in ("-", "_", " "):
            for sfx in suffixes:
                tail = sep + sfx
                if stem.endswith(tail):
                    stem = stem[:-len(tail)]
                    break
        if norm(stem) == norm_game:
            return f
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify but don't save into rulebooks/")
    parser.add_argument("--candidates", default=CANDIDATES)
    args = parser.parse_args()

    if not os.path.exists(args.candidates):
        print(f"Missing candidates file: {args.candidates}")
        sys.exit(1)

    os.makedirs(RULEBOOKS_DIR, exist_ok=True)

    with open(args.candidates, encoding="utf-8") as f:
        candidates = json.load(f)

    log = {"results": []}
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            log = json.load(f)

    done_games = {r["game"] for r in log["results"]}

    saved = skipped = failed = 0
    for game, urls in candidates.items():
        if game in done_games:
            continue

        existing = already_have(game)
        if existing:
            print(f"SKIP  {game}: already have {existing}")
            log["results"].append({"game": game, "status": "exists",
                                    "filename": existing})
            skipped += 1
            continue

        print(f"\n[{game}]")
        if not urls:
            print(f"  -> no candidates")
            log["results"].append({"game": game, "status": "no_candidates"})
            failed += 1
            continue

        success = False
        for url in urls:
            print(f"  trying: {url}")
            tmpf = os.path.join(tempfile.gettempdir(),
                                 f"rb_{abs(hash(url))}.pdf")
            try:
                if not download_pdf(url, tmpf):
                    print(f"    [download failed]")
                    continue
                ok, reason = verify_pdf(tmpf, game)
                print(f"    verify: {reason}")
                if not ok:
                    continue
                if args.dry_run:
                    print(f"    [dry-run] would save")
                    log["results"].append({
                        "game": game, "status": "dry_run_ok",
                        "url": url, "verify": reason,
                    })
                    success = True
                    break
                target = os.path.join(RULEBOOKS_DIR, safe_filename(game))
                shutil.move(tmpf, target)
                print(f"  -> saved: {target}")
                log["results"].append({
                    "game": game, "status": "saved",
                    "url": url, "filename": os.path.basename(target),
                    "verify": reason,
                })
                saved += 1
                success = True
                break
            finally:
                if os.path.exists(tmpf):
                    try:
                        os.remove(tmpf)
                    except OSError:
                        pass

        if not success:
            print(f"  -> all candidates failed")
            log["results"].append({"game": game, "status": "all_failed",
                                    "urls": urls})
            failed += 1

        # Persist log after each game
        with open(LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)

        # Be polite to publisher servers
        time.sleep(1.0)

    print()
    print("=" * 60)
    print(f"saved:   {saved}")
    print(f"skipped: {skipped}")
    print(f"failed:  {failed}")


if __name__ == "__main__":
    main()
