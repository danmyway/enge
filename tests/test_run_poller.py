"""`enge.run.poller`: the loop's own wait for a generation (R7-1, Q-R6-E',
Q-R6-I). A fake clock and a scripted state fetch stand in for time and
Testing Farm, so nothing sleeps or touches the network.
"""

import unittest


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def _script(states):
    """fetch_state from {task: [state per poll]}; the last entry repeats.
    A None entry is a failed poll."""
    polls = {task: 0 for task in states}

    def fetch(task_id):
        seq = states[task_id]
        state = seq[min(polls[task_id], len(seq) - 1)]
        polls[task_id] += 1
        return state

    return fetch, polls


def _wait(states, timeout=24 * 3600.0):
    from enge.run.poller import wait_for_tasks

    clock = FakeClock()
    fetch, polls = _script(states)
    progress = []
    outcome = wait_for_tasks(
        list(states),
        fetch,
        clock=clock,
        timeout=timeout,
        on_progress=lambda *args: progress.append(args),
    )
    return outcome, clock, polls, progress


class TestWaitForTasks(unittest.TestCase):
    def test_waits_until_every_task_is_terminal(self):
        outcome, clock, polls, _ = _wait(
            {"a": ["queued", "running", "complete"], "b": ["error"], "c": ["canceled"]}
        )

        self.assertEqual(sorted(outcome.terminal), ["a", "b", "c"])
        self.assertEqual(outcome.unharvested, ())
        self.assertEqual(polls, {"a": 3, "b": 1, "c": 1})
        self.assertEqual(clock.sleeps, [30.0, 30.0])

    def test_three_failed_polls_in_a_row_make_a_task_unreachable(self):
        outcome, clock, polls, _ = _wait(
            {"a": [None, None, None, "complete"], "b": ["complete"]}
        )

        self.assertEqual(outcome.unreachable, ("a",))
        self.assertEqual(outcome.unharvested, ("a",))
        self.assertEqual(polls["a"], 3)
        self.assertEqual(clock.sleeps, [60.0, 60.0])

    def test_a_successful_poll_resets_the_failure_count(self):
        outcome, _, polls, _ = _wait(
            {"a": [None, None, "running", None, None, "complete"]}
        )

        self.assertEqual(outcome.terminal, ("a",))
        self.assertEqual(polls["a"], 6)

    def test_tasks_still_running_at_the_timeout_are_timed_out(self):
        outcome, clock, _, _ = _wait(
            {"a": ["running"], "b": ["complete"]}, timeout=100.0
        )

        self.assertEqual(outcome.timed_out, ("a",))
        self.assertEqual(outcome.terminal, ("b",))
        self.assertEqual(outcome.unharvested, ("a",))
        self.assertEqual(clock.t, 100.0)

    def test_progress_reports_each_change_and_a_heartbeat(self):
        running = ["running"] * 40 + ["complete"]
        _, _, _, progress = _wait({"a": ["complete"], "b": running})

        # (finished, total, running, elapsed): the first change at once, a
        # heartbeat 15 minutes later, the last change when b finishes.
        self.assertEqual(
            progress, [(1, 2, 1, 0.0), (1, 2, 1, 900.0), (2, 2, 0, 1200.0)]
        )
