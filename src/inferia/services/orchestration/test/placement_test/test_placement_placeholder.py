"""Regression test: placement candidates must exclude placeholder rows.

Adding a Nosana/Akash pool inserts a marker row in ``compute_inventory``
so the new compute pool shows up in the UI. Those placeholder rows are
intentionally not real nodes — the actual provisioning happens lazily
at deploy time via the provider adapter. If the dispatcher accidentally
picks a placeholder as ``best_node`` it tries to run the worker strategy
against a non-existent container, which manifests as a hung deployment.

Rather than spin up Postgres, we verify the SQL the repository builds
contains the placeholder-exclusion clause. This catches accidental
clause deletion / refactor in a single fast unit test.
"""

from __future__ import annotations

import inspect

from inferia.services.orchestration.repositories import placement_repo


def _source() -> str:
    return inspect.getsource(placement_repo.PlacementRepository.fetch_candidate_nodes)


def test_excludes_placeholder_provider_instance_id():
    src = _source()
    # Placeholder rows carry ``provider_instance_id`` values starting with
    # ``placeholder:<pool_id>`` — see deployment_server.create_pool. The
    # filter must be present so they never land in candidates.
    assert "placeholder:" in src, \
        "fetch_candidate_nodes lost the placeholder exclusion clause"
    assert "NOT LIKE" in src.upper(), \
        "placeholder clause must be a NOT LIKE filter"
    # The SQL also gates on state='ready' — checking it here keeps that
    # contract pinned alongside the placeholder clause they coexist with.
    assert "ci.state = 'ready'" in src, \
        "candidate selection lost the state=ready filter"


def test_select_returns_agent_kind_column():
    """The dispatcher reads ``best_node['agent_kind']`` to route worker
    deployments to the worker strategy. The SELECT must surface it."""
    src = _source()
    assert "ci.agent_kind" in src, \
        "fetch_candidate_nodes must SELECT agent_kind for strategy routing"
