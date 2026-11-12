"""Face detection (YuNet) + face embeddings (SFace) from the OpenCV model zoo. CPU-only, no torch."""
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import httpx
import numpy as np

from app.config import get_settings

_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
DETECTOR_URL = f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx"
RECOGNIZER_URL = f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx"

EMBEDDING_DIM = 128
# Phone photos are huge; YuNet doesn't need more than this to find a face.
MAX_SIDE = 1280


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h in the original image
    score: float
    embedding: np.ndarray  # L2-normalized, so cosine similarity == dot product


def _download(url: str, models_dir: Path) -> Path:
    dest = models_dir / url.rsplit("/", 1)[1]
    if not dest.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        tmp.replace(dest)
    return dest


def decode_image(data: bytes) -> np.ndarray | None:
    """JPEG/PNG/... bytes -> BGR image (EXIF rotation applied), or None if it isn't an image."""
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


class FaceEngine:
    def __init__(self, models_dir: Path):
        detector = _download(DETECTOR_URL, models_dir)
        recognizer = _download(RECOGNIZER_URL, models_dir)
        self._detector = cv2.FaceDetectorYN.create(str(detector), "", (320, 320), score_threshold=0.8)
        self._recognizer = cv2.FaceRecognizerSF.create(str(recognizer), "")
        # OpenCV DNN objects aren't thread-safe and FastAPI runs sync endpoints in a thread pool.
        self._lock = threading.Lock()

    def faces(self, image: np.ndarray) -> list[Face]:
        """All faces in the image, largest first."""
        scale = min(1.0, MAX_SIDE / max(image.shape[:2]))
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = image.shape[:2]

        with self._lock:
            self._detector.setInputSize((w, h))
            _, detections = self._detector.detect(image)
            if detections is None:
                return []
            embeddings = [
                self._recognizer.feature(self._recognizer.alignCrop(image, d)).flatten()
                for d in detections
            ]

        faces = [
            Face(
                box=tuple(int(round(v / scale)) for v in d[:4]),
                score=float(d[-1]),
                embedding=e / np.linalg.norm(e),
            )
            for d, e in zip(detections, embeddings)
        ]
        return sorted(faces, key=lambda f: f.box[2] * f.box[3], reverse=True)


@lru_cache
def get_engine() -> FaceEngine:
    return FaceEngine(Path(get_settings().models_dir))
