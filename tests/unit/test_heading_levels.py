"""Unit tests for heading-level inference from section numbering (PDF headings)."""

import pytest

from crosscodex_ingestion.heading_levels import infer_heading_levels


class TestDecimalNumbering:
    def test_decimal_depth_sets_the_level(self):
        titles = ["1. Introduction", "1.1. Purpose", "1.2. Organization", "2. Fundamentals", "2.1. Assumptions"]
        assert infer_heading_levels(titles) == [1, 2, 2, 1, 2]

    def test_leading_zeros_and_missing_trailing_dot_are_numbering(self):
        titles = ["3. The Security Requirements", "3.1. Access Control", "03.01.01 Account Management"]
        assert infer_heading_levels(titles) == [1, 2, 3]

    def test_a_number_under_a_new_parent_may_be_lower_than_one_seen_before(self):
        # NIST SP 800-171r3 shows 03.13.11 as an example in section 2.2, long before 3.1 and 03.01.01.
        titles = [
            "2.2. Methodology",
            "03.13.11 Cryptographic Protection",
            "3. Requirements",
            "3.1. Access Control",
            "03.01.01 Account Management",
        ]
        assert infer_heading_levels(titles) == [2, 3, 1, 2, 3]

    def test_levels_are_compressed_to_the_depths_present(self):
        assert infer_heading_levels(["1.1 Purpose", "1.2 Audience", "2.1 Requirements"]) == [1, 1, 1]
        assert infer_heading_levels(["1.1 Purpose", "1.1.1 Scope", "2.1 Requirements"]) == [1, 2, 1]


class TestUnnumberedHeadings:
    def test_unnumbered_heading_nests_under_the_last_numbered_heading(self):
        titles = [
            "3.1. Access Control",
            "03.01.01 Account Management",
            "DISCUSSION",
            "REFERENCES",
            "03.01.02 Access Enforcement",
            "DISCUSSION",
        ]
        assert infer_heading_levels(titles) == [1, 2, 3, 3, 2, 3]

    def test_unnumbered_headings_before_any_numbering_are_top_level(self):
        titles = ["Abstract", "Keywords", "1. Overview", "2. Core", "2.1. Functions"]
        assert infer_heading_levels(titles) == [1, 1, 1, 1, 2]


class TestDivisionKeywords:
    def test_chapter_with_a_word_ordinal_ranks_with_top_level_numbers(self):
        # NIST SP 800-53r5: "CHAPTER ONE", its subtitle, then 1.1, 1.2 ...
        titles = [
            "CHAPTER ONE",
            "INTRODUCTION",
            "1.1 PURPOSE AND APPLICABILITY",
            "CHAPTER TWO",
            "2.1 REQUIREMENTS AND CONTROLS",
        ]
        assert infer_heading_levels(titles) == [1, 2, 2, 1, 2]

    def test_chapters_articles_and_annexes_nest(self):
        # NIS2, Official Journal PDF
        titles = [
            "CHAPTER I",
            "GENERAL PROVISIONS",
            "Article 1",
            "Subject matter",
            "CHAPTER II",
            "Article 7",
            "ANNEX I",
            "SECTORS OF HIGH CRITICALITY",
            "ANNEX II",
        ]
        assert infer_heading_levels(titles) == [1, 2, 2, 3, 1, 2, 1, 2, 1]

    def test_section_sits_between_chapter_and_article(self):
        titles = ["CHAPTER III", "Section 1", "Article 12", "Article 13", "Section 2", "Article 14"]
        assert infer_heading_levels(titles) == [1, 2, 3, 3, 2, 3]

    def test_lettered_appendix_ranks_with_top_level_numbers(self):
        # NIST CSF 2.0
        titles = [
            "1. Overview",
            "3. Profiles and Tiers",
            "3.1. CSF Profiles",
            "Appendix A. CSF Core",
            "IDENTIFY (ID): The organization's ...",
            "Appendix B. CSF Tiers",
        ]
        assert infer_heading_levels(titles) == [1, 1, 2, 1, 2, 1]

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Chapter 1", "Chapter 2"),
            ("chapter i", "chapter ii"),
            ("CHAPTER ELEVEN", "CHAPTER TWELVE"),
            ("Article 3: Scope", "Article 4 - Definitions"),
            ("ANNEX A", "ANNEX C"),
            ("Appendix A.", "Appendix B."),
        ],
    )
    def test_keyword_ordinal_forms(self, first, second):
        assert infer_heading_levels([first, "Body", second]) == [1, 2, 1]


class TestNoiseIsNotStructure:
    def test_number_that_does_not_advance_past_its_sibling_is_not_numbering(self):
        # NIST SP 800-53r5: a list item "3. Include ..." promoted to a heading inside CHAPTER THREE.
        titles = [
            "CHAPTER THREE",
            "3.1 ACCESS CONTROL",
            "AC-8 SYSTEM USE NOTIFICATION",
            "3. Include a description of the authorized uses of the system.",
            "AC-9 PREVIOUS LOGON",
        ]
        assert infer_heading_levels(titles) == [1, 2, 3, 3, 3]

    def test_article_that_goes_backwards_is_not_numbering(self):
        titles = ["CHAPTER I", "Article 5", "Article 3", "CHAPTER II", "Article 6"]
        assert infer_heading_levels(titles) == [1, 2, 3, 1, 2]

    @pytest.mark.parametrize(
        "title",
        [
            "2024 Annual Report",
            "800-53 Overview",
            "1234. Too long",
            "Chapter",
            "Chapters 1 and 2",
            "Annexes",
            "Article MCMXCIXQ",
            "Section AB",
            "1.x Draft",
            "1.2.3.4.5.6.7 Too deep",
            "Chapter Mix",
        ],
    )
    def test_text_that_only_looks_numbered_is_not_numbering(self, title):
        assert infer_heading_levels(["1. Intro", title, "2. Next"]) == [1, 2, 1]

    def test_section_keyword_and_two_part_numbers_share_a_rank_but_not_a_sequence(self):
        # "Section 9" does not make the later "1.2" go backwards.
        titles = ["1. Scope", "Section 9", "1.2 Terms", "2. Rules"]
        assert infer_heading_levels(titles) == [1, 2, 2, 1]

    def test_annexes_are_not_sequence_checked(self):
        titles = ["CHAPTER I", "Article 1", "ANNEX C", "ANNEX A"]
        assert infer_heading_levels(titles) == [1, 2, 1, 1]

    def test_mixed_case_roman_numerals_are_numbering(self):
        assert infer_heading_levels(["Chapter Iv", "Article 1", "Chapter v"]) == [1, 2, 1]


class TestNoEvidence:
    def test_unnumbered_headings_stay_at_level_one(self):
        assert infer_heading_levels(["Abstract", "Keywords", "Audience"]) == [1, 1, 1]

    def test_a_single_numbered_heading_is_not_enough_evidence(self):
        assert infer_heading_levels(["Abstract", "1. Introduction", "Scope"]) == [1, 1, 1]

    def test_no_headings(self):
        assert infer_heading_levels([]) == []
