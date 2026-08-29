from smart_lock.config import load_config
from smart_lock.face_id import create_face_authenticator
from smart_lock.liveness import create_liveness_checker
from smart_lock.speaker_id import create_speaker_authenticator


def main() -> None:
    config = load_config("config.yaml")
    face = create_face_authenticator(config.face)
    liveness = create_liveness_checker(config.liveness)
    speaker = create_speaker_authenticator(config.speaker)
    print(f"face_backend={type(face).__name__}")
    print(f"liveness_backend={type(liveness).__name__}")
    print(f"speaker_backend={type(speaker).__name__}")
    if hasattr(speaker, "ensure_model"):
        speaker.ensure_model()
        print("speaker_model=ready")
    print(f"face_people={face.people() if hasattr(face, 'people') else []}")
    print(f"voice_people={speaker.people() if hasattr(speaker, 'people') else []}")
    if hasattr(liveness, "close"):
        liveness.close()


if __name__ == "__main__":
    main()
