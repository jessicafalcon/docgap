"""The every-column docs regenerate byte for byte from the dictionary and cover every mart column."""

from __future__ import annotations

import code_list_seeds
import pytest

from eval.column_docs import (
    CODE_LIST_SHEET,
    DICTIONARY,
    DOCS,
    VARIABLE_SHEET,
    build,
    column_docs,
    label_texts,
    variable_texts,
)


def test_the_docs_regenerate_byte_identically_from_the_dictionary() -> None:
    assert build() == DOCS.read_bytes()


def test_the_docs_read_the_dictionary_the_seeds_read() -> None:
    assert (DICTIONARY, VARIABLE_SHEET, CODE_LIST_SHEET) == (
        code_list_seeds.DICTIONARY,
        code_list_seeds.VARIABLE_SHEET,
        code_list_seeds.SHEET,
    )


def test_a_column_the_dictionary_lacks_fails() -> None:
    with pytest.raises(ValueError, match=r"\['NEW_COL'\]"):
        column_docs(["SOI_ANN", "NEW_COL"], {"SOI_ANN": "Année de soins"}, {})


def test_a_variable_text_wins_over_a_code_list_line_of_the_same_name() -> None:
    docs = column_docs(["X_LIB"], {"X_LIB": "variable"}, {"X_LIB": "list"})
    assert docs == {"X_LIB": "variable"}


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([("SOI_ANN", "Année")], "no line names its columns"),
        ([("Nom variable", "Libellé", "Commentaires"), ("SOI_ANN", " ", None)], "no label"),
        (
            [("Nom variable", "Libellé", "Commentaires"), ("A", "x", None), ("A", "y", None)],
            "A listed twice",
        ),
    ],
)
def test_a_malformed_variable_sheet_fails(rows: list[tuple[object, ...]], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        variable_texts(rows)


def test_a_variable_opening_two_code_lists_fails() -> None:
    with pytest.raises(ValueError, match="a second code list for PRS_NAT"):
        label_texts([("PRS_NAT", "Libellé"), (0, "SANS OBJET"), (None,), ("PRS_NAT", "Autre")])
