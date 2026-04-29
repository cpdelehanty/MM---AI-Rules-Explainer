"""
Aliases for rulebook -> cafe_games.name resolution.

Most rulebooks resolve via a normalize-and-compare match (case +
punctuation insensitive). The handful that differ in actual wording —
typos, "&" vs "and", etc. — go here.

Keys are the title that `process_rulebooks.extract_game_title_from_filename`
produces from the PDF filename. Values are the canonical cafe_games.name.
"""

ALIASES = {
    "A Fake Artist Goes To New York": "Fake Artist Goes to New York",
    "Blockbuster Party Game":         "Blockbuster Movie Game",
    "Chamelion":                       "The Chameleon",
    "Lama":                            "Llama",
    "Lewis And Clark":                 "Lewis & Clark",
    "Marco Polo Ii":                   "Marco Polo II: In the Service of the Khan",
    "The Voyages Of Marco Polo":       "Marco Polo",
    "Wits And Wagers":                 "Wits & Wagers",
}
