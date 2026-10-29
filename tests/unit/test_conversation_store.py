import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from app.persistence.sqlite_store import ConversationStore, SessionNotFoundError, StaleSummaryError


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path / "conv.sqlite3")


def test_messages_are_ordered_and_window_returns_latest(store):
    session = store.create_session("t")
    for i in range(12):
        store.append_message(session.id, "user" if i % 2 == 0 else "assistant", f"m{i}")
    assert [m.seq for m in store.list_messages(session.id)] == list(range(1, 13))
    assert [m.content for m in store.list_messages(session.id, last=3)] == ["m9", "m10", "m11"]
    assert [m.seq for m in store.list_messages(session.id, after_seq=10)] == [11, 12]
    assert store.require_session(session.id).message_count == 12


def test_concurrent_appends_never_collide(store):
    session = store.create_session()

    def writer(n):
        for i in range(20):
            store.append_message(session.id, "user", f"w{n}-{i}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seqs = [m.seq for m in store.list_messages(session.id)]
    assert seqs == list(range(1, 101))


def test_unknown_session_and_role_constraint(store):
    with pytest.raises(SessionNotFoundError):
        store.append_message("0" * 32, "user", "x")
    session = store.create_session()
    with pytest.raises(sqlite3.IntegrityError):
        store.append_message(session.id, "system", "x")  # CHECK role


def test_summary_optimistic_versioning(store):
    session = store.create_session()
    first = store.save_summary(session.id, "s1", {"entities": ["BTC"]}, covers_until_seq=4, expected_version=0)
    assert first.version == 1
    store.save_summary(session.id, "s2", {}, covers_until_seq=8, expected_version=1)
    with pytest.raises(StaleSummaryError):
        store.save_summary(session.id, "s-velho", {}, covers_until_seq=6, expected_version=1)
    assert store.get_summary(session.id).summary == "s2"


def test_analysis_lookup_is_session_scoped_and_cascade_delete(store):
    a, b = store.create_session(), store.create_session()
    analysis = store.save_analysis(a.id, 2, "asset_comparison", {"assets": ["BTC"]}, {}, datetime.now(UTC))
    assert store.get_analysis(a.id, analysis.id) is not None
    assert store.get_analysis(b.id, analysis.id) is None  # outra sessão não lê
    store.append_message(a.id, "user", "x")
    store.save_summary(a.id, "s", {}, 1, 0)
    assert store.delete_session(a.id)
    assert store.get_analysis(a.id, analysis.id) is None and store.list_messages(a.id) == []
    assert store.get_summary(a.id) is None


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "conv.sqlite3"
    first = ConversationStore(path)
    session = first.create_session()
    first.append_message(session.id, "user", "Compare BTC e ETH")
    first.set_active_context(session.id, {"assets": ["BTC", "ETH"]})
    reopened = ConversationStore(path)  # migração idempotente
    assert reopened.require_session(session.id).active_context == {"assets": ["BTC", "ETH"]}
    assert reopened.list_messages(session.id)[0].content == "Compare BTC e ETH"


def test_metadata_merge(store):
    session = store.create_session()
    message = store.append_message(session.id, "user", "x", {"a": 1})
    store.update_message_metadata(session.id, message.seq, {"b": 2})
    assert store.list_messages(session.id)[0].metadata == {"a": 1, "b": 2}
