# Audit 0094 - City Standalone Protocol Reproduction Fix

## Scope

This audit records a source-level correction for `personHasCityOfDeath`
reproduction. No hidden TEST gold, web, RAG, external KB, external factual
corpus, or local neural TEST inference was used.

## Finding

The local winning prediction artifact:

`outputs/submission-best/predictions.jsonl`

contains exactly 15 non-empty `personHasCityOfDeath` rows. Those 15 rows match
the user's standalone Colab two-stage Mistral protocol output exactly:

- `Viktor Savinykh` -> `Moscow`
- `Robby Müller` -> `Amsterdam`
- `Bruce Chatwin` -> `Nice`
- `George A. Romero` -> `Toronto`
- `Jean-Paul Proust` -> `Paris`
- `Sid Ganis` -> `Los Angeles`
- `Mary Ellen Mark` -> `New York City`
- `John Lewis (civil rights leader)` -> `Atlanta`
- `Petr Hájek (logician)` -> `Prague`
- `Maeve Binchy` -> `Dublin`
- `Haing S. Ngor` -> `Los Angeles`
- `Jean Ichbiah` -> `Paris`
- `Michael Parks` -> `Los Angeles`
- `Angela Carter` -> `London`
- `Uladzimir Basalyha` -> `Minsk`

The current source, however, implemented the City layer as an empty-only rescue
and did not pass the standalone Colab system prompt to Mistral. That could not
guarantee reproduction when a fresh core run produced any non-empty City output
different from the winning artifact.

## Correction

Added `leaderboard_repair.city_rescue_mode`.

Default:

```yaml
city_rescue_mode: EMPTY_ONLY
```

Current leaderboard profiles E1 through F1 now explicitly use:

```yaml
city_rescue_mode: DIRECT_ALL_STANDALONE
```

In this mode the City layer ignores the upstream City output, runs the same
two-stage standalone protocol for every `personHasCityOfDeath` row, and replaces
the row with either a singleton city or `[]`.

The system prompt now matches the user's standalone Colab prompt:

```text
You are a factual knowledge-base completion system.

Use only factual knowledge encoded in your model parameters.

Pay close attention to the exact identity of the named person,
including qualifiers such as occupations or parentheses.

Do not invent an answer merely to avoid UNKNOWN.

Follow the requested output format exactly.
Do not explain unless explicitly asked.
```

Parser behavior was aligned with the standalone protocol:

- life status reads the first non-empty line
- city recall accepts first-line `CITY: <city name>` case-insensitively
- `UNKNOWN` still maps to no object

## Active Relation Semantics

For Profile E3 and Profile F1:

| Relation | Final strategy | Mutation |
|---|---|---|
| `awardWonBy` | Core -> AwardMetadataNormalizer | cleanup/dedupe only |
| `companyTradesAtStockExchange` | E3: core only; F1: empty-only stock rescue | F1 may fill empty stock rows |
| `countryLandBordersCountry` | core only | no post-repair |
| `personHasCityOfDeath` | two-stage standalone Mistral City protocol | DIRECT_ALL replacement |
| `hasCapacity` | MistralCapacityMultiView | DIRECT_ALL replacement |
| `hasArea` | MistralAreaMultiView | DIRECT_ALL replacement |

## Validation

Focused validation:

```text
python -m pytest tests/test_profile_e1_mistral_city_rescue.py tests/test_leaderboard_repair.py -q
23 passed
```

Pyflakes focused validation:

```text
python -m pyflakes src/cover_kbc/leaderboard_repair tests/test_profile_e1_mistral_city_rescue.py tests/test_leaderboard_repair.py
PASS
```

## Status

This fixes source/config reproducibility for the City layer. It does not claim a
new hidden TEST score. The hidden leaderboard scores remain user-provided.
