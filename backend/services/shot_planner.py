"""Shot planner — total duration + rhythm → sequence/shot distribution.

Used by workflow mode to turn a single "I want a 40s fast video" intent into:
  - N Kling Omni calls, each with AT MOST 2 shots (outfit consistency degrades
    sharply as shot count per call increases — tested empirically)
  - Each shot ≥ 3s (Kling Omni minimum)
  - Arc beat role per shot (so GPT can write role-aware prompts)

Why 2 shots per call: Kling Omni multishot can pack up to ~15s/call, but with
3+ shots the model loses the garment. 2 shots per call is the sweet spot —
multishot benefit (tight continuity) + stable outfit.

For longer videos we chain calls (last frame → next first frame) and concat
with ffmpeg — same pattern as multi-outfit workflow today.
"""

from __future__ import annotations

from typing import List, Optional

# Kling Omni hard limits
KLING_MAX_SEQUENCE_DURATION = 15
KLING_MIN_SHOT_DURATION = 3
KLING_MAX_SHOT_DURATION = 10

# Outfit-consistency cap: no more than 2 shots inside a single Kling call
MAX_SHOTS_PER_SEQUENCE = 2

# Workflow-level caps
MAX_TOTAL_DURATION = 60
MIN_TOTAL_DURATION = 6

# rhythm → preferred shot length (seconds)
_RHYTHM_SHOT_LEN = {
    "slow":   6,   # paired → 12s/call
    "normal": 3,   # paired → 6s/call — kullanıcı tercihi: 4s fazla uzun kalıyor
    "fast":   3,   # paired → 6s/call (Kling minimum shot)
}


def _rhythm_shot_len(rhythm: str) -> int:
    return _RHYTHM_SHOT_LEN.get(rhythm, _RHYTHM_SHOT_LEN["normal"])


def clamp_total_duration(total: int) -> int:
    return max(MIN_TOTAL_DURATION, min(MAX_TOTAL_DURATION, int(total)))


def _distribute_total(total: int, n_shots: int) -> List[int]:
    """Spread `total` seconds across `n_shots` respecting per-shot min/max."""
    base = total // n_shots
    extra = total - (base * n_shots)
    durations = [base + (1 if i < extra else 0) for i in range(n_shots)]
    return [max(KLING_MIN_SHOT_DURATION, min(KLING_MAX_SHOT_DURATION, d)) for d in durations]


def plan_sequences(total_duration: int, rhythm: str = "normal") -> List[List[dict]]:
    """Split total_duration into shots, grouped into sequences of max 2 shots.

    Returns a list of sequences. Each sequence is a list of shot dicts:
        {"duration": int, "seq_index": int, "shot_index": int, "global_index": int}

    Example — 40s normal rhythm (target 4s/shot):
        10 shots of 4s → 5 sequences of [4,4]  → 5 Kling calls
    Example — 40s slow rhythm (target 6s/shot):
        7 shots averaging ~5.7s → 4 sequences ([6,6],[6,6],[5,5],[6]) → 4 calls
    Example — 40s fast rhythm (target 3s/shot):
        13 shots averaging ~3s → 7 sequences → 7 calls
    """
    total_duration = clamp_total_duration(total_duration)
    target_shot = _rhythm_shot_len(rhythm)

    # Shot count: keep shots close to target length
    n_shots = max(1, round(total_duration / target_shot))
    # Clamp so each shot stays ≥ KLING_MIN_SHOT_DURATION
    n_shots = min(n_shots, total_duration // KLING_MIN_SHOT_DURATION)
    n_shots = max(1, n_shots)

    shot_durations = _distribute_total(total_duration, n_shots)

    # Group shots into sequences of ≤ MAX_SHOTS_PER_SEQUENCE, each ≤ 15s
    sequences: List[List[dict]] = []
    global_idx = 0
    seq_index = 0
    i = 0
    while i < len(shot_durations):
        chunk = shot_durations[i:i + MAX_SHOTS_PER_SEQUENCE]
        # Safety: if the 2-shot chunk exceeds Kling 15s cap, keep only 1 shot
        if sum(chunk) > KLING_MAX_SEQUENCE_DURATION and len(chunk) > 1:
            chunk = chunk[:1]
            step = 1
        else:
            step = len(chunk)

        seq_shots = []
        for si, d in enumerate(chunk):
            seq_shots.append({
                "duration": int(d),
                "seq_index": seq_index,
                "shot_index": si,
                "global_index": global_idx,
            })
            global_idx += 1
        sequences.append(seq_shots)
        seq_index += 1
        i += step

    return sequences


def total_shot_count(sequences: List[List[dict]]) -> int:
    return sum(len(s) for s in sequences)


def flat_shot_list(sequences: List[List[dict]]) -> List[dict]:
    """Flatten for GPT prompter (which writes one prompt per shot)."""
    out: List[dict] = []
    for seq in sequences:
        out.extend(seq)
    return out


def validate_rhythm(rhythm: Optional[str]) -> str:
    r = (rhythm or "normal").lower()
    return r if r in _RHYTHM_SHOT_LEN else "normal"
