"""360° video'dan element için frame çıkartıcı.

Kullanıcı WhatsApp'tan 360° dönüş videosu gönderdiğinde, videodan eşit aralıklı
frame'ler çıkarıp Kling Omni element creation'a bu frame'leri besliyoruz.

Avantaj: aynı ışık/kamera/model → kimlik tutarlılığı foto setinden çok daha iyi.

Kullanım:
    paths = await extract_rotation_frames(video_url, output_dir, count=4)
    # paths = ["/uploads/xxx_0.jpg", "/uploads/xxx_1.jpg", ...]
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB — WA videoları ~30MB civarı
DOWNLOAD_TIMEOUT = 60


async def _download_video(url: str, dest_path: str) -> int:
    """Video'yu stream ederek disk'e indir. Byte sayısını döner."""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise ValueError(f"Video dosyası çok büyük (>{MAX_VIDEO_BYTES // 1024 // 1024}MB)")
                    f.write(chunk)
    return total


async def _probe_duration(video_path: str) -> float:
    """ffprobe ile video süresini saniye olarak döner."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()[:200]}")
    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise RuntimeError(f"ffprobe returned invalid duration: {stdout.decode()[:100]}")


async def _extract_frame_at(video_path: str, timestamp_s: float, out_path: str) -> None:
    """ffmpeg ile verilen saniyede tek frame çıkar. Yüksek kaliteli jpeg."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-ss", f"{timestamp_s:.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",          # yüksek kalite jpeg (1=best, 31=worst)
        "-vf", "scale='if(lt(iw,ih),1280,-2)':'if(lt(iw,ih),-2,1280)'",  # kısa kenar 1280
        out_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extract failed @ {timestamp_s:.1f}s: {stderr.decode()[:200]}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"Extracted frame too small/missing: {out_path}")


async def extract_rotation_frames(
    video_url: str,
    output_dir: str,
    count: int = 4,
    cleanup_video: bool = True,
) -> list[str]:
    """Video URL'inden N eşit aralıklı frame çıkar → output_dir'e kaydet.

    Returns: list of local file paths (count tane)

    360° dönüş videolarında 0%/25%/50%/75% noktaları sırasıyla
    ön / 3/4 / arka / diğer 3/4 açısına denk gelir (varsayım).
    """
    if count < 2 or count > 6:
        raise ValueError("count must be between 2 and 6")

    os.makedirs(output_dir, exist_ok=True)
    tmp_video = os.path.join(output_dir, f"_tmp_vid_{uuid.uuid4().hex}.mp4")

    try:
        bytes_read = await _download_video(video_url, tmp_video)
        logger.info("Video downloaded: %d bytes → %s", bytes_read, tmp_video)

        duration = await _probe_duration(tmp_video)
        if duration < 1.0:
            raise ValueError(f"Video çok kısa ({duration:.1f}s) — 360° için en az 2-3s gerekli")

        # Eşit aralıklı timestamp'ler. Son frame'i duration'a DEĞİL, son %95'e
        # koyarız (loop sonunda genelde ilk frame'e geri dönülür, onu almamak için).
        timestamps = [duration * (i / count) for i in range(count)]
        # İlk frame'i tam 0 yerine 0.1s'e çek (bazı codec'lerde frame 0 siyah gelir)
        timestamps[0] = min(0.1, duration * 0.02)

        frame_paths: list[str] = []
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(output_dir, f"{uuid.uuid4().hex}_{i}.jpg")
            await _extract_frame_at(tmp_video, ts, frame_path)
            frame_paths.append(frame_path)
            logger.info("Frame %d/%d extracted @ %.2fs → %s", i + 1, count, ts, frame_path)

        return frame_paths

    finally:
        if cleanup_video and os.path.isfile(tmp_video):
            try:
                os.unlink(tmp_video)
            except Exception as e:
                logger.debug("Failed to cleanup temp video %s: %s", tmp_video, e)
