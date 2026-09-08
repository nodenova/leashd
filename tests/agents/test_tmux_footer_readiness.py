"""A running background shell rewrites claude's footer, and leashd has to keep
recognising the composer through it.

Every screen in this module is a real claude 2.1.263 capture, anonymised. The
footer is budgeted: a live background shell spends the budget on ``· N shell ·
… · ↓ to manage`` and the hint leashd matched on is dropped, in every
permission mode. Keying readiness on those hints made a healthy idle pane look
like an open dialog — ``await_ready`` spun for its whole 45s timeout, the
user's message was never typed in, and the turn died as "Claude's terminal
never reached the prompt". The same predicate also gates native slash commands
and the idle-completion backstop, so all three stalled together.

The mode indicator is the segment that survives every variant. What must NOT
survive is a dialog reading as a composer: prompt text typed into a selector is
answered as keystrokes.
"""

from __future__ import annotations

import pytest

from leashd.agents.runtimes.tmux_session import (
    TmuxClaudeSession,
    TmuxSessionManager,
    reset_tmux_session_manager,
)
from leashd.core.config import LeashdConfig

# claude 2.1.263, captured by cycling shift+tab with and without a live
# background shell. The right column is what leashd used to match on.
IDLE_FOOTERS = {
    "manual": "  ⏸ manual mode on · ? for shortcuts · ← for agents",
    "accept_edits": "  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents",
    "plan": "  ⏸ plan mode on (shift+tab to cycle) · ← for agents",
    "auto": "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents",
    "bypass": "  ⏵⏵ bypass permissions on",
}

BACKGROUND_SHELL_FOOTERS = {
    "manual": "  ⏸ manual mode on · 1 shell · ← for agents · ↓ to manage",
    "accept_edits": "  ⏵⏵ accept edits on · 1 shell · ← for agents · ↓ to manage",
    "plan": "  ⏸ plan mode on · 1 shell · ← for agents · ↓ to manage",
    "auto": "  ⏵⏵ auto mode on · 1 shell · ← for agents · ↓ to manage",
    "auto_with_diff_panel": (
        "  ⏵⏵ auto mode on · 1 shell · ← for agents · /diff to hide diff · ↓ to manage"
    ),
}

RULE = "─" * 150

# The pane leashd gave up on in production: idle at the composer, two shells of
# output behind it, the /diff side panel open on the right, one background
# watcher still polling. ``❯\xa0`` with the non-breaking space is claude's own
# ghost suggestion, not typed text — it is drawn on an empty composer.
PRODUCTION_NEVER_READY_SCREEN = (
    ".85, and only 16 MB RSS so it's not the culprit.        " + "─" * 60 + "\n"
    "  Let me see what is, since memory pressure could        No diff content\n"
    "  threaten the run.                                      " + "─" * 60 + "\n"
    "  Ran 1 shell command                                    pkg/worker/judge.py\n"
    "                                                         " + "─" * 60 + "\n"
    '⏺ False alarm — system-wide free is 43%, so that was        1 +"""Sampled '
    "scoring over a generated corpus.\n"
    "  a transient spike. Re-arming a lighter watcher:           2 +\n"
    "                                                            3 +The failure "
    "this exists to catch is the one\n"
    "  Ran 1 shell command                                         +no checker "
    "can. Every rule in\n"
    "                                                            4 +`pkg/worker/"
    "verify.py` can only find what\n"
    "⏺ The batch is fine and running unattended. Status:           +someone "
    "already knew to write down.\n"
    "                                                            5 +\n"
    "  - 204 rows, 88.9% pass — holding steady past n=150       6 +A generator "
    "can satisfy every rule and\n"
    "  - Watcher re-armed, polling every 2 minutes                 +still "
    "produce a corpus that reads like\n"
    "  - Expected halt at roughly 940 rows                        7 +a language "
    "model talking to itself.\n"
    "                                                            8 +\n"
    "  Nothing needs your attention until then. When it         9 +That corpus "
    "passes at 95% and teaches\n"
    "  stops I'll report the numbers.                             +the model to "
    "sound wrong.\n"
    "                                                           10 +\n"
    "✻ Churned for 50s · done 10:46 PM · 1 shell still         11 +\n"
    "  running                                                 12 +Three design "
    "choices, each because the\n"
    "                                    ✘ Auto-update failed · Run claude "
    "doctor\n" + RULE + "\n"
    "❯ retry the last step\n" + RULE + "\n"
    "  ⏵⏵ auto mode on · 1 shell · ← for agents · /diff to hide diff · ↓ to "
    "manage                    /rc"
)

# Same pane, seconds later: a native ask rule opened a permission dialog. The
# dialog REPLACES the footer with its own confirm line, which is exactly why
# the composer check must read the footer region and not the whole screen.
PERMISSION_DIALOG_SCREEN = (
    "⏺ Fetching HTTP status from example.com\n"
    "  ⎿  $ curl -s -o /dev/null -w '%{http_code}' https://example.com\n" + RULE + "\n"
    " Bash command\n"
    "   curl -s -o /dev/null -w '%{http_code}' https://example.com\n"
    "   Fetch HTTP status from example.com\n"
    " Ask rule Bash(*curl*) overrides auto mode for this command.\n"
    " /permissions to let auto mode decide\n"
    " Do you want to proceed?\n"
    " ❯ 1. Yes\n"
    "   2. No\n"
    " Esc to cancel · Tab to amend"
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_tmux_session_manager()
    yield
    reset_tmux_session_manager()


@pytest.fixture
def cfg(tmp_path):
    return LeashdConfig(
        approved_directories=[tmp_path],
        agent_runtime="tmux",
        tmux_socket_dir=tmp_path / "tmux",
        audit_log_path=tmp_path / "audit.jsonl",
    )


@pytest.fixture
def no_real_sleep(monkeypatch):
    async def _instant(_):
        return None

    import leashd.agents.runtimes.tmux_session as ts

    monkeypatch.setattr(ts.asyncio, "sleep", _instant)


class _FakePane:
    """Replays scripted screens and records keystrokes."""

    def __init__(self, screens):
        self._screens = list(screens)
        self.sent: list[tuple[str, bool]] = []

    def cmd(self, *_args):
        from types import SimpleNamespace

        screen = self._screens.pop(0) if len(self._screens) > 1 else self._screens[0]
        return SimpleNamespace(stdout=screen.split("\n"))

    def send_keys(self, keys, enter=False, literal=True):
        self.sent.append((keys, literal))


def _session(tsm, *, session_id="sess1"):
    cs = TmuxClaudeSession(
        session_id=session_id,
        chat_id="web:c1",
        user_id="u1",
        working_directory="/work",
        mode="auto",
        task_run_id=None,
        plan_origin=None,
        tmux_name=f"leashd_{session_id}",
        settings_path=tsm._socket_dir / f"{session_id}.settings.json",
    )
    tsm._sessions[session_id] = cs
    return cs


def _screen(footer: str) -> str:
    return f"⏺ done\n{RULE}\n❯ \n{RULE}\n{footer}"


@pytest.mark.parametrize("mode", sorted(BACKGROUND_SHELL_FOOTERS))
def test_background_shell_footer_is_still_a_composer(cfg, mode):
    """The regression. A live background shell costs the footer its hint in
    every permission mode; the pane is idle and takes a prompt regardless."""
    cs = _session(TmuxSessionManager(cfg))
    assert cs.composer_footer_present(_screen(BACKGROUND_SHELL_FOOTERS[mode])) is True


@pytest.mark.parametrize("mode", sorted(IDLE_FOOTERS))
def test_undegraded_footer_is_still_a_composer(cfg, mode):
    cs = _session(TmuxSessionManager(cfg))
    assert cs.composer_footer_present(_screen(IDLE_FOOTERS[mode])) is True


@pytest.mark.parametrize("mode", sorted(BACKGROUND_SHELL_FOOTERS))
def test_background_shell_footer_reads_as_idle(cfg, mode):
    """``is_idle_at_composer`` gates the turn-completion backstop and native
    slash commands; both hung on these footers."""
    cs = _session(TmuxSessionManager(cfg))
    assert cs.is_idle_at_composer(_screen(BACKGROUND_SHELL_FOOTERS[mode])) is True


@pytest.mark.parametrize("mode", sorted(BACKGROUND_SHELL_FOOTERS))
def test_background_shell_footer_mid_turn_is_not_idle(cfg, mode):
    """A working pane co-renders ``esc to interrupt`` on the same line — the
    no-false-positive guarantee the backstop depends on, which the degraded
    footer must not weaken."""
    cs = _session(TmuxSessionManager(cfg))
    screen = _screen(
        BACKGROUND_SHELL_FOOTERS[mode].replace(
            " · ← for agents", " · esc to interrupt · ← for agents"
        )
    )
    assert cs.is_idle_at_composer(screen) is False
    assert cs.composer_footer_present(screen) is True


async def test_await_ready_accepts_the_production_never_ready_screen(
    cfg, no_real_sleep
):
    """The captured pane leashd gave up on. Before the fix this returned False
    for the full 45s timeout and the user's message was dropped."""
    tsm = TmuxSessionManager(cfg)
    cs = _session(tsm)
    cs.attach(object(), _FakePane([PRODUCTION_NEVER_READY_SCREEN]))
    assert await cs.await_ready(timeout=5.0) is True


async def test_await_ready_still_times_out_on_a_permission_dialog(cfg, no_real_sleep):
    """The dialog owns the screen, so the pane is genuinely not ready — the
    timeout is the correct outcome and must survive the widened matching."""
    tsm = TmuxSessionManager(cfg)
    cs = _session(tsm)
    cs.attach(object(), _FakePane([PERMISSION_DIALOG_SCREEN]))
    assert await cs.await_ready(timeout=0.5) is False


def test_permission_dialog_is_never_a_composer(cfg):
    """Typing prompt text into a selector answers it as keystrokes. This is the
    guarantee the whole change is bounded by."""
    cs = _session(TmuxSessionManager(cfg))
    assert cs.composer_footer_present(PERMISSION_DIALOG_SCREEN) is False
    assert cs.is_idle_at_composer(PERMISSION_DIALOG_SCREEN) is False
    assert cs._composer_accepts_input(PERMISSION_DIALOG_SCREEN) is False


def test_mode_indicator_in_scrollback_does_not_fake_a_composer(cfg):
    """A footer quoted back in the transcript is content, not a live footer.
    Only the footer region counts, or a dialog under a pasted screenshot would
    read as ready."""
    cs = _session(TmuxSessionManager(cfg))
    screen = (
        "⏺ Here is what the pane looked like:\n"
        "  ⏵⏵ auto mode on · 1 shell · ← for agents · ↓ to manage\n"
        "⏺ and then the dialog opened\n" + RULE + "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. No\n"
        " Esc to cancel · Tab to amend"
    )
    assert cs.composer_footer_present(screen) is False


def test_trust_dialog_still_wins_over_the_footer(cfg):
    """The folder-trust gate must keep its dedicated drive: Enter or Escape on
    it exits claude and kills the pane."""
    cs = _session(TmuxSessionManager(cfg))
    screen = (
        " Quick safety check\n"
        " ❯ No, exit\n"
        "   Yes, I trust this folder\n"
        " Enter to confirm · Esc to cancel"
    )
    assert cs.trust_prompt_present(screen) is True
    assert cs.composer_footer_present(screen) is False


def test_unrelated_output_is_not_a_composer(cfg):
    cs = _session(TmuxSessionManager(cfg))
    assert cs.composer_footer_present("just some text") is False
    assert cs.composer_footer_present("⏺ Working… (esc to interrupt)") is False
    assert cs.composer_footer_present("") is False
