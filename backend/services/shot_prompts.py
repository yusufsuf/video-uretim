"""Shot ID → Kling prompt mapping for the order system.

Each entry: { "name": str, "duration": int (seconds), "prompt": str (≤480 chars) }
Used by /api/orders/{code}/studio-config to convert customer shot selections
into studio-ready Kling prompts.
"""

SHOT_PROMPTS: dict[str, dict] = {
    # ── Wide / General ──────────────────────────────────────────────────────
    "wide_full": {
        "name": "Tam Pist Genel",
        "duration": 5,
        "prompt": (
            "Wide full-length runway shot. Camera static at runway end, model walks confidently "
            "toward the lens with long deliberate strides. Entire garment visible from collar to hem. "
            "Runway lights cast clean symmetrical glow. Cinematic editorial pace, 50mm lens, "
            "shallow focus on model, background softly blurred."
        ),
    },
    "wide_ultra": {
        "name": "Ultra Geniş Mekan",
        "duration": 5,
        "prompt": (
            "Ultra-wide establishing shot inside a vast minimalist studio. Camera slowly pulls "
            "backward as model walks forward, emphasizing environmental scale against the human figure. "
            "Full body visible at all times. Cold ambient light, hard floor shadows. "
            "Cinematic motion, 24fps aesthetic."
        ),
    },
    "wide_overhead": {
        "name": "Kuş Bakışı",
        "duration": 5,
        "prompt": (
            "Overhead bird's-eye camera directly above the model. Model slowly turns in place, "
            "arms relaxed at sides, camera gently descends. Garment silhouette, patterns, and drape "
            "visible from above. Neutral light grey floor. Calm editorial mood."
        ),
    },
    "wide_tele": {
        "name": "Telefoto Sıkışık",
        "duration": 5,
        "prompt": (
            "Telephoto compressed perspective from a long distance. Model walks slowly across frame "
            "from left to right, parallel to camera axis. Background elements heavily compressed, "
            "flat layered fashion-editorial look. Soft bokeh background, crisp model in focus. "
            "Muted color palette, magazine-quality motion."
        ),
    },

    # ── Medium ──────────────────────────────────────────────────────────────
    "med_frontal": {
        "name": "Orta Boy Önden",
        "duration": 5,
        "prompt": (
            "Medium frontal shot framed from mid-thigh to just above the head. Camera locked. "
            "Model faces directly into the lens with a confident expression, makes a subtle weight "
            "shift hip to hip, garment hem sways lightly. Clean studio lighting, soft shadows. "
            "Fashion editorial pacing."
        ),
    },
    "med_low": {
        "name": "Hafif Alçak Önden",
        "duration": 5,
        "prompt": (
            "Medium shot from a slightly low camera angle, lens at hip level looking up. Model stands "
            "tall facing camera, takes one slow deliberate step forward. Low perspective emphasizes "
            "garment volume and model's stature. Dramatic side-key lighting. Controlled editorial motion."
        ),
    },
    "med_3q": {
        "name": "Üç Çeyrek Açı",
        "duration": 5,
        "prompt": (
            "Three-quarter angle medium shot, camera 45 degrees to model's left. Model begins facing "
            "away and slowly rotates toward camera, pausing at the three-quarter angle. Garment "
            "silhouette transitions from profile to three-quarter, showing volume and structure. "
            "Soft wrap lighting. Smooth deliberate turn."
        ),
    },
    "side_full": {
        "name": "Yan Tam Profil",
        "duration": 5,
        "prompt": (
            "Full-body side profile shot. Camera locked at 90 degrees to model. Model walks parallel "
            "to camera from right to left with natural runway stride, maintaining perfect posture. "
            "Garment profile — collar, chest, waist, hem — fully legible. Hard directional overhead "
            "light. Clean minimalist background."
        ),
    },
    "side_3q": {
        "name": "Yan Üç Çeyrek",
        "duration": 5,
        "prompt": (
            "Three-quarter profile shot from model's right side, mid-body framing. Camera subtly "
            "tracks as model walks forward at a diagonal angle toward the lens. Garment side panels "
            "and silhouette visible. Soft light from camera-left. Smooth dolly-forward motion, "
            "editorial fashion pacing."
        ),
    },

    # ── Close-up ────────────────────────────────────────────────────────────
    "cu_jacket": {
        "name": "Kıyafet Üst Detay",
        "duration": 4,
        "prompt": (
            "Tight close-up on the upper garment from chest to waist. Camera slowly pushes in. "
            "Model makes a subtle shoulder roll, fabric reacts naturally. Crisp detail of stitching, "
            "structure, and material. Studio light with slight specular sheen on fabric."
        ),
    },
    "cu_fabric": {
        "name": "Kumaş Doku",
        "duration": 4,
        "prompt": (
            "Extreme close-up on fabric texture — zoomed to show weave, knit, or drape detail. "
            "Camera drifts slowly across fabric surface, revealing texture and thread detail. "
            "Model's gentle breath causes fabric to shift subtly. Soft diffused lighting, "
            "no harsh reflections. Tactile sensory mood."
        ),
    },
    "cu_collar": {
        "name": "Yaka / Boyun",
        "duration": 4,
        "prompt": (
            "Close-up from collarbone to chin, focused on collar, neckline, or lapel detail. "
            "Camera slowly tilts upward from collar toward model's jaw. Model turns head slightly "
            "to the side. Fabric edge, seam, and collar texture razor-sharp. Elegant beauty-editorial "
            "lighting with subtle rim light."
        ),
    },
    "cu_hem": {
        "name": "Etek Eteği Hareketi",
        "duration": 5,
        "prompt": (
            "Low-angle close-up focused on the dress hem approximately 20cm above the floor. "
            "Camera locked low. Model takes two slow steps forward, hem sweeps and floats naturally. "
            "Fabric movement is the hero — swirl, drape, and settle. Soft backlight creates a halo "
            "on the fabric edge. No feet or shoes in frame."
        ),
    },
    "cu_belt": {
        "name": "Kemer / Bel Detayı",
        "duration": 4,
        "prompt": (
            "Close-up on waist area — belt, waistband, or cinched fabric detail. Camera locked at "
            "waist height. Model shifts weight from one hip to the other, waist detail moves and "
            "catches light differently. Hardware, stitching, and fabric tension visible in sharp "
            "detail. Side directional light emphasizes three-dimensional structure."
        ),
    },

    # ── Rear ────────────────────────────────────────────────────────────────
    "rear_full": {
        "name": "Tam Arkadan Takip",
        "duration": 5,
        "prompt": (
            "Full-body rear tracking shot. Camera follows directly behind model as she walks away. "
            "Back of garment — seams, zipper, drape, and hem movement — fully visible. Camera glides "
            "forward at model's pace. Runway setting. Soft overhead light. Clean authoritative "
            "editorial motion."
        ),
    },
    "rear_3q": {
        "name": "Üç Çeyrek Arka",
        "duration": 5,
        "prompt": (
            "Three-quarter rear angle, camera 45 degrees behind model's right shoulder. Model walks "
            "forward away from camera at a diagonal. Both back of garment and partial side silhouette "
            "visible. Camera stays locked as model moves away. Strong back-key light separates "
            "garment from background."
        ),
    },
    "rear_low": {
        "name": "Alçak Arkadan",
        "duration": 5,
        "prompt": (
            "Low-angle rear shot, camera near floor level aiming upward from behind. Model stands "
            "still then takes one slow step forward. Hem sweeps across frame, back silhouette seen "
            "from below. Upward perspective emphasizes height and garment length. Dramatic directional "
            "light from above."
        ),
    },

    # ── Low Angle ────────────────────────────────────────────────────────────
    "low_front": {
        "name": "Zemin Seviyesi Önden",
        "duration": 5,
        "prompt": (
            "Extreme low-angle front shot — camera nearly at floor level aiming upward. Model walks "
            "confidently toward camera and steps past as lens reaches ground level. Garment hem sweeps "
            "across the lower frame. Powerful empowering perspective. Hard overhead lighting."
        ),
    },
    "low_power": {
        "name": "Güçlü Alçak Açı",
        "duration": 5,
        "prompt": (
            "Strong low-angle shot, camera at knee height aiming upward. Model stands facing camera "
            "with legs slightly apart, posture tall and commanding. Slowly turns to a strong "
            "contrapposto pose. Full garment length visible from below. High-contrast studio lighting."
        ),
    },

    # ── Pivot / Turn ─────────────────────────────────────────────────────────
    "pivot_front": {
        "name": "Pivot — Önden Tutar",
        "duration": 6,
        "prompt": (
            "Camera locked in medium full-body frontal position. Model performs a full 360-degree "
            "slow pivot turn in place, beginning and ending facing camera. Garment wraps, floats, "
            "and drapes at different angles during the turn. Fabric reacts naturally — skirt flare, "
            "hem sweep, lapel catch. Soft wrap lighting. Editorial pacing."
        ),
    },
    "pivot_side": {
        "name": "Pivot — Yan Profil",
        "duration": 6,
        "prompt": (
            "Camera locked in side-profile medium full-body position. Model turns slowly from "
            "three-quarter front to three-quarter rear, pausing at pure side-profile midpoint. "
            "Camera slightly crabs to maintain profile framing. Garment silhouette changes elegantly — "
            "front panels, side seams, back structure all revealed."
        ),
    },
    "pivot_slow": {
        "name": "Ağır Çekim Pivot",
        "duration": 7,
        "prompt": (
            "Slow-motion half-turn — model rotates from direct front to direct rear in extreme slow "
            "motion. Camera locked at medium full-body distance. Every garment detail visible in "
            "decelerated movement — fabric tension, hem behavior, seam alignment. Hair moves in slow "
            "motion. Cinematic overcranked aesthetic. Even studio lighting with soft rim light."
        ),
    },

    # ── Face ─────────────────────────────────────────────────────────────────
    "face_close": {
        "name": "Yüz Yakın",
        "duration": 4,
        "prompt": (
            "Tight close-up portrait from mid-chest to top of head. Camera slowly pushes in. "
            "Model holds a composed editorial expression then makes a subtle slow head turn 15 degrees "
            "to the right. Collar and upper neckline visible at bottom of frame. Beauty light — soft "
            "diffused key with subtle rim. Sharp focus on the eyes."
        ),
    },
    "face_med": {
        "name": "Yüz + Üst Beden",
        "duration": 5,
        "prompt": (
            "Medium portrait from waist to just above head. Camera locked. Model faces front, "
            "transitions to a relaxed three-quarter look to camera-left, weight shift causes upper "
            "garment to move naturally. Collar, upper body, and facial expression all visible. "
            "Fashion portrait lighting — large soft key, gentle fill, subtle hair light. Editorial mood."
        ),
    },
}
