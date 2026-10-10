"""在无浏览器、无网络条件下回归会话标题与异步 UI 隔离。"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCENARIOS = (
    "refreshKeepsCurrentOlderConversation",
    "loadOlderConversations",
    "olderHistoryFailureCanRetry",
    "restoredReportsAndUnsafeLinks",
    "historyScrollControls",
    "currentHistoryRevealsFilteredVideo",
    "videoNavigationRevealsHistory",
    "staleHeaders",
    "staleDoneAndController",
    "staleFinally",
    "manualWins",
    "eachRunAndSearch",
    "doneWithoutNewline",
    "escapedTitles",
    "navigationRace",
    "inactiveRename",
    "uploadTitleNonblocking",
    "ordinaryTitleNonblocking",
    "deleteCancelled",
    "deleteCurrentConversation",
    "deleteCurrentRun",
    "deleteInactiveNavigation",
    "deleteDuringNavigation",
    "deleteRejectedActiveRun",
    "deleteNoDuplicate",
    "deletedBindingCannotRevive",
    "deletedTitlesCannotRevive",
    "deletionEscapedAndRunBinding",
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_conversation_title_frontend(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("前端行为回归需要 Node.js")
    script = Path(__file__).parent / "js" / "conversation_titles.test.cjs"
    result = subprocess.run(
        [node, str(script), scenario],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {scenario}" in result.stdout
