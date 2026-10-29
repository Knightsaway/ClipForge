from __future__ import annotations

import os
import urllib.parse

from clipforge import bugreport, perf


def test_summarize_cpu_like_task_manager():
    # 2 cores; 1 CPU-second per wall second = one core busy = 50 % of the PC
    s = [perf.Sample(t=float(i), cpu=float(i), ram=100 + i, written=2.0 * i) for i in range(11)]
    r = perf.summarize(s, cores=2, processes=3)
    assert round(r.cpu_pct, 1) == 50.0
    assert round(r.write_mbs, 1) == 2.0
    assert r.ram_peak == 110
    assert r.processes == 3
    assert "CPU 50.0%" in r.text()


def test_summarize_needs_two_samples():
    assert perf.summarize([perf.Sample(0, 0, 0, 0)]) is None


def test_own_process_is_measurable():
    s, found = perf.sample([os.getpid()])
    assert found == 1 and s.ram > 1


def test_scrub_hides_user_names():
    t = bugreport.scrub(r"saved C:\Users\Dani\Videos\ClipForge\a.mp4 and D:/Users/eva/x")
    assert "Dani" not in t and "eva" not in t


def test_issue_url_prefills_form_and_fits():
    url = bugreport.issue_url("Knightsaway/ClipForge", "no clip saved", "Windows 11", "line\n" * 5000)
    assert url.startswith("https://github.com/Knightsaway/ClipForge/issues/new?")
    assert len(url) <= bugreport.MAX_URL
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["template"] == ["bug_report.yml"] and q["what"] == ["no clip saved"]


def test_repo_from_update_url():
    assert bugreport.repo_from("github:someone/Fork@main") == "someone/Fork"
    assert bugreport.repo_from("") == "Knightsaway/ClipForge"
