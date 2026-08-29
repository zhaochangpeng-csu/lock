from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Optional, Protocol
import zipfile

import cv2
import numpy as np

from .config import FaceConfig
from .results import AuthResult

LOGGER = logging.getLogger(__name__)


class FaceAuthenticator(Protocol):
    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        ...


class OpenCVLBPHFaceAuthenticator:
    def __init__(self, config: FaceConfig) -> None:
        self._config = config
        cascade_path = self._resolve_cascade_path()
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError(f"Failed to load Haar cascade: {cascade_path}")
        self._model = cv2.face.LBPHFaceRecognizer_create() if hasattr(cv2, "face") else None
        self._template_model_path = Path(self._config.model_path).with_suffix(".npz")
        self._templates: dict[int, np.ndarray] = {}
        self._labels: dict[int, str] = {}
        self._load_model()
        if self._model is None:
            LOGGER.warning("OpenCV face module missing; using template matcher fallback")

    @staticmethod
    def _resolve_cascade_path() -> str:
        candidates: list[Path] = []
        cv2_data = getattr(cv2, "data", None)
        if cv2_data is not None:
            candidates.append(Path(cv2_data.haarcascades) / "haarcascade_frontalface_default.xml")

        candidates.extend(
            [
                Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
                Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
                Path("/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
                Path("/usr/local/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
            ]
        )

        for path in candidates:
            if path.exists():
                return str(path)
        return str(candidates[0])

    def detect_largest_face(self, frame: np.ndarray) -> tuple[Optional[tuple[int, int, int, int]], Optional[np.ndarray]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=self._config.haar_scale_factor,
            minNeighbors=self._config.haar_min_neighbors,
            minSize=(self._config.min_face_size, self._config.min_face_size),
        )
        if len(faces) == 0:
            return None, None
        x, y, w, h = max(faces, key=lambda rect: rect[2] * rect[3])
        face = gray[y : y + h, x : x + w]
        face = cv2.resize(face, (self._config.width, self._config.height), interpolation=cv2.INTER_CUBIC)
        return (int(x), int(y), int(w), int(h)), face

    def enroll_sample(self, name: str, frame: np.ndarray) -> Path:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")
        bbox, face = self.detect_largest_face(frame)
        if bbox is None or face is None:
            raise ValueError("no face detected")

        person_dir = Path(self._config.data_dir) / clean_name
        person_dir.mkdir(parents=True, exist_ok=True)
        save_path = person_dir / f"{clean_name}_{len(list(person_dir.glob('*.png'))) + 1:03d}.png"
        cv2.imwrite(str(save_path), face)
        LOGGER.info("Saved face sample: %s", save_path)
        return save_path

    def train(self) -> int:
        data_dir = Path(self._config.data_dir)
        model_path = Path(self._config.model_path)
        labels_path = Path(self._config.labels_path)
        images: list[np.ndarray] = []
        labels: list[int] = []
        label_names: dict[int, str] = {}

        if not data_dir.exists():
            return 0

        for label, person_dir in enumerate(sorted(p for p in data_dir.iterdir() if p.is_dir())):
            label_names[label] = person_dir.name
            for image_path in sorted(person_dir.glob("*.png")) + sorted(person_dir.glob("*.jpg")):
                image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    continue
                image = cv2.resize(image, (self._config.width, self._config.height))
                images.append(image)
                labels.append(label)

        if not images:
            return 0

        model_path.parent.mkdir(parents=True, exist_ok=True)
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        if self._model is not None:
            self._model.train(images, np.asarray(labels, dtype=np.int32))
            self._model.save(str(model_path))
        else:
            templates = {}
            label_array = np.asarray(labels, dtype=np.int32)
            for label in sorted(set(labels)):
                person_images = [img for img, image_label in zip(images, label_array) if image_label == label]
                template = np.mean(np.asarray(person_images, dtype=np.float32), axis=0)
                norm = np.linalg.norm(template)
                templates[f"label_{label}"] = template / norm if norm > 0 else template
            np.savez(str(self._template_model_path), **templates)
            self._templates = {int(key.split("_")[1]): value for key, value in templates.items()}
        labels_path.write_text(json.dumps(label_names, ensure_ascii=False, indent=2), encoding="utf-8")
        self._labels = label_names
        LOGGER.info("Trained LBPH face model: samples=%s people=%s", len(images), len(label_names))
        return len(images)

    def people(self) -> list[str]:
        data_dir = Path(self._config.data_dir)
        if not data_dir.exists():
            return []
        return sorted(p.name for p in data_dir.iterdir() if p.is_dir())

    def sample_count(self, name: str) -> int:
        person_dir = Path(self._config.data_dir) / name.strip()
        if not person_dir.exists():
            return 0
        return len(list(person_dir.glob("*.png"))) + len(list(person_dir.glob("*.jpg")))

    def _load_model(self) -> None:
        model_path = Path(self._config.model_path)
        labels_path = Path(self._config.labels_path)
        if not labels_path.exists():
            return
        if self._model is not None and model_path.exists():
            self._model.read(str(model_path))
        elif self._model is None and self._template_model_path.exists():
            raw_templates = np.load(str(self._template_model_path))
            self._templates = {
                int(key.split("_")[1]): np.asarray(raw_templates[key], dtype=np.float32)
                for key in raw_templates.files
            }
        else:
            return
        raw = json.loads(labels_path.read_text(encoding="utf-8"))
        self._labels = {int(k): str(v) for k, v in raw.items()}

    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        bbox, face = self.detect_largest_face(frame)
        if bbox is None or face is None:
            if dry_run and self._config.accept_mock_frame_in_dry_run:
                return AuthResult("face", True, 1.0, "dry-run mock face accepted")
            return AuthResult("face", False, 0.0, "no face detected")

        area_ratio = (bbox[2] * bbox[3]) / float(frame.shape[0] * frame.shape[1])

        if not self._labels:
            score = min(1.0, max(0.0, area_ratio / 0.12))
            return AuthResult(
                "face",
                dry_run and self._config.accept_any_detected_face_in_dry_run,
                score,
                "face detected but model not trained",
                {"bbox": bbox, "area_ratio": area_ratio},
            )

        if self._model is not None:
            label, distance = self._model.predict(face)
            identity = self._labels.get(int(label), "unknown")
            score = max(0.0, min(1.0, 1.0 - float(distance) / self._config.lbph_confidence_max))
        else:
            label, score = self._match_template(face)
            distance = 1.0 - score
            identity = self._labels.get(int(label), "unknown")

        if dry_run and self._config.accept_any_detected_face_in_dry_run:
            return AuthResult("face", True, max(score, self._config.min_score), "dry-run face accepted")

        passed = score >= self._config.min_score
        reason = f"recognized {identity}" if passed else f"unknown or low confidence ({identity})"
        return AuthResult(
            "face",
            passed,
            score,
            reason,
            {"bbox": bbox, "area_ratio": area_ratio, "identity": identity, "distance": float(distance)},
        )

    def _match_template(self, face: np.ndarray) -> tuple[int, float]:
        vector = face.astype(np.float32)
        norm = np.linalg.norm(vector)
        vector = vector / norm if norm > 0 else vector
        best_label = -1
        best_score = 0.0
        for label, template in self._templates.items():
            score = float(np.dot(vector.reshape(-1), template.reshape(-1)))
            if score > best_score:
                best_label = label
                best_score = score
        return best_label, max(0.0, min(1.0, best_score))


def create_face_authenticator(config: FaceConfig) -> FaceAuthenticator:
    if config.backend == "insightface":
        return InsightFaceAuthenticator(config)
    if config.backend in {"opencv_mvp", "opencv_lbph"}:
        return OpenCVLBPHFaceAuthenticator(config)
    raise ValueError(f"Unsupported face backend: {config.backend}")


class InsightFaceAuthenticator:
    def __init__(self, config: FaceConfig) -> None:
        self._config = config
        self._app = self._create_app()
        Path(config.embedding_dir).mkdir(parents=True, exist_ok=True)
        Path(config.data_dir).mkdir(parents=True, exist_ok=True)

    def _create_app(self):
        from insightface.app import FaceAnalysis

        self._ensure_insightface_model_present()
        app = FaceAnalysis(
            name=self._config.insightface_model,
            root=self._config.insightface_root,
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        app.prepare(
            ctx_id=self._config.insightface_ctx_id,
            det_size=tuple(self._config.insightface_det_size),
        )
        return app

    def _ensure_insightface_model_present(self) -> None:
        model_dir = Path(self._config.insightface_root).expanduser() / "models" / self._config.insightface_model
        if model_dir.exists() and list(model_dir.glob("*.onnx")):
            return
        zip_path = model_dir.with_suffix(".zip")
        if zip_path.exists() and zip_path.stat().st_size > 0:
            self._extract_insightface_zip(zip_path, model_dir)
        flat_models = list(model_dir.parent.glob("*.onnx"))
        if flat_models:
            model_dir.mkdir(parents=True, exist_ok=True)
            for source in flat_models:
                target = model_dir / source.name
                if not target.exists():
                    shutil.copy2(source, target)
        if not model_dir.exists() or not list(model_dir.glob("*.onnx")):
            raise FileNotFoundError(
                f"InsightFace model files are missing: {model_dir}. "
                f"Run python3 download_models.py first, or copy {self._config.insightface_model}.zip there."
            )

    @staticmethod
    def _extract_insightface_zip(zip_path: Path, model_dir: Path) -> None:
        model_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for member in archive.namelist():
                    if not member.endswith(".onnx"):
                        continue
                    target = model_dir / Path(member).name
                    with archive.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile:
            raise RuntimeError(f"InsightFace model zip is broken: {zip_path}")

    def detect_largest_face(
        self, frame: np.ndarray
    ) -> tuple[Optional[tuple[int, int, int, int]], Optional[np.ndarray]]:
        faces = self._app.get(frame)
        if not faces:
            return None, None
        face = max(faces, key=lambda item: self._bbox_area(item.bbox))
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame.shape[1], x2)
        y2 = min(frame.shape[0], y2)
        return (x1, y1, max(0, x2 - x1), max(0, y2 - y1)), self._embedding(face)

    def enroll_sample(self, name: str, frame: np.ndarray) -> Path:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")

        faces = self._app.get(frame)
        if not faces:
            raise ValueError("no face detected")
        face = max(faces, key=lambda item: self._bbox_area(item.bbox))
        embedding = self._embedding(face)

        embedding_dir = Path(self._config.embedding_dir) / clean_name
        image_dir = Path(self._config.data_dir) / clean_name
        embedding_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(embedding_dir.glob("*.npy"))) + 1
        embedding_path = embedding_dir / f"{clean_name}_{index:03d}.npy"
        image_path = image_dir / f"{clean_name}_{index:03d}.jpg"
        np.save(str(embedding_path), embedding)

        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(frame.shape[1], x2)
        y2 = min(frame.shape[0], y2)
        if x2 > x1 and y2 > y1:
            cv2.imwrite(str(image_path), frame[y1:y2, x1:x2])
        LOGGER.info("Saved InsightFace sample: %s", embedding_path)
        return embedding_path

    def train(self) -> int:
        return sum(self.sample_count(name) for name in self.people())

    def people(self) -> list[str]:
        root = Path(self._config.embedding_dir)
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def sample_count(self, name: str) -> int:
        person_dir = Path(self._config.embedding_dir) / name.strip()
        if not person_dir.exists():
            return 0
        return len(list(person_dir.glob("*.npy")))

    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        faces = self._app.get(frame)
        if not faces:
            if dry_run and self._config.accept_mock_frame_in_dry_run:
                return AuthResult("face", True, 1.0, "dry-run mock face accepted")
            return AuthResult("face", False, 0.0, "no face detected")

        face = max(faces, key=lambda item: self._bbox_area(item.bbox))
        embedding = self._embedding(face)
        profiles = self._load_profiles()
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        bbox = (x1, y1, max(0, x2 - x1), max(0, y2 - y1))
        area_ratio = (bbox[2] * bbox[3]) / float(frame.shape[0] * frame.shape[1])

        if not profiles:
            return AuthResult(
                "face",
                dry_run and self._config.accept_any_detected_face_in_dry_run,
                0.0,
                "InsightFace model ready but face library is empty",
                {"bbox": bbox, "area_ratio": area_ratio},
            )

        best_name = "unknown"
        best_score = 0.0
        for name, samples in profiles.items():
            centroid = np.mean(np.asarray(samples, dtype=np.float32), axis=0)
            centroid = self._normalize(centroid)
            score = self._cosine_score(embedding, centroid)
            if score > best_score:
                best_name = name
                best_score = score

        passed = best_score >= self._config.min_score
        reason = f"recognized {best_name}" if passed else f"unknown or low confidence ({best_name})"
        return AuthResult(
            "face",
            passed,
            float(best_score),
            reason,
            {"bbox": bbox, "area_ratio": area_ratio, "identity": best_name},
        )

    def _load_profiles(self) -> dict[str, list[np.ndarray]]:
        root = Path(self._config.embedding_dir)
        profiles: dict[str, list[np.ndarray]] = {}
        if not root.exists():
            return profiles
        for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            samples = [self._normalize(np.load(str(path))) for path in sorted(person_dir.glob("*.npy"))]
            if samples:
                profiles[person_dir.name] = samples
        return profiles

    @staticmethod
    def _embedding(face) -> np.ndarray:
        embedding = getattr(face, "normed_embedding", None)
        if embedding is None:
            embedding = getattr(face, "embedding")
        return InsightFaceAuthenticator._normalize(np.asarray(embedding, dtype=np.float32))

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector

    @staticmethod
    def _bbox_area(bbox) -> float:
        return float(max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]))

    @staticmethod
    def _cosine_score(a: np.ndarray, b: np.ndarray) -> float:
        cosine = float(np.dot(a, b))
        return max(0.0, min(1.0, (cosine + 1.0) / 2.0))
