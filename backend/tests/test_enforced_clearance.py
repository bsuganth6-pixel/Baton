"""Unit tests for clearance enforcement logic."""
from __future__ import annotations

import pytest

from app.models import Role
from app.api.sources import _enforced_clearance


class MockUser:
    def __init__(self, role: Role) -> None:
        self.role = role


def test_leaver_enforced_clearance_rises_to_2() -> None:
    """Leaver's clearance is raised to CONFIDENTIAL (2) when they try lower."""
    user = MockUser(Role.LEAVER)
    # leaver can upload up to level 2
    assert _enforced_clearance(0, user) == 2
    assert _enforced_clearance(1, user) == 2
    assert _enforced_clearance(2, user) == 2
    assert _enforced_clearance(3, user) == 3  # leaver cannot upload 3, but if they try, backend rejects with 403


def test_successor_enforced_clearance_rises_to_2() -> None:
    """Successor's clearance is raised to CONFIDENTIAL (2) when they try lower."""
    user = MockUser(Role.SUCCESSOR)
    assert _enforced_clearance(0, user) == 2
    assert _enforced_clearance(1, user) == 2
    assert _enforced_clearance(2, user) == 2
    assert _enforced_clearance(3, user) == 3


def test_teammate_enforced_clearance_rises_to_1() -> None:
    """Teammate's clearance is raised to INTERNAL (1) when they try lower."""
    user = MockUser(Role.TEAMMATE)
    assert _enforced_clearance(0, user) == 1
    assert _enforced_clearance(1, user) == 1
    assert _enforced_clearance(2, user) == 2
    assert _enforced_clearance(3, user) == 3


def test_admin_retains_explicit_clearance() -> None:
    """Admin can assign any clearance without it being raised."""
    user = MockUser(Role.ADMIN)
    assert _enforced_clearance(0, user) == 0
    assert _enforced_clearance(1, user) == 1
    assert _enforced_clearance(2, user) == 2
    assert _enforced_clearance(3, user) == 3


def test_manager_retains_explicit_clearance() -> None:
    """Manager can assign any clearance without it being raised."""
    user = MockUser(Role.MANAGER)
    assert _enforced_clearance(0, user) == 0
    assert _enforced_clearance(1, user) == 1
    assert _enforced_clearance(2, user) == 2
    assert _enforced_clearance(3, user) == 3