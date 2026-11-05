from datetime import datetime, timezone

from domain.model_availability import ModelAvailabilityStore


def test_model_availability_persists_until_cooldown_expires(tmp_path) -> None:
    store = ModelAvailabilityStore(str(tmp_path / "availability.db"))

    assert store.is_available("groq/test")
    unavailable_until = store.mark_unavailable("groq/test", 60)

    assert unavailable_until > datetime.now(timezone.utc)
    assert not store.is_available("groq/test")
