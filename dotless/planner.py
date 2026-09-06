"""Description -> remix recipe (JSON).

If ANTHROPIC_API_KEY is set, Claude turns the user's text into a recipe that the engine
renders. Without a key, a per-genre default recipe is used so the pipeline still runs.
"""
import copy
import json
import os
import re

GENRE_DEFAULTS = {
    "ukg": {
        "genre": "ukg", "bpm": 132, "swing": 0.58, "energy": 0.8,
        "drums": {"pattern": "two_step_shuffle", "hat_density": 1},
        "bass": {"style": "bounce"},
        "stabs": {"style": "organ"},
        "vocal": {"chop": "phrases", "stutter": 0.15, "pitch_shift": 2},
        "sections": [
            {"name": "intro", "bars": 8, "layers": ["hats", "vocal_chops", "stabs"]},
            {"name": "build", "bars": 8, "layers": ["drums", "stabs", "vocal_chops", "riser"]},
            {"name": "drop", "bars": 16, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "breakdown", "bars": 8, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 16, "layers": ["drums", "bass", "stabs", "melody", "vocal_chops"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "bass"]},
        ],
    },
    "dnb": {
        "genre": "dnb", "bpm": 174, "swing": 0.5, "energy": 0.85,
        "drums": {"pattern": "two_step_break", "hat_density": 2},
        "bass": {"style": "reese"},
        "stabs": {"style": "pad"},
        "vocal": {"chop": "phrases", "stutter": 0.1, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 16, "layers": ["hats", "stabs", "vocal"]},
            {"name": "build", "bars": 8, "layers": ["drums", "stabs", "vocal_chops", "riser"]},
            {"name": "drop", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal_chops"]},
            {"name": "breakdown", "bars": 16, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "stabs"]},
        ],
    },
    "footwork": {
        "genre": "footwork", "bpm": 160, "swing": 0.5, "energy": 0.9,
        "drums": {"pattern": "tresillo_808", "hat_density": 2},
        "bass": {"style": "808"},
        "stabs": {"style": "stab"},
        "vocal": {"chop": "words", "stutter": 0.6, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 8, "layers": ["vocal_chops", "inst_chops"]},
            {"name": "drop", "bars": 24, "layers": ["drums", "bass", "vocal_chops", "inst_chops"]},
            {"name": "breakdown", "bars": 8, "layers": ["vocal", "stabs", "riser"]},
            {"name": "drop2", "bars": 24, "layers": ["drums", "bass", "vocal_chops", "melody", "inst_chops"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "vocal_chops"]},
        ],
    },
    "house": {
        "genre": "house", "bpm": 126, "swing": 0.54, "energy": 0.75,
        "drums": {"pattern": "four_floor", "hat_density": 1},
        "bass": {"style": "sub"},
        "stabs": {"style": "organ"},
        "vocal": {"chop": "phrases", "stutter": 0.05, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 16, "layers": ["drums", "hats", "vocal_chops"]},
            {"name": "drop", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "breakdown", "bars": 16, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 32, "layers": ["drums", "bass", "stabs", "vocal_chops"]},
            {"name": "outro", "bars": 16, "layers": ["drums", "bass"]},
        ],
    },
}

ARTIST_HINTS = [
    ("rashad", "footwork"), ("spinn", "footwork"), ("juke", "footwork"), ("footwork", "footwork"),
    ("virji", "ukg"), ("interplanetary", "ukg"), ("garage", "ukg"), ("ukg", "ukg"), ("2-step", "ukg"),
    ("drum and bass", "dnb"), ("drum & bass", "dnb"), ("dnb", "dnb"), ("jungle", "dnb"), ("liquid", "dnb"),
    ("house", "house"), ("disco", "house"),
]

ALLOWED = {
    "genre": list(GENRE_DEFAULTS),
    "drums.pattern": ["two_step_shuffle", "two_step_break", "tresillo_808", "four_floor"],
    "bass.style": ["bounce", "reese", "808", "sub", "none"],
    "stabs.style": ["organ", "pad", "stab", "none"],
    "vocal.chop": ["phrases", "words", "none"],
    "layers": ["drums", "hats", "bass", "stabs", "inst_chops", "melody", "vocal", "vocal_chops", "riser"],
}

SYSTEM = """You are the arrangement planner for .less, a remix engine aimed at DJs and producers.
You receive a user's text description of the remix they want plus analysis of the source song
(tempo, key, per-bar chords). Reply with ONE JSON object and nothing else - no prose, no code fences.

Schema (all fields required):
{"genre": <one of %(genre)s>, "bpm": <number>, "swing": <0.5-0.67>, "energy": <0-1>,
 "key_root": <note name>, "mode": <"major"|"minor">,
 "drums": {"pattern": <one of %(drums.pattern)s>, "hat_density": <1 = 8ths, 2 = 16ths>},
 "bass": {"style": <one of %(bass.style)s>},
 "stabs": {"style": <one of %(stabs.style)s>},
 "vocal": {"chop": <one of %(vocal.chop)s>, "stutter": <0-1 probability of retrigger stutters>,
           "pitch_shift": <semitones, -5..7>},
 "sections": [{"name": <str>, "bars": <int>, "layers": [<subset of %(layers)s>]}, ...],
 "notes": <one short sentence on the intent>}

House style vocabulary:
- DJ Rashad / DJ Spinn / juke / footwork -> genre footwork, 160 bpm, tresillo_808 drums, 808 bass,
  vocal chop "words" with high stutter (rapid retriggers), sparse stabs.
- Sammy Virji / Interplanetary Criminal / UK garage -> genre ukg, 130-136 bpm, two_step_shuffle with
  swing 0.56-0.62, bounce bass, organ stabs, pitched-up vocal chops (+2 to +4 semitones).
- drum and bass / jungle / liquid -> genre dnb, 170-176 bpm, two_step_break, reese bass, pad stabs.
- house / disco -> genre house, 122-128 bpm, four_floor, sub bass, organ stabs.
"melody" plays the source's own instrumental (its synths/melody/chords) continuously, high-pass
filtered above the new bass and ducked under the kick, in sync with "vocal". Use it whenever the
user asks to keep the original melody, synths, chords or instrumental. "inst_chops" instead
re-triggers beat slices of the instrumental on the grid.
Keep the source key (key_root / mode) unless the user asks to change it.
Total bars must be between 48 and 128. Sections named "drop*" are the high-energy parts and must
include drums and bass. Use "riser" as the last layer of a section that leads into a drop.
"""


def guess_genre(description, fallback="ukg"):
    d = (description or "").lower()
    for needle, genre in ARTIST_HINTS:
        if needle in d:
            return genre
    return fallback


def default_recipe(genre, analysis):
    r = copy.deepcopy(GENRE_DEFAULTS.get(genre, GENRE_DEFAULTS["ukg"]))
    r["key_root"] = analysis.get("key_root", "A")
    r["mode"] = analysis.get("mode", "minor")
    r["notes"] = "default recipe for %s" % r["genre"]
    return r


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else text


def _validate(recipe, base):
    """Fill gaps and clamp values so the engine never sees garbage."""
    out = copy.deepcopy(base)
    for k in ("genre", "bpm", "swing", "energy", "key_root", "mode", "notes"):
        if k in recipe:
            out[k] = recipe[k]
    if out["genre"] not in ALLOWED["genre"]:
        out["genre"] = base["genre"]
    out["bpm"] = float(min(200, max(60, out["bpm"])))
    out["swing"] = float(min(0.67, max(0.5, out.get("swing", 0.5))))
    out["energy"] = float(min(1.0, max(0.1, out.get("energy", 0.8))))
    for grp, key in (("drums", "pattern"), ("bass", "style"), ("stabs", "style"), ("vocal", "chop")):
        val = (recipe.get(grp) or {}).get(key)
        if val in ALLOWED["%s.%s" % (grp, key)]:
            out[grp][key] = val
    if "drums" in recipe and recipe["drums"].get("hat_density") in (1, 2):
        out["drums"]["hat_density"] = recipe["drums"]["hat_density"]
    if "vocal" in recipe:
        out["vocal"]["stutter"] = float(min(1, max(0, recipe["vocal"].get("stutter", out["vocal"]["stutter"]))))
        out["vocal"]["pitch_shift"] = int(min(7, max(-5, recipe["vocal"].get("pitch_shift", 0))))
    secs = []
    for s in recipe.get("sections") or []:
        try:
            bars = int(s["bars"])
            layers = [l for l in s.get("layers", []) if l in ALLOWED["layers"]]
            if bars > 0 and layers:
                secs.append({"name": str(s.get("name", "section")), "bars": bars, "layers": layers})
        except (KeyError, TypeError, ValueError):
            continue
    if secs and 16 <= sum(s["bars"] for s in secs) <= 160:
        out["sections"] = secs
    return out


def make_recipe(description, genre, analysis):
    genre = genre if genre in GENRE_DEFAULTS else guess_genre(description)
    base = default_recipe(genre, analysis)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        base["notes"] = "no ANTHROPIC_API_KEY set - used default %s recipe" % genre
        return base
    try:
        import anthropic
        client = anthropic.Anthropic()
        user = json.dumps({
            "description": description or "",
            "requested_genre": genre,
            "analysis": {k: analysis.get(k) for k in ("bpm", "key_root", "mode", "duration_sec")},
            "first_chords": (analysis.get("chords") or [])[:16],
        })
        msg = client.messages.create(
            model=os.environ.get("DOTLESS_MODEL", "claude-sonnet-5"),
            max_tokens=1500,
            system=SYSTEM % {k: json.dumps(v) for k, v in ALLOWED.items()},
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        data = json.loads(_extract_json(text))
        return _validate(data, base)
    except Exception as exc:  # never let the planner kill a job
        base["notes"] = "planner fallback (%s): default %s recipe" % (type(exc).__name__, genre)
        return base
