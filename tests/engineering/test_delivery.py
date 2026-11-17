"""Automatic dev delivery must use the exact fully green, current candidate."""
import copy
import unittest

from test_engineering import ci_gate


class DevDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.head, self.base, self.merge = "a" * 40, "b" * 40, "c" * 40
        self.repo = "OpenTenon/tenon"
        self.pull = {"number": 7, "state": "open", "draft": False, "mergeable": True, "mergeable_state": "clean",
                     "head": {"ref": "dev-123-tooling", "sha": self.head, "repo": {"full_name": self.repo}},
                     "base": {"ref": "dev", "sha": self.base, "repo": {"full_name": self.repo}},
                     "merge_commit_sha": self.merge}
        self.run = {"pull_requests": [{"number":7}], "id": 42, "run_attempt": 1, "head_sha": self.head, "event": "pull_request",
                    "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success"}
        self.jobs = [{"name": name, "run_id": 42, "run_attempt": 1,
                      "status": "completed", "conclusion": "success"}
                     for name in sorted(ci_gate.REQUIRED_JOBS | {"ci-required"})]

    def assess(self, contains=True):
        return ci_gate.check_dev_delivery(self.pull, self.run, self.jobs, self.head,
                                          self.base, self.repo, contains)

    def test_exact_green_candidate_can_integrate_into_dev(self):
        self.assertFalse(self.assess())
        self.run["head_sha"] = self.merge
        self.assertFalse(self.assess())

    def test_pending_failed_cancelled_skipped_and_neutral_jobs_block(self):
        for outcome in (None, "failure", "cancelled", "skipped", "neutral", "timed_out"):
            with self.subTest(outcome=outcome):
                self.jobs[0]["conclusion"] = outcome
                self.assertTrue(self.assess())

    def test_missing_or_duplicated_jobs_block(self):
        original = copy.deepcopy(self.jobs)
        self.jobs.pop()
        self.assertTrue(self.assess())
        self.jobs = original + [copy.deepcopy(original[0])]
        self.assertTrue(self.assess())

    def test_changed_head_or_stale_ci_blocks(self):
        self.pull["head"]["sha"] = "d" * 40
        self.assertTrue(self.assess())
        self.pull["head"]["sha"] = self.head
        self.run["head_sha"] = "d" * 40
        self.assertTrue(self.assess())

    def test_main_and_foreign_repository_are_never_automatic_targets(self):
        self.pull["base"]["ref"] = "main"
        self.assertTrue(self.assess())
        self.pull["base"]["ref"] = "dev"
        self.pull["head"]["repo"]["full_name"] = "another/repo"
        self.assertTrue(self.assess())

    def test_advanced_dev_requires_fresh_integration_and_ci(self):
        self.assertTrue(self.assess(contains=False))
        self.pull["base"]["sha"] = "e" * 40
        self.assertTrue(self.assess())

    def test_draft_conflict_review_block_and_old_attempt_block(self):
        self.pull["draft"] = True
        self.assertTrue(self.assess())
        self.pull["draft"] = False
        self.pull["mergeable_state"] = "blocked"
        self.assertTrue(self.assess())
        self.pull["mergeable_state"] = "clean"
        self.jobs[0]["run_attempt"] = 0
        self.assertTrue(self.assess())

    def test_ci_from_another_pr_cannot_authorize_delivery(self):
        self.run["pull_requests"] = [{"number": 8}]
        self.assertTrue(self.assess())

    def test_workflow_must_be_the_completed_pr_ci_run(self):
        for key, value in (("event", "push"), ("path", ".github/workflows/another.yml"),
                           ("status", "in_progress"), ("conclusion", "failure")):
            original = self.run[key]
            self.run[key] = value
            self.assertTrue(self.assess())
            self.run[key] = original


if __name__ == "__main__":
    unittest.main()
