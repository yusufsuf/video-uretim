"""Camera / cinematography technique library for the Kling prompt composer.

UI bu listeyi Türkçe label + Türkçe açıklama ile kullanıcıya gösterir; GPT'ye
ise `en_camera` alanı (İngilizce kamera talimatı) gider. Kullanıcı tek tek shot
seçimi yaptığında bu talimat o shot'ın camera_type contract'ı olur.
"""

from __future__ import annotations

from typing import Optional

TECHNIQUES: list[dict] = [
    {
        "id": "wide_establishing",
        "tr_label": "Geniş açı (Wide)",
        "tr_desc": "Geniş plan, mankeni ortamıyla birlikte gösterir",
        "en_camera": "wide establishing shot with slow locked tracking, 24mm lens, full-body framing with environment visible",
    },
    {
        "id": "orbit",
        "tr_label": "Orbit",
        "tr_desc": "Manken olduğu yerde durur, kamera etrafında tam tur döner",
        "en_camera": "smooth 360° orbit around the stationary subject, 35mm, steady medium framing",
    },
    {
        "id": "moving_orbit",
        "tr_label": "Hareketli orbit",
        "tr_desc": "Manken yürür, kamera birlikte eşzamanlı etrafında döner",
        "en_camera": "moving orbit — camera arcs around the subject while they walk forward, 35mm, constant radius",
    },
    {
        "id": "low_angle_hero",
        "tr_label": "Alttan kahraman",
        "tr_desc": "Alttan yukarı çekim, güçlü silüet",
        "en_camera": "low-angle hero shot from below looking up, 24mm, strong heroic silhouette against the sky or ceiling",
    },
    {
        "id": "high_angle",
        "tr_label": "Tepeden bakış",
        "tr_desc": "Yukarıdan aşağı çekim, mankeni mekanda gösterir",
        "en_camera": "high-angle shot from above looking down, 35mm, subject framed within the surrounding space",
    },
    {
        "id": "dolly_in",
        "tr_label": "Dolly in",
        "tr_desc": "Kamera yavaşça mankene yaklaşır",
        "en_camera": "slow controlled dolly-in toward the subject, 50mm, medium to close-up framing",
    },
    {
        "id": "dolly_out",
        "tr_label": "Dolly out",
        "tr_desc": "Kamera yavaşça mankenden uzaklaşır, sahne genişler",
        "en_camera": "slow controlled dolly-out away from the subject, 35mm, gradually revealing more of the environment",
    },
    {
        "id": "hem_to_head_tilt_up",
        "tr_label": "Etekten başa tilt",
        "tr_desc": "Alttan yukarı dikey kayma — etek ucundan yüze",
        "en_camera": "vertical tilt-up from hem to face, 50mm, slow deliberate reveal ending at the eyes",
    },
    {
        "id": "head_to_hem_tilt_down",
        "tr_label": "Baştan eteğe tilt",
        "tr_desc": "Yukarıdan aşağı dikey iniş — yüzden etek ucuna",
        "en_camera": "vertical tilt-down from face to hem, 50mm, slow descent revealing the full silhouette",
    },
    {
        "id": "side_tracking_profile",
        "tr_label": "Yan takip (profil)",
        "tr_desc": "Yürüyen mankeni yandan profil takibi",
        "en_camera": "side tracking profile, camera dollies parallel to the walking subject, 35mm, constant distance",
    },
    {
        "id": "three_quarter_turn_orbit",
        "tr_label": "Çeyrek dönüş orbit",
        "tr_desc": "Manken 3/4 pozisyondan döner, kamera yarım orbit atar",
        "en_camera": "three-quarter turn orbit, camera arcs 90–180° while the subject rotates in place, 50mm",
    },
    {
        "id": "over_shoulder_back",
        "tr_label": "Omuz üstü arka",
        "tr_desc": "Mankenin arkasından omuz üstü plan",
        "en_camera": "over-the-shoulder from behind, following the subject's eye-line into the scene, 50mm",
    },
    {
        "id": "descending_follow",
        "tr_label": "Alçalarak takip",
        "tr_desc": "Kamera yukarıdan alçalarak mankeni takip eder",
        "en_camera": "descending follow — crane or drone lowers while tracking forward motion, 35mm",
    },
    {
        "id": "ascending_pull_back",
        "tr_label": "Yükselerek geri çekim",
        "tr_desc": "Kamera yükselerek geri çekilir, sahne açılır",
        "en_camera": "ascending pull-back, camera rises while dollying back, revealing the full scene, 24mm",
    },
    {
        "id": "close_up_fabric",
        "tr_label": "Kumaş makro",
        "tr_desc": "Kumaş dokusuna ultra yakın plan",
        "en_camera": "macro close-up on fabric texture and garment construction, 85mm, shallow depth of field",
    },
    {
        "id": "dolly_in_face",
        "tr_label": "Yüze push-in",
        "tr_desc": "Duygu odaklı yüze yavaş yaklaşma",
        "en_camera": "slow push-in toward the face for an emotional beat, 85mm, tight framing",
    },
    {
        "id": "whip_pan_reveal",
        "tr_label": "Whip pan reveal",
        "tr_desc": "Hızlı yatay kaydırma, yeni açı açılır",
        "en_camera": "whip pan reveal — fast horizontal sweep ending on the subject in a new composition, 35mm",
    },
    {
        "id": "final_back_walk",
        "tr_label": "Arkadan final",
        "tr_desc": "Manken kameradan uzaklaşarak yürür (kapanış)",
        "en_camera": "final back walk — camera holds steady as the subject walks away into the frame, 35mm, closing shot",
    },
    {
        "id": "static_hold",
        "tr_label": "Statik (sabit)",
        "tr_desc": "Kamera sabit, yalnızca manken hareket eder",
        "en_camera": "static locked camera, only the subject moves within the frame, 50mm",
    },
    {
        "id": "medium_tracking",
        "tr_label": "Orta plan takip",
        "tr_desc": "Mankeni orta planda öne doğru takip eder",
        "en_camera": "medium tracking shot following forward motion, 35mm, steady dolly",
    },
]

TECHNIQUES_BY_ID: dict[str, dict] = {t["id"]: t for t in TECHNIQUES}


def get_technique(technique_id: Optional[str]) -> Optional[dict]:
    if not technique_id:
        return None
    return TECHNIQUES_BY_ID.get(technique_id)
