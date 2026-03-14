from app.services.issue_suggestions import suggest_likely_issues


class TestSuggestLikelyIssues:
    def test_low_faithfulness_suggests_meaning_issues(self):
        suggestions = suggest_likely_issues(
            scores={
                "faithfulness_score": 4.0,
                "clarity_score": 8.0,
                "specificity_score": 7.0,
                "structure_score": 7.0,
                "conciseness_score": 7.0,
            },
            framework="chain-of-thought",
            framework_issue_freq=None,
            user_issue_freq=None,
        )
        issue_ids = [s.issue_id for s in suggestions]
        assert "changed_meaning" in issue_ids or "hallucinated_content" in issue_ids

    def test_framework_history_suggests_recurring_issues(self):
        suggestions = suggest_likely_issues(
            scores={
                d: 8.0
                for d in [
                    "faithfulness_score",
                    "clarity_score",
                    "specificity_score",
                    "structure_score",
                    "conciseness_score",
                ]
            },
            framework="chain-of-thought",
            framework_issue_freq={"lost_key_terms": 3},
            user_issue_freq=None,
        )
        assert any(s.issue_id == "lost_key_terms" for s in suggestions)

    def test_max_three_suggestions(self):
        suggestions = suggest_likely_issues(
            scores={
                d: 3.0
                for d in [
                    "faithfulness_score",
                    "clarity_score",
                    "specificity_score",
                    "structure_score",
                    "conciseness_score",
                ]
            },
            framework="chain-of-thought",
            framework_issue_freq={"lost_key_terms": 5, "too_verbose": 4},
            user_issue_freq={"changed_meaning": 6},
        )
        assert len(suggestions) <= 3

    def test_no_suggestions_when_all_scores_high(self):
        suggestions = suggest_likely_issues(
            scores={
                d: 9.0
                for d in [
                    "faithfulness_score",
                    "clarity_score",
                    "specificity_score",
                    "structure_score",
                    "conciseness_score",
                ]
            },
            framework="chain-of-thought",
            framework_issue_freq=None,
            user_issue_freq=None,
        )
        assert len(suggestions) == 0

    def test_deduplicates_by_highest_confidence(self):
        suggestions = suggest_likely_issues(
            scores={
                "faithfulness_score": 4.0,
                "clarity_score": 8.0,
                "specificity_score": 8.0,
                "structure_score": 8.0,
                "conciseness_score": 8.0,
            },
            framework="chain-of-thought",
            framework_issue_freq={"changed_meaning": 5},
            user_issue_freq=None,
        )
        ids = [s.issue_id for s in suggestions]
        assert ids.count("changed_meaning") <= 1
