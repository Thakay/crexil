"""Harness proof: pytest resolves the app package from the project
root. Pins only the region policy's id and precedence: a green run means
wiring, not behaviour. The real characterization is test_regions.py.
"""

from app import regions


def test_policy_id_is_nd3_v1():
    assert regions.POLICY_ID == "nd3-v1"


def test_precedence_matches_nd3_v1():
    # KEEP > DELETE > REPLACE > MODIFY, strongest first
    assert regions.PRECEDENCE == ("keep", "delete", "replace", "modify")
