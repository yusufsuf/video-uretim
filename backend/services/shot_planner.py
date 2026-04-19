"""Shot planner — total duration + rhythm → sequence/shot distribution.

Used by workflow mode to turn a single "I want a 30s fast video" intent into:
  - N sequences (each ≤ 15s, a single Kling multishot call)
  - M shots per sequence (each ≥ 3s to satisfy Kling Omni minimum)
  - Arc beat role per shot (so GPT can write role-aware prompts)

Why sequences: Kling Omni caps each multishot call at 15s total. For longer
videos we chain multiple Kling calls (last frame → next first frame) and concat
the clips locally with ffmpeg — same pattern as multi-outfit workflow today.
"""

from __future__ import annotations

import math
from typing import List, Optional

# Kling Omni hard limits
KLING_MAX_SEQUENCE_DURATION = 15
KLING_MIN_SHOT_DURATION = 3
KLING_MAX_SHOT_DURATION = 10

# Workflow-level caps
MAX_TOTAL_DURATION = 60
MIN_TOTAL_DURATION = 6

# rhythm → preferred shot length (seconds)
_RHYTHM_SHOT_LEN = {
    "slow":   6,   # 2-3 shots per 15s sequence
    "normal": 4,   # 3-5 shots per 15s sequence
    "fast":   3,   # 5 shots per 15s sequence (Kling minimum)
}


def _rhythm_shot_len(rhythm: str) -> int:
    return _RHYTHM_SHOT_LEN.get(rhythm, _RHYTHM_SHOT_LEN["normal"])


def clamp_total_duration(total: int) -> int:
    return max(MIN_TOTAL_DURATION, min(MAX_TOTAL_DURATION, int(total)))


def plan_sequences(total_duration: int, rhythm: str = "normal") -> List[List[dict]]:
    """Split total_duration into N sequences (≤15s each) and M shots per sequence.

    Returns a list of sequences. Each sequence is a list of shot dicts:
        {"duration": int, "seq_index": int, "shot_index": int, "global_index": int}

    Example — 30s normal rhythm:
        [
          [{duration: 5, ...}, {duration: 5, ...}, {duration: 5, ...}],  # seq 0 (15s)
          [{duration: 5, ...}, {duration: 5, ...}, {duration: 5, ...}],  # seq 1 (15s)
        ]
    """
    total_duration = clamp_total_duration(total_duration)
    target_shot = _rhythm_shot_len(rhythm)

    # Split into sequences of at most 15s each, distributed as evenly as possible
    n_sequences = max(1, math.ceil(total_duration / KLING_MAX_SEQUENCE_DURATION))
    base_seq_dur = total_duration // n_sequences
    extra = total_duration - (base_seq_dur * n_sequences)
    seq_durations = [base_seq_dur + (1 if i < extra else 0) for i in range(n_sequences)]

    sequences: List[List[dict]] = []
    global_idx = 0

    for seq_i, seq_dur in enumerate(seq_durations):
        # Fit as many target-length shots as possible, respecting Kling limits
        n_shots = max(1, round(seq_dur / target_shot))
        # Ensure each shot is at least KLING_MIN_SHOT_DURATION
        max_shots_allowed = seq_dur // KLING_MIN_SHOT_DURATION
        n_shots = max(1, min(n_shots, max_shots_allowed))

        base_shot = seq_dur // n_shots
        shot_extra = seq_dur - (base_shot * n_shots)

        # Clamp each shot to [MIN, MAX]; redistribute any surplus toward middle shots
        shot_durations = []
        for si in range(n_shots):
            d = base_shot + (1 if si < shot_extra else 0)
            d = max(KLING_MIN_SHOT_DURATION, min(KLING_MAX_SHOT_DURATION, d))
            shot_durations.append(d)

        # If rounding pushed us over KLING_MAX_SEQUENCE_DURATION, trim the last shot
        while sum(shot_durations) > KLING_MAX_SEQUENCE_DURATION:
            shot_durations[-1] -= 1
            if shot_durations[-1] < KLING_MIN_SHOT_DURATION:
                shot_durations.pop()

        shots = []
        for si, d in enumerate(shot_durations):
            shots.append({
                "duration": int(d),
                "seq_index": seq_i,
                "shot_index": si,
                "global_index": global_idx,
            })
            global_idx += 1

        sequences.append(shots)

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
