"""A take's grid label: the run's index, the cell's letters from A, and `.n` for a
later output of the same cell. Cells past Z continue as spreadsheet columns do,
so every position of a run keeps a name of its own, and the run view and the
delivered package names read this one function."""

import re

from app import agent_door, crexil_delivery
from app.agent_door import label


def test_cells_are_lettered_from_a_and_runs_numbered_from_zero():
    assert label(0, 0) == '0A'
    assert label(0, 1) == '0B'
    assert label(12, 3) == '12D'


def test_a_later_output_of_a_cell_adds_its_number_from_two():
    assert label(3, 1, 0) == '3B'
    assert label(3, 1, 1) == '3B.2'
    assert label(3, 1, 2) == '3B.3'


def test_cells_past_z_continue_as_aa_ab_and_on():
    assert label(0, 25) == '0Z'
    assert label(0, 26) == '0AA'
    assert label(0, 27) == '0AB'
    assert label(0, 51) == '0AZ'
    assert label(0, 52) == '0BA'
    assert label(0, 701) == '0ZZ'
    assert label(0, 702) == '0AAA'
    assert label(7, 26, 2) == '7AA.3'


def test_every_position_has_its_own_name_of_capital_letters():
    names = [label(4, pos) for pos in range(2000)]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r'4[A-Z]+', name) for name in names)


def test_the_run_view_and_the_package_names_read_this_label():
    assert crexil_delivery.grid_label is agent_door.label
