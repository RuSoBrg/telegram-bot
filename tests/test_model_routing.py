from routing.catalog import ModelEntry
from routing.router import ModelRouter


def test_catalog_entries_are_ordered_by_their_declared_score() -> None:
    catalog = {
        "test": [
            ModelEntry("fake", "first", "", ("tools",), 1, 100),
            ModelEntry("fake", "second", "", ("tools",), 1, 80),
        ]
    }

    assert [entry.model for entry in catalog["test"]] == ["first", "second"]
    assert ModelRouter(catalog=catalog, availability=_availability()).catalog == catalog


def _availability():
    class AlwaysAvailable:
        def is_available(self, provider, model):
            return True

        def mark_success(self, provider, model):
            pass

        def mark_unavailable(self, *args):
            pass

    return AlwaysAvailable()
