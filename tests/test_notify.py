"""Updates delivered as GitHub issues — no mail account or password."""

from datetime import date

import pytest

from vacation_finder import notify


class FakeResponse:
    def __init__(self, status=201, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        if method == "GET":
            return FakeResponse(200, [])
        return FakeResponse(201, {"number": 7})


def notifier(session):
    return notify.GitHubNotifier(
        repo="owner/repo", token="ghs_runtoken", notify_user="owner", session=session
    )


def test_update_is_an_issue_assigned_to_you_with_a_mention():
    session = FakeSession()
    assert notifier(session).post("Title", "Body", notify.DIGEST_LABEL) == 7
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://api.github.com/repos/owner/repo/issues")
    assert kwargs["json"]["assignees"] == ["owner"]
    assert kwargs["json"]["labels"] == ["vacation-digest"]
    assert kwargs["json"]["body"].startswith("@owner")
    assert kwargs["headers"]["Authorization"] == "Bearer ghs_runtoken"


def test_previous_digests_are_closed_first():
    session = FakeSession([
        FakeResponse(200, [{"number": 3}, {"number": 4, "pull_request": {}}]),
        FakeResponse(200, {}),                       # PATCH #3
        FakeResponse(201, {"number": 5}),            # POST the new one
    ])
    notifier(session).post("T", "B", notify.DIGEST_LABEL, close_previous=True)
    methods = [(m, u.rsplit("/", 2)[-2:]) for m, u, _ in session.calls]
    assert methods[1] == ("PATCH", ["issues", "3"])     # the PR (#4) is left alone
    assert session.calls[1][2]["json"]["state"] == "closed"
    assert session.calls[2][0] == "POST"


def test_unassignable_user_still_gets_the_update():
    session = FakeSession([FakeResponse(422, {"message": "Validation Failed"}),
                           FakeResponse(201, {"number": 9})])
    assert notifier(session).post("T", "B", notify.ALERT_LABEL) == 9
    assert "assignees" not in session.calls[1][2]["json"]


def test_outside_actions_it_is_not_configured(monkeypatch):
    for var in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GITHUB_REPOSITORY_OWNER", "NOTIFY_USER"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(notify.NotConfigured):
        notify.GitHubNotifier(session=FakeSession()).post("T", "B", notify.DIGEST_LABEL)


def test_owner_is_notified_by_default(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/Vacation-finder")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("NOTIFY_USER", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY_OWNER", raising=False)
    assert notify.GitHubNotifier(session=FakeSession()).user == "someone"


def test_digest_has_links_and_flags_what_to_act_on():
    from vacation_finder.forecast import Outlook
    from vacation_finder.report import TripReport

    from test_pricing import make_trip

    report = TripReport(trip=make_trip(name="Spain"))
    report.outlook = Outlook("Good time to book", ["the fare is low"], "medium")
    report.latest = None
    title, body = notify.digest_markdown(
        [report], date(2026, 10, 26), "https://dash", "https://sheet"
    )
    assert title == "Vacation watch — Oct 26"          # no priced trips yet
    assert "[Dashboard (charts)](https://dash)" in body
    assert "[Spreadsheet (every price)](https://sheet)" in body
