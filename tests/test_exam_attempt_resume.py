"""Opening the exam page must not mint a new attempt every time.

The portal calls KG's `start` on every load of the exam page, so without
resume a refresh, a back button, or a second click of the "your exam is
open" email each left another IN_PROGRESS row behind. Members ended up
with a string of attempts they never sat — which is what the reports were
showing — and because the attempt cap counts everything that is not
ABANDONED, a member could be locked out with "Attempt limit reached"
before answering a single question.
"""
import inspect

from routers import kg as kg_routes


def test_the_exam_page_starts_on_every_load():
    """Stated, because it is the thing that makes resume load-bearing
    rather than a nicety. If this ever stops being true the comment in
    KG's start_attempt should be revisited too."""
    src = inspect.getsource(kg_routes.portal_kg_exam_start)
    assert "kg.start_attempt(" in src


def test_an_old_email_link_does_not_reopen_a_passed_exam():
    """?start_exam=1 bounces the member straight into the exam. The
    enrollment stays EXAM_READY until milestones are recorded, so status
    alone says nothing about whether they have sat it — and an old email
    opened weeks later would drop them back into the paper."""
    src = inspect.getsource(kg_routes.portal_kg_cycle)
    start = src.index("want_start =")
    end = src.index("return templates.TemplateResponse", start)
    bounce = src[start:end]
    assert "not exam_passed" in bounce, (
        "the deep-link bounce must not fire for someone who already passed"
    )


def test_the_take_exam_button_is_hidden_once_passed():
    """Same reasoning, for the button rather than the deep link."""
    tpl = (
        kg_routes.templates.get_template("kg/portal_cycle.html")
        if hasattr(kg_routes.templates, "get_template")
        else None
    )
    source = tpl.environment.loader.get_source(tpl.environment, "kg/portal_cycle.html")[0]
    assert "and not exam_passed" in source


def test_passing_is_read_from_the_attempt_not_the_enrollment():
    """The enrollment sits in EXAM_READY with milestones outstanding, so
    it cannot answer "have they passed?". The attempt can."""
    src = inspect.getsource(kg_routes.portal_kg_cycle)
    assert 'attempts[0].get("passed")' in src
