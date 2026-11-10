
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="Content Creator Video Renderer")

API_SECRET = os.environ.get("API_SECRET", "")
MAX_CLIPS = 20
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB per clip


class RenderRequest(BaseModel):
    clips: list[HttpUrl]


@app.get("/health")
def health():
    return {"status": "ok", "service": "FFmpeg renderer"}


@app.post("/render")
def render_video(
    request: RenderRequest,
    x_api_key: str = Header(default="")
):
    if not API_SECRET or x_api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not request.clips or len(request.clips) > MAX_CLIPS:
        raise HTTPException(
            status_code=400,
            detail=f"Send between 1 and {MAX_CLIPS} clips"
        )

    workdir = Path(tempfile.mkdtemp(prefix="video-"))

    try:
        clip_paths = []

        for index, url in enumerate(request.clips):
            if url.scheme != "https":
                raise HTTPException(
                    status_code=400,
                    detail="Only HTTPS clip URLs are accepted"
                )

            clip_path = workdir / f"scene_{index:03d}.mp4"
            req = urllib.request.Request(
                str(url),
                headers={"User-Agent": "ContentCreatorRenderer/1.0"}
            )

            total = 0
            with urllib.request.urlopen(req, timeout=60) as response:
                with open(clip_path, "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_FILE_SIZE:
                            raise HTTPException(
                                status_code=413,
                                detail="A clip exceeds the 100 MB limit"
                            )
                        output.write(chunk)

            if total == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Clip {index + 1} is empty"
                )

            clip_paths.append(clip_path)

        list_file = workdir / "clips.txt"

        with open(list_file, "w", encoding="utf-8") as file:
            for path in clip_paths:
                file.write(f"file '{path.name}'\n")

        output_path = workdir / "final_video.mp4"

        command = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            str(output_path)
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail="FFmpeg failed: " + result.stderr[-2000:]
            )

        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="final_video.mp4",
            background=BackgroundTask(
                shutil.rmtree, workdir, ignore_errors=True
            )
        )

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(error))
